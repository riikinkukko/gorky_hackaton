"""Responder: тексты ответов. Всегда абсолютная дата, правильные числительные и падежи."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.contracts import Item, ItemStatus, ItemType, Slots
from app.domain import recurrence, ru
from app.domain.timeutil import item_time

LIST_FULL_LIMIT = 5  # до стольких записей читаем полностью
LIST_PREVIEW = 3  # сколько показываем в длинном списке
_SIMILAR = ("похожая запись", "похожие записи", "похожих записей")


@dataclass
class Text:
    text: str
    suggestions: list[str] = field(default_factory=list)
    shown: list[Item] = field(default_factory=list)  # что реально назвали — для контекста «её»


def _q(title: str) -> str:
    return f"«{title}»"


def when_str(item: Item, now: datetime) -> str:
    t = item_time(item)
    if t is None:
        return "без срока"
    return ru.fmt_dt(t, item.has_time, now)


def item_line(item: Item, now: datetime, *, with_date: bool = True) -> str:
    t = item_time(item)
    done = " (выполнено)" if item.status == ItemStatus.DONE else ""
    if t is None:
        return f"{item.title}{done}"
    if with_date:
        return f"{when_str(item, now)} — {item.title}{done}"
    time_part = ru.fmt_time(t.astimezone(now.tzinfo)) if item.has_time else "в течение дня"
    return f"{time_part} — {item.title}{done}"


def _bullets(items: list[Item], now: datetime, with_date: bool) -> str:
    return "\n".join(f"• {item_line(i, now, with_date=with_date)}" for i in items)


def _nearest_adj(t: ItemType | None) -> str:
    return "Ближайшее" if t == ItemType.REMINDER else "Ближайшая"


def _composition(items: list[Item]) -> str:
    """«2 встречи, 5 задач и 1 напоминание»."""
    parts = []
    for t in (ItemType.MEETING, ItemType.TASK, ItemType.REMINDER):
        n = sum(1 for i in items if i.type == t)
        if n:
            parts.append(ru.count(n, ru.type_forms(t)))
    if len(parts) <= 1:
        return ""
    return ", ".join(parts[:-1]) + " и " + parts[-1]


def _about(slots: Slots) -> str:
    """Хвост «с Иваном», «с Вектором», «по запросу «договор»»."""
    bits: list[str] = []
    names = list(slots.participants) + ([slots.organization] if slots.organization else [])
    if names:
        bits.append(ru.with_prep(ru.join_names(names)))
    if slots.query:
        bits.append(f"по запросу «{slots.query}»")
    return (" " + " ".join(bits)) if bits else ""


class Responder:
    # ---------- создание ----------

    def created(self, item: Item, now: datetime, *, deadline: bool = False) -> Text:
        t = item_time(item)
        if item.rrule and t is not None:
            rule = recurrence.describe(item.rrule, t)
            time_part = f", {ru.fmt_time(t.astimezone(now.tzinfo))}" if item.has_time else ""
            head = {
                ItemType.MEETING: "Записал повторяющуюся встречу",
                ItemType.REMINDER: "Буду напоминать",
                ItemType.TASK: "Добавил повторяющуюся задачу",
            }[item.type]
            what = item.title.lower() if item.type == ItemType.REMINDER else _q(item.title)
            return Text(
                f"{head} {what} — {rule}{time_part}. "
                f"Первый раз — {ru.fmt_dt(t, item.has_time, now)}.",
                ["Отменить"],
            )
        if item.type == ItemType.REMINDER:
            text = f"Напомню: {item.title[:1].lower() + item.title[1:]} — {when_str(item, now)}."
        elif item.type == ItemType.MEETING:
            text = f"Записал встречу {_q(item.title)} — {when_str(item, now)}."
        else:
            if t is None:
                text = f"Добавил задачу {_q(item.title)}."
            elif deadline:
                text = f"Добавил задачу {_q(item.title)} — до {when_str(item, now)}."
            else:
                text = f"Добавил задачу {_q(item.title)} — {when_str(item, now)}."
        return Text(text, ["Отменить"])

    def created_default_time(self, item: Item, now: datetime) -> Text:
        """«Напомню сдать тестовое в пт, 30 октября, в 10:00 — сказать другое время?»."""
        t = item_time(item)
        assert t is not None
        t = t.astimezone(now.tzinfo)
        what = item.title[:1].lower() + item.title[1:]
        when = f"{ru.fmt_date(t.date(), now)}, в {ru.fmt_time(t)}"
        return Text(
            f"Напомню {what} в {when} — сказать другое время?", ["Другое время", "Отменить"]
        )

    def past_offer(
        self, start: datetime, has_time: bool, proposed: datetime, now: datetime
    ) -> Text:
        """«Вчера уже прошло — поставить на сегодня?» / «Сегодня в 09:00 уже прошло —
        поставить на завтра, 09:00?»."""
        day = ru.day_phrase(start.date(), now)
        was = f"{day} в {ru.fmt_time(start)}" if has_time else day
        new_day = ru.day_phrase(proposed.date(), now)
        new = f"{new_day}, {ru.fmt_time(proposed)}" if has_time else new_day
        verb = "поставить" if new.startswith(("в ", "во ")) else "поставить на"
        return Text(f"{ru.capitalize(was)} уже прошло — {verb} {new}?", [f"Да, {new}", "Нет"])

    def list_more(
        self,
        items: list[Item],
        rest: int,
        start: datetime,
        end: datetime,
        type_: ItemType | None,
        now: datetime,
    ) -> Text:
        """«а потом что» → следующая порция списка."""
        phrase = ru.period_phrase(start, end, now)
        if not items:
            return Text(f"Это всё {phrase}." if phrase.startswith(("на ", "в ", "во ", "с "))
                        else f"Это всё на {phrase}.")  # fmt: skip
        single_day = (end - start).total_seconds() <= 86400
        tail = f"\nЕщё {rest} — скажите «дальше»." if rest > 0 else "\nЭто всё."
        return Text(f"Дальше:\n{_bullets(items, now, not single_day)}{tail}",
                    ["Дальше"] if rest > 0 else [], items)  # fmt: skip

    def duration(self, item: Item, now: datetime) -> Text:
        """«Сколько длится X?» → «… длится 1 час: 20:45–21:45»."""
        if item.starts_at and item.ends_at and item.ends_at > item.starts_at:
            minutes = int((item.ends_at - item.starts_at).total_seconds() // 60)
            h, m = divmod(minutes, 60)
            parts = ([ru.count(h, ("час", "часа", "часов"))] if h else []) + (
                [ru.count(m, ("минута", "минуты", "минут"))] if m else []
            )
            s = item.starts_at.astimezone(now.tzinfo)
            e = item.ends_at.astimezone(now.tzinfo)
            return Text(
                f"{_q(item.title)} ({ru.fmt_date(s.date(), now)}) длится {' '.join(parts)}: "
                f"{ru.fmt_time(s)}–{ru.fmt_time(e)}.",
                [],
                [item],
            )
        return Text(
            f"У {_q(item.title)} ({when_str(item, now)}) длительность не указана.", [], [item]
        )

    # ---------- уточнения ----------

    def not_understood_title(self) -> Text:
        return Text(
            "Не понял, что записать. Скажите, например: «завтра в 10 позвонить Ивану».",
            ["Что у меня сегодня?"],
        )

    def ask_title(self, type_: ItemType) -> Text:
        return Text(
            {
                ItemType.MEETING: "Как назвать встречу?",
                ItemType.REMINDER: "О чём напомнить?",
                ItemType.TASK: "Что нужно сделать?",
            }[type_]
        )

    def ask_date(self, type_: ItemType, title: str | None) -> Text:
        if type_ == ItemType.REMINDER:
            about = f" {title[:1].lower() + title[1:]}" if title else ""
            return Text(f"Когда напомнить{about}?", ["Через час", "Завтра в 10:00"])
        return Text("На какой день и время поставить встречу?", ["Сегодня", "Завтра"])

    def ask_time(self, type_: ItemType, day: datetime | None, now: datetime) -> Text:
        what = "напомнить" if type_ == ItemType.REMINDER else "поставить встречу"
        q = "В какое время" if type_ == ItemType.REMINDER else "На какое время"
        suggestions = ["10:00", "15:00", "18:00"]
        return Text(f"{q} {what}?", suggestions)

    def ask_list_date(self, text: str, now: datetime) -> Text:
        """«31 ноября» → «В ноябре 30 дней. Показать 30 ноября или 1 декабря?»."""
        m = re.search(r"(\d{1,2})\s+([а-яё]+)", ru.norm(text))
        if m:
            month = next(
                (k for k, g in enumerate(ru.MONTHS_GEN, 1) if ru.norm(g) == m.group(2)), None
            )
            if month is not None:
                year = now.year if month >= now.month else now.year + 1
                days = calendar.monthrange(year, month)[1]
                if int(m.group(1)) > days:
                    last = date(year, month, days)
                    nxt = last + timedelta(days=1)
                    a = ru.fmt_date(last, now, weekday=False)
                    b = ru.fmt_date(nxt, now, weekday=False)
                    prep = ru.MONTHS_PREP[month - 1]
                    return Text(
                        f"В {prep} {ru.count(days, ('день', 'дня', 'дней'))}. "
                        f"Показать {a} или {b}?",
                        [a, b],
                    )
        return Text("Не понял дату. Какой день показать?", ["Сегодня", "Завтра"])

    def ask_when_update(self) -> Text:
        return Text("На когда перенести?", ["Завтра", "На час позже"])

    def not_understood_time(self) -> Text:
        return Text(
            "Не понял время. Скажите, например: «в 15:00» или «завтра в 10».", ["15:00", "Отмена"]
        )

    def choose(self, candidates: list[Item], now: datetime) -> Text:
        lines = "\n".join(f"{n}. {item_line(i, now)}" for n, i in enumerate(candidates, 1))
        sugg = [f"{n}. {i.title}"[:40] for n, i in enumerate(candidates, 1)]
        return Text(f"Нашёл несколько подходящих. Какую именно?\n{lines}", sugg, candidates)

    def target_not_found(self, slots: Slots) -> Text:
        desc = slots.target.description if slots.target and slots.target.description else None
        tail = f" «{desc}»" if desc else ""
        return Text(f"Не нашёл запись{tail}. Уточните, пожалуйста, название или дату.")

    def no_context(self) -> Text:
        return Text(
            "Не понял, о какой записи речь. Назовите её, например: «перенеси встречу с Марией»."
        )

    # ---------- конфликт ----------

    def conflict(
        self, requested: Item, conflicts: list[Item], suggestion: datetime | None, now: datetime
    ) -> Text:
        t = item_time(requested)
        assert t is not None
        req_time = ru.fmt_time(t.astimezone(now.tzinfo))
        day = ru.capitalize(ru.day_phrase(t.astimezone(now.tzinfo).date(), now))

        def at(c: Item) -> str:
            # время того, с чем пересекается: «смета в 10:45», а не запрошенные «10:00»
            ct = item_time(c)
            return f"в {ru.fmt_time(ct.astimezone(now.tzinfo))} " if ct is not None else ""

        if len(conflicts) == 1:
            busy = f"{day} {at(conflicts[0])}у вас {_q(conflicts[0].title)}"
        else:
            listed = ", ".join(f"{at(c)}{_q(c.title)}" for c in conflicts[:3])
            busy = f"{day} на это время уже есть: {listed}"
        if suggestion is None:
            return Text(
                f"{busy}. Всё равно поставить на {req_time}?",
                [f"Всё равно {req_time}", "Отмена"],
                conflicts,
            )
        s = suggestion.astimezone(now.tzinfo)
        s_str = ru.fmt_time(s) if s.date() == t.date() else ru.fmt_dt(s, True, now)
        return Text(
            f"{busy}. Поставить на {s_str} или всё равно на {req_time}?",
            [ru.fmt_time(s) if s.date() == t.date() else s_str, f"Всё равно {req_time}", "Отмена"],
            conflicts,
        )

    # ---------- списки ----------

    def listed(
        self,
        items: list[Item],
        start: datetime,
        end: datetime,
        type_: ItemType | None,
        now: datetime,
    ) -> Text:
        phrase = ru.period_phrase(start, end, now)
        head_phrase = ru.capitalize(phrase)
        single_day = (
            ru.period_phrase(start, end, now) in ("сегодня", "завтра", "послезавтра")
            or start.astimezone(now.tzinfo).date() == end.astimezone(now.tzinfo).date()
            or (end - start).total_seconds() <= 86400
        )
        n = len(items)
        forms = ru.type_forms(type_)
        if n <= LIST_FULL_LIMIT:
            text = (
                f"{head_phrase} у вас {ru.count(n, forms)}:\n{_bullets(items, now, not single_day)}"
            )
            return Text(text, [], items)
        upcoming = [i for i in items if (item_time(i) or now) >= now]
        preview = (upcoming or items)[:LIST_PREVIEW]
        which = "Ближайшие три" if upcoming else "Первые три"
        comp = _composition(items)
        comp_part = f": {comp}" if comp else ""
        sugg = ["Только встречи", "Только задачи"] if type_ is None else ["Покажи все"]
        clarify = " Показать только встречи или только задачи?" if type_ is None else ""
        text = (
            f"{head_phrase} у вас {ru.count(n, forms)}{comp_part}. {which}:\n"
            f"{_bullets(preview, now, not single_day)}\n{clarify.strip()}"
        ).rstrip()
        return Text(text, sugg, preview)

    def empty_list(
        self,
        start: datetime,
        end: datetime,
        type_: ItemType | None,
        next_item: Item | None,
        now: datetime,
    ) -> Text:
        phrase = ru.capitalize(ru.period_phrase(start, end, now))
        what = ru.type_word(type_, "gen_pl") if type_ is not None else None
        text = f"{phrase} {what} нет." if what else f"{phrase} ничего не запланировано."
        if next_item is not None:
            text += f" {_nearest_adj(type_)} — {_q(next_item.title)}, {when_str(next_item, now)}."
            return Text(text, [], [next_item])
        return Text(text)

    # ---------- поиск ----------

    def found(self, results: list[Item], slots: Slots, now: datetime, *, more: int = 0) -> Text:
        """more — сколько ещё менее точных совпадений не названо (их видно в карточках)."""
        t = self._found(results, slots, now)
        if more and results:
            t.text += f"\nЕщё {ru.count(more, _SIMILAR)} — уточните, если нужно не это."
        return t

    def _found(self, results: list[Item], slots: Slots, now: datetime) -> Text:
        n = len(results)
        type_ = slots.type
        if n == 0:
            return Text(f"Не нашёл {ru.type_word(type_, 'gen_pl')}{_about(slots)}.")

        def key_time(i: Item) -> datetime:
            return item_time(i) or now

        past = sorted([i for i in results if key_time(i) < now], key=key_time, reverse=True)
        future = sorted([i for i in results if key_time(i) >= now], key=key_time)

        if slots.tense == "past" or (not future):
            ordered = past + future
            label = "Последняя"
        else:
            ordered = future + past
            label = "Ближайшая"
        if type_ == ItemType.REMINDER:
            label = label[:-2] + "ее"

        if n == 1:
            i = results[0]
            return Text(f"Нашёл: {_q(i.title)} — {when_str(i, now)}.", [], [i])

        if n <= LIST_FULL_LIMIT:
            first, rest = ordered[0], ordered[1:]
            same = all(r.title == first.title for r in rest)
            text = f"{label} — {when_str(first, now)}: {_q(first.title)}."
            if rest:
                if same:
                    more = (
                        " и ".join(when_str(r, now) for r in rest)
                        if len(rest) <= 2
                        else ", ".join(when_str(r, now) for r in rest)
                    )
                    verb = "Ещё были" if slots.tense == "past" or not future else "Ещё"
                    text += f" {verb}: {more}."
                else:
                    text += "\nЕщё:\n" + _bullets(rest, now, True)
            return Text(text, [], ordered)

        preview = ordered[:LIST_PREVIEW]
        which = "Последние три" if label.startswith("Последн") else "Ближайшие три"
        text = (
            f"Нашёл {ru.count(n, ru.type_forms(type_))}{_about(slots)}. {which}:\n"
            f"{_bullets(preview, now, True)}\nУточнить по человеку или дате?"
        )
        return Text(text, ["Только прошедшие", "Только будущие"], preview)

    # ---------- изменения ----------

    def continuation_without_focus(self) -> Text:
        return Text(
            "Не понял, к какой записи это относится. Скажите целиком, например: "
            "«перенеси встречу с Иваном на 12».",
            ["Что у меня сегодня?"],
        )

    def updated(self, before: Item, after: Item, now: datetime) -> Text:
        if after.rrule and after.rrule != before.rrule and (t := item_time(after)) is not None:
            rule = recurrence.describe(after.rrule, t)
            tm = f", {ru.fmt_time(t.astimezone(now.tzinfo))}" if after.has_time else ""
            return Text(f"Теперь {_q(after.title)} повторяется: {rule}{tm}.", ["Отменить"])
        if after.person != before.person and item_time(before) == item_time(after):
            return Text(f"Добавил участника: {_q(after.title)}.", ["Отменить"])
        if before.title != after.title and item_time(before) == item_time(after):
            return Text(f"Переименовал: {_q(before.title)} → {_q(after.title)}.", ["Отменить"])
        return Text(f"Перенёс {_q(after.title)} на {when_str(after, now)}.", ["Отменить"])

    def completed(self, item: Item, now: datetime) -> Text:
        return Text(f"Отметил выполненной: {_q(item.title)} ({when_str(item, now)}).", ["Отменить"])

    def confirm_delete(self, item: Item, now: datetime) -> Text:
        what = ru.type_word(item.type, "acc")
        return Text(
            f"Удалить {what} {_q(item.title)} — {when_str(item, now)}?", ["Да", "Нет"], [item]
        )

    def deleted(self, item: Item, now: datetime) -> Text:
        return Text(f"Удалил {_q(item.title)}. Если передумаете — скажите «отмени».", ["Отменить"])

    def delete_kept(self) -> Text:
        return Text("Хорошо, не удаляю.")

    def cancelled(self) -> Text:
        return Text("Хорошо, отменил.")

    def undone(self, kind: str, item: Item | None, now: datetime) -> Text:
        title = _q(item.title) if item else "последнее действие"
        msg = {
            "create": f"Отменил: {title} больше нет в списке.",
            "delete": f"Вернул {title}.",
            "update": f"Вернул как было: {title} — {when_str(item, now) if item else ''}.",
            "complete": f"Снял отметку о выполнении: {title}.",
        }[kind]
        return Text(msg)

    def undone_batch(self, kind: str, items: list[Item]) -> Text:
        n = ru.count(len(items), ru.RECORD)
        msg = {
            "create": f"Отменил создание: {n}.",
            "delete": f"Вернул {n}.",
            "update": f"Вернул как было: {n}.",
            "complete": f"Снял отметку о выполнении: {n}.",
        }[kind]
        return Text(msg, [], items[:LIST_PREVIEW])

    # ---------- массовые действия ----------

    _MANY_VERB = {  # noqa: RUF012
        "delete_item": ("Удалить", "Удалил"),
        "complete_item": ("Отметить выполненными", "Отметил выполненными"),
        "update_item": ("Перенести", "Перенёс"),
    }

    def confirm_many(
        self,
        intent: str,
        items: list[Item],
        type_: ItemType | None,
        period: tuple[datetime, datetime] | None,
        now: datetime,
        new_when: str | None = None,
    ) -> Text:
        """«Удалить 3 встречи на завтра: …? Да / Нет». Без периода — «все предстоящие» и число."""
        ask = self._MANY_VERB[intent][0]
        what = ru.count(len(items), ru.type_forms_acc(type_))
        tail = f" {new_when}" if new_when else ""
        if period is None:
            n = ru.count(len(items), ru.type_forms(type_))
            head = f"{ask} все предстоящие {ru.type_plural(type_)} — {n}{tail}?"
        else:
            when = ru.period_phrase(period[0], period[1], now)
            when = when if when.startswith(("в ", "во ", "на ", "с ")) else f"на {when}"
            head = f"{ask} {what} {when}{tail}?"
        preview = items[:LIST_FULL_LIMIT] if len(items) <= LIST_FULL_LIMIT else items[:LIST_PREVIEW]
        more = f"\n…и ещё {len(items) - len(preview)}" if len(items) > len(preview) else ""
        return Text(f"{head}\n{_bullets(preview, now, True)}{more}", ["Да", "Нет"], preview)

    def done_many(self, intent: str, items: list[Item], type_: ItemType | None) -> Text:
        did = self._MANY_VERB[intent][1]
        what = ru.count(len(items), ru.type_forms_acc(type_))
        return Text(f"{did} {what}. Если передумаете — скажите «отмени».", ["Отменить"])

    def nothing_for_many(
        self, type_: ItemType | None, period: tuple[datetime, datetime] | None, now: datetime
    ) -> Text:
        what = ru.type_word(type_, "gen_pl")
        if period is None:
            return Text(f"Предстоящих {what} нет.")
        return Text(f"{ru.capitalize(ru.period_phrase(period[0], period[1], now))} {what} нет.")

    # ---------- подтверждение рискованных действий ----------

    def confirm_action(self, intent: str, before: Item, after: Item | None, now: datetime) -> Text:
        """«Дали эту задачу» → не делаем молча: «Отметить выполненной «…»?»."""
        if intent == "complete_item":
            q = f"Отметить выполненной {_q(before.title)} ({when_str(before, now)})?"
        elif intent == "delete_item":
            return self.confirm_delete(before, now)
        else:
            assert after is not None
            q = f"Перенести {_q(before.title)} на {when_str(after, now)}?"
        return Text(q, ["Да", "Нет"], [before])

    def action_kept(self) -> Text:
        return Text("Хорошо, ничего не меняю.")

    # ---------- время: окно и варианты ----------

    def offer_slot(
        self,
        type_: ItemType,
        start: datetime,
        end: datetime | None,
        now: datetime,
        busy_at: datetime | None = None,
    ) -> Text:
        """«Завтра в 13:00 занято. 12:00–13:00 свободно — поставить?»."""
        day = ru.capitalize(ru.day_phrase(start.astimezone(now.tzinfo).date(), now))
        s = ru.fmt_time(start.astimezone(now.tzinfo))
        span = f"{s}–{ru.fmt_time(end.astimezone(now.tzinfo))}" if end and end > start else f"в {s}"
        if busy_at is not None:
            busy = ru.fmt_time(busy_at.astimezone(now.tzinfo))
            return Text(
                f"{day} в {busy} занято. Свободно {span} — поставить?", [f"Да, {s}", "Другое время"]
            )
        return Text(f"{day} {span} свободно — поставить?", [f"Да, {s}", "Другое время"])

    def created_many(self, items: list[Item], type_: ItemType, now: datetime) -> Text:
        """«Записал 2 встречи: • 08:00 — Пара • 14:40 — Пара»."""
        what = ru.count(len(items), ru.type_forms_acc(type_))
        verb = "Записал" if type_ == ItemType.MEETING else "Добавил"
        return Text(f"{verb} {what}:\n{_bullets(items, now, True)}", ["Отменить"], items)

    def window_busy(self, start: datetime, sugg: datetime | None, now: datetime) -> Text:
        day = ru.capitalize(ru.day_phrase(start.astimezone(now.tzinfo).date(), now))
        if sugg is None:
            return Text(f"{day} в это время всё занято. На какое время поставить?")
        s = sugg.astimezone(now.tzinfo)
        same = s.date() == start.astimezone(now.tzinfo).date()
        when = ru.fmt_time(s) if same else ru.fmt_dt(s, True, now)
        return Text(
            f"{day} в это время всё занято. Ближайшее свободное — {when}. Поставить?",
            [f"Да, {ru.fmt_time(s)}", "Другое время"],
        )

    def ask_options(self, options: list[datetime], now: datetime) -> Text:
        """«в 5 или в 6» → «Во сколько: 17:00 или 18:00?» + кнопки."""
        times = [ru.fmt_time(o.astimezone(now.tzinfo)) for o in options]
        joined = ", ".join(times[:-1]) + " или " + times[-1] if len(times) > 1 else times[0]
        return Text(f"Во сколько поставить: {joined}?", times)

    def nothing_to_undo(self) -> Text:
        return Text("Отменять нечего.")

    def nothing_to_confirm(self) -> Text:
        return Text("Мне сейчас нечего подтверждать. Чем помочь?", ["Что у меня сегодня?"])

    def smalltalk(self) -> Text:
        return Text(
            "Я помогаю с задачами, напоминаниями и встречами. Например: «Что у меня сегодня?» "
            "или «Напомни завтра в 10 позвонить Ивану».",
            ["Что у меня сегодня?", "Что на неделе?"],
        )

    def unknown(self) -> Text:
        return Text(
            "Не совсем понял. Могу создать задачу или встречу, показать планы или найти запись.",
            ["Что у меня сегодня?", "Найди встречу с Иваном"],
        )

    def error(self) -> Text:
        return Text("Что-то пошло не так. Повторите, пожалуйста, или скажите иначе.")

"""DialogManager: слот-филлинг как конечный автомат, контекст, подтверждения, undo.

Состояние диалога — DialogState из контракта; менеджер изменяет его на месте,
сохранение состояния — забота вызывающего кода (api/, чат 0).

Соглашения поверх контракта (без его изменения):
- для update/complete/delete поля slots.type / participants / organization / when описывают
  ЦЕЛЬ («встречу с Марией в субботу»), новое время — slots.new_when;
- в состоянии awaiting="confirm_conflict": pending.slots.when — запрошенное время,
  pending.slots.new_when — предложенный свободный слот (оба абсолютные);
- в состоянии awaiting="target": state.last_item_ids — показанные кандидаты по порядку.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.contracts import (
    AssistantReply,
    Command,
    DateResolver,
    DialogState,
    Intent,
    Item,
    ItemRef,
    ItemStatus,
    ItemType,
    Slots,
    TimeSpec,
)
from app.dialog.normalize import (
    after_correction,
    clean_title,
    colloquial_time,
    cut_correction,
    guard_title,
    meaningful,
    only_time_words,
    relative_date,
    strip_phrase,
)
from app.dialog.targets import ALL, LAST, Resolution, TargetResolver, content_stems
from app.dialog.turns import (
    Continuation,
    TurnKind,
    classify_turn,
    continuation_of,
    correction_text,
    drop_inherited,
)
from app.domain import ru
from app.domain.responder import LIST_FULL_LIMIT, LIST_PREVIEW, Responder, Text
from app.domain.service import ItemService
from app.domain.timeutil import combine, day_bounds, default_duration_min, item_time

CANCEL_WORDS = (
    "отмена", "отменить", "отбой", "не надо", "нет не надо", "не нужно", "нет не нужно",
    "забудь", "стоп", "передумал", "передумала",
)  # fmt: skip
FORCE_WORDS = ("всё равно", "все равно", "оставь", "оставить", "так и ставь", "так и оставь")
YES_WORDS = {
    "да",
    "ага",
    "давай",
    "конечно",
    "хорошо",
    "ок",
    "окей",
    "угу",
    "подтверждаю",
    "удаляй",
    "верно",
}

NEW_COMMAND_INTENTS = {Intent.LIST, Intent.SEARCH, Intent.UPDATE, Intent.COMPLETE, Intent.DELETE}


FILLERS = {
    "ну", "э", "ээ", "эээ", "э-э", "эм", "эмм", "мм", "ммм", "хм", "так", "вот", "короче",
    "слушай", "типа", "значит", "это", "а", "ой", "ой-ой", "блин", "кстати", "пожалуй",
    "наверное", "наверно", "давай", "давайте", "ладно",
}  # fmt: skip


def _norm(text: str) -> str:
    return ru.norm(text).strip(" .!?,…")


def _words(text: str) -> list[str]:
    """Слова короткого ответа без пунктуации и пауз («э-э… ну да» → [«да»])."""
    return [w for w in re.findall(r"[a-zа-яё0-9:-]+", ru.norm(text)) if w not in FILLERS]


def _is_cancel(text: str) -> bool:
    t = " ".join(_words(text))
    return any(t == w or t.startswith(w + " ") for w in CANCEL_WORDS)


def _is_yes(text: str) -> bool:
    raw = _norm(text).replace(",", " ").split()
    if raw and raw[0] in YES_WORDS:
        return True
    words = _words(text)
    # «ну да, давай» → да; «давай» само по себе — тоже согласие
    return (bool(words) and words[0] in YES_WORDS) or (
        not words and bool(raw) and any(w in ("давай", "давайте", "ладно") for w in raw)
    )


def _has_force(text: str) -> bool:
    t = _norm(text)
    return any(w in t for w in FORCE_WORDS)


def _shift_minutes(spec: TimeSpec | None) -> int | None:
    """«на час позже» → +60. NLU отдаёт такой new_when без start (контракт: parse_shift чата 1)."""
    if spec is None or spec.start is not None or not spec.text:
        return None
    try:
        from app.nlu import parse_shift
    except ImportError:  # диалог должен работать и без пакета NLU
        return None
    return parse_shift(spec.text)


# «в 5 или в 6», «может быть в 5, может быть в 6», «либо в 5, либо в 6»
_ALTERNATIVES = re.compile(r"\bили\b|\bлибо\b|может быть")  # выбор одного из вариантов
_BOTH = re.compile(r"\bи\b")  # «в восемь и в 14:40» — оба события
_ALT_SPLIT = re.compile(r"\bили\b|\bлибо\b|может быть|\bи\b|,")
_NUMBER_WORDS = set(
    "один одна две два три четыре пять шесть семь восемь девять десять одиннадцать "
    "двенадцать полдень полночь и или".split()
)
SAFE_CONFIDENCE = 0.85  # ниже — рискованные действия по «эту/её» только с подтверждением
MASS_HORIZON = timedelta(days=3660)  # «удали все встречи» без периода — все предстоящие
SLOT_STEP = timedelta(minutes=15)
# «в обед», «утром», «вечером»: окно (если NLU не дал конец) и обычное время в нём
DAY_WINDOWS = (
    ("обед", 12, 14, 13),
    ("утр", 8, 12, 9),
    ("вечер", 18, 21, 19),
    ("днём", 12, 17, 13),
    ("днем", 12, 17, 13),
    ("дня", 12, 17, 13),
)


_CONTINUE = re.compile(
    r"^(?:а\s+|и\s+|ну\s+)?(?:потом|дальше|ещё|еще|следующие|остальные|покажи\s+(?:ещё|еще|дальше)"
    r"|что\s+(?:ещё|еще|дальше|потом)|а\s+что\s+(?:ещё|еще|дальше|потом))(?:\s+что)?[\s?!.]*$"
)
_DURATION_Q = re.compile(
    r"сколько\s+(?:по\s+времени\s+)?(?:длится|продлится|будет\s+длиться|идет|займет)"
    r"|как\s+долго|какая\s+длительность|длительность"
)
_QUESTION_WORDS = {"сколько", "будет", "по", "времени", "как", "долго", "какая", "у", "меня"}
_DURATION_WORD = re.compile(r"^(?:длит\w*|продлит\w*|длительн\w*|долго|сколько|займ\w*)$")


def _is_continuation(cmd: Command) -> bool:
    """«а потом что», «дальше», «что ещё» — продолжение прошлого списка."""
    if cmd.intent not in (Intent.LIST, Intent.SEARCH, Intent.UNKNOWN, Intent.SMALLTALK):
        return False
    s = cmd.slots
    if s.when is not None and s.when.start is not None:
        return False  # «а на следующей неделе?» — новый период, это обычный список
    return bool(_CONTINUE.match(ru.norm(cmd.raw_text).strip()))


def _period(when: TimeSpec | None, now: datetime) -> tuple[datetime, datetime]:
    """Период списка: контракт v1.1 — [start, end), для дня end = 00:00 следующего дня."""
    if when is None or when.start is None:
        return day_bounds(now)
    if when.is_period:
        return when.start, when.end or day_bounds(when.start)[1]
    return day_bounds(when.start)


DEFAULT_REMINDER_HOUR = 10


def _default_reminder_time(day: datetime, now: datetime) -> datetime:
    """Напоминание без времени — в 10:00 того дня; если 10:00 сегодня уже прошло — через час."""
    at = day.astimezone(now.tzinfo).replace(
        hour=DEFAULT_REMINDER_HOUR, minute=0, second=0, microsecond=0
    )
    if at <= now:
        at = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return at


def _edit_command(cmd: Command, item_id: int, spec: TimeSpec | None) -> Command:
    """Что фактически выполнено при продолжении: правка записи (для last_command и debug)."""
    return Command(
        intent=Intent.UPDATE,
        source=cmd.source,
        raw_text=cmd.raw_text,
        confidence=cmd.confidence,
        slots=Slots(target=ItemRef(item_id=item_id), new_when=spec,
                    recurrence=cmd.slots.recurrence, participants=cmd.slots.participants),
    )  # fmt: skip


def _is_all(cmd: Command) -> bool:
    return bool(cmd.slots.target and cmd.slots.target.description == ALL)


def _by_context(cmd: Command) -> bool:
    """Цель названа только местоимением/контекстом: «эту задачу», «её»."""
    s, ref = cmd.slots, cmd.slots.target
    if ref is not None and (ref.item_id is not None or ref.description in (LAST, ALL)):
        return False
    described = bool(ref and ref.description and content_stems(ref.description))
    has_when = bool(s.when and s.when.start)
    return not (described or s.participants or s.organization or has_when)


def _is_new_command(cmd: Command) -> bool:
    """Полноценная новая команда, а не ответ на уточнение."""
    return cmd.intent in NEW_COMMAND_INTENTS or (
        cmd.intent == Intent.CREATE and bool(cmd.slots.title)
    )


def _abs_spec(dt: datetime, label: str, has_time: bool = True) -> TimeSpec:
    return TimeSpec(text=label, start=dt, has_time=has_time, has_date=True)


def merge_spec(base: TimeSpec | None, answer: TimeSpec, now: datetime) -> TimeSpec:
    """«завтра» + «в три» → завтра 15:00; «в три» + «завтра» → завтра 15:00."""
    if base is None or base.start is None:
        return answer
    dt, has_time = combine(base.start, base.has_time, answer, now)
    if not base.has_time and not answer.has_time:
        dt, has_time = answer.start, False
    return TimeSpec(
        text=f"{base.text} {answer.text}".strip(),
        start=dt,
        has_time=has_time,
        has_date=base.has_date or answer.has_date,
    )


class DialogManager:
    def __init__(
        self,
        service: ItemService,
        responder: Responder | None = None,
        dates: DateResolver | None = None,
    ) -> None:
        self.service = service
        self.dates = dates  # запасной разбор ответа на «На какое время?», если NLU не дал when
        self.r = responder or Responder()
        self.targets = TargetResolver(service)

    # ================= вход =================

    async def handle(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        reply, _ = await self.handle_turn(cmd, state, now)
        return reply

    async def handle_turn(
        self, cmd: Command, state: DialogState, now: datetime
    ) -> tuple[AssistantReply, Command]:
        """Ответ + команда, которая фактически исполнялась в этом ходе.

        Если ход завершил уточнение («в три»), это собранная pending-команда
        («встреча с Иваном завтра в 15:00»), а не голый ответ — её и пишем в last_command.
        """
        # Таблица «состояние × тип реплики» (turns.py): здесь — единственное место решения.
        kind = classify_turn(cmd, state)
        if kind == "full":
            cmd = drop_inherited(cmd, state.last_command)  # новая команда не наследует слоты
        if state.awaiting and state.pending is not None:
            pending = state.pending  # _continue дополняет слоты этого же объекта
            reply = await self._continue(cmd, state, now, kind)
            if reply is not None:
                done = reply.action not in ("none", "clarify") and state.pending is not pending
                return reply, (pending if done else cmd)
            self._clear(state)  # пользователь начал новую команду — старую бросаем
        elif state.awaiting:
            self._clear(state)
        if (
            _is_continuation(cmd)
            and state.last_command
            and state.last_command.intent == Intent.LIST
        ):
            return await self._list_more(state.last_command, state, now), state.last_command
        if kind == "continuation":
            focused = await self._continue_focus(cmd, state, now)
            if focused is not None:
                return focused
        return await self._dispatch(cmd, state, now), cmd

    async def _dispatch(
        self, cmd: Command, state: DialogState, now: datetime, *, force: bool = False
    ) -> AssistantReply:
        if cmd.intent in (Intent.CREATE, Intent.UNKNOWN) and _DURATION_Q.search(
            ru.norm(cmd.raw_text)
        ):
            return await self._duration(cmd, state, now)
        match cmd.intent:
            case Intent.CREATE:
                return await self._create(cmd, state, now, force=force)
            case Intent.LIST:
                return await self._list(cmd, state, now)
            case Intent.SEARCH:
                return await self._search(cmd, state, now)
            case Intent.UPDATE:
                return await self._update(cmd, state, now)
            case Intent.COMPLETE:
                return await self._complete(cmd, state, now)
            case Intent.DELETE:
                return await self._delete(cmd, state, now)
            case Intent.UNDO:
                return await self._undo(state, now)
            case Intent.CONFIRM:
                return self._reply(self.r.nothing_to_confirm(), "none")
            case Intent.REJECT:
                return self._reply(Text("Хорошо."), "none")
            case Intent.SMALLTALK:
                return self._reply(self.r.smalltalk(), "none")
            case _:
                return self._reply(self.r.unknown(), "none")

    # ================= продолжение незавершённого =================

    async def _continue(
        self, cmd: Command, state: DialogState, now: datetime, kind: TurnKind = "full"
    ) -> AssistantReply | None:
        awaiting, pending = state.awaiting, state.pending
        assert pending is not None
        text = cmd.raw_text

        if awaiting == "time_optional":
            return await self._continue_optional_time(cmd, state, now, kind)

        if (
            cmd.intent == Intent.UNDO
            or kind == "reject"
            or (kind != "continuation" and (cmd.intent == Intent.REJECT or _is_cancel(text)))
        ):
            self._clear(state)
            if awaiting in ("confirm_delete", "confirm_many") and pending.intent == Intent.DELETE:
                t = self.r.delete_kept()
            elif awaiting in ("confirm_action", "confirm_many"):
                t = self.r.action_kept()
            else:
                t = self.r.cancelled()
            return self._reply(t, "none")

        if awaiting in ("confirm_action", "confirm_many"):
            if (
                kind == "continuation"
                and awaiting == "confirm_action"
                and pending.intent == Intent.UPDATE
            ):
                # «нет, в 11» на «Перенести … на 12:00?» — меняем время в предложенном переносе
                spec = self._answer_spec(cmd, correction_text(text), now)
                if spec is not None and spec.start is not None:
                    pending.slots.new_when = merge_spec(pending.slots.new_when, spec, now)
                    if pending.confidence >= SAFE_CONFIDENCE:
                        return await self._resume(state, now)
                    # команда была восстановлена из обрывка — новое время тоже подтверждаем
                    target = pending.slots.target
                    before = (
                        await self.service.get(state.user_id, target.item_id)
                        if target and target.item_id
                        else None
                    )
                    if before is not None:
                        assert pending.slots.new_when is not None
                        after = self.service.moved(before, pending.slots.new_when, now)
                        return self._confirm_action(pending, state, before, after, now)
            if cmd.intent == Intent.CONFIRM or _is_yes(text):
                if awaiting == "confirm_many":
                    return await self._apply_many(state, now)
                return await self._resume(state, now)
            if kind != "continuation" and (_is_new_command(cmd) or cmd.intent == Intent.CREATE):
                return None
            return self._reply(Text("Скажите «да» или «нет».", ["Да", "Нет"]), "clarify")

        if awaiting == "title":
            if cmd.intent in NEW_COMMAND_INTENTS or _DURATION_Q.search(ru.norm(text)):
                return None
            if (
                cmd.intent == Intent.CREATE
                and meaningful(cmd.slots.title)
                and cmd.slots.when is not None
                and cmd.slots.when.start is not None
                and kind == "full"
            ):
                return None  # «и напомни мне в четверг вечером …» — новая запись, не название
            title = (cmd.slots.title or text).strip().strip(".!?")
            pending.slots.title = clean_title(title)
            if cmd.slots.when and cmd.slots.when.start:
                pending.slots.when = merge_spec(pending.slots.when, cmd.slots.when, now)
            return await self._resume(state, now)

        if awaiting in ("time", "date"):
            # «в последний день месяца» в ответ на «Когда напомнить?» NLU может принять за
            # поиск или новую встречу. Ответ из одних слов времени — это ответ: тип и название
            # записи остаются прежними.
            answer_like = (
                kind == "continuation"
                or only_time_words(text)
                or (cmd.intent == Intent.CREATE and not meaningful(cmd.slots.title))
            )
            if not answer_like and (
                cmd.intent in NEW_COMMAND_INTENTS
                or (cmd.intent == Intent.CREATE and cmd.slots.title)
            ):
                return None
            spec = self._answer_spec(cmd, text, now)
            if spec is None or spec.start is None:
                return self._reply(self.r.not_understood_time(), "clarify")
            field = "new_when" if pending.intent == Intent.UPDATE else "when"
            setattr(pending.slots, field, merge_spec(getattr(pending.slots, field), spec, now))
            return await self._resume(state, now)

        if awaiting == "target":
            cands = [
                i
                for i in [await self.service.get(state.user_id, x) for x in state.last_item_ids]
                if i
            ]
            # «вторую» / «с дизайнером» — выбор; полная команда («Что у меня сегодня?») — новый
            # запрос. Короткое «с дизайнером» NLU может принять за поиск — тогда пробуем выбор.
            new_cmd = (
                kind != "continuation"
                and _is_new_command(cmd)
                and not TargetResolver.is_choice(text)
            )
            if new_cmd and cmd.intent != Intent.SEARCH:
                return None
            chosen = TargetResolver.pick(text, cands)
            if chosen is None and new_cmd:
                return None
            if chosen is None:
                return self._reply(self.r.choose(cands, now), "clarify", cands)
            pending.slots.target = ItemRef(item_id=chosen.id)
            return await self._resume(state, now)

        if awaiting == "confirm_delete":
            if cmd.intent == Intent.CONFIRM or _is_yes(text):
                target = pending.slots.target
                item = (
                    await self.service.get(state.user_id, target.item_id)
                    if target and target.item_id
                    else None
                )
                self._clear(state)
                if item is None:
                    return self._reply(self.r.target_not_found(pending.slots), "none")
                state.last_action_id = await self.service.delete(item, state.session_id)
                state.last_item_ids = []
                return self._reply(self.r.deleted(item, now), "deleted", [item])
            if kind != "continuation" and (
                cmd.intent in NEW_COMMAND_INTENTS or cmd.intent == Intent.CREATE
            ):
                return None
            return self._reply(Text("Удалить? Скажите «да» или «нет».", ["Да", "Нет"]), "clarify")

        if awaiting == "confirm_conflict":
            return await self._continue_conflict(cmd, state, now, kind)

        return None

    async def _continue_conflict(
        self, cmd: Command, state: DialogState, now: datetime, kind: TurnKind = "full"
    ) -> AssistantReply | None:
        pending = state.pending
        assert pending is not None
        requested, suggested = pending.slots.when, pending.slots.new_when
        assert requested is not None and requested.start is not None
        text = cmd.raw_text

        if kind != "continuation" and _is_new_command(cmd):
            return None  # «Что у меня сегодня?» во время вопроса — новая команда, не «другое время»
        force = False
        corrected = (
            self._answer_spec(cmd, correction_text(text), now) if kind == "continuation" else None
        )
        if corrected is not None and corrected.start is not None and not _has_force(text):
            chosen = merge_spec(requested, corrected, now)  # «давай в 17», «нет, в 17»
        elif _has_force(text):
            chosen, force = requested, True
        elif cmd.intent == Intent.CONFIRM or _is_yes(text):
            chosen, force = (
                (suggested, True) if suggested and suggested.start else (requested, True)
            )
        else:
            spec = cmd.slots.when or cmd.slots.new_when
            if spec is None or spec.start is None:
                spec = self._answer_spec(cmd, correction_text(text), now)
            if spec is not None and spec.start is not None:
                chosen = merge_spec(requested, spec, now)
            else:
                sugg = (
                    suggested.start.astimezone(now.tzinfo)
                    if suggested and suggested.start
                    else None
                )
                hint = f"«да» — на {ru.fmt_time(sugg)}, " if sugg else ""
                return self._reply(
                    Text(
                        f"Скажите {hint}«всё равно» или другое время.",
                        ([ru.fmt_time(sugg)] if sugg else [])
                        + [
                            f"Всё равно {ru.fmt_time(requested.start.astimezone(now.tzinfo))}",
                            "Отмена",
                        ],
                    ),
                    "clarify",
                )
        assert chosen.start is not None
        self._clear(state)
        if pending.intent == Intent.UPDATE:
            target = pending.slots.target
            before = (
                await self.service.get(state.user_id, target.item_id)
                if target and target.item_id
                else None
            )
            if before is None:
                return self._reply(self.r.target_not_found(pending.slots), "none")
            after = self.service.moved_to(before, chosen.start, True, now)
            return await self._apply_update(state, before, after, now, force=force)
        cmd2 = pending.model_copy(deep=True)
        cmd2.slots.when = _abs_spec(chosen.start, requested.text, chosen.has_time)
        cmd2.slots.new_when = None
        return await self._create(cmd2, state, now, force=force)

    def _time_options(self, when: TimeSpec, said: str, now: datetime) -> list[datetime]:
        """Времена из «в 5 или в 6» / «в восемь и в 14:40»: поле options (если появится
        в контракте), иначе части фразы через DateResolver, иначе start/end.
        Только если в сказанном есть «или / либо / может быть / и» — окно «12–14» не варианты."""
        opts = getattr(when, "options", None)
        if opts:
            return sorted(opts)
        if not (_ALTERNATIVES.search(said) or _BOTH.search(said)):
            return []
        assert when.start is not None
        out: list[datetime] = []
        if self.dates is not None:
            # сначала фраза времени от NLU, затем вся сказанная фраза (NLU мог оставить
            # в when.text только первый вариант: «в 5 часов вечера»)
            for source in (ru.norm(when.text), said):
                found: list[datetime] = []
                for part in _ALT_SPLIT.split(source):
                    spec = self._resolve_answer(part, now)
                    if spec is not None and spec.start is not None and spec.has_time:
                        t = spec.start.astimezone(now.tzinfo)
                        dt = when.start.replace(
                            hour=t.hour, minute=t.minute, second=0, microsecond=0
                        )
                        if dt not in found:
                            found.append(dt)
                if len(found) >= 2:
                    out = found
                    break
        if len(out) < 2 and when.end is not None and when.end != when.start:
            out = [when.start, when.end]  # NLU положил два времени в start/end
        uniq = sorted({o.replace(second=0, microsecond=0) for o in out})
        return uniq if len(uniq) >= 2 else []  # «13:30 или 13:30» — не выбор

    @staticmethod
    def _window(when: TimeSpec) -> tuple[datetime, datetime, datetime] | None:
        """(начало, конец, обычное время) окна «в обед / утром / вечером»."""
        if when.start is None:
            return None
        low = ru.norm(when.text)
        hours = next(((a, b, c) for k, a, b, c in DAY_WINDOWS if k in low), None)
        if hours is not None:
            start = when.start.replace(hour=hours[0], minute=0, second=0, microsecond=0)
            return start, start.replace(hour=hours[1]), start.replace(hour=hours[2])
        if when.end is not None and when.start < when.end <= when.start + timedelta(hours=12):
            mid = when.start + (when.end - when.start) / 2
            return when.start, when.end, mid.replace(minute=0, second=0, microsecond=0)
        return None

    def _fix_when_from_speech(self, cmd: Command, state: DialogState, now: datetime) -> None:
        """Подстраховка разбора времени по сказанному (NLU мог промахнуться):
        - самоисправление «в три, нет, в четыре» / «в среду, ой нет, в четверг» — берём
          последнее; из названия убираем «ой нет в четверг»;
        - «полчетвёртого», «без четверти двенадцать», если NLU время не нашёл;
        - «Покажи встречи на понедельник → добавь созвон в три»: только время — день из
          только что показанного списка."""
        s = cmd.slots
        raw = cmd.raw_text or ""
        base = s.when if s.when is not None and s.when.start is not None else None
        tail = after_correction(raw)
        if tail and only_time_words(tail):
            spec = self._resolve_answer(tail, now) or colloquial_time(tail, now)
            if spec is not None and spec.start is not None:
                merged = merge_spec(base, spec, now)
                s.when = merged.model_copy(update={"ambiguous": False, "end": None})
                if s.title:
                    s.title = cut_correction(s.title)
                return
        if s.title:  # «Встреча с Олегом то есть с Игорем» → «Встреча с Игорем»
            s.title = cut_correction(s.title)
        if base is None or not base.has_time:
            spec = colloquial_time(raw, now)
            if spec is not None:
                s.when = merge_spec(base, spec, now).model_copy(update={"ambiguous": False})
                base = s.when
        last = state.last_command
        if (
            base is None
            and re.search(r"(?<![а-яё])туда(?![а-яё])|этот\s+день", ru.norm(raw))
            and last is not None
            and last.intent == Intent.LIST
            and last.slots.when is not None
            and last.slots.when.start is not None
            and not last.slots.when.is_period
        ):
            day = last.slots.when.start.astimezone(now.tzinfo)
            s.when = TimeSpec(text="туда", start=day.replace(hour=0, minute=0), has_date=True)
            return
        if (
            base is not None
            and base.has_time
            and not base.has_date
            and last is not None
            and last.intent == Intent.LIST
            and last.slots.when is not None
            and last.slots.when.start is not None
            and not last.slots.when.is_period
        ):
            assert base.start is not None
            day = last.slots.when.start.astimezone(now.tzinfo)
            t = base.start.astimezone(now.tzinfo)
            s.when = base.model_copy(
                update={
                    "start": day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0),
                    "has_date": True,
                }
            )

    def _past_offer(self, when: TimeSpec, now: datetime) -> tuple[TimeSpec, Text] | None:
        """«На вчера» → «Вчера уже прошло — поставить на сегодня?»;
        «сегодня в 9» вечером → «…уже прошло — поставить на завтра, 09:00?»."""
        if when.start is None or when.is_period:
            return None
        start = when.start.astimezone(now.tzinfo)
        today = now.date()
        if start.date() < today:
            proposed_dt = start.replace(year=today.year, month=today.month, day=today.day)
            if when.has_time and proposed_dt <= now:
                proposed_dt += timedelta(days=1)
        elif when.has_time and start < now - timedelta(minutes=1):
            proposed_dt = start + timedelta(days=1)
        else:
            return None
        proposed = TimeSpec(
            text=when.text, start=proposed_dt, has_time=when.has_time, has_date=True
        )
        return proposed, self.r.past_offer(start, when.has_time, proposed_dt, now)

    async def _create_each(
        self, cmd: Command, state: DialogState, times: list[datetime], now: datetime
    ) -> AssistantReply:
        """Несколько событий из одной фразы — все сразу, одним пакетом для «отмени»."""
        s = cmd.slots
        type_ = s.type or ItemType.TASK
        words = [w for w in (s.title or "").split() if ru.norm(w) not in _NUMBER_WORDS]
        title = clean_title(" ".join(words)) if words else ru.capitalize(ru.type_word(type_, "nom"))
        saved: list[Item] = []
        action_ids: list[int] = []
        for t in times:
            item = self.service.build(
                state.user_id, type_, title, t, True, now,
                duration_min=s.duration_min, participants=s.participants,
                organization=s.organization,
            )  # fmt: skip
            it, a = await self.service.create(item, state.session_id)
            saved.append(it)
            action_ids.append(a)
        self.service.register_batch(state.user_id, action_ids)
        state.last_item_ids, state.last_action_id = [i.id for i in saved], action_ids[-1]
        return self._reply(self.r.created_many(saved, type_, now), "created", saved)

    async def _create_in_window(
        self, cmd: Command, state: DialogState, when: TimeSpec, now: datetime
    ) -> AssistantReply:
        """«Встреча завтра в обед» → сразу на обычное время окна (13:00), если оно свободно;
        занято — ближайшее свободное в окне с вопросом."""
        window = self._window(when)
        assert window is not None and cmd.slots.type is not None
        start, end, usual = window
        type_ = cmd.slots.type
        minutes = cmd.slots.duration_min or default_duration_min(type_, cmd.slots.title or "") or 30
        duration = timedelta(minutes=minutes)

        async def free(t: datetime) -> bool:
            if type_ != ItemType.MEETING:
                return True  # задача и напоминание время не занимают
            return not await self.service.conflicts.check(state.user_id, t, t + duration)

        exact = cmd.model_copy(deep=True)
        if usual >= now and await free(usual):
            exact.slots.when = _abs_spec(usual, when.text)
            return await self._create(exact, state, now)
        slots: list[datetime] = []
        t = start
        while t + duration <= end:
            if t >= now:
                slots.append(t)
            t += SLOT_STEP
        slots.sort(key=lambda x: abs(x - usual))
        slot = None
        for t in slots:
            if await free(t):
                slot = t
                break
        if slot is None:
            sugg = await self.service.conflicts.suggest(state.user_id, start, start + duration, now)
            text = self.r.window_busy(start, sugg, now)
            if sugg is None:
                exact.slots.when = TimeSpec(
                    text=when.text, start=start.replace(hour=0), has_date=True
                )
                return self._ask(state, exact, "time", text)
            slot = sugg
        else:
            show_end = slot + duration if type_ == ItemType.MEETING else None
            text = self.r.offer_slot(type_, slot, show_end, now, busy_at=usual)
        exact.slots.when = _abs_spec(slot, when.text)
        exact.slots.new_when = _abs_spec(slot, "свободный слот")
        state.pending, state.awaiting = exact, "confirm_conflict"
        return self._reply(text, "clarify")

    async def _continue_optional_time(
        self, cmd: Command, state: DialogState, now: datetime, kind: TurnKind = "full"
    ) -> AssistantReply | None:
        """После «Напомню … в 10:00 — сказать другое время?»."""
        pending = state.pending
        assert pending is not None
        text = cmd.raw_text
        if cmd.intent == Intent.UNDO or _is_cancel(text):
            self._clear(state)  # «Отменить» под ответом — убрать только что созданное
            return await self._undo(state, now)
        if _is_yes(text) or cmd.intent in (Intent.CONFIRM, Intent.REJECT):
            self._clear(state)  # «да» / «нет, не надо» — время оставляем
            return self._reply(Text("Хорошо, оставляю так."), "none")
        if re.search(r"друго[ей] врем", ru.norm(text)):
            state.awaiting = "time"
            return self._reply(
                Text("Во сколько напомнить?", ["09:00", "12:00", "18:00"]), "clarify"
            )
        explicit_target = bool(
            cmd.slots.target
            and (
                cmd.slots.target.item_id is not None or content_stems(cmd.slots.target.description)
            )
        )
        if kind != "continuation" and (
            (cmd.intent == Intent.CREATE and cmd.slots.title)
            or explicit_target
            or (cmd.intent in NEW_COMMAND_INTENTS and cmd.intent != Intent.UPDATE)
        ):
            return None  # новая команда — напоминание остаётся на 10:00
        spec = cmd.slots.new_when or cmd.slots.when
        if spec is None or spec.start is None or not spec.has_time:
            spec = self._answer_spec(cmd, correction_text(text), now)
        if spec is None or spec.start is None or not spec.has_time:
            return None
        pending.slots.new_when = spec.model_copy(update={"has_date": False})  # дата — та же
        return await self._resume(state, now)

    def _answer_spec(self, cmd: Command, text: str, now: datetime) -> TimeSpec | None:
        for spec in (cmd.slots.when, cmd.slots.new_when):
            if spec is not None and spec.start is not None:
                return spec
        spec = self._resolve_answer(text, now)
        if spec is not None:
            return spec
        rel = relative_date(text, now)
        if rel:
            return rel[0]
        return colloquial_time(text, now)

    def _resolve_answer(self, text: str, now: datetime) -> TimeSpec | None:
        if self.dates is None:
            return None
        try:
            spec = self.dates.resolve(text, now)
        except Exception:
            return None
        return spec if spec.start is not None and (spec.has_time or spec.has_date) else None

    async def _resume(self, state: DialogState, now: datetime) -> AssistantReply:
        cmd = state.pending
        assert cmd is not None
        self._clear(state)
        return await self._dispatch(cmd, state, now)

    # ================= создание =================

    async def _create(
        self, cmd: Command, state: DialogState, now: datetime, *, force: bool = False
    ) -> AssistantReply:
        s = cmd.slots
        type_ = s.type or ItemType.TASK
        s.type = type_
        default_time = False
        self._fix_when_from_speech(cmd, state, now)
        title = (s.title or "").strip()
        if (s.when is None or s.when.start is None) and (
            rel := relative_date(f"{title} {s.when.text if s.when else ''}", now)
        ):
            # «в последний день месяца сдать отчёт»: DateResolver пока такого не знает
            s.when = rel[0]
            title = strip_phrase(title, rel[1]) if title else title
        rejected = False
        if title:
            # страховка независимо от NLU: «Что», «Нет не», «Встреча с», мат, время в названии
            guarded = guard_title(title)
            numbers_only = all(w in _NUMBER_WORDS for w in ru.norm(title).split())
            if guarded is None and numbers_only and s.when is not None and s.when.has_time:
                # «Две восемь и»: NLU оставил в названии числа из времени, а время разобрано
                guarded = ru.capitalize(ru.type_word(type_, "nom"))
            rejected = guarded is None
            title = guarded or ""
        if not title and type_ == ItemType.MEETING and (s.participants or s.organization):
            names = s.participants + ([s.organization] if s.organization else [])
            title = "Встреча " + ru.with_prep(ru.join_names(names))
            rejected = False
        if not title:
            pending = cmd.model_copy(deep=True)
            pending.slots.title = None
            text = self.r.not_understood_title() if rejected else self.r.ask_title(type_)
            return self._ask(state, pending, "title", text)
        s.title = title

        when = s.when if s.when and s.when.start else None
        if when is not None and when.ambiguous and not force:
            assert when.start is not None
            # Выбор («в 5 или в 6») или оба события («в восемь и в 14:40») — по сказанному.
            # NLU бывает непоследователен: вычищает «может быть» из raw_text или переписывает
            # «и» в «или» в when.text, поэтому «и» в самой реплике важнее «или» от NLU.
            raw, wt = ru.norm(cmd.raw_text), ru.norm(when.text)
            choice = bool(
                _ALTERNATIVES.search(raw) or (_ALTERNATIVES.search(wt) and not _BOTH.search(raw))
            )
            said = f"{raw} {wt}"
            opts = self._time_options(when, said, now)
            day = when.start.replace(hour=0, minute=0, second=0, microsecond=0)
            date_only = TimeSpec(text=when.text, start=day, has_time=False, has_date=True)
            if len(opts) >= 2 and choice:
                # пользователь сам назвал варианты: «в 5 или в 6» → кнопки
                pending = cmd.model_copy(deep=True)
                pending.slots.when = date_only
                return self._ask(state, pending, "time", self.r.ask_options(opts, now))
            if len(opts) >= 2:
                # «две пары: в восемь и в 14:40» — это два события, а не выбор
                return await self._create_each(cmd, state, opts, now)
            if choice:
                # варианты названы, но разобрать их не вышло — спрашиваем время, не угадываем
                pending = cmd.model_copy(deep=True)
                pending.slots.when = date_only
                return self._ask(state, pending, "time", self.r.ask_time(type_, day, now))
            if self._window(when) is not None:
                return await self._create_in_window(cmd, state, when, now)
            if when.end is not None and when.end - when.start > timedelta(days=1):
                # «на выходных», «на следующей неделе»: день не выбран — спрашиваем день и время
                pending = cmd.model_copy(deep=True)
                pending.slots.when = None
                return self._ask(state, pending, "date", self.r.ask_date(type_, s.title))
            # иначе время неясно — спрашиваем только его (дата названа) или день и время
            if when.has_date and type_ == ItemType.MEETING:
                pending = cmd.model_copy(deep=True)
                pending.slots.when = date_only
                return self._ask(state, pending, "time", self.r.ask_time(type_, day, now))
            if when.has_date:
                when = date_only
                s.when = date_only
        if when is not None and not force and not s.recurrence:
            past = self._past_offer(when, now)
            if past is not None:
                proposed, text = past
                pending = cmd.model_copy(deep=True)
                pending.slots.when = proposed
                pending.slots.new_when = proposed
                state.pending, state.awaiting = pending, "confirm_conflict"
                return self._reply(text, "clarify")
        if type_ in (ItemType.MEETING, ItemType.REMINDER):
            if when is None or when.is_period or (when.ambiguous and not when.has_date):
                if when is not None and when.is_period:
                    s.when = None
                return self._ask(state, cmd, "date", self.r.ask_date(type_, s.title))
            if not when.has_time and type_ == ItemType.MEETING:
                # у встречи время важно — спрашиваем
                return self._ask(state, cmd, "time", self.r.ask_time(type_, when.start, now))
            if not when.has_time:
                # напоминание с датой без времени: не задаём пустой вопрос, ставим 10:00
                # и говорим об этом («…в 10:00 — сказать другое время?»)
                assert when.start is not None
                when = _abs_spec(_default_reminder_time(when.start, now), when.text)
                s.when = when
                default_time = True

        dt = when.start if when else None
        has_time = when.has_time if when else False
        deadline = bool(when and ru.norm(when.text).strip().startswith("до "))
        item = self.service.build(
            state.user_id,
            type_,
            s.title,
            dt,
            has_time,
            now,
            duration_min=s.duration_min,
            participants=s.participants,
            organization=s.organization,
            rrule=s.recurrence,
        )

        if item.type == ItemType.MEETING and item.starts_at and item.ends_at and not force:
            conflicts = await self.service.conflicts.check(
                state.user_id, item.starts_at, item.ends_at
            )
            if conflicts:
                sugg = await self.service.conflicts.suggest(
                    state.user_id, item.starts_at, item.ends_at, now
                )
                pending = cmd.model_copy(deep=True)
                pending.slots.when = _abs_spec(item.starts_at, when.text if when else "")
                pending.slots.new_when = _abs_spec(sugg, "свободный слот") if sugg else None
                state.pending, state.awaiting = pending, "confirm_conflict"
                reply = self._reply(
                    self.r.conflict(item, conflicts, sugg, now), "conflict", conflicts
                )
                # items конфликта — то, с чем пересекается (UI: «Пересекается с»);
                # что собирались создать — в debug.draft (для eval и отладки)
                reply.debug = {"draft": item.model_dump(mode="json", exclude_defaults=True)}
                return reply

        saved, action_id = await self.service.create(item, state.session_id)
        state.last_item_ids, state.last_action_id = [saved.id], action_id
        if default_time:
            # ответ «в 15» / «другое время» переставит напоминание; новая команда — просто идёт
            state.pending = Command(
                intent=Intent.UPDATE,
                source=cmd.source,
                raw_text=cmd.raw_text,
                slots=Slots(target=ItemRef(item_id=saved.id)),
            )
            state.awaiting = "time_optional"
            return self._reply(self.r.created_default_time(saved, now), "created", [saved])
        return self._reply(self.r.created(saved, now, deadline=deadline), "created", [saved])

    # ================= списки и поиск =================

    async def _list(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        s = cmd.slots
        when = s.when
        if when is not None and (when.ambiguous or (when.start is None and when.text.strip())):
            # «31 ноября»: даты нет — не подменяем её «сегодня», а спрашиваем
            pending = cmd.model_copy(deep=True)
            pending.slots.when = None
            return self._ask(state, pending, "date", self.r.ask_list_date(when.text, now))
        start, end = _period(when, now)
        items = await self.service.list_period(state.user_id, start, end, s.type)
        if s.status is not None:
            items = [i for i in items if i.status == s.status]
        if not items:
            nxt = await self.service.next_after(state.user_id, end, s.type)
            t = self.r.empty_list(start, end, s.type, nxt, now)
        else:
            t = self.r.listed(items, start, end, s.type, now)
        state.last_item_ids = [i.id for i in t.shown]
        return self._reply(t, "listed", items or t.shown)

    async def _list_more(self, last: Command, state: DialogState, now: datetime) -> AssistantReply:
        """Следующая порция прошлого списка: после последней показанной записи."""
        start, end = _period(last.slots.when, now)
        items = await self.service.list_period(state.user_id, start, end, last.slots.type)
        if last.slots.status is not None:
            items = [i for i in items if i.status == last.slots.status]
        keys = [(i.id, item_time(i)) for i in items]
        shown = set(state.last_item_ids)
        last_idx = max((n for n, (i, _) in enumerate(keys) if i in shown), default=-1)
        chunk = items[last_idx + 1 : last_idx + 1 + LIST_FULL_LIMIT]
        rest = len(items) - (last_idx + 1) - len(chunk)
        t = self.r.list_more(chunk, rest, start, end, last.slots.type, now)
        state.last_item_ids = [i.id for i in chunk] or state.last_item_ids
        return self._reply(t, "listed", chunk)

    async def _search(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        if _DURATION_Q.search(ru.norm(cmd.raw_text)):
            return await self._duration(cmd, state, now)
        found = await self.service.find(state.user_id, cmd.slots, now)
        items = found.main
        t = self.r.found(items, cmd.slots, now, more=len(found.weaker))
        state.last_item_ids = [i.id for i in t.shown]
        if 1 < len(items) <= LIST_FULL_LIMIT and t.shown:
            # ответ назвал одну запись («Ближайшая — X», «Последняя — X»): «его / её» — это X
            state.last_item_ids = [t.shown[0].id]
        rest = [i for i in items if i not in t.shown] + found.weaker
        return self._reply(t, "found", (t.shown + rest)[:10])

    async def _duration(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        """«Сколько длится встреча с Марией?» → длительность записи, а не просто «нашёл»."""
        source = cmd.slots.query or (cmd.raw_text if cmd.intent != Intent.SEARCH else "")
        q = " ".join(
            w
            for w in re.findall(r"[а-яёa-z0-9]+", ru.norm(source))
            if not _DURATION_WORD.match(w) and w not in _QUESTION_WORDS
        )
        slots = Slots(
            type=cmd.slots.type,
            participants=cmd.slots.participants,
            organization=cmd.slots.organization,
            query=q or None,
        )
        found = await self.service.find(state.user_id, slots, now)
        if not found.main:
            return self._reply(self.r.found([], slots, now), "found")
        future = [i for i in found.main if (item_time(i) or now) >= now]
        item = (future or found.main)[0]
        state.last_item_ids = [item.id]
        return self._reply(self.r.duration(item, now), "found", [item])

    # ================= продолжение: применить к тому, что в фокусе =================

    async def _continue_focus(
        self, cmd: Command, state: DialogState, now: datetime
    ) -> tuple[AssistantReply, Command] | None:
        """Продолжение без открытого вопроса. Фокус по правилу v7:
        только что создали/изменили → меняем эту запись; после списка/поиска → цель среди
        показанных (местоимение) или тот же список на другой период («а послезавтра?»).
        Новая запись из продолжения не создаётся никогда («Её», «Лучше», «Пусть повторяется»)."""
        if cmd.intent in (
            Intent.UPDATE, Intent.COMPLETE, Intent.DELETE, Intent.CONFIRM, Intent.UNDO,
        ):  # fmt: skip
            return None  # NLU уже понял продолжение как правку — обычный путь
        text = correction_text(cmd.raw_text)
        cont = continuation_of(cmd, text)
        if cont is None:
            return None
        last = state.last_command
        spec = self._continuation_spec(cmd, text, now)

        # 1) только что создали или изменили — правим эту запись
        #    («Поставь задачу купить билеты → на завтра»: NLU мог отдать список)
        if (
            last is not None
            and last.intent in (Intent.CREATE, Intent.UPDATE)
            and state.last_action_id is not None
            and state.last_item_ids
            and cont.tense is None
        ):
            item = await self.service.get(state.user_id, state.last_item_ids[0])
            if item is not None:
                reply = await self._apply_continuation(cmd, state, item, cont, spec, now)
                return reply, _edit_command(cmd, item.id, spec)

        # уточнение поиска: «только прошлые», «а будущие?» — тот же поиск с другим временем
        if last is not None and last.intent == Intent.SEARCH and cont.tense is not None:
            again = last.model_copy(deep=True)
            again.slots.tense = cont.tense
            again.raw_text = cmd.raw_text
            return await self._search(again, state, now), again
        if cmd.intent in (Intent.LIST, Intent.SEARCH):
            return None  # «а послезавтра?», «а в этом году?» — NLU собрал запрос сам

        # 2) после списка / поиска
        if last is not None and last.intent in (Intent.LIST, Intent.SEARCH):
            if cont.pronoun or cont.repeat or cont.participants:
                upd = Command(
                    intent=Intent.UPDATE,
                    source=cmd.source,
                    raw_text=cmd.raw_text,
                    confidence=cmd.confidence,
                    slots=Slots(type=cmd.slots.type, target=ItemRef(pronoun=True), new_when=spec),
                )
                res = await self.targets.resolve(upd.slots, state, now)
                if res.kind == "ok" and res.item is not None:
                    item = await self.service.get(state.user_id, res.item.id) or res.item
                    reply = await self._apply_continuation(cmd, state, item, cont, spec, now)
                    return reply, _edit_command(cmd, item.id, spec)
                return await self._update(upd, state, now), upd
            if last.intent == Intent.LIST and spec is not None and spec.start is not None:
                again = last.model_copy(deep=True)
                again.slots.when = spec.model_copy(update={"has_time": False})
                again.raw_text = cmd.raw_text
                return await self._list(again, state, now), again

        return self._reply(self.r.continuation_without_focus(), "clarify"), cmd

    def _continuation_spec(self, cmd: Command, text: str, now: datetime) -> TimeSpec | None:
        for sp in (cmd.slots.when, cmd.slots.new_when):
            if sp is not None and (sp.start is not None or _shift_minutes(sp) is not None):
                return sp
        if _shift_minutes(TimeSpec(text=text)) is not None:
            return TimeSpec(text=text)
        return self._answer_spec(cmd, text, now)

    async def _apply_continuation(
        self,
        cmd: Command,
        state: DialogState,
        item: Item,
        cont: Continuation,
        spec: TimeSpec | None,
        now: datetime,
    ) -> AssistantReply:
        """«нет, давай в двенадцать» / «сделай её каждую неделю» / «и с Марией» — правка записи."""
        after = item
        shift = _shift_minutes(spec)
        base = item_time(item)
        if shift is not None and base is not None:
            after = self.service.moved_to(item, base + timedelta(minutes=shift), item.has_time, now)
        elif spec is not None and spec.start is not None:
            after = self.service.moved(item, spec, now)
        if cont.repeat and (t := item_time(after)) is not None:
            rule = cont.repeat
            if rule == "FREQ=WEEKLY":
                rule += (
                    ";BYDAY="
                    + ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[t.astimezone(now.tzinfo).weekday()]
                )
            after = after.model_copy(update={"rrule": rule})
        if cont.participants:
            lemmas = {ru.stem(p): p for p in cmd.slots.participants}  # «Марией» → «Мария»
            names = [lemmas.get(ru.stem(n), ru.capitalize(n)) for n in cont.participants]
            new = [n for n in names if ru.stem(n) not in {ru.stem(p) for p in after.person}]
            if new:
                title = after.title
                if after.type == ItemType.MEETING:
                    title = f"{title} и {ru.join_names(new)}"
                after = after.model_copy(update={"person": [*after.person, *new], "title": title})
        if after == item:
            return self._reply(self.r.continuation_without_focus(), "clarify")
        return await self._apply_update(state, item, after, now)

    # ================= изменения =================

    async def _target(
        self, cmd: Command, state: DialogState, now: datetime, *, prefer_open: bool = False
    ) -> tuple[Item | None, AssistantReply | None]:
        res: Resolution = await self.targets.resolve(cmd.slots, state, now, prefer_open=prefer_open)
        if res.kind == "ok" and res.item is not None:
            stored = await self.service.get(state.user_id, res.item.id)
            return stored or res.item, None
        if res.kind == "choose":
            state.last_item_ids = [c.id for c in res.candidates]
            pending = cmd.model_copy(deep=True)
            state.pending, state.awaiting = pending, "target"
            return None, self._reply(self.r.choose(res.candidates, now), "clarify", res.candidates)
        if res.kind == "no_context":
            return None, self._reply(self.r.no_context(), "clarify")
        return None, self._reply(self.r.target_not_found(cmd.slots), "none")

    async def _update(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        if _is_all(cmd):
            return await self._mass(cmd, state, now)
        risky = self._risky(cmd, state)
        item, early = await self._target(cmd, state, now)
        if early is not None:
            return early
        assert item is not None
        s = cmd.slots
        if s.new_title and not (s.new_when and s.new_when.start):
            after = item.model_copy(update={"title": ru.capitalize(s.new_title), "updated_at": now})
            return await self._apply_update(state, item, after, now, force=True)
        shift = _shift_minutes(s.new_when)
        if shift is not None and item_time(item) is not None:
            base = item_time(item)
            assert base is not None
            after = self.service.moved_to(item, base + timedelta(minutes=shift), item.has_time, now)
            if s.new_title:
                after = after.model_copy(update={"title": ru.capitalize(s.new_title)})
            if risky:
                return self._confirm_action(cmd, state, item, after, now)
            return await self._apply_update(state, item, after, now)
        if not (s.new_when and s.new_when.start):
            pending = cmd.model_copy(deep=True)
            pending.slots = Slots(target=ItemRef(item_id=item.id), new_title=s.new_title)
            state.pending, state.awaiting = pending, "date"
            state.last_item_ids = [item.id]
            return self._reply(self.r.ask_when_update(), "clarify", [item])
        after = self.service.moved(item, s.new_when, now)
        if s.new_title:
            after = after.model_copy(update={"title": ru.capitalize(s.new_title)})
        if risky:
            return self._confirm_action(cmd, state, item, after, now)
        return await self._apply_update(state, item, after, now)

    async def _apply_update(
        self, state: DialogState, before: Item, after: Item, now: datetime, *, force: bool = False
    ) -> AssistantReply:
        if (
            after.type == ItemType.MEETING
            and after.starts_at
            and after.ends_at
            and not force
            and after.starts_at != before.starts_at
        ):
            conflicts = await self.service.conflicts.check(
                state.user_id, after.starts_at, after.ends_at, exclude_id=after.id
            )
            if conflicts:
                old = (
                    (before.starts_at, before.ends_at or before.starts_at + timedelta(hours=1))
                    if before.starts_at
                    else None
                )
                sugg = await self.service.conflicts.suggest(
                    state.user_id, after.starts_at, after.ends_at, now, exclude_id=after.id,
                    avoid=old,
                )  # fmt: skip
                state.pending = Command(
                    intent=Intent.UPDATE,
                    source="rules",
                    raw_text="",
                    slots=Slots(
                        target=ItemRef(item_id=before.id),
                        when=_abs_spec(after.starts_at, "запрошенное время"),
                        new_when=_abs_spec(sugg, "свободный слот") if sugg else None,
                    ),
                )
                state.awaiting = "confirm_conflict"
                state.last_item_ids = [before.id]
                return self._reply(
                    self.r.conflict(after, conflicts, sugg, now), "conflict", conflicts
                )
        saved, action_id = await self.service.save_update(before, after, state.session_id)
        state.last_item_ids, state.last_action_id = [saved.id], action_id
        return self._reply(self.r.updated(before, saved, now), "updated", [saved])

    async def _complete(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        if _is_all(cmd):
            return await self._mass(cmd, state, now)
        risky = self._risky(cmd, state)
        item, early = await self._target(cmd, state, now, prefer_open=True)
        if early is not None:
            return early
        assert item is not None
        if item.status == ItemStatus.DONE:
            state.last_item_ids = [item.id]
            return self._reply(Text(f"«{item.title}» уже отмечена выполненной."), "none", [item])
        if risky:
            return self._confirm_action(cmd, state, item, None, now)
        saved, action_id = await self.service.complete(item, state.session_id, now)
        state.last_item_ids, state.last_action_id = [saved.id], action_id
        return self._reply(self.r.completed(saved, now), "completed", [saved])

    async def _delete(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        if _is_all(cmd):
            return await self._mass(cmd, state, now)
        item, early = await self._target(cmd, state, now)
        if early is not None:
            return early
        assert item is not None
        pending = Command(
            intent=Intent.DELETE,
            source=cmd.source,
            raw_text=cmd.raw_text,
            slots=Slots(target=ItemRef(item_id=item.id)),
        )
        state.pending, state.awaiting = pending, "confirm_delete"
        state.last_item_ids = [item.id]
        return self._reply(self.r.confirm_delete(item, now), "clarify", [item])

    async def _undo(self, state: DialogState, now: datetime) -> AssistantReply:
        res = await self.service.undo(state.session_id, state.user_id, state.last_action_id)
        if res is None:
            return self._reply(self.r.nothing_to_undo(), "none")
        state.last_action_id = None
        if res.batch:
            kind = res.action.kind
            state.last_item_ids = [i.id for i in res.batch] if kind != "create" else []
            cards = res.batch if kind != "create" else []
            return self._reply(self.r.undone_batch(kind, res.batch), "undone", cards)
        state.last_item_ids = [res.item.id] if res.item and res.action.kind != "create" else []
        # после отмены создания записи больше нет — карточку не возвращаем (просьба чата 5)
        cards = [res.item] if res.item and res.action.kind != "create" else []
        return self._reply(self.r.undone(res.action.kind, res.item, now), "undone", cards)

    # ================= безопасность и массовые действия =================

    @staticmethod
    def _risky(cmd: Command, state: DialogState) -> bool:
        """«Дали эту задачу» (обрезано «У») → не делаем молча, если цель — из контекста,
        а контекст неоднозначен (несколько записей) или NLU не уверен."""
        return _by_context(cmd) and (
            len(state.last_item_ids) > 1 or cmd.confidence < SAFE_CONFIDENCE
        )

    def _confirm_action(
        self, cmd: Command, state: DialogState, before: Item, after: Item | None, now: datetime
    ) -> AssistantReply:
        slots = Slots(target=ItemRef(item_id=before.id), new_title=cmd.slots.new_title)
        if after is not None and (t := item_time(after)) is not None:
            slots.new_when = _abs_spec(t, "подтверждено", after.has_time)
        pending = Command(
            intent=cmd.intent,
            slots=slots,
            confidence=cmd.confidence,  # цель уже item_id — повторной проверки не будет
            source=cmd.source,
            raw_text=cmd.raw_text,
        )
        state.pending, state.awaiting = pending, "confirm_action"
        state.last_item_ids = [before.id]
        return self._reply(
            self.r.confirm_action(cmd.intent.value, before, after, now), "clarify", [before]
        )

    async def _mass(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        """«Удали все встречи на завтра» → показать и спросить; выполнить после «да»."""
        s = cmd.slots
        if s.when is not None and s.when.start is not None:
            w = s.when
            assert w.start is not None
            period: tuple[datetime, datetime] | None = (
                (w.start, w.end) if w.is_period and w.end else day_bounds(w.start)
            )
            lo, hi = period  # type: ignore[misc]
        else:
            period, lo, hi = None, now, now + MASS_HORIZON
        found = await self.service.list_period(state.user_id, lo, hi, s.type)
        ids: list[int] = []
        for i in found:
            if i.id not in ids and (period is not None or (item_time(i) or now) >= now):
                ids.append(i.id)
        items = [x for x in [await self.service.get(state.user_id, i) for i in ids] if x]
        if cmd.intent == Intent.COMPLETE:
            items = [i for i in items if i.status != ItemStatus.DONE]
        if not items:
            return self._reply(self.r.nothing_for_many(s.type, period, now), "none")
        new_when_text = None
        if cmd.intent == Intent.UPDATE:
            nw = s.new_when
            if _shift_minutes(nw) is None and not (nw and nw.start):
                pending = cmd.model_copy(deep=True)
                state.pending, state.awaiting = pending, "date"
                return self._reply(self.r.ask_when_update(), "clarify", items[:LIST_PREVIEW])
            assert nw is not None
            new_when_text = nw.text
        state.pending, state.awaiting = cmd.model_copy(deep=True), "confirm_many"
        state.last_item_ids = [i.id for i in items]
        t = self.r.confirm_many(cmd.intent.value, items, s.type, period, now, new_when_text)
        return self._reply(t, "clarify", t.shown)  # карточки — только показанные, не все 50

    async def _apply_many(self, state: DialogState, now: datetime) -> AssistantReply:
        cmd = state.pending
        assert cmd is not None
        ids = list(state.last_item_ids)
        self._clear(state)
        action_ids: list[int] = []
        done: list[Item] = []
        for item_id in ids:
            item = await self.service.get(state.user_id, item_id)
            if item is None:
                continue
            if cmd.intent == Intent.DELETE:
                action_ids.append(await self.service.delete(item, state.session_id))
                done.append(item)
            elif cmd.intent == Intent.COMPLETE:
                saved, a = await self.service.complete(item, state.session_id, now)
                action_ids.append(a)
                done.append(saved)
            else:
                shift = _shift_minutes(cmd.slots.new_when)
                base = item_time(item)
                if shift is not None and base is not None:
                    after = self.service.moved_to(
                        item, base + timedelta(minutes=shift), item.has_time, now
                    )
                else:
                    assert cmd.slots.new_when is not None
                    after = self.service.moved(item, cmd.slots.new_when, now)
                saved, a = await self.service.save_update(item, after, state.session_id)
                action_ids.append(a)
                done.append(saved)
        if not action_ids:
            return self._reply(self.r.target_not_found(cmd.slots), "none")
        self.service.register_batch(state.user_id, action_ids)
        state.last_action_id = action_ids[-1]
        state.last_item_ids = [] if cmd.intent == Intent.DELETE else [i.id for i in done]
        action = {Intent.DELETE: "deleted", Intent.COMPLETE: "completed"}.get(cmd.intent, "updated")
        return self._reply(self.r.done_many(cmd.intent.value, done, cmd.slots.type), action, done)

    # ================= служебное =================

    def _ask(self, state: DialogState, cmd: Command, awaiting: str, t: Text) -> AssistantReply:
        state.pending, state.awaiting = cmd.model_copy(deep=True), awaiting
        return self._reply(t, "clarify")

    @staticmethod
    def _clear(state: DialogState) -> None:
        state.pending, state.awaiting = None, None

    @staticmethod
    def _reply(t: Text, action: str, items: list[Item] | None = None) -> AssistantReply:
        return AssistantReply(
            text=t.text,
            action=action,
            items=items if items is not None else t.shown,
            suggestions=t.suggestions,
        )

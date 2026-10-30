"""Разбор реплики как ответа на уточняющий вопрос (DialogState.awaiting).

«Поставь встречу с Иваном завтра» → «На какое время?» → «в три»:
здесь «в три» — не новая команда, а время для `state.pending`. Возвращаем pending
с дополненными слотами. Если реплика похожа на новую команду — возвращаем None.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.contracts import Command, DialogState, Intent, ItemRef, TimeSpec

from .dates import RuleDateResolver, remove_spans
from .entities import EntityNormalizer
from .router import NO, UNDO, YES, RuleRouter
from .text import simple_norm

_FILLER = re.compile(
    r"(?<![а-яё])(давай|давайте|ну|лучше|пусть будет|"
    r"пусть|наверное|можно|тогда|поставь|ставь|на|в|во|к|"
    r"часов|часа|час|да|нет|не|а|и|это|мне|пожалуйста|окей|ок|хорошо|наверно|где-то|примерно|около)(?![а-яё])"
)
_ORDINALS = {
    "первую": 0,
    "первый": 0,
    "первое": 0,
    "1": 0,
    "вторую": 1,
    "второй": 1,
    "второе": 1,
    "2": 1,
    "третью": 2,
    "третий": 2,
    "третье": 2,
    "3": 2,
    "четвертую": 3,
    "четвёртую": 3,
    "4": 3,
    "пятую": 4,
    "5": 4,
    "последнюю": -1,
    "последний": -1,
    "последнее": -1,
}
_KEEP_ORIGINAL = re.compile(
    r"(?<![а-яё])(всё равно|все равно|оставь|оставляй|как есть|на это же время)(?![а-яё])"
)


def merge_when(old: TimeSpec | None, new: TimeSpec, new_has_date: bool) -> TimeSpec:
    """Объединяет ранее известную дату и уточнение («завтра» + «в три» → завтра 15:00)."""
    if old is None or old.start is None or new.start is None:
        return new
    text = f"{old.text} {new.text}".strip()
    if new_has_date and new.has_time:
        return new.model_copy(update={"text": text, "has_date": True})
    if new_has_date and not new.has_time and old.has_time:
        start = new.start.replace(hour=old.start.hour, minute=old.start.minute)
        return TimeSpec(text=text, start=start, has_time=True, has_date=True)
    if not new_has_date and new.has_time:
        start = old.start.replace(hour=new.start.hour, minute=new.start.minute)
        return TimeSpec(text=text, start=start, has_time=True, has_date=True)
    return new.model_copy(update={"text": text})


class ContextResolver:
    def __init__(
        self, router: RuleRouter, resolver: RuleDateResolver, normalizer: EntityNormalizer
    ) -> None:
        self.router = router
        self.dates = resolver
        self.ents = normalizer

    def answer(self, text: str, state: DialogState, now: datetime) -> Command | None:
        aw = state.awaiting
        if not aw:
            return None
        t = text.strip()
        clean = re.sub(r"[?!.,;:]+", " ", simple_norm(t)).strip()
        clean = re.sub(r"\s+", " ", clean)
        pending = state.pending

        if aw.startswith("confirm"):
            # 25.10: «нет, в 11», «да, 21:15» — продолжение: новое время в предложенной команде
            mc = re.match(
                r"^(?:нет|не|да|давай|ну|лучше|а лучше)(?:[\s,]+(?:давай|лучше))?[\s,]+(.+)$",
                t,
                re.I,
            )
            if (
                mc
                and pending is not None
                and pending.intent in (Intent.UPDATE, Intent.CREATE)
                and aw != "confirm_delete"
            ):
                upd = self._time_answer(mc.group(1), pending, now)
                if upd is not None:
                    return upd.model_copy(update={"raw_text": t})
            if aw == "confirm_conflict" and pending is not None:
                if _KEEP_ORIGINAL.search(clean):
                    return self._cmd(Intent.CONFIRM, t)
                shifted = self._shift_answer(clean, pending)
                if shifted is not None:
                    return shifted
                upd = self._time_answer(t, pending, now)
                if upd is not None:
                    return upd
            if YES.fullmatch(clean) or re.match(r"^(да|ага|давай|конечно)\b", clean):
                return self._cmd(Intent.CONFIRM, t)
            if (
                NO.fullmatch(clean)
                or UNDO.fullmatch(clean)
                or re.match(r"^(нет|не надо|не нужно)\b", clean)
            ):
                return self._cmd(Intent.REJECT, t)
            return None

        if NO.fullmatch(clean) or UNDO.fullmatch(clean):
            return self._cmd(Intent.REJECT, t)
        if pending is None:
            return None

        if aw in ("time", "date", "datetime"):
            core = re.sub(  # «Да, 21:15», «если можно завтра в восемь»
                r"(?i)^(?:(?:да|давай|ну|ок|хорошо|если можно|можно|лучше|тогда)[\s,]+)+", "", t
            )
            if aw in ("date", "datetime") and re.fullmatch(r"\d{1,2}", core.strip()):
                core = core.strip() + " числа"  # «31» в ответ на «какого числа?»
            ans = self._time_answer(core, pending, now)
            if ans is not None:
                return ans.model_copy(update={"raw_text": t})
            # обрезанный ответ («ри» вместо «в три») — не новая команда: вернуть ожидание,
            # диалог переспросит время (adversarial rm-114)
            new = self.router.route(t, None, now)
            if new is None or new.intent in (Intent.UNKNOWN, Intent.SMALLTALK):
                return pending.model_copy(update={"raw_text": t, "source": "rules"})
            return None
        if aw == "title":
            if self._looks_like_new_command(t, state, now):
                return None
            title = re.sub(
                r"^(?:назови|название|пусть называется|это)\s+", "", t, flags=re.IGNORECASE
            ).strip(" .,!")
            if not title:
                return None
            slots = pending.slots.model_copy(update={"title": title[:1].upper() + title[1:]})
            return pending.model_copy(
                update={"slots": slots, "raw_text": t, "source": "rules", "confidence": 0.9}
            )
        if aw == "target":
            words = clean.split()
            idx = next((_ORDINALS[w] for w in words if w in _ORDINALS), None)
            if idx is not None and state.last_item_ids:
                ids = state.last_item_ids
                if -len(ids) <= idx < len(ids):
                    ref = ItemRef(item_id=ids[idx])
                    slots = pending.slots.model_copy(update={"target": ref})
                    return pending.model_copy(
                        update={"slots": slots, "raw_text": t, "source": "rules"}
                    )
            if self._looks_like_new_command(t, state, now):
                return None
            ls = self.ents.lemmas(t)
            ref = ItemRef(description=t.strip(" .,!?"))
            slots = pending.slots.model_copy(
                update={
                    "target": ref,
                    "query": self.ents.expand_query(t, lemmas=ls),
                    "participants": self.ents.people(t, ls) or pending.slots.participants,
                }
            )
            return pending.model_copy(
                update={"slots": slots, "raw_text": t, "source": "rules", "confidence": 0.85}
            )
        return None

    # ---------- помощники ----------

    @staticmethod
    def _shift_answer(clean: str, pending: Command) -> Command | None:
        """«а на час позже можно?» при конфликте — сдвиг запрошенного времени."""
        from .router import parse_shift

        shift = parse_shift(clean)
        w = pending.slots.when
        if shift is None or w is None or w.start is None:
            return None
        from datetime import timedelta

        start = w.start + timedelta(minutes=shift)
        when = w.model_copy(update={"start": start, "end": None, "text": clean})
        slots = pending.slots.model_copy(update={"when": when})
        return pending.model_copy(update={"slots": slots, "raw_text": clean, "source": "rules"})

    def _time_answer(self, t: str, pending: Command, now: datetime) -> Command | None:
        src = t.strip()
        # голое число («три», «15», «пятнадцать тридцать») → «в …»
        if re.fullmatch(r"[\w\s:.]+", src) and not re.search(
            r"(?<![а-яё])(в|во|на|к|через|завтра|сегодня|послезавтра)(?![а-яё])", simple_norm(src)
        ):
            probe = self.dates.extract("в " + src, now, "point")
            if probe.found and not remove_spans("в " + src, probe.spans).strip():
                src = "в " + src
        dp = self.dates.extract(src, now, "point")
        if not dp.found:
            return None
        rest = simple_norm(remove_spans(src, dp.spans))
        rest = _FILLER.sub(" ", re.sub(r"[?!.,;:]+", " ", rest)).strip()
        if rest:
            return None  # что-то кроме времени — вероятно, новая команда
        if pending.intent == Intent.UPDATE:
            key = "new_when"
        else:
            key = "when"
        old = getattr(pending.slots, key)
        when = merge_when(old, dp.spec, dp.has_date)  # type: ignore[arg-type]
        slots = pending.slots.model_copy(update={key: when})
        return pending.model_copy(
            update={"slots": slots, "raw_text": t, "source": "rules", "confidence": 0.95}
        )

    def _looks_like_new_command(self, t: str, state: DialogState, now: datetime) -> bool:
        cmd = self.router.route(t, None, now)
        return (
            cmd is not None
            and cmd.confidence >= 0.9
            and cmd.intent
            in (
                Intent.LIST,
                Intent.SEARCH,
                Intent.CREATE,
                Intent.UPDATE,
                Intent.DELETE,
                Intent.COMPLETE,
            )
        )

    @staticmethod
    def _cmd(intent: Intent, text: str) -> Command:
        return Command(intent=intent, confidence=0.95, source="rules", raw_text=text)

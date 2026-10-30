"""FakeNLU на регулярках. Понимает 5 типов фраз:

- «Что у меня сегодня?», «Какие встречи завтра?»            → list_items
- «Напомни (мне) завтра в 10 утра позвонить Ивану»          → create_item (reminder)
- «Поставь встречу с Иваном завтра (в 15)»  → create_item (meeting); без времени — уточнение
- «Найди …», «Когда у меня была встреча с Иваном?»          → search_items
- «Отмени», «Отмена»                                         → undo
- Ответ на уточнение «в три», когда awaiting == "time"      → create_item со слотом when

Настоящий NLU делает чат 1 (backend/app/nlu/).
"""

from __future__ import annotations

import re
from datetime import datetime

from app.contracts import (
    Command,
    DateResolver,
    DialogState,
    Intent,
    ItemType,
    Slots,
)
from app.fakes.dates import strip_time_words

_UNDO = re.compile(r"^(отмени(ть)?|отмена)(\s+(это|последнее))?$", re.I)
_LIST = re.compile(r"\b(что|какие|есть ли|покажи)\b.*\b(сегодня|завтра|послезавтра)\b", re.I)
_REMIND = re.compile(r"^напомни(?:\s+мне)?\s+(?P<rest>.+)$", re.I)
_MEETING = re.compile(
    r"^(?:поставь|добавь|создай|запланируй)\s+(?P<kind>встречу|созвон)\s+(?P<rest>.+)$", re.I
)
_SEARCH = re.compile(r"^(?:найди|поищи|когда)\s+(?P<rest>.+)$", re.I)
_PAST = re.compile(r"\b(был|была|было|были|звонил|ставил|встречался)\b", re.I)
_WITH = re.compile(r"\bс[о]?\s+(?P<name>[А-ЯЁA-Z][а-яёa-z]+)")
_STOP = {
    "у",
    "меня",
    "мне",
    "я",
    "была",
    "был",
    "было",
    "были",
    "с",
    "со",
    "про",
    "по",
    "о",
    "об",
    "все",
    "всё",
    "когда",
    "мой",
    "мои",
    "моя",
    "последний",
    "раз",
}


def _clean(text: str) -> str:
    return text.strip().rstrip("?!. ")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


class FakeNLU:
    def __init__(self, dates: DateResolver) -> None:
        self.dates = dates

    async def parse(self, text: str, state: DialogState, now: datetime) -> Command:
        t = _clean(text)

        def cmd(intent: Intent, slots: Slots | None = None, conf: float = 0.9) -> Command:
            return Command(
                intent=intent,
                slots=slots or Slots(),
                confidence=conf,
                source="rules",
                raw_text=text,
            )

        if _UNDO.match(t):
            return cmd(Intent.UNDO, conf=1.0)

        if state.awaiting == "time":
            spec = self.dates.resolve(t, now)
            if spec.has_time:
                return cmd(Intent.CREATE, Slots(when=spec))

        if _LIST.search(t):
            low = t.lower()
            item_type = (
                ItemType.MEETING
                if "встреч" in low
                else ItemType.TASK
                if "задач" in low
                else ItemType.REMINDER
                if "напомина" in low
                else None
            )
            return cmd(
                Intent.LIST, Slots(type=item_type, when=self.dates.resolve(t, now, "period"))
            )

        if m := _REMIND.match(t):
            rest = m.group("rest")
            return cmd(
                Intent.CREATE,
                Slots(
                    type=ItemType.REMINDER,
                    title=_cap(strip_time_words(rest)),
                    when=self.dates.resolve(rest, now),
                ),
            )

        if m := _MEETING.match(t):
            rest = m.group("rest")
            kind = "Созвон" if m.group("kind").lower() == "созвон" else "Встреча"
            who = _WITH.search(rest)
            return cmd(
                Intent.CREATE,
                Slots(
                    type=ItemType.MEETING,
                    title=f"{kind} {strip_time_words(rest)}".strip(),
                    when=self.dates.resolve(rest, now),
                    participants=[who.group("name")] if who else [],
                ),
            )

        if m := _SEARCH.match(t):
            rest = m.group("rest")
            words = [w for w in re.findall(r"[\wё]+", rest.lower()) if w not in _STOP]
            item_type = ItemType.MEETING if re.search(r"\bвстреч", rest, re.I) else None
            return cmd(
                Intent.SEARCH,
                Slots(
                    type=item_type,
                    query=" ".join(w for w in words if not w.startswith("встреч")) or None,
                    tense="past" if _PAST.search(t) else "any",
                ),
                conf=0.7,
            )

        return cmd(Intent.UNKNOWN, conf=0.0)

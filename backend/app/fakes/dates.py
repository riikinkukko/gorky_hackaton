"""FakeDateResolver: сегодня / завтра / послезавтра + «в 10», «в 10 утра», «в 15:30», «в три».

Настоящий резолвер делает чат 1 (backend/app/nlu/). Это только заглушка, чтобы контур работал.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Literal

from app.contracts import TimeSpec

_DAY_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_NUM_WORDS = {
    "час": 1,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}
_NUM_RE = "|".join(sorted(_NUM_WORDS, key=len, reverse=True))

DAY_RE = re.compile(r"\b(?P<day>послезавтра|завтра|сегодня)\b", re.I)
TIME_RE = re.compile(
    rf"\bв\s+(?P<h>\d{{1,2}}|{_NUM_RE})(?:[:.](?P<m>\d{{2}}))?"
    r"(?:\s+(?P<part>утра|дня|вечера|ночи))?\b",
    re.I,
)


def strip_time_words(text: str) -> str:
    """Убирает из фразы распознанные выражения даты и времени."""
    text = DAY_RE.sub(" ", text)
    text = TIME_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.")


class FakeDateResolver:
    def resolve(
        self, text: str, now: datetime, mode: Literal["point", "period"] = "point"
    ) -> TimeSpec:
        day_m = DAY_RE.search(text)
        time_m = TIME_RE.search(text)
        if day_m is None and time_m is None:
            return TimeSpec(text=text)

        offset = _DAY_WORDS[day_m.group("day").lower()] if day_m else 0
        base = (now + timedelta(days=offset)).date()
        tz = now.tzinfo

        if time_m is not None:
            raw_h = time_m.group("h").lower()
            hour = int(raw_h) if raw_h.isdigit() else _NUM_WORDS[raw_h]
            minute = int(time_m.group("m") or 0)
            part = (time_m.group("part") or "").lower()
            if part in ("дня", "вечера") and hour < 12:
                hour += 12
            elif not part and hour < 8:  # «в 3» → 15:00
                hour += 12
            hour %= 24
            start = datetime.combine(base, time(hour, minute), tzinfo=tz)
            if day_m is None and start < now:  # «в 10», а 10:00 уже прошло → завтра
                start += timedelta(days=1)
            return TimeSpec(text=text, start=start, has_time=True, has_date=day_m is not None)

        day_start = datetime.combine(base, time(0, 0), tzinfo=tz)
        if mode == "period":
            return TimeSpec(
                text=text,
                start=day_start,
                end=day_start + timedelta(days=1),
                is_period=True,
                has_date=True,
            )
        return TimeSpec(text=text, start=day_start, has_time=False, has_date=True)

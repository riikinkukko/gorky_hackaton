"""ScriptedNLU — тестовый фейк NLU: фраза → заранее заданная Command.

Сценарии в tests/dialog одновременно документируют, какой Command ожидается от
настоящего NLU (чат 1) на каждую фразу.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from app.contracts import Command, DialogState, Intent, Slots, TimeSpec

Rule = Command | Callable[[datetime, DialogState], Command]


def _key(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").strip(" .!?").split())


class ScriptedNLU:
    def __init__(self, rules: dict[str, Rule] | None = None) -> None:
        self.rules: dict[str, Rule] = {}
        for k, v in (rules or {}).items():
            self.add(k, v)

    def add(self, text: str, rule: Rule) -> None:
        self.rules[_key(text)] = rule

    async def parse(self, text: str, state: DialogState, now: datetime) -> Command:
        rule = self.rules.get(_key(text))
        if rule is None:
            return Command(intent=Intent.UNKNOWN, source="fallback", raw_text=text, confidence=0.0)
        cmd = rule(now, state) if callable(rule) else rule.model_copy(deep=True)
        return cmd.model_copy(update={"raw_text": text})


# ---------- помощники для TimeSpec, как их отдаст DateResolver ----------


def midnight(now: datetime, days: int = 0) -> datetime:
    return (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def on_day(now: datetime, days: int, text: str) -> TimeSpec:
    """Только дата: «завтра», «в пятницу»."""
    return TimeSpec(text=text, start=midnight(now, days), has_time=False, has_date=True)


def at(now: datetime, days: int, hour: int, minute: int = 0, text: str = "") -> TimeSpec:
    """Дата и время: «завтра в 10 утра»."""
    return TimeSpec(
        text=text,
        start=midnight(now, days).replace(hour=hour, minute=minute),
        has_time=True,
        has_date=True,
    )


def time_only(now: datetime, hour: int, minute: int = 0, text: str = "") -> TimeSpec:
    """Только время («в три»): дата — сегодняшняя, has_date=False (контракт v1.1)."""
    return at(now, 0, hour, minute, text).model_copy(update={"has_date": False})


def period(now: datetime, start_days: int, end_days: int, text: str) -> TimeSpec:
    """Период по дням [start_days, end_days] → [00:00 первого, 00:00 после последнего)."""
    return TimeSpec(
        text=text,
        start=midnight(now, start_days),
        end=midnight(now, end_days + 1),
        is_period=True,
        has_date=True,
    )


def cmd(intent: Intent, raw: str = "", **slots: object) -> Command:
    return Command(intent=intent, source="rules", raw_text=raw, slots=Slots(**slots))  # type: ignore[arg-type]

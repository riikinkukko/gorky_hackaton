"""Повторяющиеся записи: RRULE (dateutil) и развёртка в списках."""

from __future__ import annotations

from datetime import datetime, timedelta

from dateutil.rrule import rrule, rrulestr

from app.contracts import Item
from app.domain import ru
from app.domain.timeutil import item_time

_BYDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def parse_rule(rule: str, dtstart: datetime) -> rrule:
    text = rule if rule.upper().startswith(("RRULE:", "DTSTART")) else f"RRULE:{rule}"
    parsed = rrulestr(text, dtstart=dtstart)
    assert isinstance(parsed, rrule)
    return parsed


def first_occurrence(
    rule: str, dtstart: datetime, not_before: datetime | None = None
) -> datetime | None:
    """Первое срабатывание правила не раньше not_before. Время суток берётся из dtstart."""
    bound = max(dtstart, not_before) if not_before else dtstart
    return parse_rule(rule, dtstart).after(bound - timedelta(seconds=1), inc=True)


def expand(item: Item, start: datetime, end: datetime) -> list[Item]:
    """Вхождения записи в [start, end). Для обычной записи — сама запись, если попадает."""
    t = item_time(item)
    if t is None:
        return []
    if not item.rrule:
        return [item] if start <= t < end else []
    duration = (item.ends_at - item.starts_at) if item.ends_at and item.starts_at else None
    out: list[Item] = []
    for occ in [o for o in parse_rule(item.rrule, t).between(start, end, inc=True) if o < end][
        :366
    ]:
        upd: dict[str, object] = {}
        if item.starts_at is not None:
            upd["starts_at"] = occ
            if duration is not None:
                upd["ends_at"] = occ + duration
        else:
            upd["due_at"] = occ
        out.append(item.model_copy(update=upd))
    return out


def describe(rule: str, dt: datetime | None = None) -> str:
    """Человеческое описание правила: «каждый понедельник», «каждый день»."""
    parts = dict(
        p.split("=", 1) for p in rule.upper().removeprefix("RRULE:").split(";") if "=" in p
    )
    freq = parts.get("FREQ")
    interval = int(parts.get("INTERVAL", "1"))
    if freq == "DAILY":
        return "каждый день" if interval == 1 else f"раз в {interval} дня"
    if freq == "WEEKLY":
        days = [_BYDAY[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in _BYDAY]
        if not days and dt is not None:
            days = [dt.weekday()]
        if days == [0, 1, 2, 3, 4]:
            return "по будням"
        if len(days) == 1:
            prefix = "" if interval == 1 else "раз в две недели, "
            return prefix + ru.EVERY_WEEKDAY[days[0]]
        return "по дням: " + ", ".join(ru.WEEKDAYS_SHORT[d] for d in days)
    if freq == "MONTHLY":
        return "каждый месяц"
    if freq == "YEARLY":
        return "каждый год"
    return "регулярно"

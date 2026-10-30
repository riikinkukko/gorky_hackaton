"""Время записей и сборка даты из частей («завтра» + «в три»)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.contracts import Item, ItemType, TimeSpec

DAY_START = time(8, 0)
DAY_END = time(21, 0)


def item_time(item: Item) -> datetime | None:
    """Опорное время записи: начало встречи или срок задачи/напоминания."""
    return item.starts_at or item.due_at


def day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def default_duration_min(type_: ItemType, title: str) -> int:
    if type_ != ItemType.MEETING:
        return 0
    return 30 if "демо" in title.lower() else 60


def spec_has_date(spec: TimeSpec, now: datetime) -> bool:
    """Есть ли в TimeSpec явная дата (контракт v1.1: флаг has_date).

    Страховка: фраза без времени («завтра») или период всегда считаются датой,
    даже если NLU забыл выставить флаг.
    """
    if spec.start is None:
        return False
    return spec.has_date or not spec.has_time or spec.is_period


def combine(
    base: datetime | None, base_has_time: bool, spec: TimeSpec, now: datetime
) -> tuple[datetime | None, bool]:
    """Накладывает новую фразу времени на известную дату.

    - «в три» к «завтра» → завтра 15:00;
    - «на завтра» к «чт 10:00» → пт 10:00 (время сохраняется);
    - «в пятницу в 11» → пятница 11:00 (полностью новая).
    Возвращает (datetime, has_time).
    """
    if spec.start is None:
        return base, base_has_time
    new = spec.start.astimezone(now.tzinfo)
    has_date = spec_has_date(spec, now)
    if base is None:
        return new, spec.has_time
    base = base.astimezone(now.tzinfo)
    if has_date and spec.has_time:
        return new, True
    if has_date:  # только дата: сохраняем время базы
        if base_has_time:
            return new.replace(hour=base.hour, minute=base.minute, second=0, microsecond=0), True
        return new.replace(hour=0, minute=0, second=0, microsecond=0), False
    # только время: сохраняем дату базы
    return base.replace(hour=new.hour, minute=new.minute, second=0, microsecond=0), True

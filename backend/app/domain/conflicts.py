"""Конфликты встреч и поиск ближайшего свободного слота (08:00–21:00)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.contracts import Item, ItemStatus, ItemType
from app.domain.timeutil import DAY_END, DAY_START, day_bounds, item_time

SLOT_STEP = timedelta(minutes=15)
SEARCH_DAYS = 7


class PeriodSource(Protocol):
    async def list_period(
        self, user_id: int, start: datetime, end: datetime, type: ItemType | None = None
    ) -> list[Item]: ...


def _blocks(item: Item, start: datetime, end: datetime) -> bool:
    """Пересекает ли запись интервал [start, end).

    Встреча — интервалом; задача/напоминание со временем — точкой внутри интервала
    (они не блокируют слот жёстко, но о них предупреждаем).
    """
    if item.status in (ItemStatus.DONE, ItemStatus.CANCELLED):
        return False
    if item.type == ItemType.MEETING and item.starts_at is not None:
        i_end = item.ends_at or item.starts_at + timedelta(minutes=60)
        return item.starts_at < end and start < i_end
    t = item_time(item)
    return item.has_time and t is not None and start <= t < end


class ConflictChecker:
    def __init__(self, source: PeriodSource) -> None:
        self._source = source

    async def check(
        self, user_id: int, start: datetime, end: datetime, exclude_id: int | None = None
    ) -> list[Item]:
        if end <= start:
            end = start + timedelta(minutes=1)
        lo, _ = day_bounds(start)
        _, hi = day_bounds(end - timedelta(microseconds=1))
        items = await self._source.list_period(user_id, lo - timedelta(hours=12), hi)
        return [i for i in items if i.id != exclude_id and _blocks(i, start, end)]

    async def suggest(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        now: datetime,
        exclude_id: int | None = None,
        avoid: tuple[datetime, datetime] | None = None,
    ) -> datetime | None:
        """Ближайшее свободное начало той же длительности: сначала тот же день
        (по удалённости от желаемого времени), потом следующие дни с 08:00.

        avoid — старое место переносимой записи: «перенеси с 9:45 на 10:00» не должно
        получить в ответ «поставить на 9:45» (или на 9:30, почти туда же)."""
        duration = max(end - start, SLOT_STEP)
        for day_offset in range(SEARCH_DAYS + 1):
            day = (start + timedelta(days=day_offset)).replace(second=0, microsecond=0)
            first = day.replace(hour=DAY_START.hour, minute=DAY_START.minute)
            last = day.replace(hour=DAY_END.hour, minute=DAY_END.minute) - duration
            cands: list[datetime] = []
            t = first
            while t <= last:
                cands.append(t)
                t += SLOT_STEP
            if day_offset == 0:
                cands = [c for c in cands if c != start]
                cands.sort(key=lambda c: (abs(c - start), c < start))
            cands = [c for c in cands if c >= now]
            # одним запросом берём записи дня, дальше считаем в памяти
            lo, hi = day_bounds(day)
            items = [
                i
                for i in await self._source.list_period(user_id, lo - timedelta(hours=12), hi)
                if i.id != exclude_id
            ]
            for c in cands:
                if avoid is not None and c < avoid[1] and avoid[0] < c + duration:
                    continue
                if not any(_blocks(i, c, c + duration) for i in items):
                    return c
        return None

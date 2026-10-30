"""Тестовые фейки хранилища и поиска по контракту v1.1 — только для tests/dialog.

В рантайме диалог получает репозиторий каркаса (InMemoryRepository) или Postgres чата 3.
Здесь — CSV-данные организаторов, чтобы сценарии проверялись на реальных id из golden_eval.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.contracts import ActionRecord, Item, ItemStatus, ItemType, Slots
from app.domain import ru
from app.domain.timeutil import item_time


class InMemoryItemRepository:
    def __init__(self, items: list[Item] | None = None) -> None:
        self._items: dict[int, Item] = {}
        self._deleted: set[int] = set()
        self._actions: list[ActionRecord] = []
        self._next_id = 1
        for it in items or []:
            self._items[it.id] = it
            self._next_id = max(self._next_id, it.id + 1)

    async def create(self, item: Item) -> Item:
        assert item.id == 0, "v1.1: id выдаёт репозиторий"
        saved = item.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self._items[saved.id] = saved
        return saved

    async def get(self, user_id: int, item_id: int) -> Item | None:
        it = self._items.get(item_id)
        if it is None or it.user_id != user_id or item_id in self._deleted:
            return None
        return it

    async def update(self, item: Item) -> Item:
        self._items[item.id] = item
        return item

    async def log_action(self, action: ActionRecord) -> int:
        rec = action.model_copy(update={"id": len(self._actions) + 1})
        self._actions.append(rec)
        return rec.id

    async def get_action(self, user_id: int, action_id: int) -> ActionRecord | None:
        if 1 <= action_id <= len(self._actions) and self._actions[action_id - 1].user_id == user_id:
            return self._actions[action_id - 1]
        return None

    async def mark_undone(self, user_id: int, action_id: int) -> None:
        rec = await self.get_action(user_id, action_id)
        if rec is not None:
            self._actions[action_id - 1] = rec.model_copy(update={"undone": True})

    async def soft_delete(self, user_id: int, item_id: int) -> None:
        if item_id in self._items and self._items[item_id].user_id == user_id:
            self._deleted.add(item_id)

    async def restore(self, user_id: int, item_id: int) -> None:
        self._deleted.discard(item_id)

    def all(self, user_id: int) -> list[Item]:
        return [
            i for i in self._items.values() if i.user_id == user_id and i.id not in self._deleted
        ]

    async def list_range(
        self, user_id: int, start: datetime, end: datetime, type: ItemType | None = None
    ) -> list[Item]:
        """Контракт v1.1: [start, end) по coalesce(starts_at, due_at).

        Серия с rrule — одной записью, если её начало < end."""
        out = []
        for it in self.all(user_id):
            if type is not None and it.type != type:
                continue
            t = item_time(it)
            if t is None:
                continue
            if (it.rrule and t < end) or start <= t < end:
                out.append(it)
        return sorted(out, key=lambda i: item_time(i) or start)

    async def find_overlaps(self, user_id: int, start: datetime, end: datetime) -> list[Item]:
        out = []
        for it in self.all(user_id):
            if it.starts_at and it.ends_at and it.starts_at < end and start < it.ends_at:
                out.append(it)
            elif it.due_at and it.has_time and start <= it.due_at < end:
                out.append(it)
        return out


_STOP = set(
    ru.stems(
        "найди найти покажи когда у меня был была были что все всё про по с со для "
        "о об и я мне задачу задача встречу встреча напоминание напоминалки "
        "когда-то ставил её ее"
    )
)


class NaiveSearchEngine:
    """Фильтры из Slots + совпадение стемов. Ранжирование: прошлое — свежие первыми,
    будущее — ближайшие, без tense — по близости к «сейчас»."""

    def __init__(self, repo: InMemoryItemRepository) -> None:
        self._repo = repo

    async def search(
        self, user_id: int, slots: Slots, now: datetime, limit: int = 20
    ) -> list[tuple[Item, float]]:
        q_stems = [s for s in ru.stems(slots.query or "") if s not in _STOP]
        who = [ru.stem(p) for p in slots.participants]
        org = ru.stem(slots.organization) if slots.organization else None
        out: list[tuple[Item, float]] = []
        for it in self._repo.all(user_id):
            t = item_time(it)
            if slots.type and it.type != slots.type:
                continue
            if slots.status and it.status != slots.status:
                continue
            if slots.tense == "past" and (t is None or t >= now):
                continue
            if slots.tense == "future" and (t is None or t < now):
                continue
            if slots.when and slots.when.start and t is not None:
                lo = slots.when.start
                hi = slots.when.end or lo + timedelta(days=1)
                if not lo <= t < hi:
                    continue
            title_stems = set(ru.stems(it.title)) | {
                ru.stem(p) for p in it.person + it.organization
            }
            if any(w not in title_stems for w in who):
                continue
            if org and org not in title_stems:
                continue
            hits = sum(1 for s in q_stems if s in title_stems)
            if q_stems and hits == 0:
                continue
            out.append((it, float(hits + len(who))))
        if q_stems and out:  # грубая релевантность: только лучшие по числу совпавших слов
            best = max(score for _, score in out)
            out = [p for p in out if p[1] == best]

        def order(pair: tuple[Item, float]) -> tuple[float, float]:
            t = item_time(pair[0]) or now
            delta = (t - now).total_seconds()
            if slots.tense == "past":
                return (-pair[1], -delta)  # свежие первыми
            if slots.tense == "future":
                return (-pair[1], delta)
            return (-pair[1], abs(delta))

        out.sort(key=order)
        return out[:limit]


# ---------- упрощённый импорт CSV для тестов ----------

NAMES = {
    "Иван",
    "Мария",
    "Елена",
    "Максим",
    "Анна",
    "Алексей",
    "Дмитрий",
    "Наталья",
    "Ольга",
    "Сергей",
}
ORGS = {"Альфа", "Вектор", "Север", "Орбита", "Партнёр", "Пульс", "Маяк", "Старт"}
_MEETING_PREFIX = ("Встреча", "Созвон", "Демо", "Провести")


def _type_of(text: str) -> ItemType:
    if text.startswith(_MEETING_PREFIX):
        return ItemType.MEETING
    if text.startswith("Напомнить"):
        return ItemType.REMINDER
    return ItemType.TASK


def load_csv(
    path: str | Path, user_id: int, now: datetime, tz: str = "Europe/Moscow"
) -> list[Item]:
    zone = ZoneInfo(tz)
    name_stems = {ru.stem(n): n for n in NAMES}
    org_stems = {ru.stem(o): o for o in ORGS}
    items = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            text = row["text"].strip()
            dt = datetime.fromisoformat(f"{row['date']}T{row['time']}").replace(tzinfo=zone)
            type_ = _type_of(text)
            st = ru.stems(text)
            person = [name_stems[s] for s in st if s in name_stems]
            org = [org_stems[s] for s in st if s in org_stems]
            minutes = 30 if text.startswith("Демо") else 60
            items.append(
                Item(
                    id=int(row["id"]),
                    user_id=user_id,
                    type=type_,
                    title=text,
                    title_original=text,
                    starts_at=dt if type_ == ItemType.MEETING else None,
                    ends_at=dt + timedelta(minutes=minutes) if type_ == ItemType.MEETING else None,
                    due_at=None if type_ == ItemType.MEETING else dt,
                    has_time=True,
                    status=ItemStatus.UNKNOWN,
                    person=person,
                    organization=org,
                    source="import",
                    created_at=now,
                    updated_at=now,
                )
            )
    return items

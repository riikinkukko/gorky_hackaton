"""InMemoryRepository — ItemRepository из контракта в памяти процесса.

Умеет засеять данные из CSV организаторов (id, date, time, text) с грубым выводом типа,
чтобы «Что у меня сегодня?» отвечало реальными записями с первого дня.
Настоящий репозиторий на PostgreSQL делает чат 3 (backend/app/storage/).
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.contracts import ActionRecord, Item, ItemType

_MEETING_PREFIXES = ("встреча", "созвон", "демо", "провести планёрку", "провести планерку")


def guess_type(text: str) -> ItemType:
    low = text.lower()
    if low.startswith(_MEETING_PREFIXES):
        return ItemType.MEETING
    if low.startswith("напомнить"):
        return ItemType.REMINDER
    return ItemType.TASK


def item_anchor(item: Item) -> datetime | None:
    """Момент, по которому запись попадает в период: начало встречи или срок задачи."""
    return item.starts_at or item.due_at


class InMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[int, Item] = {}
        self._deleted: set[int] = set()
        self._next_id = 1
        self._actions: dict[int, ActionRecord] = {}

    # ---------- ItemRepository ----------
    async def create(self, item: Item) -> Item:
        """id во входной записи игнорируется, репозиторий выдаёт свой."""
        ts = datetime.now(UTC)
        new = item.model_copy(update={"id": self._next_id, "created_at": ts, "updated_at": ts})
        self._items[new.id] = new
        self._next_id += 1
        return new

    async def get(self, user_id: int, item_id: int) -> Item | None:
        item = self._items.get(item_id)
        if item is None or item.user_id != user_id or item_id in self._deleted:
            return None
        return item

    async def update(self, item: Item) -> Item:
        if item.id not in self._items or item.id in self._deleted:
            raise KeyError(f"item {item.id} not found")
        updated = item.model_copy(update={"updated_at": datetime.now(UTC)})
        self._items[item.id] = updated
        return updated

    async def soft_delete(self, user_id: int, item_id: int) -> None:
        if await self.get(user_id, item_id) is not None:
            self._deleted.add(item_id)

    async def restore(self, user_id: int, item_id: int) -> None:
        item = self._items.get(item_id)
        if item is not None and item.user_id == user_id:
            self._deleted.discard(item_id)

    async def list_range(
        self, user_id: int, start: datetime, end: datetime, type: ItemType | None = None
    ) -> list[Item]:
        result = []
        for item in self.all_items(user_id):
            anchor = item_anchor(item)
            if anchor is None:
                continue
            # контракт: серия с rrule — одной записью, если началась до конца периода
            in_range = anchor < end if item.rrule else start <= anchor < end
            if not in_range:
                continue
            if type is not None and item.type != type:
                continue
            result.append(item)
        return sorted(result, key=lambda i: (item_anchor(i), i.id))

    async def find_overlaps(self, user_id: int, start: datetime, end: datetime) -> list[Item]:
        result = []
        for item in self.all_items(user_id):
            if item.starts_at is None:
                continue
            item_end = item.ends_at or item.starts_at + timedelta(minutes=60)
            if item.starts_at < end and start < item_end:
                result.append(item)
        return sorted(result, key=lambda i: (i.starts_at, i.id))

    async def log_action(self, action: ActionRecord) -> int:
        action_id = len(self._actions) + 1
        self._actions[action_id] = action.model_copy(update={"id": action_id})
        return action_id

    async def get_action(self, user_id: int, action_id: int) -> ActionRecord | None:
        action = self._actions.get(action_id)
        return action if action is not None and action.user_id == user_id else None

    async def mark_undone(self, user_id: int, action_id: int) -> None:
        action = await self.get_action(user_id, action_id)
        if action is not None:
            self._actions[action_id] = action.model_copy(update={"undone": True})

    # ---------- вспомогательное (не часть контракта) ----------
    def all_items(self, user_id: int) -> list[Item]:
        return [
            i for i in self._items.values() if i.user_id == user_id and i.id not in self._deleted
        ]

    def load_csv(self, path: str | Path, user_id: int, tz: ZoneInfo) -> int:
        """Тот же разбор строк, что у PostgreSQL-импорта (app.storage.enrich.csv_item):
        исправленные падежи, люди, компании, тема, тип, длительность. Иначе поиск в памяти
        и в PostgreSQL видел бы разные данные."""
        from app.storage.enrich import csv_item

        now = datetime.now(UTC)
        count = 0
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                item, _ = csv_item(row, user_id, tz)
                item = item.model_copy(update={"created_at": now, "updated_at": now})
                self._items[item.id] = item
                self._next_id = max(self._next_id, item.id + 1)
                count += 1
        return count

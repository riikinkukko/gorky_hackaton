"""ItemRepository поверх PostgreSQL (контракт из app.contracts)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from dateutil.rrule import rrulestr
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.contracts import ActionRecord, Item, ItemStatus, ItemType
from app.storage.config import DEFAULT_TZ
from app.storage.db import get_sessionmaker
from app.storage.embeddings import Embedder, embedding_text, get_embedder
from app.storage.models import ActionRow, ItemRow, UserRow
from app.storage.ranking import make_fields

# Поля Item, которые хранятся один в один.
_PLAIN = (
    "type",
    "title",
    "title_original",
    "starts_at",
    "ends_at",
    "due_at",
    "has_time",
    "status",
    "rrule",
    "person",
    "organization",
    "topic",
    "object",
    "source",
)


def _tz(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("naive datetime: в хранилище только tz-aware время")
    return dt.astimezone(DEFAULT_TZ)


def row_to_item(r: ItemRow) -> Item:
    return Item(
        id=r.id,
        user_id=r.user_id,
        type=ItemType(r.type),
        title=r.title,
        title_original=r.title_original,
        starts_at=_tz(r.starts_at),
        ends_at=_tz(r.ends_at),
        due_at=_tz(r.due_at),
        has_time=r.has_time,
        status=ItemStatus(r.status),
        rrule=r.rrule,
        person=list(r.person or []),
        organization=list(r.organization or []),
        topic=r.topic,
        object=r.object,
        source=r.source,
        created_at=_tz(r.created_at),
        updated_at=_tz(r.updated_at),
    )


def fill_search_fields(row: ItemRow, embedder: Embedder) -> None:
    """Пересчитать нормализованные поля и эмбеддинг из содержательных полей."""
    f = make_fields(row.title, row.topic, row.object, row.person or [], row.organization or [])
    row.person_norm = list(f.person_norm)
    row.organization_norm = list(f.organization_norm)
    row.search_text = f.search_text
    row.embedding = embedder.embed(
        [embedding_text(row.title, row.topic, row.object, row.person or [], row.organization or [])]
    )[0]


def _alive(user_id: int) -> Any:
    return and_(
        ItemRow.user_id == user_id,
        ItemRow.deleted_at.is_(None),
        ItemRow.status != ItemStatus.CANCELLED.value,
    )


class SqlItemRepository:
    """Реализация `ItemRepository`.

    Семантика:
    - `create` игнорирует `item.id`, `created_at`, `updated_at` (v1.1: id=0 по умолчанию) —
      их ставит БД.
    - `list_range` возвращает записи, чей момент (`starts_at` или `due_at`) попадает в
      [start, end), встречи, пересекающие период, и повторяющиеся серии, начатые до `end`
      (развёртку RRULE в списке делает домен, см. `expand_occurrences`).
    - `find_overlaps` возвращает встречи, пересекающие [start, end), и задачи/напоминания
      со временем внутри интервала (домен решает: конфликт или предупреждение).
      Повторяющиеся встречи разворачиваются.
    """

    def __init__(
        self, sessionmaker: async_sessionmaker | None = None, embedder: Embedder | None = None
    ) -> None:
        self._sm = sessionmaker or get_sessionmaker()
        self._emb = embedder or get_embedder()

    @property
    def sessionmaker(self) -> async_sessionmaker:
        return self._sm

    @property
    def embedder(self) -> Embedder:
        return self._emb

    # ---------- контракт ----------

    async def create(self, item: Item) -> Item:
        row = ItemRow(user_id=item.user_id, **{f: _val(getattr(item, f)) for f in _PLAIN})
        fill_search_fields(row, self._emb)
        async with self._sm() as s, s.begin():
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row_to_item(row)

    async def get(self, user_id: int, item_id: int) -> Item | None:
        async with self._sm() as s:
            row = await s.scalar(
                select(ItemRow).where(
                    ItemRow.id == item_id, ItemRow.user_id == user_id, ItemRow.deleted_at.is_(None)
                )
            )
            return row_to_item(row) if row else None

    async def update(self, item: Item) -> Item:
        async with self._sm() as s, s.begin():
            row = await s.scalar(
                select(ItemRow)
                .where(ItemRow.id == item.id, ItemRow.user_id == item.user_id)
                .with_for_update()
            )
            if row is None:
                raise KeyError(f"item {item.id} not found for user {item.user_id}")
            for f in _PLAIN:
                setattr(row, f, _val(getattr(item, f)))
            row.updated_at = func.now()
            fill_search_fields(row, self._emb)
            await s.flush()
            await s.refresh(row)
            return row_to_item(row)

    async def soft_delete(self, user_id: int, item_id: int) -> None:
        await self._set_deleted(user_id, item_id, func.now())

    async def restore(self, user_id: int, item_id: int) -> None:
        await self._set_deleted(user_id, item_id, None)

    async def list_range(
        self, user_id: int, start: datetime, end: datetime, type: ItemType | None = None
    ) -> list[Item]:
        _tz(start), _tz(end)
        cond = [
            ItemRow.user_id == user_id,
            ItemRow.deleted_at.is_(None),
            or_(
                and_(ItemRow.sort_at >= start, ItemRow.sort_at < end),
                and_(ItemRow.starts_at < end, ItemRow.ends_at > start),
                and_(ItemRow.rrule.is_not(None), ItemRow.sort_at < end),
            ),
        ]
        if type is not None:
            cond.append(ItemRow.type == type.value)
        async with self._sm() as s:
            rows = (
                await s.scalars(select(ItemRow).where(*cond).order_by(ItemRow.sort_at, ItemRow.id))
            ).all()
            return [row_to_item(r) for r in rows]

    async def find_overlaps(self, user_id: int, start: datetime, end: datetime) -> list[Item]:
        _tz(start), _tz(end)
        meeting_end = func.coalesce(ItemRow.ends_at, ItemRow.starts_at + timedelta(minutes=60))
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(ItemRow)
                    .where(
                        _alive(user_id),
                        or_(
                            and_(
                                ItemRow.type == "meeting",
                                ItemRow.starts_at < end,
                                meeting_end > start,
                            ),
                            and_(
                                ItemRow.type != "meeting",
                                ItemRow.has_time,
                                ItemRow.sort_at >= start,
                                ItemRow.sort_at < end,
                            ),
                        ),
                        ItemRow.rrule.is_(None),
                    )
                    .order_by(ItemRow.sort_at, ItemRow.id)
                )
            ).all()
            series = (
                await s.scalars(
                    select(ItemRow).where(
                        _alive(user_id), ItemRow.rrule.is_not(None), ItemRow.sort_at < end
                    )
                )
            ).all()
        out = [row_to_item(r) for r in rows]
        for r in series:
            it = row_to_item(r)
            for occ_start, occ_end in expand_occurrences(it, start - timedelta(days=1), end):
                if occ_start < end and occ_end > start:
                    out.append(it.model_copy(update={"starts_at": occ_start, "ends_at": occ_end}))
                    break
        return out

    # ---------- сверх контракта ----------

    async def ensure_user(self, user: str | int) -> int:
        """Пользователь по id («1» — демо-пользователь из make seed) или по имени."""
        if isinstance(user, int) or str(user).isdigit():
            uid = int(user)
            async with self._sm() as s, s.begin():
                if await s.scalar(select(UserRow.id).where(UserRow.id == uid)) is None:
                    s.add(UserRow(id=uid, name=f"user-{uid}"))
                    await s.flush()
                    await s.execute(
                        text(
                            "SELECT setval(pg_get_serial_sequence('users','id'), "
                            "GREATEST((SELECT max(id) FROM users), 1))"
                        )
                    )
            return uid
        return await self.get_or_create_user(str(user))

    async def get_or_create_user(self, name: str) -> int:
        async with self._sm() as s, s.begin():
            uid = await s.scalar(select(UserRow.id).where(UserRow.name == name))
            if uid is None:
                u = UserRow(name=name)
                s.add(u)
                await s.flush()
                uid = u.id
            return uid

    async def get_many(self, user_id: int, ids: list[int]) -> list[Item]:
        async with self._sm() as s:
            rows = (
                await s.scalars(
                    select(ItemRow).where(ItemRow.user_id == user_id, ItemRow.id.in_(ids))
                )
            ).all()
            by_id = {r.id: row_to_item(r) for r in rows}
            return [by_id[i] for i in ids if i in by_id]

    async def log_action(self, action: ActionRecord) -> int:
        """Записать действие для undo; id и created_at ставит база → DialogState.last_action_id."""
        async with self._sm() as s, s.begin():
            row = ActionRow(
                user_id=action.user_id,
                session_id=action.session_id,
                kind=action.kind,
                item_id=action.item_id,
                before=action.before.model_dump(mode="json") if action.before else None,
                after=action.after.model_dump(mode="json") if action.after else None,
                undone_at=func.now() if action.undone else None,
            )
            s.add(row)
            await s.flush()
            return row.id

    async def get_action(self, user_id: int, action_id: int) -> ActionRecord | None:
        async with self._sm() as s:
            a = await s.scalar(
                select(ActionRow).where(ActionRow.id == action_id, ActionRow.user_id == user_id)
            )
            if a is None:
                return None
            return ActionRecord(
                id=a.id,
                session_id=a.session_id or "",
                user_id=a.user_id,
                kind=a.kind,
                item_id=a.item_id,
                before=Item.model_validate(a.before) if a.before else None,
                after=Item.model_validate(a.after) if a.after else None,
                undone=a.undone_at is not None,
                created_at=_tz(a.created_at),
            )

    async def mark_undone(self, user_id: int, action_id: int) -> None:
        async with self._sm() as s, s.begin():
            res = await s.execute(
                update(ActionRow)
                .where(ActionRow.id == action_id, ActionRow.user_id == user_id)
                .values(undone_at=func.coalesce(ActionRow.undone_at, func.now()))
            )
            if res.rowcount == 0:
                raise KeyError(f"action {action_id} not found for user {user_id}")

    async def _set_deleted(self, user_id: int, item_id: int, value: Any) -> None:
        async with self._sm() as s, s.begin():
            res = await s.execute(
                update(ItemRow)
                .where(ItemRow.id == item_id, ItemRow.user_id == user_id)
                .values(deleted_at=value, updated_at=func.now())
            )
            if res.rowcount == 0:
                raise KeyError(f"item {item_id} not found for user {user_id}")


def expand_occurrences(
    item: Item, start: datetime, end: datetime, limit: int = 500
) -> list[tuple[datetime, datetime]]:
    """Вхождения повторяющейся записи в [start, end): (начало, конец)."""
    base = item.starts_at or item.due_at
    if base is None:
        return []
    dur = (item.ends_at - item.starts_at) if item.starts_at and item.ends_at else timedelta(0)
    if not item.rrule:
        return [(base, base + dur)] if start <= base < end else []
    rule = rrulestr(item.rrule, dtstart=base)
    out = []
    for occ in rule.xafter(start - dur, count=limit, inc=True):
        if occ >= end:
            break
        out.append((occ, occ + dur))
    return out


def _val(v: Any) -> Any:
    if isinstance(v, (ItemType, ItemStatus)):
        return v.value
    if isinstance(v, datetime):
        return _tz(v)
    return v

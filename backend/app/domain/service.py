"""ItemService: бизнес-операции над записями. Не знает про диалог и про NLU."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.contracts import (
    ActionRecord,
    Item,
    ItemRepository,
    ItemStatus,
    ItemType,
    SearchEngine,
    Slots,
    TimeSpec,
)
from app.domain import recurrence, ru
from app.domain.conflicts import ConflictChecker
from app.domain.timeutil import combine, default_duration_min, item_time

NEXT_EVENT_HORIZON = timedelta(days=366)
TIER_TOLERANCE = 0.1  # «тот же уровень релевантности»: score в пределах 10% от первого


@dataclass
class Found:
    """Результат поиска для ответа: main — что называем, weaker — «ещё N похожих»."""

    main: list[Item]
    weaker: list[Item]


def _item_stems(item: Item) -> set[str]:
    return set(ru.stems(item.title)) | {ru.stem(x) for x in [*item.person, *item.organization]}


@dataclass
class UndoResult:
    action: ActionRecord
    item: Item | None
    batch: list[Item] = field(default_factory=list)  # массовое действие: все возвращённые записи


class ItemService:
    """Журнал undo — через ItemRepository.log_action / get_action / mark_undone (контракт v1.1)."""

    def __init__(self, repo: ItemRepository, search: SearchEngine) -> None:
        self.repo = repo
        self.search_engine = search
        self.conflicts = ConflictChecker(self)
        # Массовое действие = N записей журнала. Ключ — (user_id, id последней записи),
        # значение — id всех записей пакета. Живёт в памяти процесса; для надёжности после
        # рестарта предложено ActionRecord.batch_id (контракт v1.3).
        self._batches: dict[tuple[int, int], list[int]] = {}

    async def _log(
        self,
        session_id: str,
        kind: str,
        item_id: int,
        user_id: int,
        before: Item | None,
        after: Item | None,
    ) -> int:
        return await self.repo.log_action(
            ActionRecord(
                session_id=session_id,
                user_id=user_id,
                kind=kind,
                item_id=item_id,
                before=before,
                after=after,
            )
        )

    # ---------- построение ----------

    def build(
        self,
        user_id: int,
        type_: ItemType,
        title: str,
        when: datetime | None,
        has_time: bool,
        now: datetime,
        *,
        duration_min: int | None = None,
        participants: list[str] | None = None,
        organization: str | None = None,
        rrule: str | None = None,
        source: str = "voice",
    ) -> Item:
        """Собирает несохранённую запись: id, created_at, updated_at ставит репозиторий."""
        if rrule and when is not None:
            when = recurrence.first_occurrence(rrule, when, now) or when
        starts_at = ends_at = due_at = None
        if type_ == ItemType.MEETING:
            starts_at = when
            minutes = duration_min or default_duration_min(type_, title)
            ends_at = when + timedelta(minutes=minutes) if when is not None else None
        else:
            due_at = when
        return Item(
            user_id=user_id,
            type=type_,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            due_at=due_at,
            has_time=has_time if when is not None else False,
            status=ItemStatus.OPEN,
            rrule=rrule,
            person=list(participants or []),
            organization=[organization] if organization else [],
            source=source,
        )

    # ---------- операции ----------

    async def create(self, item: Item, session_id: str) -> tuple[Item, int]:
        saved = await self.repo.create(item)
        return saved, await self._log(session_id, "create", saved.id, saved.user_id, None, saved)

    async def list_period(
        self, user_id: int, start: datetime, end: datetime, type: ItemType | None = None
    ) -> list[Item]:
        """Записи периода [start, end) с развёрткой повторов, по возрастанию времени."""
        raw = await self.repo.list_range(user_id, start, end, type)
        out: list[Item] = []
        for item in raw:
            if type is not None and item.type != type:
                continue
            if item.status == ItemStatus.CANCELLED:
                continue
            out.extend(recurrence.expand(item, start, end))
        out.sort(key=lambda i: (item_time(i) or start, i.id))
        return out

    async def next_after(
        self, user_id: int, after: datetime, type: ItemType | None = None
    ) -> Item | None:
        items = await self.list_period(user_id, after, after + NEXT_EVENT_HORIZON, type)
        return next((i for i in items if (item_time(i) or after) >= after), None)

    async def search(
        self, user_id: int, slots: Slots, now: datetime, limit: int = 20
    ) -> list[tuple[Item, float]]:
        return await self.search_engine.search(user_id, slots, now, limit)

    async def find(self, user_id: int, slots: Slots, now: datetime, limit: int = 200) -> Found:
        """Поиск для ответа пользователю поверх SearchEngine.

        1. Люди и организации из запроса — жёсткое условие: «с Сергеем» не отвечаем
           записями «Позвонить Анной». Если движок их не учёл — фильтруем сами.
        2. Пустой ответ при названном имени — повтор с именем в query (движок мог
           не смотреть на organization).
        3. Если у движка есть явный лучший уровень совпадений (≤ 5 записей) — отвечаем им,
           остальное — «ещё N похожих».
        """
        results = await self.search_engine.search(user_id, slots, now, limit)
        names = [*slots.participants, *([slots.organization] if slots.organization else [])]
        if not results and names:
            q = " ".join(filter(None, [slots.query, *names]))
            results = await self.search_engine.search(
                user_id, slots.model_copy(update={"query": q}), now, limit
            )
        if names:
            want = [ru.stem(n) for n in names]
            results = [(i, sc) for i, sc in results if all(w in _item_stems(i) for w in want)]
        if not results:
            return Found([], [])
        # Порядок движка — главный сигнал: у гибридного поиска score не обязан расти
        # с позицией. Лучший уровень — префикс выдачи со score в пределах TIER_TOLERANCE
        # от первого (одинаковые «Позвонить Сергею» за разные годы).
        first = results[0][1]
        n = 1
        while n < len(results) and abs(results[n][1] - first) <= TIER_TOLERANCE * abs(first):
            n += 1
        items = [i for i, _ in results]
        if n < len(results):
            return Found(items[:n], items[n:])
        return Found(items, [])

    async def get(self, user_id: int, item_id: int) -> Item | None:
        return await self.repo.get(user_id, item_id)

    def moved(self, item: Item, spec: TimeSpec, now: datetime) -> Item:
        """Копия записи, перенесённая по фразе («на завтра», «на 19:00»). Не сохраняет."""
        base = item_time(item)
        new_dt, has_time = combine(base, item.has_time, spec, now)
        if new_dt is None:
            return item
        return self.moved_to(item, new_dt, has_time, now)

    def moved_to(self, item: Item, new_dt: datetime, has_time: bool, now: datetime) -> Item:
        """Копия записи с новым абсолютным временем (длительность встречи сохраняется)."""
        upd: dict[str, object] = {"has_time": has_time, "updated_at": now}
        if item.starts_at is not None:
            duration = (item.ends_at - item.starts_at) if item.ends_at else None
            upd["starts_at"] = new_dt
            upd["ends_at"] = new_dt + duration if duration is not None else None
        else:
            upd["due_at"] = new_dt
        return item.model_copy(update=upd)

    async def save_update(self, before: Item, after: Item, session_id: str) -> tuple[Item, int]:
        saved = await self.repo.update(after)
        return saved, await self._log(session_id, "update", saved.id, saved.user_id, before, saved)

    async def complete(self, item: Item, session_id: str, now: datetime) -> tuple[Item, int]:
        saved = await self.repo.update(
            item.model_copy(update={"status": ItemStatus.DONE, "updated_at": now})
        )
        return saved, await self._log(session_id, "complete", saved.id, saved.user_id, item, saved)

    async def delete(self, item: Item, session_id: str) -> int:
        await self.repo.soft_delete(item.user_id, item.id)
        return await self._log(session_id, "delete", item.id, item.user_id, item, None)

    def register_batch(self, user_id: int, action_ids: list[int]) -> None:
        if len(action_ids) > 1:
            self._batches[(user_id, action_ids[-1])] = list(action_ids)

    async def undo(self, session_id: str, user_id: int, action_id: int | None) -> UndoResult | None:
        """Откатывает действие action_id (обычно state.last_action_id) этой же сессии.

        Если это последняя запись массового действия — откатывается весь пакет.
        """
        if action_id is None:
            return None
        batch = self._batches.pop((user_id, action_id), None)
        if batch:
            results = [await self._undo_one(session_id, user_id, a) for a in reversed(batch)]
            done = [r for r in results if r is not None]
            if not done:
                return None
            items = [r.item for r in reversed(done) if r.item is not None]
            return UndoResult(done[0].action, items[0] if items else None, items)
        return await self._undo_one(session_id, user_id, action_id)

    async def _undo_one(self, session_id: str, user_id: int, action_id: int) -> UndoResult | None:
        action = await self.repo.get_action(user_id, action_id)
        if action is None or action.undone or action.session_id != session_id:
            return None
        item: Item | None
        if action.kind == "create":
            await self.repo.soft_delete(user_id, action.item_id)
            item = action.after
        elif action.kind == "delete":
            await self.repo.restore(user_id, action.item_id)
            item = await self.repo.get(user_id, action.item_id)
        else:  # update / complete — возвращаем снимок
            assert action.before is not None
            item = await self.repo.update(action.before)
        await self.repo.mark_undone(user_id, action.id)
        return UndoResult(action, item)

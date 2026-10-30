"""Поиск по записям в памяти (STORAGE_IMPL=memory) — тот же алгоритм, что и в PostgreSQL.

Кандидаты — все записи пользователя, прошедшие фильтры (их мало: сотни, а не миллионы);
ранжирование — app.storage.ranking, как у HybridSearchEngine. Поисковые поля записи
считаются один раз и пересчитываются, только если запись изменилась.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.contracts import Item, Slots
from app.storage.ranking import (
    SCORE_FILTER_ONLY,
    SCORE_STRUCTURE_ONLY,
    Doc,
    SearchFields,
    SearchPlan,
    is_constrained,
    passes_filters,
    plan_query,
    rank_all,
    rank_matches,
    search_fields,
)


class ItemSource(Protocol):
    def all_items(self, user_id: int) -> list[Item]: ...  # без удалённых


class InMemorySearchEngine:
    """Реализация `SearchEngine` над любым хранилищем с all_items(user_id)."""

    def __init__(self, repo: ItemSource) -> None:
        self.repo = repo
        self.last_plan: SearchPlan | None = None
        self._fields: dict[int, tuple[Item, SearchFields]] = {}

    def _doc(self, item: Item) -> Doc:
        cached = self._fields.get(item.id)
        if cached is None or cached[0] != item:
            cached = (item, search_fields(item))
            self._fields[item.id] = cached
        return Doc(item, cached[1])

    async def search(
        self, user_id: int, slots: Slots, now: datetime, limit: int = 20
    ) -> list[tuple[Item, float]]:
        plan = plan_query(slots)
        self.last_plan = plan
        if not is_constrained(slots, plan):
            return []
        docs = [
            d
            for d in map(self._doc, self.repo.all_items(user_id))
            if passes_filters(d, slots, plan, now)
        ]
        if not plan.groups:
            return rank_all(docs, plan, now, SCORE_FILTER_ONLY)[:limit]
        ranked = rank_matches(docs, plan, now)
        if ranked is None:
            ranked = rank_all(docs, plan, now, SCORE_STRUCTURE_ONLY)
        return ranked[:limit]

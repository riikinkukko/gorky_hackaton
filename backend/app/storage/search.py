"""Гибридный поиск в PostgreSQL: SQL-фильтры → кандидаты (FTS + триграммы + вектор) →
общее ранжирование из app.storage.ranking (тот же код, что у поиска в памяти).

Кандидаты: FTS (simple по леммам + russian по заголовку), триграммы по нормализованному тексту,
k-NN по эмбеддингу. Они отвечают только за полноту отбора; уровень, порядок и «не нашёл»
решает ranking.rank_matches. Описание алгоритма — в docstring app/storage/ranking.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, literal, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.contracts import Item, ItemStatus, Slots
from app.storage.db import get_sessionmaker
from app.storage.embeddings import Embedder, get_embedder
from app.storage.models import ItemRow
from app.storage.ranking import (
    SCORE_FILTER_ONLY,
    SCORE_STRUCTURE_ONLY,
    TIME_WORDS,
    TYPE_WORDS,
    Doc,
    SearchFields,
    SearchPlan,
    is_constrained,
    period,
    plan_query,
    rank_all,
    rank_matches,
)
from app.storage.repository import row_to_item

__all__ = ["TIME_WORDS", "TYPE_WORDS", "HybridSearchEngine", "SearchPlan", "plan_query"]

CANDIDATES_PER_LIST = 200
VECTOR_CANDIDATES = 50
TRIGRAM_MIN = 0.35
VECTOR_MAX_DISTANCE = 0.75


def _tsquery_simple(groups: list[frozenset[str]]) -> str:
    terms = []
    for g in groups:
        for t in sorted(g):
            parts = [p for p in t.split("_") if p]
            terms.append(" <-> ".join(parts) if len(parts) > 1 else parts[0])
    return " | ".join(f"({t})" for t in terms)


def _docs(rows: Iterable[ItemRow]) -> list[Doc]:
    return [
        Doc(
            row_to_item(r),
            SearchFields(
                tuple(r.person_norm or ()), tuple(r.organization_norm or ()), r.search_text
            ),
        )
        for r in rows
    ]


class HybridSearchEngine:
    """Реализация `SearchEngine` из контракта."""

    def __init__(
        self, sessionmaker: async_sessionmaker | None = None, embedder: Embedder | None = None
    ) -> None:
        self._sm = sessionmaker or get_sessionmaker()
        self._emb = embedder or get_embedder()
        self.last_plan: SearchPlan | None = None

    async def search(
        self, user_id: int, slots: Slots, now: datetime, limit: int = 20
    ) -> list[tuple[Item, float]]:
        plan = plan_query(slots)
        self.last_plan = plan
        if not is_constrained(slots, plan):
            return []
        where = self._filters(user_id, slots, plan, now)
        async with self._sm() as s:
            if not plan.groups:
                # только структурные фильтры: всё подходящее, по времени
                rows = (await s.scalars(select(ItemRow).where(*where))).all()
                return rank_all(_docs(rows), plan, now, SCORE_FILTER_ONLY)[:limit]
            cand: dict[int, ItemRow] = {}
            for q in self._candidate_queries(where, plan):
                for row, _ in (await s.execute(q)).all():
                    cand.setdefault(row.id, row)
            ranked = rank_matches(_docs(cand.values()), plan, now)
            if ranked is None:
                # смысловые слова не совпали, но названы человек/компания: всё по ним
                rows = (await s.scalars(select(ItemRow).where(*where))).all()
                ranked = rank_all(_docs(rows), plan, now, SCORE_STRUCTURE_ONLY)
        return ranked[:limit]

    # ---------- части ----------

    def _filters(self, user_id: int, slots: Slots, plan: SearchPlan, now: datetime) -> list[Any]:
        """SQL-двойник ranking.passes_filters (держать в синхроне; это проверяет тест паритета)."""
        w: list[Any] = [ItemRow.user_id == user_id, ItemRow.deleted_at.is_(None)]
        if slots.type is not None:
            w.append(ItemRow.type == slots.type.value)
        if slots.status is not None:
            w.append(ItemRow.status == slots.status.value)
        else:
            w.append(ItemRow.status != ItemStatus.CANCELLED.value)
        if plan.persons:
            w.append(ItemRow.person_norm.overlap(plan.persons))
        if plan.organizations:
            w.append(ItemRow.organization_norm.overlap(plan.organizations))
        if plan.tense == "past":
            w.append(ItemRow.sort_at < now)
        elif plan.tense == "future":
            w.append(ItemRow.sort_at >= now)
        if rng := period(slots):
            w.append(and_(ItemRow.sort_at >= rng[0], ItemRow.sort_at < rng[1]))
        return w

    def _candidate_queries(self, where: list[Any], plan: SearchPlan) -> list[Select]:
        qs: list[Select] = []
        tsq: Any = func.to_tsquery("simple", _tsquery_simple(plan.groups))
        raw = [w for w in plan.raw_words if w.isalnum()]
        if raw:
            tsq = tsq.op("||")(func.to_tsquery("russian", " | ".join(raw)))
        rank = func.ts_rank_cd(ItemRow.search_tsv, tsq)
        qs.append(
            select(ItemRow, rank)
            .where(*where, ItemRow.search_tsv.op("@@")(tsq))
            .order_by(rank.desc(), ItemRow.id)
            .limit(CANDIDATES_PER_LIST)
        )

        qtext = " ".join(" ".join(sorted(g)) for g in plan.groups)
        sim = func.word_similarity(
            literal(" ".join(sorted(next(iter(g)) for g in plan.groups))), ItemRow.search_text
        )
        qs.append(
            select(ItemRow, sim)
            .where(*where, sim >= TRIGRAM_MIN)
            .order_by(sim.desc(), ItemRow.id)
            .limit(CANDIDATES_PER_LIST)
        )

        qvec = self._emb.embed([qtext], query=True)[0]
        dist = ItemRow.embedding.cosine_distance(qvec)
        qs.append(
            select(ItemRow, dist)
            .where(*where, ItemRow.embedding.is_not(None), dist <= VECTOR_MAX_DISTANCE)
            .order_by(dist, ItemRow.id)
            .limit(VECTOR_CANDIDATES)
        )
        return qs

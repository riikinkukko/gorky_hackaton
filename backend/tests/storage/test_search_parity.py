"""Паритет поиска: PostgreSQL (HybridSearchEngine) и память (FakeSearchEngine) на одних данных
дают одинаковую выдачу — те же записи, в том же порядке, с теми же баллами.

Повод: «Найди встречу, старую» в памяти находила созвоны со «Стартом» (совпадение по
4 буквам), а PostgreSQL отвечал «не нашёл по „старый“».
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.contracts import DialogState, ItemType, Slots, TimeSpec
from app.fakes.repository import InMemoryRepository
from app.fakes.search import FakeSearchEngine
from app.nlu import HybridNLU
from app.storage.search import HybridSearchEngine
from eval.run_eval import baseline_slots

from .conftest import CSV

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)
GOLDEN = Path(__file__).resolve().parents[2] / "eval" / "golden_eval.jsonl"
GOLDEN_SEARCH = [
    r["query"]
    for r in map(json.loads, GOLDEN.read_text(encoding="utf-8").splitlines())
    if r["category"] == "search" and not r.get("history")
]
EXTRA = [
    "Найди встречу, старую",
    "Найди старую встречу",
    "Старую встречу",
    "Покажи прошедшие встречи",
    "Покажи будущие встречи",
    "Найди давнюю встречу с Иваном",
    "Что было со Стартом",
    "Найди созвоны с Альфой по CRM",
    "Найди смету",
    "Найди всё про сайт",
    "Найди задачу про отчёт для Ольги",
    "Найди квантовую хромодинамику",
]
QUERIES = GOLDEN_SEARCH + EXTRA

# Запросы без NLU: только структурные слоты.
NOV = TimeSpec(
    text="в ноябре",
    start=datetime(2026, 11, 1, tzinfo=MSK),
    end=datetime(2026, 12, 1, tzinfo=MSK),
    is_period=True,
    has_date=True,
)
SLOT_CASES = {
    "type+past": Slots(type=ItemType.MEETING, tense="past"),
    "person": Slots(participants=["Мария"]),
    "org+future": Slots(organization="Вектор", tense="future"),
    "period": Slots(when=NOV),
    "period+type": Slots(when=NOV, type=ItemType.MEETING),
    "reminder+query": Slots(type=ItemType.REMINDER, query="CRM"),
    "empty": Slots(),
    "nonsense": Slots(query="квантовая хромодинамика"),
    "tense-word-only": Slots(query="старый"),
}
pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def memory_engine(demo_user: int) -> FakeSearchEngine:
    repo = InMemoryRepository()
    repo.load_csv(CSV, demo_user, MSK)
    return FakeSearchEngine(repo)


@pytest.fixture(scope="module")
def nlu() -> HybridNLU:
    return HybridNLU(None)  # правила, без LLM — детерминированно


def _view(res: list) -> list[tuple[int, float]]:
    return [(item.id, score) for item, score in res]


async def _both(sm, memory_engine, user_id: int, slots: Slots, limit: int = 200):
    pg = await HybridSearchEngine(sm).search(user_id, slots, NOW, limit=limit)
    mem = await memory_engine.search(user_id, slots, NOW, limit=limit)
    return _view(pg), _view(mem)


@pytest.mark.parametrize("query", QUERIES)
async def test_parity_with_nlu_slots(sm, demo_user, memory_engine, nlu, query):
    """Слоты, как их отдаёт NLU (правила), — то, что видит пользователь."""
    cmd = await nlu.parse(query, DialogState(session_id="p", user_id=demo_user), NOW)
    pg, mem = await _both(sm, memory_engine, demo_user, cmd.slots)
    assert pg == mem, f"{query!r}: slots={cmd.slots.model_dump(exclude_defaults=True)}"


@pytest.mark.parametrize("query", QUERIES)
async def test_parity_with_raw_query(sm, demo_user, memory_engine, query):
    """Без NLU: весь текст в query — поиск сам достаёт имена, компании и время."""
    pg, mem = await _both(sm, memory_engine, demo_user, baseline_slots(query, NOW))
    assert pg == mem, query


@pytest.mark.parametrize("case", list(SLOT_CASES))
async def test_parity_structural_slots(sm, demo_user, memory_engine, case):
    pg, mem = await _both(sm, memory_engine, demo_user, SLOT_CASES[case], limit=1000)
    assert pg == mem, case


async def test_old_meeting_means_past_meetings(sm, demo_user, memory_engine, nlu):
    """«Найди встречу, старую»: прошлые встречи, свежие первыми — в обоих хранилищах."""
    cmd = await nlu.parse("Найди встречу, старую", DialogState(session_id="p", user_id=1), NOW)
    assert cmd.slots.tense == "past" and not cmd.slots.query
    for engine in (HybridSearchEngine(sm), memory_engine):
        res = await engine.search(demo_user, cmd.slots, NOW, limit=1000)
        times = [i.starts_at for i, _ in res]
        assert len(res) == 133 and all(i.type is ItemType.MEETING for i, _ in res)
        assert all(t is not None and t < NOW for t in times) and times == sorted(times)[::-1]


async def test_tense_word_in_query_without_nlu(sm, demo_user, memory_engine):
    """Страховка поиска: NLU оставил «старый» в query и tense=any — это всё равно прошлое."""
    slots = Slots(type=ItemType.MEETING, query="старый")
    pg, mem = await _both(sm, memory_engine, demo_user, slots, limit=1000)
    assert pg == mem and len(pg) == 133


async def test_memory_engine_sees_updates(demo_user):
    """Кеш поисковых полей в памяти пересчитывается после изменения записи."""
    repo = InMemoryRepository()
    repo.load_csv(CSV, demo_user, MSK)
    eng = FakeSearchEngine(repo)
    slots = Slots(query="квантовая хромодинамика")
    assert await eng.search(demo_user, slots, NOW) == []
    it = await repo.get(demo_user, 530)
    await repo.update(it.model_copy(update={"title": "Квантовая хромодинамика с Иваном"}))
    assert [i.id for i, _ in await eng.search(demo_user, slots, NOW)] == [530]


async def test_memory_data_matches_postgres_import(demo_user):
    """Данные в памяти совпадают с PostgreSQL-импортом: исправленные заголовки и сущности."""
    repo = InMemoryRepository()
    repo.load_csv(CSV, demo_user, MSK)
    it = await repo.get(demo_user, 2)
    assert (it.title, it.title_original, it.person) == (
        "Отправить договор Ивану",
        "Отправить договор Иваном",
        ["Иван"],
    )
    assert (await repo.get(demo_user, 239)).organization == ["Старт"]

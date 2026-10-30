"""SearchEngine на живой PostgreSQL: эталонные поисковые запросы и фильтры из Slots."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.contracts import ItemType, Slots, TimeSpec
from app.storage.search import HybridSearchEngine
from eval.metrics import recall_at_k
from eval.run_eval import baseline_slots

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)
GOLDEN = Path(__file__).resolve().parents[2] / "eval" / "golden_eval.jsonl"
SEARCH_ROWS = [
    r
    for r in map(json.loads, GOLDEN.read_text(encoding="utf-8").splitlines())
    if r["category"] == "search" and not r.get("history")
]
pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("row", SEARCH_ROWS, ids=[r["qid"] for r in SEARCH_ROWS])
async def test_golden_search_baseline(sm, demo_user, row):
    """Без NLU (базовый разборщик): Recall@5 = 1 и верный ответ на «когда…»."""
    exp = row["expected"]
    res = await HybridSearchEngine(sm).search(demo_user, baseline_slots(row["query"], NOW), NOW)
    ids = [i.id for i, _ in res if i.source == "import"]
    rel = set(exp.get("all_ids") or exp.get("relevant_ids") or [])
    if "answer_id" in exp:
        assert ids[:1] == [exp["answer_id"]]
        rel.add(exp["answer_id"])
    if rel:
        assert recall_at_k(ids, rel, 5) == 1.0, ids[:5]
    else:
        assert ids == []


async def test_slots_from_nlu_are_respected(sm, demo_user):
    """Структурные слоты от NLU работают и без слов в query."""
    eng = HybridSearchEngine(sm)
    res = await eng.search(
        demo_user, Slots(type=ItemType.MEETING, participants=["Иван"], tense="past"), NOW
    )
    assert [i.id for i, _ in res] == [530, 429, 309]
    res = await eng.search(demo_user, Slots(organization="Вектором", tense="future"), NOW)
    assert res and all(i.organization == ["Вектор"] for i, _ in res)
    times = [i.starts_at or i.due_at for i, _ in res]
    assert times == sorted(times)


async def test_period_filter(sm, demo_user):
    when = TimeSpec(
        text="в 2026",
        start=datetime(2026, 1, 1, tzinfo=MSK),
        end=datetime(2027, 1, 1, tzinfo=MSK),
        is_period=True,
    )
    res = await HybridSearchEngine(sm).search(
        demo_user, Slots(query="хакатон", when=when, tense="past"), NOW
    )
    assert {i.id for i, _ in res} == {541, 547, 549, 573, 596, 648, 653}


async def test_nothing_for_empty_or_unknown(sm, demo_user):
    eng = HybridSearchEngine(sm)
    assert await eng.search(demo_user, Slots(), NOW) == []
    assert await eng.search(demo_user, Slots(query="квантовая хромодинамика"), NOW) == []


@pytest.mark.parametrize("row", SEARCH_ROWS, ids=[r["qid"] for r in SEARCH_ROWS])
async def test_score_is_tier_and_never_grows(sm, demo_user, row):
    """score — уровень релевантности: не растёт вниз по списку (диалог берёт лучший уровень)."""
    res = await HybridSearchEngine(sm).search(demo_user, baseline_slots(row["query"], NOW), NOW)
    scores = [s for _, s in res]
    assert scores == sorted(scores, reverse=True)


async def test_last_time_answer_is_first_of_top_tier(sm, demo_user):
    """«Когда я последний раз звонил Сергею?»: три равные записи, первая — самая свежая (q22)."""
    slots = Slots(participants=["Сергей"], query="позвонить", tense="past")  # как отдаёт NLU
    res = await HybridSearchEngine(sm).search(demo_user, slots, NOW)
    top = max(s for _, s in res)
    tier = [i.id for i, s in res if s == top]
    assert tier == [217, 141, 17]


async def test_nlu_expanded_query_is_one_concept(sm, demo_user):
    """NLU раскрыл «деньги» в «бюджет смета» — это «или», а не два обязательных слова (q26)."""
    slots = Slots(type=ItemType.MEETING, query="планёрка бюджет смета")
    res = await HybridSearchEngine(sm).search(demo_user, slots, NOW)
    assert {i.id for i, _ in res} == {16, 50, 180, 373, 459, 524}
    assert {s for _, s in res} == {1.0}


async def test_big_top_tier_is_returned_alone(sm, demo_user):
    """8 демо по мобилке (q23): нижние уровни не отдаются, их нельзя вынести вперёд по времени."""
    slots = Slots(type=ItemType.MEETING, query="демо мобильное приложение")
    res = await HybridSearchEngine(sm).search(demo_user, slots, NOW)
    assert {i.id for i, _ in res} == {70, 262, 364, 413, 438, 543, 551, 749}

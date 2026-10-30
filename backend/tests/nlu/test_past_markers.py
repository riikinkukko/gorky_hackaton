"""«Старую встречу», «прошедшие», «давнюю» — признак прошлого, а не тема поиска.

Живой тест: «Старую встречу» GigaChat разобрал без tense → 190 будущих встреч.
"""

from __future__ import annotations

import pytest

from app.contracts import Intent, ItemType
from app.nlu import HybridNLU
from app.nlu.dates import RuleDateResolver
from app.nlu.entities import EntityNormalizer
from app.nlu.llm.prompts import build_messages
from app.nlu.llm.tools import TOOLS, to_command, validate_call

from .conftest import NOW, dt, parse, state


@pytest.mark.parametrize(
    "text",
    [
        "Найди встречу, старую",
        "Найди старую встречу",
        "Старую встречу",
        "Покажи прошедшие встречи",
        "Найди давнюю встречу с Иваном",
        "давным-давно была встреча с Марией",
        "Ранние встречи с Иваном",
        "Прежние планёрки",
    ],
)
def test_past_adjectives_set_tense_and_leave_query(nlu: HybridNLU, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent in (Intent.SEARCH, Intent.LIST)
    assert c.slots.tense == "past"
    q = (c.slots.query or "").lower()
    assert not any(w in q for w in ("стар", "прошедш", "давн", "ранн", "прежн")), q


def test_future_adjectives(nlu: HybridNLU) -> None:
    assert parse(nlu, "Покажи будущие встречи").slots.tense == "future"
    assert parse(nlu, "Предстоящие созвоны").slots.tense == "future"


@pytest.mark.parametrize(
    "text",
    [
        "Созвон со Стартом в пятницу в 11",  # «Старт» — компания, не «старый»
        "Подготовиться к встрече со Стартом в пятницу",
        "Напомни ранним утром в пятницу позвонить Ивану",  # «ранним утром» — часть дня
    ],
)
def test_no_false_past(nlu: HybridNLU, text: str) -> None:
    c = parse(nlu, text)
    assert c.slots.tense != "past"
    if c.slots.when and c.slots.when.start:
        assert c.slots.when.start > NOW  # «в пятницу» — будущая, а не прошлая


def test_past_marker_turns_weekday_back() -> None:
    """dates: при маркере прошлого «в пятницу» — прошлая пятница."""
    spec = RuleDateResolver().resolve("старую встречу в пятницу", NOW)
    assert spec.start is not None and spec.start.date() == dt(2026, 10, 16).date()


def _llm(name: str, args: dict, text: str):
    return to_command(
        name, validate_call(name, args), text, NOW, RuleDateResolver(), EntityNormalizer()
    )


@pytest.mark.parametrize(
    "name, args",
    [
        ("list_items", {"type": "meeting"}),  # как на стенде: модель не дала ни tense, ни query
        ("search_items", {"type": "meeting", "query": "старая встреча"}),
        ("search_items", {"type": "meeting", "tense": "any"}),
    ],
)
def test_llm_without_tense_falls_back_to_rules(name: str, args: dict) -> None:
    c = _llm(name, args, "Старую встречу")
    assert c.slots.tense == "past" and c.slots.type == ItemType.MEETING
    assert "стар" not in (c.slots.query or "")


def test_llm_tense_wins_when_given() -> None:
    assert _llm("search_items", {"tense": "future"}, "Старую встречу").slots.tense == "future"


def test_prompt_and_tools_ask_for_tense() -> None:
    system = build_messages("Старую встречу", state(), NOW)[0]["content"]
    assert "прошлое или будущее" in system and "«старую встречу»" in system
    assert "«прошедшие встречи»" in system
    by_name = {t["name"]: t for t in TOOLS}
    for name in ("search_items", "list_items"):
        tense = by_name[name]["parameters"]["properties"]["tense"]
        assert tense["enum"] == ["past", "future", "any"]
        assert "старую встречу" in tense["description"]

"""Заглушки удовлетворяют протоколам контракта (проверяется и mypy, и здесь на рантайме)."""

from __future__ import annotations

import inspect

from app import contracts
from app.fakes.dates import FakeDateResolver
from app.fakes.nlu import FakeNLU
from app.fakes.orchestrator import FakeOrchestrator
from app.fakes.repository import InMemoryRepository
from app.fakes.search import FakeSearchEngine
from app.fakes.voice import FakeSTT, FakeTTS

PAIRS = [
    (contracts.DateResolver, FakeDateResolver),
    (contracts.NLU, FakeNLU),
    (contracts.ItemRepository, InMemoryRepository),
    (contracts.SearchEngine, FakeSearchEngine),
    (contracts.STTProvider, FakeSTT),
    (contracts.TTSProvider, FakeTTS),
    (contracts.Orchestrator, FakeOrchestrator),
]


def test_fakes_implement_protocol_methods() -> None:
    for proto, impl in PAIRS:
        for name, member in inspect.getmembers(proto, inspect.isfunction):
            if name.startswith("_"):
                continue
            assert hasattr(impl, name), f"{impl.__name__} нет метода {name}"
            assert inspect.iscoroutinefunction(member) == inspect.iscoroutinefunction(
                getattr(impl, name)
            ), f"{impl.__name__}.{name}: async не совпадает с контрактом"


def test_contract_v11_defaults() -> None:
    item = contracts.Item(user_id=1, type=contracts.ItemType.TASK, title="x")
    assert item.id == 0 and item.created_at.tzinfo is not None
    assert contracts.TimeSpec(text="в три").has_date is False
    assert contracts.CONTRACT_VERSION == "1.3"


def test_contract_v13_turn_optional() -> None:
    cmd = contracts.Command(intent=contracts.Intent.UNKNOWN, source="rules", raw_text="x")
    assert cmd.turn is None
    cmd = contracts.Command(
        intent=contracts.Intent.UNKNOWN, source="rules", raw_text="нет, в 11", turn="continuation"
    )
    assert cmd.turn == "continuation"


async def test_memory_repo_returns_series_started_before_period() -> None:
    from datetime import datetime

    repo = InMemoryRepository()
    start = datetime.fromisoformat("2026-10-05T10:00:00+03:00")
    await repo.create(
        contracts.Item(
            user_id=1,
            type=contracts.ItemType.MEETING,
            title="Планёрка",
            starts_at=start,
            rrule="FREQ=WEEKLY;BYDAY=MO",
        )
    )
    week = datetime.fromisoformat("2026-10-26T00:00:00+03:00")
    from datetime import timedelta

    assert len(await repo.list_range(1, week, week + timedelta(days=7))) == 1

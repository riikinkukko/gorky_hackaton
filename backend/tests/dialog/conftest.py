from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.contracts import AssistantReply, DialogState  # noqa: E402
from app.dialog import build_orchestrator  # noqa: E402
from tests.dialog.fake_storage import InMemoryItemRepository, NaiveSearchEngine, load_csv  # noqa: E402
from tests.dialog.scripted_nlu import ScriptedNLU  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)  # «сейчас» из golden_eval (среда)
CSV = BACKEND.parent / "data" / "voice_assistant_events_1000.csv"
USER = 1


class Harness:
    """Один пользователь, одна сессия: say() прогоняет фразу через Orchestrator."""

    def __init__(self, nlu: ScriptedNLU, seed: bool = True) -> None:
        self.nlu = nlu
        self.repo = InMemoryItemRepository(load_csv(CSV, USER, NOW) if seed else [])
        self.orch = build_orchestrator(
            settings=None,
            nlu=nlu,
            dates=None,  # type: ignore[arg-type]
            repo=self.repo,
            search=NaiveSearchEngine(self.repo),
        )
        self.state = DialogState(session_id="s1", user_id=USER)
        self.now = NOW
        self.log: list[tuple[str, AssistantReply]] = []

    async def say(self, text: str) -> AssistantReply:
        reply = await self.orch.handle(text, self.state, self.now)
        self.log.append((text, reply))
        return reply

    def ids(self, reply: AssistantReply) -> list[int]:
        return [i.id for i in reply.items]


@pytest.fixture
def h() -> Harness:
    from tests.dialog.phrases import build_nlu

    return Harness(build_nlu())


@pytest.fixture
def empty() -> Harness:
    from tests.dialog.phrases import build_nlu

    return Harness(build_nlu(), seed=False)

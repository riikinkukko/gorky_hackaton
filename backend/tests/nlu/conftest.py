from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.contracts import Command, DialogState
from app.nlu import HybridNLU

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)  # среда, как в golden_eval


def state(**kw) -> DialogState:
    return DialogState(session_id="t", user_id=1, **kw)


@pytest.fixture(scope="session")
def nlu() -> HybridNLU:
    return HybridNLU(None)


def parse(nlu: HybridNLU, text: str, st: DialogState | None = None, now: datetime = NOW) -> Command:
    return asyncio.run(nlu.parse(text, st or state(), now))


def dt(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=MSK)

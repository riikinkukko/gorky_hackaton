"""Сквозные голосовые тесты (зона чата 7): транскрипт → /ws/voice → настоящие NLU и диалог.

Аудио не нужно: заглушка STT_PROVIDER=fake возвращает текст кадра «text:…» как транскрипт,
поэтому тест проходит тот же путь, что живая реплика после GigaAM. Хранилище — память
с CSV организаторов, «сейчас» — как в golden_eval (ср, 21.10.2026 14:00 МСК).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime.fromisoformat("2026-10-21T14:00:00+03:00")


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        freeze_now=NOW,
        nlu_impl="real",
        dialog_impl="real",
        storage_impl="memory",
        stt_provider="fake",
        tts_provider="fake",
        llm_provider="rules",
        seed_csv=str(ROOT / "data" / "voice_assistant_events_1000.csv"),
    )
    with TestClient(create_app(settings)) as c:
        yield c


class VoiceTurn:
    def __init__(self, transcript: str, reply: dict[str, Any]) -> None:
        self.transcript = transcript
        self.reply = reply
        self.action: str = reply["action"]
        self.debug: dict[str, Any] = reply.get("debug") or {}
        self.intent: str | None = self.debug.get("intent")
        self.slots: dict[str, Any] = self.debug.get("slots") or {}
        self.items: list[dict[str, Any]] = reply.get("items") or []

    @property
    def item(self) -> dict[str, Any]:
        assert self.items, f"нет записей в ответе: {self.reply['text']!r}"
        return self.items[0]

    def at(self) -> str | None:
        it = self.item
        v = it.get("starts_at") or it.get("due_at")
        return v[:16] if v else None


def say(client: TestClient, transcript: str, session: str) -> VoiceTurn:
    """Одна голосовая реплика: start → кадр «text:…» → end; затем откат изменений."""
    with client.websocket_connect(f"/ws/voice?session_id={session}&user_id=1") as ws:
        ws.send_json({"type": "start", "mime": "audio/webm;codecs=opus"})
        ws.send_bytes(b"text:" + transcript.encode())
        ws.send_json({"type": "end"})
        final, reply = None, None
        while reply is None:
            msg = ws.receive()
            if msg.get("text") is None:
                continue
            m = json.loads(msg["text"])
            if m["type"] == "transcript" and m.get("final"):
                final = m["text"]
            elif m["type"] == "reply":
                reply = m
    turn = VoiceTurn(final or "", reply)
    if turn.action in {"created", "updated", "completed", "deleted"}:
        client.post("/api/undo", json={"session_id": session}, headers={"X-User-Id": "1"})
    return turn

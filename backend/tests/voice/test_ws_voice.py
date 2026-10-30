"""Протокол /ws/voice поверх контейнера: наш STT (декодирование, partials), ошибки, audio_mime."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.contracts import AssistantReply, DialogState
from app.main import create_app
from app.voice.config import reload_settings
from app.voice.stt import ConstSTT
from app.voice.tts import CachedTTS, NullTTS, SilenceTTS

from .conftest import make_audio, needs_ffmpeg

pytestmark = needs_ffmpeg
NOW = datetime.fromisoformat("2026-10-21T14:00:00+03:00")


class EchoOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, DialogState, datetime]] = []

    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        self.calls.append((text, state.model_copy(deep=True), now))
        state.last_item_ids = [len(self.calls)]
        return AssistantReply(text=f"Понял: {text}", action="none", suggestions=["ок"])


class BoomOrchestrator:
    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        raise RuntimeError("boom")


def _client(orch: Any, *, text: str = "Что у меня сегодня?", tts: Any = None) -> TestClient:
    app = create_app(Settings(_env_file=None, app_env="test", freeze_now=NOW))  # type: ignore[call-arg]
    vs = reload_settings()
    c = app.state.container
    c.stt, c.tts, c.orchestrator = ConstSTT(vs, text=text), tts or CachedTTS(SilenceTTS(vs)), orch
    c.impl["stt"] = "const"
    return TestClient(app)


def _turn(ws: WebSocketTestSession, *, expect_audio: bool = True) -> tuple[list[dict], bytes]:
    msgs: list[dict] = []
    while True:
        m = ws.receive()
        if m.get("bytes") is not None:
            return msgs, m["bytes"]
        msgs.append(json.loads(m["text"]))
        if not expect_audio and msgs[-1]["type"] == "reply":
            return msgs, b""


def test_full_protocol_with_local_stt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_PARTIALS", "off")
    orch = EchoOrchestrator()
    audio = make_audio("webm", 2.0)
    with (
        _client(orch) as client,
        client.websocket_connect("/ws/voice?session_id=s1&user_id=7") as ws,
    ):
        for i in range(0, len(audio), 4000):
            ws.send_bytes(audio[i : i + 4000])
        ws.send_json({"type": "end"})
        msgs, ogg = _turn(ws)
    assert msgs[0] == {"type": "transcript", "text": "Что у меня сегодня?", "final": True}
    reply = msgs[1]
    assert reply["type"] == "reply" and reply["text"] == "Понял: Что у меня сегодня?"
    assert reply["audio_mime"] == "audio/ogg" and ogg[:4] == b"OggS"
    voice = reply["debug"]["voice"]
    assert voice["stt_provider"] == "const" and 1.9 < voice["audio_s"] < 2.2
    assert {"decode_ms", "stt_ms"} <= voice.keys()
    _text, state, now = orch.calls[0]
    assert (state.session_id, state.user_id) == ("s1", 7) and now == NOW


def test_lifespan_prewarms_tts_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_PREWARM", "1")
    vs = reload_settings()
    tts = CachedTTS(SilenceTTS(vs))
    with _client(EchoOrchestrator(), tts=tts):
        pass
    assert tts.misses >= 5  # частые фразы синтезированы на старте


def test_state_is_shared_between_turns_and_with_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_PARTIALS", "off")
    orch = EchoOrchestrator()
    audio = make_audio("webm", 1.0)
    with _client(orch) as client:
        with client.websocket_connect("/ws/voice?session_id=s2", headers={"X-User-Id": "3"}) as ws:
            for _ in range(2):
                ws.send_bytes(audio)
                ws.send_json({"type": "end"})
                _turn(ws)
        r = client.post(
            "/api/chat", json={"text": "дальше", "session_id": "s2"}, headers={"X-User-Id": "3"}
        )
        assert r.status_code == 200
    assert [call[1].last_item_ids for call in orch.calls] == [[], [1], [2]]
    assert {call[1].user_id for call in orch.calls} == {3}


def test_partials_then_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_PARTIALS", "on")
    monkeypatch.setenv("VOICE_PARTIAL_INTERVAL_S", "0")
    audio = make_audio("webm", 3.0)
    with (
        _client(EchoOrchestrator()) as client,
        client.websocket_connect("/ws/voice?session_id=s3") as ws,
    ):
        step = len(audio) // 6
        for i in range(0, len(audio), step):
            ws.send_bytes(audio[i : i + step])
            time.sleep(0.15)
        ws.send_json({"type": "end"})
        msgs, _ = _turn(ws)
    kinds = [(m["type"], m.get("final")) for m in msgs]
    assert ("transcript", False) in kinds
    assert kinds.index(("transcript", True)) > max(
        i for i, k in enumerate(kinds) if k == ("transcript", False)
    )
    assert kinds.count(("transcript", True)) == 1


def test_empty_end_sends_error_and_spoken_reply() -> None:
    orch = EchoOrchestrator()
    with _client(orch) as client, client.websocket_connect("/ws/voice?session_id=s4") as ws:
        ws.send_json({"type": "end"})
        msgs, audio = _turn(ws)
    assert [m["type"] for m in msgs] == ["transcript", "error", "reply"]
    assert msgs[1]["message"].startswith("Не расслышал") and msgs[2]["text"] == msgs[1]["message"]
    assert audio[:4] == b"OggS" and orch.calls == []


def test_garbage_audio_and_turn_failure_keep_socket_alive() -> None:
    with (
        _client(BoomOrchestrator()) as client,
        client.websocket_connect("/ws/voice?session_id=s5") as ws,
    ):
        ws.send_bytes(b"\x1a\x45\xdf\xa3 broken")
        ws.send_json({"type": "end"})
        msgs, _ = _turn(ws)
        assert [m["type"] for m in msgs] == ["error", "reply"]
        assert msgs[0]["message"].startswith("Не получилось распознать")
        ws.send_bytes(make_audio("webm", 1.0))
        ws.send_json({"type": "end"})
        msgs, _ = _turn(ws)
        assert [m["type"] for m in msgs] == ["transcript", "error", "reply"]
        assert msgs[2]["text"].startswith("Что-то пошло не так")


def test_safari_mp4_start_and_cancel() -> None:
    orch = EchoOrchestrator()
    with _client(orch) as client, client.websocket_connect("/ws/voice?session_id=s6") as ws:
        ws.send_bytes(b"junk that must be dropped")
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "start", "mime": "audio/mp4"})
        ws.send_bytes(make_audio("mp4", 1.5))
        ws.send_json({"type": "end"})
        msgs, _ = _turn(ws)
    assert msgs[1]["debug"]["voice"]["audio_s"] > 1.3


def test_browser_tts_sends_no_audio_frame() -> None:
    with _client(EchoOrchestrator(), tts=NullTTS()) as client:
        with client.websocket_connect("/ws/voice?session_id=s7") as ws:
            ws.send_bytes(make_audio("webm", 1.0))
            ws.send_json({"type": "end"})
            msgs, _ = _turn(ws, expect_audio=False)
            assert msgs[-1]["audio_mime"] is None
            ws.send_json(
                {"type": "end"}
            )  # следующий ход: если бы пришёл бинарный кадр, тут был бы он
            nxt = json.loads(ws.receive()["text"])
            assert nxt["type"] in {"transcript", "error"}


def test_health_reports_voice_providers() -> None:
    with _client(EchoOrchestrator()) as client:
        body = client.get("/health").json()
    assert body["stt"] == "ok" and body["tts"] == "ok"


@pytest.mark.parametrize("tts_provider", ["none", "browser"])
def test_light_profile_without_server_tts(tts_provider: str) -> None:
    """Лёгкий профиль: TTS_PROVIDER=none — контейнер собирает NullTTS, reply без аудио-кадра.

    Настоящие NLU и диалог на данных CSV, STT — заглушка каркаса (сырой текст с префиксом text:).
    """
    from pathlib import Path

    csv = Path(__file__).resolve().parents[3] / "data" / "voice_assistant_events_1000.csv"
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        freeze_now=NOW,
        seed_csv=str(csv),
        nlu_impl="real",
        dialog_impl="real",
        stt_provider="fake",
        tts_provider=tts_provider,
    )
    app = create_app(settings)
    assert isinstance(app.state.container.tts, NullTTS)
    with TestClient(app) as client:
        assert client.get("/health").json()["tts"] == "ok"
        with client.websocket_connect("/ws/voice?session_id=light&user_id=1") as ws:
            for phrase in ("Что у меня сегодня?", "Поставь встречу с Иваном завтра", "в три"):
                ws.send_bytes(f"text:{phrase}".encode())
                ws.send_json({"type": "end"})
                transcript = ws.receive_json()
                assert transcript == {"type": "transcript", "text": phrase, "final": True}
                reply = ws.receive_json()
                assert reply["type"] == "reply" and reply["audio_mime"] is None and reply["text"]
            # аудио-кадра нет: следующее сообщение — снова JSON нового хода
            ws.send_json({"type": "end"})
            assert ws.receive_json()["type"] in {"transcript", "error"}
        r = client.post("/api/tts", json={"text": "Привет"})
        assert r.status_code == 200 and r.content == b""
    assert reply["action"] == "created"  # «в три» закрыло уточнение времени

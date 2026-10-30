"""Безопасность голоса: SEC-01/10/11 (вход в /ws/voice), SEC-02 (декодер), SEC-03 (лимиты).

Тесты чата 8 в tests/security проверяют критерии «починено»; здесь — поведение подробнее,
включая переходный режим ALLOW_HEADER_AUTH и то, что легитимные сценарии не сломаны.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.session import COOKIE
from app.config import Settings
from app.contracts import AssistantReply, DialogState
from app.main import create_app
from app.voice.audio import AudioError, decode_to_pcm16k
from app.voice.config import reload_settings
from app.voice.stt import ConstSTT
from app.voice.tts import CachedTTS, SilenceTTS

from .conftest import make_audio, needs_ffmpeg

NOW = datetime.fromisoformat("2026-10-21T14:00:00+03:00")
SECRET = "s" * 48
# wss: cookie сессии с флагом Secure TestClient (как и браузер) по ws:// не отправит
WS = "wss://testserver/ws/voice"


class Echo:
    def __init__(self) -> None:
        self.users: list[int] = []

    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        self.users.append(state.user_id)
        return AssistantReply(text=f"Понял: {text}", action="none")


def _client(**kw: Any) -> tuple[TestClient, Echo]:
    settings = Settings(_env_file=None, freeze_now=NOW, **{"app_env": "test", **kw})  # type: ignore[call-arg]
    app = create_app(settings)
    echo = Echo()
    app.state.container.orchestrator = echo
    return TestClient(app, base_url="https://testserver"), echo


def _turn(
    ws: Any, payload: bytes = b"text:\xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"
) -> list[dict]:
    """Одна реплика; возвращает JSON-сообщения до reply включительно (аудио-кадр съедается)."""
    ws.send_bytes(payload)
    ws.send_json({"type": "end"})
    msgs: list[dict] = []
    while True:
        m = ws.receive()
        if m.get("text") is None:
            continue
        msgs.append(__import__("json").loads(m["text"]))
        if msgs[-1]["type"] == "reply":
            ws.receive()  # FakeTTS: бинарный кадр после reply
            return msgs


# ---------------------------------------------------------------- SEC-01: кто пользователь


def test_prod_cookie_identifies_user_and_ignores_query() -> None:
    client, echo = _client(app_env="prod", session_secret=SECRET)
    assert client.post("/api/session", json={"user_id": 3}).status_code == 200
    with client.websocket_connect(WS + "?session_id=a&user_id=1", headers={"X-User-Id": "1"}) as ws:
        _turn(ws)
    assert echo.users == [3]


def test_prod_without_cookie_rejected_with_4401() -> None:
    client, _ = _client(app_env="prod", session_secret=SECRET)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS + "?session_id=a&user_id=1") as ws:
            ws.receive()
    assert exc.value.code == 4401


def test_prod_transition_mode_accepts_query_user_for_demo_users_only() -> None:
    """Стенд: APP_ENV=prod + ALLOW_HEADER_AUTH=true, пока фронт не перешёл на cookie."""
    client, echo = _client(app_env="prod", session_secret=SECRET, allow_header_auth=True)
    with client.websocket_connect(WS + "?session_id=a&user_id=2") as ws:
        _turn(ws)
    assert echo.users == [2]
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS + "?session_id=a&user_id=999") as ws:
            ws.receive()
    assert exc.value.code == 4401


def test_forged_cookie_falls_back_to_rules_of_mode() -> None:
    client, _ = _client(app_env="prod", session_secret=SECRET)
    client.cookies.set(COOKIE, "eyJ1aWQiOjF9.AAAA")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS + "?session_id=a") as ws:
            ws.receive()


def test_outside_prod_query_user_still_works() -> None:
    client, echo = _client()
    with client.websocket_connect(WS + "?session_id=a&user_id=2") as ws:
        _turn(ws)
    with client.websocket_connect(WS + "?session_id=b", headers={"X-User-Id": "3"}) as ws:
        _turn(ws)
    assert echo.users == [2, 3]


# ---------------------------------------------------------------- SEC-10: Origin


@pytest.mark.parametrize(
    "origin,ok",
    [
        ("https://voice.example", True),  # из CORS_ORIGINS
        ("https://testserver", True),  # тот же хост
        ("http://testserver", True),  # схему не сравниваем: за nginx→Caddy она http
        ("https://evil.example", False),
        ("https://testserver.evil.example", False),
        ("null", False),
    ],
)
def test_origin_check(origin: str, ok: bool) -> None:
    client, _ = _client(cors_origins="https://voice.example")
    if ok:
        with client.websocket_connect(WS + "?session_id=o", headers={"Origin": origin}) as ws:
            assert _turn(ws)[-1]["type"] == "reply"
    else:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(WS + "?session_id=o", headers={"Origin": origin}):
                pass
        assert exc.value.code == 1008


# ---------------------------------------------------------------- SEC-11: session_id


@pytest.mark.parametrize("sid", ["x" * 101, "a b", "../x", "сессия", "a%00b"])
def test_bad_session_id_rejected(sid: str) -> None:
    client, _ = _client()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS + "", params={"session_id": sid}):
            pass
    assert exc.value.code == 1008


def test_good_session_ids_accepted() -> None:
    client, _ = _client()
    for sid in ("a", "x" * 100, "Ab-9_z", "f" * 32):
        with client.websocket_connect(WS + f"?session_id={sid}") as ws:
            assert _turn(ws)[-1]["type"] == "reply"


# ---------------------------------------------------------------- SEC-03: сокеты, частота, очередь


def test_sockets_per_session_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client()
    with (
        client.websocket_connect(WS + "?session_id=s") as a,
        client.websocket_connect(WS + "?session_id=s") as b,
    ):
        with client.websocket_connect(WS + "?session_id=s") as c:
            assert c.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as exc:
                c.receive_json()
            assert exc.value.code == 1013
        with client.websocket_connect(WS + "?session_id=other") as d:
            assert _turn(d)[-1]["type"] == "reply"  # другая сессия — можно
        assert _turn(a)[-1]["type"] == "reply" and _turn(b)[-1]["type"] == "reply"
    with client.websocket_connect(WS + "?session_id=s") as e:  # слоты освободились
        assert _turn(e)[-1]["type"] == "reply"


def test_total_sockets_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_MAX_SOCKETS", "2")
    reload_settings()
    client, _ = _client()
    with (
        client.websocket_connect(WS + "?session_id=a"),
        client.websocket_connect(WS + "?session_id=b"),
    ):
        with client.websocket_connect(WS + "?session_id=c") as ws:
            assert "подключений" in ws.receive_json()["message"]


def test_idle_socket_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_IDLE_TIMEOUT_S", "0.3")
    reload_settings()
    client, _ = _client()
    with client.websocket_connect(WS + "?session_id=idle") as ws:
        _turn(ws)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4408


def test_turns_per_minute_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_MAX_TURNS_PER_MIN", "3")
    reload_settings()
    client, echo = _client()
    with client.websocket_connect(WS + "?session_id=fast") as ws:
        for _ in range(3):
            assert _turn(ws)[0]["type"] == "transcript"
        msgs = _turn(ws)
        assert [m["type"] for m in msgs] == ["error", "reply"]
        assert msgs[0]["message"].startswith("Слишком часто")
    assert len(echo.users) == 3
    with client.websocket_connect(WS + "?session_id=other") as ws:  # лимит — на сессию
        assert _turn(ws)[0]["type"] == "transcript"


class SlowSTT(ConstSTT):
    async def _recognize(self, pcm: Any) -> str:
        await asyncio.sleep(0.6)
        return "медленно"


@needs_ffmpeg
def test_busy_when_queue_wait_exceeds_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("VOICE_QUEUE_TIMEOUT_S", "0.2")
    monkeypatch.setenv("VOICE_PARTIALS", "off")
    vs = reload_settings()
    client, _ = _client()
    client.app.state.container.stt = SlowSTT(vs)  # type: ignore[attr-defined]
    audio = make_audio("webm", 1.0)
    # with client — один event loop на все сокеты, как у uvicorn (иначе у каждого свой семафор)
    with (
        client,
        client.websocket_connect(WS + "?session_id=q1") as a,
        client.websocket_connect(WS + "?session_id=q2") as b,
    ):
        a.send_bytes(audio)
        a.send_json({"type": "end"})
        time.sleep(0.1)
        b.send_bytes(audio)
        b.send_json({"type": "end"})
        first_b = b.receive_json()
        assert first_b == {"type": "error", "message": "Сервер занят, повторите через пару секунд."}
        assert a.receive_json() == {"type": "transcript", "text": "медленно", "final": True}


@needs_ffmpeg
def test_api_tts_busy_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_MAX_TTS_CONCURRENCY", "1")
    monkeypatch.setenv("VOICE_QUEUE_TIMEOUT_S", "0.1")
    vs = reload_settings()

    class SlowTTS(SilenceTTS):
        async def _synthesize(self, text: str) -> bytes:
            await asyncio.sleep(0.5)
            return await super()._synthesize(text)

    client, _ = _client()
    client.app.state.container.tts = CachedTTS(SlowTTS(vs))  # type: ignore[attr-defined]
    from concurrent.futures import ThreadPoolExecutor

    with client, ThreadPoolExecutor(2) as pool:
        codes = sorted(
            pool.map(lambda t: client.post("/api/tts", json={"text": t}).status_code, ["а", "б"])
        )
    assert codes == [200, 503]


# ---------------------------------------------------------------- SEC-02: декодер


def _opus(seconds: float, bitrate: str = "6k") -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "libopus",
            "-b:a",
            bitrate,
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout


@needs_ffmpeg
def test_long_valid_webm_is_cut_at_input() -> None:
    """Настоящий webm на 5 мин: декодируется только начало, быстро."""
    data = _opus(300)
    t = time.perf_counter()
    pcm = asyncio.run(decode_to_pcm16k(data, "audio/webm;codecs=opus"))
    assert pcm.duration_s == pytest.approx(reload_settings().max_utterance_s, abs=0.05)
    assert time.perf_counter() - t < 1.5


@needs_ffmpeg
@pytest.mark.parametrize("mime", ["audio/flac", "application/octet-stream", "", "video/webm"])
def test_unknown_mime_rejected(mime: str) -> None:
    with pytest.raises(AudioError):
        asyncio.run(decode_to_pcm16k(make_audio("webm", 1.0), mime))


@needs_ffmpeg
def test_wav_fast_path_also_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_MAX_UTTERANCE_S", "2")
    reload_settings()
    pcm = asyncio.run(decode_to_pcm16k(make_audio("wav", 6.0), "audio/wav"))
    assert pcm.duration_s == pytest.approx(2.0)


@needs_ffmpeg
def test_ws_rejects_flac_declared_as_webm_without_running_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_PARTIALS", "off")
    vs = reload_settings()
    client, echo = _client()
    client.app.state.container.stt = ConstSTT(vs, text="не должно")  # type: ignore[attr-defined]
    flac = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            "600",
            "-c:a",
            "flac",
            "-f",
            "flac",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    with client.websocket_connect(WS + "?session_id=bomb") as ws:
        ws.send_bytes(flac)
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "error"
    assert echo.users == []


@needs_ffmpeg
def test_ffmpeg_stdout_is_bounded_even_without_input_limit() -> None:
    """Вторая линия защиты: если -t не сработал, читаем не больше max_stdout и убиваем ffmpeg."""
    from app.voice.audio import _run_ffmpeg

    args = [
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=16000:cl=mono",
        "-t",
        "36000",
        "-f",
        "s16le",
        "-ac",
        "1",
        "pipe:1",
    ]  # 10 часов тишины без ограничения на входе
    t = time.perf_counter()
    out = asyncio.run(_run_ffmpeg(args, max_stdout=640_000))
    assert len(out) == 640_000
    assert time.perf_counter() - t < 2.0

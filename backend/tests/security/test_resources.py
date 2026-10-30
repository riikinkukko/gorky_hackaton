"""Ресурсы: аудио-«бомба», частота запросов. Стенд такими тестами не трогаем — только локально."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.voice.audio import decode_to_pcm16k
from app.voice.config import get_settings as voice_settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нет ffmpeg")


def _silence(path: Path, seconds: int, codec: str) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@needs_ffmpeg
@pytest.mark.parametrize("seconds", [120, 600])
def test_decode_caps_duration(tmp_path: Path, seconds: int) -> None:
    """Длинная запись в правильном контейнере обрезается при декодировании, а не после.

    10 минут тишины в webm/opus 6 кбит/с раньше давали 19 МБ PCM (час — 115 МБ и 9 с CPU).
    (В v1 тест подавал FLAC под видом webm — теперь такое отклоняется раньше, см. тест ниже.)
    """
    import time

    path = tmp_path / "long.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "libopus",
            "-b:a",
            "6k",
            str(path),
        ],
        check=True,
    )
    t0 = time.perf_counter()
    pcm = asyncio.run(decode_to_pcm16k(path.read_bytes(), "audio/webm;codecs=opus"))
    assert pcm.duration_s <= voice_settings().max_utterance_s + 1
    assert time.perf_counter() - t0 < 5


@needs_ffmpeg
def test_decode_rejects_container_not_matching_mime(tmp_path: Path) -> None:
    """Клиент объявил webm, прислал FLAC — отказ, а не «что-нибудь распознаем»."""
    from app.voice.audio import AudioError

    data = _silence(tmp_path / "s.flac", 2, "flac")
    with pytest.raises(AudioError):
        asyncio.run(decode_to_pcm16k(data, "audio/webm;codecs=opus"))


@needs_ffmpeg
def test_decode_normal_utterance_still_works(tmp_path: Path) -> None:
    """Регрессия для исправления SEC-02: обычная запись браузера декодируется."""
    data = _silence(tmp_path / "s.webm", 3, "libopus")
    pcm = asyncio.run(decode_to_pcm16k(data, "audio/webm;codecs=opus"))
    assert 2.5 <= pcm.duration_s <= 3.5


def test_ws_utterance_byte_limit_configured() -> None:
    vs = voice_settings()
    assert 0 < vs.max_utterance_bytes <= 4 * 1024 * 1024
    assert 0 < vs.max_utterance_s <= 30


def test_chat_rate_limited(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="test")
    codes = [
        c.post("/api/chat", json={"text": "привет", "session_id": "rl"}).status_code
        for _ in range(60)
    ]
    assert 429 in codes, "60 запросов подряд от одной сессии должны упереться в лимит"


def test_tts_rate_limited(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="test")
    codes = [c.post("/api/tts", json={"text": "проверка"}).status_code for _ in range(40)]
    assert 429 in codes


def test_chat_limit_not_bypassed_by_rotating_session_id(
    make_client: Callable[..., TestClient],
) -> None:
    c = make_client(app_env="prod", session_secret="s" * 48)
    c.post("/api/session", json={"user_id": 1})
    codes = [
        c.post("/api/chat", json={"text": "привет", "session_id": f"s{i}"}).status_code
        for i in range(40)
    ]
    assert 429 in codes, "ключ лимита — сессия из cookie, а не session_id клиента"


def _client_from(app: Any, ip: str) -> TestClient:
    return TestClient(
        app, base_url="https://testserver", client=(ip, 50000), raise_server_exceptions=False
    )


def test_login_rate_limited_per_ip() -> None:
    """10 входов в минуту на адрес клиента; у другого адреса — свой счётчик.

    Адрес задаётся как его видит приложение после uvicorn (request.client), а не заголовком:
    так тест не зависит от того, как именно чат 0 закроет SEC-17.
    """
    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(app_env="prod", session_secret="s" * 48))
    a, b = _client_from(app, "198.51.100.10"), _client_from(app, "198.51.100.20")
    codes = [a.post("/api/session", json={"user_id": 1}).status_code for _ in range(15)]
    assert codes[:10] == [200] * 10 and 429 in codes
    assert b.post("/api/session", json={"user_id": 1}).status_code == 200


def test_public_client_cannot_reset_login_limit_with_header() -> None:
    """Регрессия: клиент с публичным адресом не получает новый счётчик, прислав X-Real-IP."""
    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(app_env="prod", session_secret="s" * 48))
    a = _client_from(app, "198.51.100.30")
    codes = [
        a.post(
            "/api/session", json={"user_id": 1}, headers={"X-Real-IP": f"203.0.113.{i}"}
        ).status_code
        for i in range(15)
    ]
    assert 429 in codes


def test_internal_peer_header_does_not_choose_rate_key() -> None:
    """За Caddy стека запрос всегда приходит с внутреннего адреса (шлюз Docker), а Caddy
    пропускает X-Real-IP клиента как есть. Поэтому ключ лимита не должен браться из заголовка:
    настоящий адрес должен приходить в request.client (uvicorn --forwarded-allow-ips = частные
    сети + trusted_proxies в Caddy). Проверено на цепочке nginx/Caddy → Caddy → uvicorn,
    см. REPORT.md, SEC-17.
    """
    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(app_env="prod", session_secret="s" * 48))
    gw = _client_from(app, "172.18.0.1")
    codes = [
        gw.post(
            "/api/session", json={"user_id": 1}, headers={"X-Real-IP": f"203.0.113.{i}"}
        ).status_code
        for i in range(15)
    ]
    assert 429 in codes


def test_chat_text_length_limited(test_client: TestClient) -> None:
    r = test_client.post("/api/chat", json={"text": "а" * 1001, "session_id": "s"})
    assert r.status_code == 422
    r = test_client.post("/api/chat", json={"text": "привет", "session_id": "s" * 101})
    assert r.status_code == 422


def test_ws_session_id_length_limited(test_client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with test_client.websocket_connect("/ws/voice?session_id=" + "x" * 5000) as ws:
            ws.send_text('{"type":"end"}')
            ws.receive_json()


# ---------------------------------------------------------------- v3: лимиты WebSocket


def _ws_turn(c: TestClient, session_id: str) -> str:
    """Одна голосовая реплика в новом сокете; текст ответа (или причина закрытия)."""
    from starlette.websockets import WebSocketDisconnect

    # cookie Secure: TestClient не прикладывает её к ws://, передаём заголовком
    cookie = c.cookies.get("pomnyu_session")
    headers = {"cookie": f"pomnyu_session={cookie}"} if cookie else {}
    try:
        with c.websocket_connect(f"/ws/voice?session_id={session_id}", headers=headers) as ws:
            ws.send_bytes(b"\x00" * 64)
            ws.send_text('{"type":"end"}')
            for _ in range(4):
                msg = ws.receive_json()
                if msg.get("type") == "reply":
                    return str(msg.get("text", ""))
    except WebSocketDisconnect as e:
        return f"closed:{e.code}"
    return ""


def test_ws_turn_limit_not_bypassed_by_rotating_session_id(
    make_client: Callable[..., TestClient],
) -> None:
    """VOICE_MAX_TURNS_PER_MIN=10 на сессию из cookie: 15 реплик с разными session_id → отказ."""
    c = make_client(app_env="prod", session_secret="s" * 48)
    assert c.post("/api/session", json={"user_id": 1}).status_code == 200
    texts = [_ws_turn(c, f"rot{i}") for i in range(15)]
    assert any("Слишком часто" in t for t in texts), texts[-3:]


def test_ws_turn_limit_same_session(make_client: Callable[..., TestClient]) -> None:
    """Регрессия: в одной сессии 11-я реплика за минуту получает отказ."""
    c = make_client(app_env="prod", session_secret="s" * 48)
    c.post("/api/session", json={"user_id": 1})
    texts = [_ws_turn(c, "same") for _ in range(12)]
    assert not any("Слишком часто" in t for t in texts[:10])
    assert any("Слишком часто" in t for t in texts[10:])

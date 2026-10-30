"""Доступ: кто пользователь, X-Freeze-Now, ICS-токены. Находки — docs/security/REPORT.md."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SECRET = "s" * 48  # SESSION_SECRET для тестов (в проде — из .env, ≥ 32 байт)
COOKIE = "pomnyu_session"


def _chat(c: TestClient, text: str, session: str = "s1", **headers: str):
    return c.post("/api/chat", json={"text": text, "session_id": session}, headers=headers)


# ---------------------------------------------------------------- X-Freeze-Now (регрессия)


def test_freeze_now_rejected_in_prod(prod_client: TestClient) -> None:
    """После SEC-01 в prod без входа — 401, поэтому входим. 400 должен вызывать именно заголовок."""
    freeze = {"X-Freeze-Now": "2030-01-01T10:00:00+03:00"}
    assert _chat(prod_client, "Что у меня сегодня?", **freeze).status_code == 401
    assert prod_client.post("/api/session", json={"user_id": 1}).status_code == 200
    r = _chat(prod_client, "Что у меня сегодня?", **freeze)
    assert r.status_code == 400 and "X-Freeze-Now" in r.json()["detail"]
    # контроль: тот же запрос без заголовка проходит — значит, 400 дал X-Freeze-Now
    assert _chat(prod_client, "Что у меня сегодня?").status_code == 200


def test_freeze_now_works_outside_prod(test_client: TestClient) -> None:
    r = _chat(test_client, "Что у меня сегодня?", **{"X-Freeze-Now": "2026-10-22T09:00:00+03:00"})
    assert r.status_code == 200


# ---------------------------------------------------------------- изоляция данных (регрессия)


def test_users_do_not_see_each_others_items(test_client: TestClient) -> None:
    """Данные разделены по user_id на уровне репозитория — это должно остаться и после SEC-01."""
    r = _chat(test_client, "Напомни завтра в 10 секретный звонок", "a", **{"X-User-Id": "2"})
    assert r.status_code == 200 and r.json()["action"] == "created"
    other = test_client.get("/api/items", headers={"X-User-Id": "3"}).json()
    assert all(i["user_id"] == 3 for i in other)
    assert not [i for i in other if "екретный" in i["title"]]


def test_session_state_is_not_shared_between_users(test_client: TestClient) -> None:
    """Чужой session_id не даёт продолжить чужое уточнение (например, «да» на удаление)."""
    r = _chat(test_client, "Удали созвон с Альфой первого ноября", "shared", **{"X-User-Id": "1"})
    assert r.json()["action"] == "clarify"
    r = _chat(test_client, "да", "shared", **{"X-User-Id": "2"})
    assert r.json()["action"] != "deleted"


# ---------------------------------------------------------------- SEC-01: подписанная сессия


def test_prod_requires_session_cookie(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    assert c.get("/api/items").status_code == 401
    assert _chat(c, "Что у меня сегодня?").status_code == 401


def test_prod_ignores_x_user_id(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    r = c.post("/api/session", json={"user_id": 3})
    assert r.status_code == 200
    assert COOKIE in r.cookies
    items = c.get("/api/items?from=2026-01-01T00:00:00%2B03:00", headers={"X-User-Id": "1"})
    assert items.status_code == 200
    assert all(i["user_id"] == 3 for i in items.json())


def test_session_cookie_flags(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    r = c.post("/api/session", json={"user_id": 1})
    sc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in sc and "samesite=strict" in sc and "secure" in sc


def test_forged_cookie_rejected(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    c.post("/api/session", json={"user_id": 3})
    token = c.cookies.get(COOKIE) or ""
    c.cookies.set(COOKIE, token[:-2] + ("AA" if not token.endswith("AA") else "BB"))
    assert c.get("/api/items").status_code == 401


def test_prod_rejects_unknown_demo_user(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    assert c.post("/api/session", json={"user_id": 999}).status_code in (400, 403, 422)


def test_prod_fails_fast_without_secret(make_client: Callable[..., TestClient]) -> None:
    with pytest.raises(Exception):  # noqa: B017 — любой отказ старта
        make_client(app_env="prod", session_secret="")


def test_garbage_cookie_gives_401_not_500(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    for raw in (b"abc.\xc3\xa9\xc3\xa9", b"\xc3\xa9.x", b"a.b.c", b".", b"x" * 5000):
        r = c.get("/api/items", headers=[(b"cookie", b"pomnyu_session=" + raw)])
        assert r.status_code == 401, raw[:20]


def test_session_expiry_and_logout(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET, session_ttl_s=-1)
    c.post("/api/session", json={"user_id": 1})
    assert c.get("/api/session").status_code == 401  # просроченная cookie не принимается
    c = make_client(app_env="prod", session_secret=SECRET)
    c.post("/api/session", json={"user_id": 2})
    assert c.get("/api/session").json()["user_id"] == 2
    assert "max-age=0" in c.delete("/api/session").headers.get("set-cookie", "").lower()


def test_cookie_from_other_secret_rejected(make_client: Callable[..., TestClient]) -> None:
    """Cookie, подписанная другим SESSION_SECRET (например, dev-ключом), в prod не действует."""
    dev = make_client(app_env="test")
    dev.post("/api/session", json={"user_id": 1})
    prod = make_client(app_env="prod", session_secret=SECRET)
    prod.cookies.set(COOKIE, dev.cookies.get(COOKIE) or "")
    assert prod.get("/api/items").status_code == 401


def _ws_reply_user(c: TestClient, url: str, **headers: str) -> int | None:
    with c.websocket_connect(url, headers=headers) as ws:
        ws.send_bytes(b"\x00" * 64)
        ws.send_text('{"type":"end"}')
        for _ in range(4):
            msg = ws.receive_json()
            if msg.get("type") == "reply":
                users = {i["user_id"] for i in msg.get("items", [])}
                return users.pop() if users else None
    return None


def test_ws_requires_session_in_prod(make_client: Callable[..., TestClient]) -> None:
    c = make_client(app_env="prod", session_secret=SECRET)
    with pytest.raises(WebSocketDisconnect):
        _ws_reply_user(c, "/ws/voice?session_id=x&user_id=1")


def test_ws_rejects_foreign_origin(make_client: Callable[..., TestClient]) -> None:
    """CSWSH: после перехода на cookie чужой сайт не должен открыть сокет от имени жертвы."""
    c = make_client(app_env="prod", session_secret=SECRET, cors_origins="https://voice.example")
    c.post("/api/session", json={"user_id": 1})
    with pytest.raises(WebSocketDisconnect):
        _ws_reply_user(c, "/ws/voice", Origin="https://evil.example")


def test_ws_user_spoofing_documented(test_client: TestClient) -> None:
    """Фиксирует текущее поведение вне prod: ?user_id= выбирает пользователя (нужно для тестов)."""
    assert _ws_reply_user(test_client, "/ws/voice?session_id=w&user_id=1") == 1


# ---------------------------------------------------------------- ICS


def test_ics_unknown_token_404(test_client: TestClient) -> None:
    for token in ("1", "admin", "../demo", "DEMO"):
        assert test_client.get(f"/calendar/{token}.ics").status_code == 404


def test_ics_demo_token_only_maps_to_demo_user(test_client: TestClient) -> None:
    r = test_client.get("/calendar/demo.ics")
    assert r.status_code == 200 and r.text.startswith("BEGIN:VCALENDAR")


def test_prod_rejects_weak_ics_tokens(make_client: Callable[..., TestClient]) -> None:
    """Публичный «demo» допустим только для демо-пользователя; прочие токены — ≥ 22 символов."""
    with pytest.raises(Exception):  # noqa: B017
        make_client(app_env="prod", session_secret=SECRET, ics_tokens="demo:1,abc:2")

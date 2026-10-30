"""Сессия демо-входа (SEC-01): подписанная cookie вместо X-User-Id от клиента.

token = base64url(json{"uid","sid","exp"}) + "." + base64url(HMAC-SHA256(secret, payload)).
Без внешних зависимостей; проверку verify_cookie() использует и WebSocket (чат 4).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.config import Settings

COOKIE = "pomnyu_session"
router = APIRouter(prefix="/api", tags=["session"])


class SessionLogin(BaseModel):
    user_id: int


class SessionInfo(BaseModel):
    user_id: int
    session_id: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret(settings: Settings) -> bytes:
    # вне prod без секрета — фиксированный dev-ключ: тестам и локальной разработке он не мешает
    return (settings.session_secret or "dev-only-insecure-session-secret").encode()


def sign(settings: Settings, uid: int, sid: str | None = None) -> tuple[str, str]:
    sid = sid or uuid.uuid4().hex
    payload = _b64(
        json.dumps(
            {"uid": uid, "sid": sid, "exp": int(time.time()) + settings.session_ttl_s}
        ).encode()
    )
    mac = _b64(hmac.new(_secret(settings), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{mac}", sid


def verify_cookie(settings: Settings, raw: str | None) -> tuple[int, str] | None:
    """(user_id, session_id) из подписанной cookie; None — подпись, срок или пользователь не те."""
    if not raw or raw.count(".") != 1:
        return None
    if len(raw) > 4096:
        return None
    payload, mac = raw.split(".")
    try:
        want = _b64(hmac.new(_secret(settings), payload.encode(), hashlib.sha256).digest())
        # байты, а не str: compare_digest на не-ASCII строках бросает TypeError → был 500 (SEC-14)
        if not hmac.compare_digest(mac.encode("utf-8", "surrogateescape"), want.encode()):
            return None
    except (UnicodeError, TypeError, ValueError):
        return None
    try:
        data = json.loads(_unb64(payload))
        uid, sid, exp = int(data["uid"]), str(data["sid"]), int(data["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if exp < time.time() or uid not in settings.demo_users():
        return None
    return uid, sid


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.container.settings
    return settings


def client_ip(request: Request) -> str:
    """IP клиента для лимитов (SEC-17).

    Настоящий адрес приносит uvicorn из X-Forwarded-For, доверяя только частным сетям
    (--forwarded-allow-ips в Dockerfile); Caddy доверяет прокси только из private_ranges.
    Присланные клиентом X-Real-IP / X-Forwarded-For не читаем.
    """
    return request.client.host if request.client else "unknown"


@router.post("/session", response_model=SessionInfo)
async def login(body: SessionLogin, request: Request, response: Response) -> SessionInfo:
    settings = _settings(request)
    request.app.state.container.limiter.check(
        f"login:{client_ip(request)}", settings.rate_login_per_min, 60
    )
    if body.user_id not in settings.demo_users():
        raise HTTPException(status_code=403, detail="Неизвестный демо-пользователь")
    token, sid = sign(settings, body.user_id)
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_ttl_s,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return SessionInfo(user_id=body.user_id, session_id=sid)


@router.get("/session", response_model=SessionInfo)
async def current(request: Request) -> SessionInfo:
    found = verify_cookie(_settings(request), request.cookies.get(COOKIE))
    if found is None:
        raise HTTPException(status_code=401, detail="Нужен вход")
    return SessionInfo(user_id=found[0], session_id=found[1])


@router.delete("/session", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.status_code = 204
    return response

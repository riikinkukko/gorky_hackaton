"""REST: /api/chat, /api/undo, /api/tts, /api/items."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.deps import ContainerDep, CookieSessionDep, UserIdDep
from app.contracts import AssistantReply, Item, ItemType

router = APIRouter(prefix="/api", tags=["chat"])
log = logging.getLogger("turn")


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=1, max_length=100)


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


async def run_turn(
    c: ContainerDep, user_id: int, session_id: str, text: str, now: datetime | None = None
) -> AssistantReply:
    state = c.sessions.get(session_id, user_id)
    reply = await c.orchestrator.handle(text, state, now or c.settings.now())
    c.sessions.save(state)
    d = reply.debug or {}
    # Одна строка на реплику: что пришло (для голоса — что распознал STT) и как понято.
    # Разбор «не понял»: docker compose logs api | grep turn
    log.info(
        "turn session=%s user=%s text=%r → intent=%s source=%s action=%s reply=%r",
        session_id[:8],
        user_id,
        text,
        d.get("intent"),
        d.get("source"),
        reply.action,
        reply.text[:80],
    )
    return reply


def _limit_chat(c: ContainerDep, user_id: int, session_id: str, cookie_sid: str | None) -> None:
    s = c.settings
    # с cookie — ключ из неё (смена session_id в теле не обходит лимит);
    # без cookie (вне prod) — session_id из тела
    c.limiter.check(f"chat:{user_id}:{cookie_sid or session_id}", s.rate_chat_per_min, 60)
    c.limiter.check("chat:service", s.rate_service_per_hour, 3600)


@router.post("/chat", response_model=AssistantReply)
async def chat(
    body: ChatRequest,
    c: ContainerDep,
    user_id: UserIdDep,
    cookie_sid: CookieSessionDep,
    x_freeze_now: Annotated[datetime | None, Header()] = None,
) -> AssistantReply:
    """X-Freeze-Now: подменить «сейчас» на один запрос (для eval по HTTP). В проде запрещён."""
    _limit_chat(c, user_id, body.session_id, cookie_sid)
    if x_freeze_now is not None:
        if c.settings.app_env == "prod":
            raise HTTPException(status_code=400, detail="X-Freeze-Now недоступен в prod")
        if x_freeze_now.tzinfo is None:
            x_freeze_now = x_freeze_now.replace(tzinfo=c.settings.tz)
    return await run_turn(c, user_id, body.session_id, body.text, x_freeze_now)


@router.post("/undo", response_model=AssistantReply)
async def undo(
    body: SessionRequest, c: ContainerDep, user_id: UserIdDep, cookie_sid: CookieSessionDep
) -> AssistantReply:
    _limit_chat(c, user_id, body.session_id, cookie_sid)
    # Кнопка «Отменить» = голосовое «отмени». Незавершённое уточнение сбрасываем,
    # иначе NLU поймёт «отмени» как выход из сценария (REJECT), а не undo.
    state = c.sessions.get(body.session_id, user_id)
    state.pending, state.awaiting = None, None
    c.sessions.save(state)
    return await run_turn(c, user_id, body.session_id, "отмени")


@router.post("/tts")
async def tts(body: TTSRequest, c: ContainerDep, user_id: UserIdDep) -> Response:
    c.limiter.check(f"tts:{user_id}", c.settings.rate_tts_per_min, 60)
    audio, mime = await c.tts.synthesize(body.text)
    return Response(content=audio, media_type=mime)


@router.get("/items", response_model=list[Item])
async def items(
    c: ContainerDep,
    user_id: UserIdDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    type: ItemType | None = None,
) -> list[Item]:
    tz = c.settings.tz
    now = c.settings.now()
    start = from_ or now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start if start.tzinfo else start.replace(tzinfo=tz)
    end = to or start + timedelta(days=7)
    end = end if end.tzinfo else end.replace(tzinfo=tz)
    return await c.repo.list_range(user_id, start, end, type)

"""/health и /calendar/{token}.ics."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from dateutil.rrule import rrulestr
from fastapi import APIRouter, HTTPException, Response

from app.api.deps import ContainerDep
from app.contracts import Item
from app.voice import provider_status

router = APIRouter(tags=["system"])


async def _db_status(database_url: str | None, storage_impl: str) -> str:
    if storage_impl == "memory" or not database_url:
        return "ok"
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(database_url, pool_pre_ping=True)
        try:
            async with asyncio.timeout(2):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return "ok"
    except Exception:
        return "down"


@router.get("/health")
async def health(c: ContainerDep) -> dict[str, object]:
    s = c.settings
    return {
        "db": await _db_status(s.database_url, s.storage_impl),
        "llm": "ok",
        "stt": await provider_status(c.stt),  # чат 4: ok | down (модель не загрузилась, нет ключа)
        "tts": await provider_status(c.tts),
        "impl": c.impl,  # расширение: какие реализации подключены
        "now": s.now().isoformat(),
        "seed_items": await _seed_count(c),  # 0 — CSV не загрузился (демо-данные пусты)
    }


async def _seed_count(c: ContainerDep) -> int | None:
    """Число записей демо-пользователя — для смоука и ежедневной проверки."""
    try:
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2030, 1, 1, tzinfo=UTC)
        return len(await c.repo.list_range(c.settings.seed_user_id, start, end))
    except Exception:
        return None


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # управляющие, кроме \t и \n (SEC-08)
_RRULE_OK = re.compile(r"[A-Z0-9=;,:+-]+")


def _ics_escape(s: str) -> str:
    s = _CTRL.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _safe_rrule(rrule: str | None) -> str | None:
    """RRULE пишем, только если это правило, а не попытка вставить свои свойства (SEC-08)."""
    if not rrule or not _RRULE_OK.fullmatch(rrule):
        return None
    try:
        rrulestr(rrule, dtstart=datetime(2026, 1, 1, tzinfo=UTC))
    except (ValueError, TypeError):
        return None
    return rrule


def _fold(line: str) -> str:
    """RFC 5545 §3.1: строки длиннее 75 октетов переносятся с пробелом в начале продолжения."""
    out: list[str] = []
    cur = b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def _ics_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _vevent(item: Item) -> list[str]:
    start = item.starts_at or item.due_at
    assert start is not None
    end = item.ends_at or start + timedelta(minutes=15)
    lines = [
        "BEGIN:VEVENT",
        f"UID:item-{item.id}@voice-assistant",
        f"DTSTAMP:{_ics_dt(item.updated_at)}",
        f"DTSTART:{_ics_dt(start)}",
        f"DTEND:{_ics_dt(end)}",
        f"SUMMARY:{_ics_escape(item.title)}",
        f"CATEGORIES:{item.type.value}",
    ]
    if rule := _safe_rrule(item.rrule):
        lines.append(f"RRULE:{rule}")
    lines.append("END:VEVENT")
    return [_fold(x) for x in lines]


@router.get("/calendar/{token}.ics")
async def calendar(token: str, c: ContainerDep) -> Response:
    user_id = c.settings.ics_token_map().get(token)
    if user_id is None:
        raise HTTPException(status_code=404, detail="unknown calendar token")
    now = c.settings.now()
    items = await c.repo.list_range(user_id, now - timedelta(days=30), now + timedelta(days=180))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//gorky-code//voice-assistant//RU",
        "X-WR-CALNAME:Голосовой ассистент",
    ]
    for item in items:
        lines += _vevent(item)
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar; charset=utf-8")

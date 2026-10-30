"""Ограничения ресурсов голосового контура (SEC-03).

* ``stt_slot()`` / ``tts_slot()`` — глобальные семафоры на декодирование+распознавание и на
  синтез (по умолчанию по 2). Кто ждёт дольше ``VOICE_QUEUE_TIMEOUT_S`` (5 с), получает
  ``VoiceBusy`` — «Сервер занят, повторите». Uvicorn работает одним процессом, поэтому
  семафоров в памяти достаточно.
* ``SocketRegistry`` — открытые WebSocket: не больше ``VOICE_MAX_SOCKETS`` всего и
  ``VOICE_MAX_SOCKETS_PER_SESSION`` на одну сессию.

Семафоры заводятся на каждый event loop отдельно: в тестах у каждого TestClient свой loop,
а asyncio.Semaphore привязывается к первому, на котором его ждали.
"""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import HTTPException

from .config import get_settings

MSG_BUSY = "Сервер занят, повторите через пару секунд."


class VoiceBusy(HTTPException):
    """Очередь к модели переполнена. Для HTTP — сразу 503 с Retry-After."""

    def __init__(self) -> None:
        super().__init__(status_code=503, detail=MSG_BUSY, headers={"Retry-After": "3"})


_sems: dict[str, weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {
    "stt": weakref.WeakKeyDictionary(),
    "tts": weakref.WeakKeyDictionary(),
}


def _semaphore(kind: str) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    per_loop = _sems[kind]
    sem = per_loop.get(loop)
    if sem is None:
        vs = get_settings()
        size = vs.max_stt_concurrency if kind == "stt" else vs.max_tts_concurrency
        sem = per_loop[loop] = asyncio.Semaphore(max(1, size))
    return sem


@contextlib.asynccontextmanager
async def _slot(kind: str, wait: bool = True) -> AsyncIterator[None]:
    sem = _semaphore(kind)
    if not wait and sem.locked():
        raise VoiceBusy()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=get_settings().queue_timeout_s)
    except TimeoutError as exc:
        raise VoiceBusy() from exc
    try:
        yield
    finally:
        sem.release()


def stt_slot(wait: bool = True) -> contextlib.AbstractAsyncContextManager[None]:
    """``wait=False`` — для промежуточных транскриптов: нет свободного слота — сразу VoiceBusy,
    чтобы они не отнимали очередь у финального распознавания."""
    return _slot("stt", wait)


def tts_slot() -> contextlib.AbstractAsyncContextManager[None]:
    return _slot("tts")


def reset() -> None:
    """Для тестов: размеры семафоров берутся заново из настроек."""
    for per_loop in _sems.values():
        per_loop.clear()


@dataclass
class SocketRegistry:
    """Сколько WebSocket открыто — всего и по (пользователь, сессия)."""

    total: int = 0
    by_session: dict[tuple[int, str], int] = field(default_factory=dict)

    def try_open(self, key: tuple[int, str], max_total: int, max_per_session: int) -> str | None:
        """None — можно; иначе причина отказа."""
        if self.total >= max_total:
            return "Слишком много голосовых подключений, попробуйте позже"
        if self.by_session.get(key, 0) >= max_per_session:
            return "Голос уже открыт в другой вкладке"
        self.total += 1
        self.by_session[key] = self.by_session.get(key, 0) + 1
        return None

    def close(self, key: tuple[int, str]) -> None:
        self.total = max(0, self.total - 1)
        left = self.by_session.get(key, 0) - 1
        if left > 0:
            self.by_session[key] = left
        else:
            self.by_session.pop(key, None)

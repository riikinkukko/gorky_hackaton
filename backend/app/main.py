from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, session, system, ws_voice
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.voice import startup as voice_startup

log = logging.getLogger(__name__)


async def _ensure_demo_users(c: Container) -> None:
    """Пользователи демо-логина (1–3) должны существовать в БД: иначе create падает на FK."""
    ensure = getattr(c.repo, "ensure_user", None)
    if ensure is None:
        return
    for uid in (int(x) for x in c.settings.demo_user_ids.split(",") if x.strip()):
        try:
            await ensure(uid)
        except Exception:  # демо не должно падать на старте из-за этого
            log.exception("не удалось создать демо-пользователя %s", uid)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    container = build_container(settings)

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        c = app_.state.container  # чат 4: загрузка моделей STT/TTS и прогрев кэша озвучки
        await voice_startup(c.stt, c.tts)
        await _ensure_demo_users(c)
        yield

    app = FastAPI(title="Голосовой ассистент · Gorky Code", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(session.router)
    app.include_router(ws_voice.router)
    app.include_router(system.router)
    return app


app = create_app()

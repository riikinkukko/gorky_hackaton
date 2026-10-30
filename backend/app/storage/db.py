"""Async-движок и фабрика сессий."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.storage.config import DATABASE_URL


@lru_cache(maxsize=4)
def get_engine(url: str = DATABASE_URL) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def get_sessionmaker(url: str = DATABASE_URL) -> async_sessionmaker:
    return async_sessionmaker(get_engine(url), expire_on_commit=False)

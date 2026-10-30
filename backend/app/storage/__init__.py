"""Слой хранения и поиска (чат 3). Фабрики для app/container.py (STORAGE_IMPL=postgres).

    repo = build_repository(settings)
    search = build_search(settings, repo)

Из settings читаются (если есть) `database_url` и `embeddings_model`; иначе — переменные
окружения DATABASE_URL и EMBEDDINGS_MODEL (см. app/storage/config.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.contracts import ItemRepository, SearchEngine


def _setting(settings: Any, name: str, default: str) -> str:
    for key in (name, name.upper()):
        v = getattr(settings, key, None)
        if v:
            # pydantic SecretStr / PostgresDsn → str
            return str(v.get_secret_value() if hasattr(v, "get_secret_value") else v)
    return default


def build_repository(settings: Any = None) -> ItemRepository:
    from app.storage.config import DATABASE_URL, EMBEDDINGS_MODEL
    from app.storage.db import get_sessionmaker
    from app.storage.embeddings import get_embedder
    from app.storage.repository import SqlItemRepository

    url = _setting(settings, "database_url", DATABASE_URL)
    model = _setting(settings, "embeddings_model", EMBEDDINGS_MODEL)
    return SqlItemRepository(get_sessionmaker(url), get_embedder(model))


def build_search(settings: Any = None, repo: Any = None) -> SearchEngine:
    """Поиск работает поверх той же базы, что и repo (берёт его sessionmaker и эмбеддер)."""
    from app.storage.repository import SqlItemRepository
    from app.storage.search import HybridSearchEngine

    if not isinstance(repo, SqlItemRepository):
        repo = build_repository(settings)
    return HybridSearchEngine(repo.sessionmaker, repo.embedder)


__all__ = ["build_repository", "build_search"]

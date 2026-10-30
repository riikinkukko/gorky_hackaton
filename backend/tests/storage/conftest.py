"""Интеграционные фикстуры: отдельная база <имя>_test, миграции, импорт CSV.

Если PostgreSQL недоступен, интеграционные тесты пропускаются (юнит-тесты идут всегда).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[2]

# Словарь синонимов ведёт чат 1 (app/nlu/synonyms.yml). Пока его нет в дереве, тесты берут
# снимок из fixtures/, чтобы не зависеть от порядка мержа веток.
_REAL_SYN = BACKEND / "app" / "nlu" / "synonyms.yml"
if not _REAL_SYN.exists() and "SYNONYMS_PATH" not in os.environ:
    import app.storage.text as _text

    _text.SYNONYMS_PATH = Path(__file__).resolve().parent / "fixtures" / "synonyms.yml"
    _text.lexicon.cache_clear()
    _text.lemma.cache_clear()
CSV = BACKEND.parent / "data" / "voice_assistant_events_1000.csv"
BASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://app:app@localhost/app")
TEST_URL = os.getenv("TEST_DATABASE_URL") or make_url(BASE_URL).set(
    database=f"{make_url(BASE_URL).database}_test"
).render_as_string(hide_password=False)


def _prepare_db() -> str | None:
    url = make_url(TEST_URL)
    try:
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()
    except Exception as e:  # нет сервера или прав
        return f"PostgreSQL недоступен: {e.__class__.__name__}"
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = TEST_URL
    import app.storage.config as cfg

    cfg.DATABASE_URL = TEST_URL
    command.upgrade(Config(str(BACKEND / "app/storage/alembic.ini")), "head")
    return None


@pytest.fixture(scope="session")
def db_url() -> str:
    reason = _prepare_db()
    if reason:
        pytest.skip(reason)
    return TEST_URL


def make_sm(url: str) -> async_sessionmaker:
    # NullPool: каждый тест в своём event loop, соединения не переиспользуются между циклами
    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


@pytest.fixture(scope="session")
def demo_user(db_url: str) -> int:
    """Пользователь с импортом CSV (id сохраняются = id из CSV)."""
    if not CSV.exists():
        pytest.skip("нет CSV организаторов")
    from app.cli import import_csv

    stats = asyncio.run(import_csv(CSV, "demo", replace=True, keep_ids=True, sm=make_sm(db_url)))
    assert stats["inserted"] == 1000
    return int(stats["user_id"])


@pytest.fixture
def sm(db_url: str) -> async_sessionmaker:
    return make_sm(db_url)

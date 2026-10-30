"""Фикстуры тестов безопасности (зона чата 8).

Тесты двух видов:
- обычные — защита уже есть, тест не даёт её сломать (регрессия);
- ``pytest.mark.xfail(reason="SEC-xx …")`` — находка ещё не исправлена. Тест описывает
  критерий «починено»: после исправления он станет XPASS, и чат 8 снимет маркер.

Внешние сервисы не нужны: заглушки STT/TTS, хранилище в памяти. Тесты с PostgreSQL
пропускаются, если база недоступна.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
CSV = ROOT / "data" / "voice_assistant_events_1000.csv"
NOW = datetime.fromisoformat("2026-10-21T14:00:00+03:00")


def make_settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "freeze_now": NOW,
        "seed_csv": str(CSV),
        "nlu_impl": "real",
        "dialog_impl": "real",
        "storage_impl": "memory",
        "stt_provider": "fake",
        "tts_provider": "fake",
        "llm_provider": "rules",
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    def _make(**kw: object) -> TestClient:
        # https: cookie сессии ставится с флагом Secure и по http не вернулась бы
        app = create_app(make_settings(**kw))
        return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)

    return _make


@pytest.fixture
def prod_client(make_client: Callable[..., TestClient]) -> TestClient:
    """Конфигурация стенда: APP_ENV=prod (так ставит docker-compose.shared.yml)."""
    return make_client(app_env="prod", session_secret="s" * 48)


@pytest.fixture
def test_client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(app_env="test")

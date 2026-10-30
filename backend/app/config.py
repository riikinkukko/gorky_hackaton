"""Настройки приложения из переменных окружения (.env)."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    default_tz: str = "Europe/Moscow"
    freeze_now: datetime | None = None  # FREEZE_NOW=2026-10-21T14:00:00+03:00

    # --- какие реализации подключать (fake — заглушки чата 0) ---
    nlu_impl: Literal["fake", "real"] = "fake"  # real → app.nlu.build_nlu / build_date_resolver
    dialog_impl: Literal["fake", "real"] = "fake"  # real → app.dialog.build_orchestrator
    storage_impl: Literal["memory", "postgres"] = "memory"  # postgres → app.storage.build_*
    stt_provider: str = "fake"  # fake | gigaam | speechkit | browser → app.voice.build_stt
    tts_provider: str = "fake"  # fake | silero | speechkit | browser → app.voice.build_tts
    llm_provider: str = "rules"  # gigachat | yandex | ollama | rules (читает чат 1)

    database_url: str | None = None
    seed_csv: str | None = None  # путь к CSV организаторов для InMemoryRepository
    seed_user_id: int = 1
    # демо-логин фронта; создаются при старте (иначе FK на items) и только им выдаётся сессия
    demo_user_ids: str = Field(
        "1,2,3", validation_alias=AliasChoices("demo_user_ids", "demo_users")
    )
    ics_tokens: str = "demo:1"  # "token:user_id,token2:user_id2"
    cors_origins: str = "http://localhost:5173"
    fake_stt_text: str = "Что у меня сегодня?"

    # --- безопасность (SEC-01, SEC-03; docs/security/REPORT.md) ---
    session_secret: str = ""  # SESSION_SECRET, ≥ 32 символов в prod: подпись cookie сессии
    session_ttl_s: int = 86_400
    # Переходный режим: в prod принимать X-User-Id, пока фронт не перешёл на /api/session.
    allow_header_auth: bool = False
    rate_chat_per_min: int = 20  # на (пользователь, сессия): /api/chat и /api/undo
    # на весь сервис: /api/chat и /api/undo; от расходов на LLM защищает бюджет NLU (чат 1)
    rate_service_per_hour: int = 3000
    rate_tts_per_min: int = 30  # на пользователя: /api/tts
    rate_login_per_min: int = 10  # POST /api/session на IP

    @field_validator("freeze_now", "database_url", "seed_csv", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v == "" else v

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        """В prod отказываемся стартовать с небезопасной конфигурацией (SEC-01, SEC-06, SEC-07)."""
        if self.app_env != "prod":
            return self
        if len(self.session_secret) < 32:
            raise ValueError("APP_ENV=prod: SESSION_SECRET должен быть не короче 32 символов")
        if self.storage_impl == "postgres" and self.database_url:
            password = urlsplit(self.database_url).password or ""
            if password in ("", "app", "postgres", "password"):
                raise ValueError("APP_ENV=prod: смените пароль PostgreSQL (POSTGRES_PASSWORD)")
        for token, uid in self.ics_token_map().items():
            weak_demo = token == "demo" and uid == self.seed_user_id
            if not weak_demo and len(token) < 22:
                raise ValueError(f"APP_ENV=prod: ICS-токен пользователя {uid} короче 22 символов")
        return self

    def demo_users(self) -> set[int]:
        return {int(x) for x in self.demo_user_ids.split(",") if x.strip()}

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.default_tz)

    def now(self) -> datetime:
        """«Сейчас» для бизнес-логики: FREEZE_NOW или текущее время в таймзоне по умолчанию."""
        if self.freeze_now is not None:
            fn = self.freeze_now
            return fn if fn.tzinfo else fn.replace(tzinfo=self.tz)
        return datetime.now(self.tz)

    def ics_token_map(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for pair in self.ics_tokens.split(","):
            if ":" in pair:
                token, uid = pair.split(":", 1)
                result[token.strip()] = int(uid)
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Настройки слоя хранения (из переменных окружения)."""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://app:app@localhost/app")
DEFAULT_TZ = ZoneInfo(os.getenv("DEFAULT_TZ", "Europe/Moscow"))
# "hash" — локальный детерминированный эмбеддер без скачивания моделей (CI, офлайн);
# иначе имя модели sentence-transformers, напр. "deepvk/USER-bge-m3".
EMBEDDINGS_MODEL = os.getenv("EMBEDDINGS_MODEL") or "hash"  # пустая строка в .env = по умолчанию

"""Одноразовая чистка стенда перед подачей (31.10.2026).

Удаляет всё, что набрали тестировщики: записи демо-пользователей 1, 2 и 3 (журнал отмены
удаляется каскадом). Записи организаторов пользователю 1 сразу после миграции заново
импортирует docker-entrypoint.sh (import-csv идемпотентен и заливает недостающие строки), так
что демо возвращается к исходным 1000 записям.

Работает только при APP_ENV=prod (стенд). В разработке, тестах и CI — ничего не делает, но
отмечается применённой, как любая миграция.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if os.environ.get("APP_ENV") != "prod":
        return
    op.execute("DELETE FROM items WHERE user_id IN (1, 2, 3)")


def downgrade() -> None:
    pass  # данные тестировщиков не восстанавливаем

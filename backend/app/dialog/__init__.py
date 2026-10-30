"""Диалог и бизнес-логика (чат 2). Точка входа для app/container.py при DIALOG_IMPL=real."""

from __future__ import annotations

from typing import Any

from app.contracts import NLU, DateResolver, ItemRepository, Orchestrator, SearchEngine


def build_orchestrator(
    *, settings: Any, nlu: NLU, dates: DateResolver, repo: ItemRepository, search: SearchEngine
) -> Orchestrator:
    """Фабрика из контракта v1.1. settings сейчас не используется (таймзона приходит через now)."""
    from app.dialog.orchestrator import make_orchestrator

    return make_orchestrator(nlu, repo, search, dates)


__all__ = ["build_orchestrator"]

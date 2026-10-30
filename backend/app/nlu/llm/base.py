"""Общий интерфейс LLM-клиента: сообщения в формате {role, content} → один вызов функции."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class LLMError(Exception):
    """Сеть, авторизация, таймаут, неожиданный формат ответа."""


class LLMClient(Protocol):
    name: str

    async def complete_with_tools(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]]
    ) -> ToolCall | None:
        """Возвращает вызов функции или None, если модель ответила текстом."""
        ...

    async def aclose(self) -> None: ...

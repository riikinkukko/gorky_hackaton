"""YandexGPT (Yandex AI Studio, Foundation Models API v1) с инструментами (tools)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import LLMError, ToolCall

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPTClient:
    name = "yandex"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        *,
        model: str = "yandexgpt-lite",
        iam_token: bool = False,
        timeout: float = 8.0,
        api_url: str = API_URL,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not folder_id:
            raise ValueError("YANDEX_API_KEY и YANDEX_FOLDER_ID обязательны")
        self.folder_id = folder_id
        self.model_uri = (
            model if model.startswith("gpt://") else f"gpt://{folder_id}/{model}/latest"
        )
        self.auth = f"Bearer {api_key}" if iam_token else f"Api-Key {api_key}"
        self.api_url = api_url
        self.http = http or httpx.AsyncClient(timeout=timeout)

    async def complete_with_tools(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]]
    ) -> ToolCall | None:
        body = {
            "modelUri": self.model_uri,
            "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "400"},
            "messages": [{"role": m["role"], "text": m["content"]} for m in messages],
            "tools": [{"function": t} for t in tools],
        }
        try:
            r = await self.http.post(
                self.api_url,
                json=body,
                headers={"Authorization": self.auth, "x-folder-id": self.folder_id},
            )
            r.raise_for_status()
            msg = r.json()["result"]["alternatives"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise LLMError(f"YandexGPT: {e}") from e
        calls = (msg.get("toolCallList") or {}).get("toolCalls") or []
        if not calls:
            return None
        fc = calls[0].get("functionCall") or {}
        args = fc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as e:
                raise LLMError(f"YandexGPT: аргументы не JSON: {args[:200]}") from e
        return ToolCall(name=fc.get("name", ""), arguments=args)

    async def aclose(self) -> None:
        await self.http.aclose()

"""GigaChat API: OAuth-токен (живёт 30 минут, кэшируем) + chat/completions с functions."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from .base import LLMError, ToolCall

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"


class GigaChatClient:
    name = "gigachat"

    def __init__(
        self,
        credentials: str,
        *,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat-2",
        verify: bool | str = True,
        timeout: float = 8.0,
        auth_url: str = AUTH_URL,
        api_url: str = API_URL,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not credentials:
            raise ValueError("GIGACHAT_CREDENTIALS не задан")
        self.credentials = credentials
        self.scope = scope
        self.model = model
        self.auth_url = auth_url
        self.api_url = api_url.rstrip("/")
        # Сертификаты Минцифры: укажите путь к CA в GIGACHAT_VERIFY_SSL или false для разработки
        self.http = http or httpx.AsyncClient(timeout=timeout, verify=verify)
        self._token: str | None = None
        self._token_exp: float = 0.0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        try:
            r = await self.http.post(
                self.auth_url,
                headers={
                    "Authorization": f"Basic {self.credentials}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self.scope},
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"GigaChat auth: {e}") from e
        self._token = data["access_token"]
        exp = data.get("expires_at", 0)
        self._token_exp = exp / 1000 if exp > 1e11 else (exp or time.time() + 1800)
        return self._token

    async def complete_with_tools(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]]
    ) -> ToolCall | None:
        token = await self._access_token()
        body = {
            "model": self.model,
            "messages": messages,
            "functions": tools,
            "function_call": "auto",
            "temperature": 0.1,
            "max_tokens": 400,
        }
        try:
            r = await self.http.post(
                f"{self.api_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if r.status_code == 401:  # токен отозван — один раз обновим
                self._token = None
                token = await self._access_token()
                r = await self.http.post(
                    f"{self.api_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise LLMError(f"GigaChat: {e}") from e
        fc = msg.get("function_call")
        if not fc:
            return None
        args = fc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as e:
                raise LLMError(f"GigaChat: аргументы не JSON: {args[:200]}") from e
        return ToolCall(name=fc.get("name", ""), arguments=args)

    async def aclose(self) -> None:
        await self.http.aclose()

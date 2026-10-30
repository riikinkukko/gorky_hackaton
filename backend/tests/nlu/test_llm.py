"""LLM-слой: клиенты на мок-транспорте, валидация, ретрай, таймаут и фолбэк."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.contracts import Intent, ItemType
from app.nlu import HybridNLU
from app.nlu.llm.base import LLMError, ToolCall
from app.nlu.llm.gigachat import GigaChatClient
from app.nlu.llm.tools import TOOLS
from app.nlu.llm.yandex import YandexGPTClient

from .conftest import dt, parse, state

AMBIGUOUS = "Иван просил инфу по поводу той штуки"  # правила не уверены → идём в LLM


class FakeLLM:
    name = "fake"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def complete_with_tools(self, messages, tools):
        self.calls.append(messages)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if r == "sleep":
            await asyncio.sleep(5)
        return r

    async def aclose(self) -> None: ...


def test_rules_first_no_llm_call() -> None:
    llm = FakeLLM()
    c = parse(HybridNLU(llm), "Что у меня сегодня?")
    assert c.intent == Intent.LIST and c.source == "rules" and llm.calls == []


def test_llm_dates_computed_by_resolver() -> None:
    llm = FakeLLM(
        ToolCall(
            "create_item",
            {
                "type": "reminder",
                "title": "Отправить инфу Ивану",
                "when_text": "в пятницу в 11",
                "participants": ["Иваном"],
            },
        )
    )
    c = parse(HybridNLU(llm), AMBIGUOUS)
    assert c.source == "llm" and c.intent == Intent.CREATE and c.slots.type == ItemType.REMINDER
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23, 11)
    assert c.slots.participants == ["Иван"]


def test_llm_invalid_then_retry() -> None:
    llm = FakeLLM(
        ToolCall("create_item", {"type": "event", "title": ""}),  # невалидно
        ToolCall("search_items", {"person": "Ивана", "tense": "past", "query": "ТЗ"}),
    )
    c = parse(HybridNLU(llm), AMBIGUOUS)
    assert len(llm.calls) == 2 and "не прошёл проверку" in llm.calls[1][-1]["content"]
    assert c.intent == Intent.SEARCH and c.slots.participants == ["Иван"]
    assert c.slots.query == "техническое задание" and c.slots.tense == "past"


def test_llm_unknown_function_then_fallback() -> None:
    llm = FakeLLM(ToolCall("send_email", {}), ToolCall("send_email", {}))
    c = parse(HybridNLU(llm), AMBIGUOUS)
    assert c.source == "fallback" and len(llm.calls) == 2


def test_llm_error_fallback() -> None:
    c = parse(HybridNLU(FakeLLM(LLMError("502"))), AMBIGUOUS)
    assert c.source == "fallback"


def test_llm_timeout_fallback() -> None:
    nlu = HybridNLU(FakeLLM("sleep"), llm_timeout=0.05)
    c = parse(nlu, AMBIGUOUS)
    assert c.source == "fallback"


def test_llm_update_target_last() -> None:
    llm = FakeLLM(
        ToolCall("update_item", {"target": {"ref": "last"}, "new_when_text": "на час позже"})
    )
    c = parse(HybridNLU(llm), "ну и это самое её тоже туда же давай попозже на часик")
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.pronoun
    assert c.slots.new_when and c.slots.new_when.start is None


def test_prompt_has_today_and_context() -> None:
    llm = FakeLLM(ToolCall("smalltalk", {}))
    parse(HybridNLU(llm), AMBIGUOUS, state(last_item_ids=[5]))
    sys = llm.calls[0][0]["content"]
    assert "2026-10-21 14:00" in sys and "среда" in sys and "Europe/Moscow" in sys


# ---------- HTTP-клиенты на мок-транспорте ----------


def _gigachat_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/oauth"):
            assert req.headers["Authorization"] == "Basic KEY"
            return httpx.Response(200, json={"access_token": "tok", "expires_at": 9999999999999})
        body = json.loads(req.content)
        assert body["functions"][0]["name"] == "create_item" and body["function_call"] == "auto"
        assert req.headers["Authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "function_call": {
                                "name": "list_items",
                                "arguments": {"period_text": "сегодня"},
                            },
                        },
                        "finish_reason": "function_call",
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def test_gigachat_client() -> None:
    seen: list[httpx.Request] = []
    client = GigaChatClient("KEY", http=httpx.AsyncClient(transport=_gigachat_transport(seen)))

    async def go() -> tuple[ToolCall | None, ToolCall | None]:
        a = await client.complete_with_tools([{"role": "user", "content": "что сегодня"}], TOOLS)
        b = await client.complete_with_tools([{"role": "user", "content": "что сегодня"}], TOOLS)
        return a, b

    a, b = asyncio.run(go())
    assert a == ToolCall("list_items", {"period_text": "сегодня"}) == b
    assert sum(r.url.path.endswith("/oauth") for r in seen) == 1  # токен кэшируется


def test_yandex_client() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["modelUri"] == "gpt://FOLDER/yandexgpt-lite/latest"
        assert body["messages"][0] == {"role": "user", "text": "найди ТЗ"}
        assert body["tools"][0]["function"]["name"] == "create_item"
        assert (
            req.headers["Authorization"] == "Api-Key K" and req.headers["x-folder-id"] == "FOLDER"
        )
        return httpx.Response(
            200,
            json={
                "result": {
                    "alternatives": [
                        {
                            "message": {
                                "role": "assistant",
                                "toolCallList": {
                                    "toolCalls": [
                                        {
                                            "functionCall": {
                                                "name": "search_items",
                                                "arguments": {"query": "ТЗ"},
                                            }
                                        }
                                    ]
                                },
                            },
                            "status": "ALTERNATIVE_STATUS_TOOL_CALLS",
                        }
                    ]
                }
            },
        )

    client = YandexGPTClient(
        "K", "FOLDER", http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    call = asyncio.run(client.complete_with_tools([{"role": "user", "content": "найди ТЗ"}], TOOLS))
    assert call == ToolCall("search_items", {"query": "ТЗ"})


def test_http_error_raises_llm_error() -> None:
    client = YandexGPTClient(
        "K",
        "F",
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy"))
        ),
    )
    try:
        asyncio.run(client.complete_with_tools([{"role": "user", "content": "x"}], TOOLS))
    except LLMError:
        return
    raise AssertionError("ожидали LLMError")


def test_build_nlu_without_keys_falls_back_to_rules(monkeypatch) -> None:
    from app.nlu import build_nlu

    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    assert build_nlu(None, provider="gigachat").llm is None

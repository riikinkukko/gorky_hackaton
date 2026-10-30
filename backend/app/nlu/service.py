"""HybridNLU — реализация протокола `NLU` из контракта.

Порядок разбора:
0. Нормализация транскрипта (asr.py): заминки, «15 00» → «15:00», искажённые имена и
   организации («с арбитой» → «с Орбитой») по словарю сущностей пользователя.
1. Ответ на уточняющий вопрос (state.awaiting) — «в три» после «На какое время?».
2. Правила (Router). Уверенность ≥ порога — сразу результат, без LLM.
3. LLM с function calling → Pydantic-валидация → один ретрай с текстом ошибки.
4. Фолбэк на правила, если LLM нет, он упал, тормозит или дважды ответил невалидно.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from pydantic import ValidationError

from app.contracts import Command, DialogState, Intent

from .asr import AsrNormalizer, AsrResult, EntitySource, KnownEntities, RepoEntitySource
from .context import ContextResolver
from .dates import RuleDateResolver
from .entities import EntityNormalizer, default_normalizer
from .llm.base import LLMClient, LLMError
from .llm.prompts import build_messages, retry_message
from .llm.tools import TOOLS, to_command, validate_call
from .router import RuleRouter
from .turns import classify_turn, continuation_never_creates, with_turn

log = logging.getLogger("app.nlu")


class HybridNLU:
    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        resolver: RuleDateResolver | None = None,
        normalizer: EntityNormalizer | None = None,
        rules_threshold: float = 0.85,
        llm_timeout: float = 6.0,
        asr_normalize: bool = True,
        entity_source: EntitySource | None = None,
    ) -> None:
        self.dates = resolver or RuleDateResolver()
        self.ents = normalizer or default_normalizer()
        self.router = RuleRouter(self.dates, self.ents)
        self.context = ContextResolver(self.router, self.dates, self.ents)
        self.llm = llm
        self.rules_threshold = rules_threshold
        self.llm_timeout = llm_timeout
        self.asr = AsrNormalizer(self.ents) if asr_normalize else None
        self.entity_source = entity_source

    def attach_repository(self, repo: object) -> None:
        """Словарь сущностей пользователя для нормализации — из его записей.

        Вызывается из app/container.py после сборки репозитория (build_nlu вызывается раньше).
        """
        self.entity_source = RepoEntitySource(repo)

    async def normalize_transcript(self, text: str, user_id: int) -> AsrResult:
        """Исходный и нормализованный текст + список исправлений (для отладки и чата 7)."""
        if self.asr is None:
            return AsrResult(original=text, text=text)
        ents: KnownEntities | None = None
        if self.entity_source is not None:
            try:
                ents = await asyncio.wait_for(self.entity_source.known_entities(user_id), 0.5)
            except Exception as e:  # словарь — подсказка, разбор без него не должен падать
                log.warning("словарь сущностей недоступен: %s", e)
        return self.asr.normalize(text, ents)

    async def parse(self, text: str, state: DialogState, now: datetime) -> Command:
        norm = await self.normalize_transcript(text, state.user_id)
        if norm.changed:
            log.info(
                "asr session=%s original=%r → text=%r fixes=%s",
                state.session_id[:8],
                norm.original,
                norm.text,
                "; ".join(norm.fixes),
            )
        text = norm.text  # raw_text команды — нормализованный текст: его читает диалог
        ans = self.context.answer(text, state, now)
        if ans is not None:
            return self._turn(ans, state, now, from_context=True)
        return self._turn(await self._parse_free(text, state, now), state, now)

    def _turn(
        self, cmd: Command, state: DialogState, now: datetime, *, from_context: bool = False
    ) -> Command:
        """Разметка «полная команда / продолжение / да / нет» (правило 25.10, turns.py)."""
        kind = classify_turn(cmd, state, now, self.dates, self.ents, from_context=from_context)
        if kind == "continuation" and not from_context:
            cmd = continuation_never_creates(cmd, state)
        return with_turn(cmd, kind)

    async def _parse_free(self, text: str, state: DialogState, now: datetime) -> Command:
        rule = self.router.route(text, state, now)
        if rule is not None and rule.confidence >= self.rules_threshold:
            return rule
        if self.llm is not None:
            try:
                cmd = await asyncio.wait_for(
                    self._parse_llm(text, state, now), timeout=self.llm_timeout
                )
                if cmd is not None:
                    return cmd
            except (TimeoutError, LLMError) as e:
                log.warning("LLM недоступен, фолбэк на правила: %s", e)
        return self.router.fallback(text, state, now)

    async def _parse_llm(self, text: str, state: DialogState, now: datetime) -> Command | None:
        assert self.llm is not None
        messages = build_messages(text, state, now)
        last_error = ""
        for attempt in range(2):
            call = await self.llm.complete_with_tools(messages, TOOLS)
            if call is None:
                if attempt == 0:
                    messages = [*messages, retry_message("ответ без вызова функции")]
                    continue
                return None
            try:
                args = validate_call(call.name, call.arguments)
                cmd = to_command(call.name, args, text, now, self.dates, self.ents)
            except (ValidationError, ValueError) as e:
                last_error = str(e)[:500]
                log.info("LLM вернул невалидный вызов (%s): %s", attempt + 1, last_error)
                messages = [*messages, retry_message(last_error)]
                continue
            # если правила видели уточнение по контексту, а LLM смолтокнул — правила честнее
            if cmd.intent == Intent.SMALLTALK and state.awaiting:
                return None
            return cmd
        return None

    async def aclose(self) -> None:
        if self.llm is not None:
            await self.llm.aclose()

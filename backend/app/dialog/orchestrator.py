"""Orchestrator: текст → NLU → DialogManager → AssistantReply (+ debug)."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from app.contracts import (
    NLU,
    AssistantReply,
    DateResolver,
    DialogState,
    ItemRepository,
    SearchEngine,
)
from app.dialog.manager import DialogManager
from app.dialog.normalize import strip_hesitations
from app.dialog.repair import repair_truncated
from app.dialog.turns import classify_turn, drop_inherited
from app.domain.responder import Responder
from app.domain.service import ItemService

log = logging.getLogger(__name__)


class DefaultOrchestrator:
    """Реализует Orchestrator из контракта: handle() меняет DialogState на месте,
    API сохраняет его после вызова."""

    def __init__(self, nlu: NLU, dialog: DialogManager) -> None:
        self.nlu = nlu
        self.dialog = dialog

    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        t0 = time.perf_counter()
        clean = strip_hesitations(text)  # «Э-э… покажи…» → «покажи…»; слова не трогаем
        try:
            cmd = await self.nlu.parse(clean, state, now)
        except Exception:  # NLU не должен ронять диалог
            log.exception("nlu failed")
            return AssistantReply(
                text=Responder().error().text, action="none", debug={"error": "nlu"}
            )
        t1 = time.perf_counter()
        parsed = cmd
        cmd = repair_truncated(cmd, clean)  # «Неси встречу на субботу» — обрезанное «Перенеси»
        repaired = cmd
        turn = classify_turn(cmd, state)
        if turn == "full":
            cmd = drop_inherited(cmd, state.last_command)  # «Что у меня в субботу?» после поиска
        try:
            reply, executed = await self.dialog.handle_turn(cmd, state, now)
        except Exception:
            log.exception("dialog failed")
            state.pending, state.awaiting = None, None
            return AssistantReply(
                text=Responder().error().text,
                action="none",
                debug={"error": "dialog", "intent": cmd.intent.value},
            )
        t2 = time.perf_counter()
        if reply.action != "clarify":
            # v1.2: для «А на следующей неделе?», «нет, в 11». Если ход завершил уточнение,
            # сохраняем полную собранную команду, а не голый ответ «в три».
            state.last_command = executed
        extra = reply.debug or {}  # диалог мог положить своё (напр. draft при конфликте)
        reply.debug = {
            **extra,
            "intent": (executed.intent if turn == "continuation" else cmd.intent).value,
            **(
                {"nlu_intent": cmd.intent.value}
                if turn == "continuation" and executed.intent != cmd.intent
                else {}
            ),
            **({"text_clean": clean} if clean != text else {}),
            "turn": turn,
            **({"repaired_from": parsed.intent.value} if repaired is not parsed else {}),
            **({"grounded_from": repaired.intent.value} if cmd is not repaired else {}),
            "source": cmd.source,
            "confidence": cmd.confidence,
            "slots": cmd.slots.model_dump(mode="json", exclude_defaults=True),
            "awaiting": state.awaiting,
            "latency_ms": {"nlu": round((t1 - t0) * 1000, 1), "dialog": round((t2 - t1) * 1000, 1)},
        }
        return reply


def make_orchestrator(
    nlu: NLU, repo: ItemRepository, search: SearchEngine, dates: DateResolver | None = None
) -> DefaultOrchestrator:
    """Сборка без settings — для тестов и скриптов."""
    service = ItemService(repo, search)
    return DefaultOrchestrator(nlu, DialogManager(service, dates=dates))

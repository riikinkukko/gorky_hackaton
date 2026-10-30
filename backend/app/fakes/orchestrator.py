"""FakeOrchestrator: минимальный диалог поверх контрактов.

create (с уточнением времени), list, search, undo. Работает с любыми NLU / ItemRepository /
SearchEngine по контракту, поэтому реальные модули соседей подключаются без правок здесь.
Настоящий оркестратор и DialogManager делает чат 2 (backend/app/dialog/, backend/app/domain/).

Соглашение: Orchestrator.handle мутирует переданный DialogState; API сохраняет его после вызова.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.contracts import (
    NLU,
    ActionRecord,
    AssistantReply,
    Command,
    DialogState,
    Intent,
    Item,
    ItemRepository,
    ItemStatus,
    ItemType,
    SearchEngine,
    TimeSpec,
)
from app.fakes.format import human_dt, item_line, plural

EXAMPLES = [
    "Что у меня сегодня?",
    "Напомни мне завтра в 10 утра позвонить Ивану",
    "Когда у меня была встреча с Иваном?",
]


class FakeOrchestrator:
    def __init__(self, nlu: NLU, repo: ItemRepository, search: SearchEngine) -> None:
        self.nlu = nlu
        self.repo = repo
        self.search = search

    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        t0 = time.perf_counter()
        cmd = await self.nlu.parse(text, state, now)

        if state.awaiting == "time" and state.pending is not None:
            if cmd.intent == Intent.CREATE and cmd.slots.when and cmd.slots.when.has_time:
                cmd = self._merge_time(state.pending, cmd.slots.when, now)
            state.pending, state.awaiting = None, None

        reply = await self._dispatch(cmd, state, now)
        if reply.action != "clarify":
            state.last_command = cmd
        reply.debug = {
            "intent": cmd.intent.value,
            "slots": cmd.slots.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
            "source": cmd.source,
            "confidence": cmd.confidence,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "impl": "fake",
        }
        return reply

    @staticmethod
    def _merge_time(pending: Command, answer: TimeSpec, now: datetime) -> Command:
        assert answer.start is not None
        if answer.has_date:  # «завтра в три» — ответ целиком заменяет дату
            start = answer.start
        else:  # «в три» — берём дату из незавершённой команды
            pw = pending.slots.when
            base = pw.start if pw and pw.start else now
            start = base.replace(hour=answer.start.hour, minute=answer.start.minute, second=0)
        when = TimeSpec(text=answer.text, start=start, has_time=True, has_date=True)
        return pending.model_copy(update={"slots": pending.slots.model_copy(update={"when": when})})

    async def _dispatch(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        match cmd.intent:
            case Intent.CREATE:
                return await self._create(cmd, state, now)
            case Intent.LIST:
                return await self._list(cmd, state, now)
            case Intent.SEARCH:
                return await self._search(cmd, state, now)
            case Intent.UNDO:
                return await self._undo(state, now)
            case _:
                return AssistantReply(
                    text="Пока я умею немногое (это заглушка). Попробуйте: " + " / ".join(EXAMPLES),
                    action="none",
                    suggestions=EXAMPLES,
                )

    async def _create(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        s = cmd.slots
        item_type = s.type or ItemType.TASK
        when = s.when
        if item_type in (ItemType.MEETING, ItemType.REMINDER) and not (when and when.has_time):
            state.pending, state.awaiting = cmd, "time"
            noun = "встречу" if item_type == ItemType.MEETING else "напоминание"
            return AssistantReply(
                text=f"На какое время поставить {noun}?",
                action="clarify",
                suggestions=["10:00", "15:00", "18:00"],
            )

        start = when.start if when else None
        is_meeting = item_type == ItemType.MEETING
        item = await self.repo.create(
            Item(
                user_id=state.user_id,
                type=item_type,
                title=s.title or "Без названия",
                starts_at=start if is_meeting else None,
                ends_at=start + timedelta(minutes=s.duration_min or 60)
                if is_meeting and start
                else None,
                due_at=None if is_meeting else start,
                has_time=bool(when and when.has_time),
                status=ItemStatus.OPEN,
                person=s.participants,
                source="text",
            )
        )
        state.last_item_ids = [item.id]
        state.last_action_id = await self.repo.log_action(
            ActionRecord(
                session_id=state.session_id,
                user_id=state.user_id,
                kind="create",
                item_id=item.id,
                after=item,
            )
        )
        when_text = f" — {human_dt(start, item.has_time, now)}" if start else ""
        return AssistantReply(
            text=f"Записал: {item.title}{when_text}.",
            action="created",
            items=[item],
            suggestions=["Отменить"],
        )

    async def _list(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        when = cmd.slots.when
        if when and when.start:
            start = when.start
            end = when.end or start + timedelta(days=1)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        items = await self.repo.list_range(state.user_id, start, end, cmd.slots.type)
        state.last_item_ids = [i.id for i in items]
        period = human_dt(start, with_time=False, now=now)
        if not items:
            return AssistantReply(text=f"На {period} ничего нет.", action="listed")
        n = len(items)
        lines = "; ".join(item_line(i, now) for i in items[:5])
        more = f" и ещё {n - 5}" if n > 5 else ""
        word = plural(n, "запись", "записи", "записей")
        return AssistantReply(
            text=f"На {period} {n} {word}: {lines}{more}.", action="listed", items=items
        )

    async def _search(self, cmd: Command, state: DialogState, now: datetime) -> AssistantReply:
        hits = await self.search.search(state.user_id, cmd.slots, now)
        items = [item for item, _ in hits]
        state.last_item_ids = [i.id for i in items[:5]]
        if not items:
            return AssistantReply(text="Ничего не нашёл.", action="found")
        if len(items) == 1:
            return AssistantReply(
                text=f"Нашёл: {item_line(items[0])}.", action="found", items=items
            )
        top = "; ".join(item_line(i) for i in items[:3])
        return AssistantReply(
            text=f"Нашёл {len(items)}. Самые подходящие: {top}.", action="found", items=items[:5]
        )

    async def _undo(self, state: DialogState, now: datetime) -> AssistantReply:
        action = (
            await self.repo.get_action(state.user_id, state.last_action_id)
            if state.last_action_id is not None
            else None
        )
        if action is None or action.undone:
            return AssistantReply(text="Нечего отменять.", action="none")
        # в фейке бывает только create → откат = мягкое удаление
        await self.repo.soft_delete(action.user_id, action.item_id)
        await self.repo.mark_undone(action.user_id, action.id)
        state.last_action_id = None
        state.last_item_ids = []
        item = action.after
        title = f": {item.title}" if item else ""
        return AssistantReply(
            text=f"Отменил{title}.", action="undone", items=[item] if item else []
        )

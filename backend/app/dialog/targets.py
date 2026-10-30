"""Поиск записи, на которую ссылается пользователь: «её», «встречу с Марией в субботу»."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.contracts import DialogState, Item, ItemRef, ItemStatus, Slots
from app.domain import ru
from app.domain.service import ItemService
from app.domain.timeutil import day_bounds, item_time

# слова, которые не несут смысла для сопоставления с заголовком
_STOP = set(
    ru.stems(
        "с со по про на в во о об для и к у мне мой моя мою мои это эту этой эта её ее его их "
        "встреча встречу встречи задача задачу задачи "
        "напоминание напоминания напоминалку запись записи "
        "напомнить напомни"
    )
)


LAST = "__last__"  # «последнюю задачу» (слот от NLU)
ALL = "__all__"  # «все встречи на завтра» (слот от NLU)
TIME_WINDOW = timedelta(minutes=30)  # «встречу на 8:00» — цель в пределах ±30 мин


@dataclass
class Resolution:
    kind: Literal["ok", "choose", "none", "no_context"]
    item: Item | None = None  # сохранённая версия (из репозитория)
    candidates: list[Item] = field(default_factory=list)


def content_stems(text: str | None) -> list[str]:
    return [s for s in ru.stems(text or "") if s not in _STOP]


def _item_stems(item: Item) -> set[str]:
    return set(ru.stems(item.title)) | {ru.stem(p) for p in item.person + item.organization}


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[int] = set()
    out = []
    for i in items:
        if i.id not in seen:
            seen.add(i.id)
            out.append(i)
    return out


class TargetResolver:
    def __init__(self, service: ItemService) -> None:
        self.service = service

    async def _stored(self, user_id: int, item_id: int) -> Item | None:
        return await self.service.get(user_id, item_id)

    async def resolve(
        self, slots: Slots, state: DialogState, now: datetime, *, prefer_open: bool = False
    ) -> Resolution:
        ref = slots.target or ItemRef()
        uid = state.user_id
        if ref.item_id is not None:
            item = await self._stored(uid, ref.item_id)
            return Resolution("ok", item) if item else Resolution("none")
        if ref.description == LAST:
            return await self._last(slots, state, now)

        has_when = bool(slots.when and slots.when.start)
        # «эту задачу», «её»: в описании нет смысловых слов — это ссылка на контекст, не поиск
        described = bool(content_stems(ref.description))
        has_filters = bool(described or slots.participants or slots.organization or has_when)

        # 1) местоимение или ничего конкретного: «её», «перенеси на завтра»
        if not has_filters:
            last = [i for i in [await self._stored(uid, x) for x in state.last_item_ids] if i]
            if slots.type is not None:
                typed = [i for i in last if i.type == slots.type]
                last = typed or last
            if not last:
                return Resolution("no_context")
            if len(last) == 1:
                return Resolution("ok", last[0])
            return Resolution("choose", candidates=last[:5])

        # 2) описание: кандидаты из периода или из поиска
        if has_when:
            assert slots.when is not None and slots.when.start is not None
            w = slots.when
            assert w.start is not None
            if w.is_period and w.end:
                lo, hi = w.start, w.end
            else:
                lo, hi = day_bounds(w.start)
            pool = await self.service.list_period(uid, lo, hi, slots.type)
            if w.has_time and not w.is_period:
                # «встречу с 23 октября на 8:00»: время названо — сначала ±30 минут от него
                # «встречу с 24 октября 9:45»: время — ключ поиска. Точное совпадение
                # важнее окна ±30 мин (рядом может стоять другая встреча, напр. на 10:00)
                near = [
                    i
                    for i in pool
                    if (t := item_time(i)) is not None
                    and i.has_time
                    and abs(t - w.start) <= TIME_WINDOW
                ]
                exact = [i for i in near if item_time(i) == w.start]
                pool = exact or near or pool
        else:
            q = Slots(
                type=slots.type,
                participants=slots.participants,
                organization=slots.organization,
                query=ref.description,
                tense=slots.tense,
            )
            pool = [i for i, _ in await self.service.search(uid, q, now, limit=100)]

        words = content_stems(ref.description)
        who = [
            ru.stem(p)
            for p in slots.participants + ([slots.organization] if slots.organization else [])
        ]
        need = max(1, math.ceil(len(words) * 0.6)) if words else 0

        def match(i: Item) -> bool:
            ts = _item_stems(i)
            if any(w not in ts for w in who):
                return False
            return sum(1 for w in words if w in ts) >= need

        cands = _dedupe([i for i in pool if match(i)])
        if not cands:
            return Resolution("none")
        if words:  # оставляем только лучшие по числу совпавших слов

            def score(i: Item) -> int:
                ts = _item_stems(i)
                return sum(1 for w in words if w in ts)

            best = max(score(c) for c in cands)
            cands = [c for c in cands if score(c) == best]
        if prefer_open:
            open_ = [c for c in cands if c.status != ItemStatus.DONE]
            cands = open_ or cands
        if len(cands) > 1:
            in_last = [c for c in cands if c.id in state.last_item_ids]
            if len(in_last) == 1:
                cands = in_last
        if len(cands) > 1 and len({ru.norm(c.title) for c in cands}) == 1:
            # одинаковые записи (повторы в данных): берём ближайшую будущую, иначе последнюю
            def dist(c: Item) -> tuple[bool, float]:
                t = item_time(c) or now
                return (t < now, abs((t - now).total_seconds()))

            cands = [min(cands, key=dist)]
        if len(cands) > 1 and not has_when and (words or who) and slots.tense != "past":
            # «Перенеси созвон с Орбитой на 16:30»: без даты речь о ближайшей будущей записи.
            # Берём её, только если она одна на своё время; иначе спрашиваем.
            future = sorted(
                (c for c in cands if (item_time(c) or now) >= now),
                key=lambda c: item_time(c) or now,
            )
            if len(future) == 1 or (
                len(future) > 1 and item_time(future[0]) != item_time(future[1])
            ):
                cands = [future[0]]
        if len(cands) == 1:
            return Resolution("ok", cands[0])
        cands.sort(key=lambda c: abs(((item_time(c) or now) - now).total_seconds()))
        return Resolution("choose", candidates=cands[:5])

    async def _last(self, slots: Slots, state: DialogState, now: datetime) -> Resolution:
        """«Последнюю задачу»: последняя созданная/изменённая в сессии, иначе ближайшая."""
        uid = state.user_id

        def fits(i: Item | None) -> bool:
            return i is not None and (slots.type is None or i.type == slots.type)

        if state.last_action_id is not None:
            action = await self.service.repo.get_action(uid, state.last_action_id)
            if action is not None and not action.undone and action.kind != "delete":
                item = await self._stored(uid, action.item_id)
                if fits(item):
                    return Resolution("ok", item)
        for item_id in state.last_item_ids:
            item = await self._stored(uid, item_id)
            if fits(item):
                return Resolution("ok", item)
        nxt = await self.service.next_after(uid, now, slots.type)
        if nxt is not None:
            stored = await self._stored(uid, nxt.id)
            return Resolution("ok", stored or nxt)
        return Resolution("none")

    @staticmethod
    def is_choice(text: str) -> bool:
        """Короткий ответ-выбор: «2», «вторую», «последнюю»."""
        t = ru.norm(text).strip(" .!?")
        if t[:1].isdigit():
            return True
        prefixes = ("перв", "втор", "трет", "четвер", "пят", "последн")
        return len(t.split()) <= 2 and any(w.startswith(prefixes) for w in t.split())

    @staticmethod
    def pick(text: str, candidates: list[Item]) -> Item | None:
        """Ответ на «Какую именно?»: «вторую», «2», «с Альфой», «1. Созвон с …»."""
        t = ru.norm(text).strip()
        ordinals = {
            "перв": 0,
            "втор": 1,
            "трет": 2,
            "четвер": 3,
            "пят": 4,
            "последн": len(candidates) - 1,
        }
        if t[:1].isdigit():
            n = int("".join(ch for ch in t[:2] if ch.isdigit())) - 1
            return candidates[n] if 0 <= n < len(candidates) else None
        for word in t.split():
            for prefix, idx in ordinals.items():
                if word.startswith(prefix) and 0 <= idx < len(candidates):
                    return candidates[idx]
        words = content_stems(text)
        if not words:
            return None
        scored = [(sum(1 for w in words if w in _item_stems(c)), c) for c in candidates]
        best = max(s for s, _ in scored)
        top = [c for s, c in scored if s == best]
        return top[0] if best > 0 and len(top) == 1 else None

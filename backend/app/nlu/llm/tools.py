"""Инструменты для function calling, их Pydantic-схемы и перевод в Command.

LLM возвращает ТЕКСТ времени (when_text: «в пятницу в 11»), абсолютную дату считает DateResolver.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.contracts import (
    Command,
    Intent,
    ItemRef,
    ItemStatus,
    ItemType,
    Slots,
    TimeSpec,
)

from ..dates import RuleDateResolver
from ..entities import EntityNormalizer
from ..entities import first as _first
from ..router import parse_shift, sanitize_title

TypeLit = Literal["task", "meeting", "reminder"]


def _none_if_empty(v: Any) -> Any:
    if isinstance(v, str) and not v.strip():
        return None
    return v


class _Args(BaseModel):
    model_config = {"extra": "ignore"}

    @field_validator("*", mode="before")
    @classmethod
    def _empty(cls, v: Any) -> Any:
        return _none_if_empty(v)


class CreateArgs(_Args):
    type: TypeLit
    title: str = Field(min_length=1, max_length=200)
    when_text: str | None = None
    duration_text: str | None = None
    participants: list[str] = Field(default_factory=list)
    organization: str | None = None
    recurrence_text: str | None = None


class ListArgs(_Args):
    period_text: str | None = None
    type: TypeLit | None = None
    status: Literal["open", "done"] | None = None
    tense: Literal["past", "future", "any"] = "any"


class SearchArgs(_Args):
    query: str | None = None
    person: str | None = None
    organization: str | None = None
    type: TypeLit | None = None
    tense: Literal["past", "future", "any"] = "any"
    period_text: str | None = None


class TargetArgs(_Args):
    ref: Literal["last", "latest", "all", "description"] = "description"
    description: str | None = None
    when_text: str | None = None


class UpdateArgs(_Args):
    target: TargetArgs
    new_when_text: str | None = None
    new_title: str | None = None


class TargetOnlyArgs(_Args):
    target: TargetArgs


class SmalltalkArgs(_Args):
    reply: str | None = None


ARG_MODELS: dict[str, type[_Args]] = {
    "create_item": CreateArgs,
    "list_items": ListArgs,
    "search_items": SearchArgs,
    "update_item": UpdateArgs,
    "complete_item": TargetOnlyArgs,
    "delete_item": TargetOnlyArgs,
    "smalltalk": SmalltalkArgs,
}
INTENT_BY_TOOL = {
    "create_item": Intent.CREATE,
    "list_items": Intent.LIST,
    "search_items": Intent.SEARCH,
    "update_item": Intent.UPDATE,
    "complete_item": Intent.COMPLETE,
    "delete_item": Intent.DELETE,
    "smalltalk": Intent.SMALLTALK,
}

_TYPE = {
    "type": "string",
    "enum": ["task", "meeting", "reminder"],
    "description": (
        "task — задача/дело, meeting — встреча/созвон/демо/планёрка, reminder — «напомни …»"
    ),
}
_TENSE = {
    "type": "string",
    "enum": ["past", "future", "any"],
    "description": (
        "Прошлое или будущее. past: «старую встречу», «прошедшие встречи», «давнюю задачу», "
        "«была», «звонил», «когда-то». future: «будущие встречи», «предстоящие», «следующая», "
        "«ближайший». any — о времени ничего не сказано"
    ),
}
_TARGET = {
    "type": "object",
    "description": "На какую запись ссылается пользователь",
    "properties": {
        "ref": {
            "type": "string",
            "enum": ["last", "latest", "all", "description"],
            "description": (
                "last — местоимение («её», «эту», «его»); latest — «последнюю задачу/запись»; "
                "all — «все встречи», «всё на завтра»; description — описана словами"
            ),
        },
        "description": {
            "type": "string",
            "description": "Описание записи словами пользователя: «встречу с Марией»",
        },
        "when_text": {
            "type": "string",
            "description": "Когда сейчас стоит эта запись, дословно: «в субботу»",
        },
    },
    "required": ["ref"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_item",
        "description": "Создать задачу, встречу или напоминание.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": _TYPE,
                "title": {
                    "type": "string",
                    "description": (
                        "Суть записи, кратко, с заглавной, без даты/времени:"
                        " «Позвонить Ивану», «Встреча с командой»"
                    ),
                },
                "when_text": {
                    "type": "string",
                    "description": (
                        "Выражение даты/времени ДОСЛОВНО из фразы: «завтра в"
                        " 10 утра». Не вычисляй дату! Пусто, если не сказано"
                    ),
                },
                "duration_text": {
                    "type": "string",
                    "description": "Длительность дословно: «на полчаса»",
                },
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Имена людей в именительном падеже: «Иван»",
                },
                "organization": {
                    "type": "string",
                    "description": "Компания в именительном падеже: «Альфа»",
                },
                "recurrence_text": {
                    "type": "string",
                    "description": "Повтор дословно: «каждый понедельник»",
                },
            },
            "required": ["type", "title"],
        },
    },
    {
        "name": "list_items",
        "description": (
            "Показать записи за период: «что у меня сегодня», «какие встречи на неделе»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period_text": {
                    "type": "string",
                    "description": "Период дословно: «сегодня», «на следующей неделе», «в ноябре»",
                },
                "type": _TYPE,
                "status": {"type": "string", "enum": ["open", "done"]},
                "tense": _TENSE,
            },
        },
    },
    {
        "name": "search_items",
        "description": (
            "Найти записи по смыслу: «когда была встреча с Иваном», «найди задачу про договор»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Ключевые слова темы без имён и служебных слов: «презентация», «договор»"
                    ),
                },
                "person": {"type": "string", "description": "Имя человека в именительном падеже"},
                "organization": {"type": "string", "description": "Компания в именительном падеже"},
                "type": _TYPE,
                "tense": _TENSE,
                "period_text": {"type": "string", "description": "Период дословно, если указан"},
            },
        },
    },
    {
        "name": "update_item",
        "description": (
            "Изменить или перенести запись: «перенеси её на завтра», «переименуй встречу»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": _TARGET,
                "new_when_text": {
                    "type": "string",
                    "description": "Новое время дословно: «на завтра», «на 19:00», «на час позже»",
                },
                "new_title": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "complete_item",
        "description": "Отметить запись выполненной: «отметь, что я позвонил Ивану».",
        "parameters": {"type": "object", "properties": {"target": _TARGET}, "required": ["target"]},
    },
    {
        "name": "delete_item",
        "description": "Удалить или отменить запись: «удали напоминание про Ивана».",
        "parameters": {"type": "object", "properties": {"target": _TARGET}, "required": ["target"]},
    },
    {
        "name": "smalltalk",
        "description": "Приветствие, благодарность, вопрос о возможностях или фраза не о задачах.",
        "parameters": {"type": "object", "properties": {"reply": {"type": "string"}}},
    },
]


def validate_call(name: str, arguments: dict[str, Any]) -> _Args:
    model = ARG_MODELS.get(name)
    if model is None:
        raise ValueError(f"неизвестная функция {name!r}; допустимы: {', '.join(ARG_MODELS)}")
    return model.model_validate(arguments)


def _duration(text: str | None) -> int | None:
    if not text:
        return None
    import re

    from ..text import normalize

    s = normalize(text).text
    m = re.search(r"(\d+|полтора)?\s*(минут\w*|мин|час\w*)", s)
    if not m:
        return None
    n = 1.5 if m.group(1) == "полтора" else float(m.group(1) or 1)
    return int(n * (60 if m.group(2).startswith("час") else 1))


def _merge_with_phrase(
    llm: TimeSpec | None, raw_text: str, now: datetime, dates: RuleDateResolver
) -> TimeSpec | None:
    """Страховка ветки LLM (живой тест 22.10: «пара по английскому в 8 утра завтра» → нет времени).

    LLM может не вернуть when_text, вернуть только дату или только время. Правила разбирают
    исходную фразу и дополняют недостающее: дату из одного, время из другого.
    """
    rule = dates.extract(raw_text, now, "point").spec
    usable = llm is not None and llm.start is not None
    if not usable:
        return rule if rule is not None and rule.start is not None else llm
    assert llm is not None and llm.start is not None
    if rule is None or rule.start is None or rule.ambiguous:
        return llm
    if not llm.has_time and rule.has_time:
        start = llm.start.replace(hour=rule.start.hour, minute=rule.start.minute)
        return llm.model_copy(update={"start": start, "has_time": True, "ambiguous": False})
    if llm.has_time and not llm.has_date and rule.has_date:
        start = rule.start.replace(hour=llm.start.hour, minute=llm.start.minute)
        return llm.model_copy(update={"start": start, "has_date": True})
    return llm


def _tense(
    llm: Literal["past", "future", "any"], raw_text: str, ents: EntityNormalizer
) -> Literal["past", "future", "any"]:
    """Страховка: модель оставила «any», а в реплике есть явный маркер времени
    («старую встречу», «прошедшие», «будущие») — берём его по правилам."""
    return llm if llm != "any" else ents.tense(raw_text)


def to_command(
    name: str,
    args: _Args,
    raw_text: str,
    now: datetime,
    dates: RuleDateResolver,
    ents: EntityNormalizer,
) -> Command:
    intent = INTENT_BY_TOOL[name]
    slots = Slots()

    def spec(text: str | None, mode: Literal["point", "period"]) -> TimeSpec | None:
        if not text:
            return None
        return dates.resolve(text, now, mode)

    def people(names: list[str]) -> list[str]:
        return [ents.normalize_person(n) for n in names if n.strip()]

    def target(t: TargetArgs) -> tuple[ItemRef, Slots]:
        desc = t.description
        ls = ents.lemmas(desc or "")
        ts = Slots(
            when=spec(t.when_text, "period"),
            participants=ents.people(desc or "", ls),
            organization=_first(ents.organizations_in(desc or "", ls)),
            query=ents.expand_query(desc or "", lemmas=ls),
        )
        typ = ents.item_type(desc or "", ls)
        ts.type = typ if typ in (ItemType.MEETING, ItemType.REMINDER) else None
        if t.ref in ("latest", "all"):  # метки для диалога, как у правил (router.MARK_*)
            ts.type = typ
            return ItemRef(description="__last__" if t.ref == "latest" else "__all__"), ts
        return ItemRef(pronoun=t.ref == "last", description=desc), ts

    if isinstance(args, CreateArgs):
        rec = dates.extract(args.recurrence_text, now).recurrence if args.recurrence_text else None
        when = _merge_with_phrase(spec(args.when_text, "point"), raw_text, now, dates)
        if rec is None and args.when_text:
            rec = dates.extract(args.when_text, now).recurrence
        slots = Slots(
            type=ItemType(args.type),
            title=sanitize_title(args.title) or None,
            when=when,
            duration_min=_duration(args.duration_text),
            participants=people(args.participants),
            organization=args.organization,
            recurrence=rec,
        )
    elif isinstance(args, ListArgs):
        slots = Slots(
            type=ItemType(args.type) if args.type else None,
            when=spec(args.period_text, "period"),
            status=ItemStatus(args.status) if args.status else None,
            tense=_tense(args.tense, raw_text, ents),
        )
    elif isinstance(args, SearchArgs):
        q = ents.expand_query(args.query) if args.query else None
        slots = Slots(
            type=ItemType(args.type) if args.type in ("meeting", "reminder") else None,
            query=q,
            participants=people([args.person] if args.person else []),
            organization=args.organization,
            tense=_tense(args.tense, raw_text, ents),
            when=spec(args.period_text, "period"),
        )
    elif isinstance(args, UpdateArgs):
        ref, slots = target(args.target)
        slots.target = ref
        slots.new_title = args.new_title
        if args.new_when_text:
            nd = dates.extract(args.new_when_text, now, "point")
            nw = nd.spec
            if nw is None:
                shift = parse_shift(args.new_when_text)
                nw = TimeSpec(text=args.new_when_text, ambiguous=shift is None)
            elif not nd.has_date and nw.has_time and slots.when and slots.when.start and nw.start:
                nw = nw.model_copy(
                    update={
                        "start": slots.when.start.replace(
                            hour=nw.start.hour, minute=nw.start.minute
                        ),
                        "has_date": True,
                    }
                )
            slots.new_when = nw
    elif isinstance(args, TargetOnlyArgs):
        ref, slots = target(args.target)
        slots.target = ref
        if intent == Intent.COMPLETE:
            slots.status = ItemStatus.DONE
    return Command(intent=intent, slots=slots, confidence=0.85, source="llm", raw_text=raw_text)

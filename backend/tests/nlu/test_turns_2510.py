"""Правило 25.10 «продолжение или новая команда»: таблица «состояние × тип фразы».

NLU размечает реплику (turns.classify_turn) и никогда не создаёт запись из продолжения;
применяет продолжение к фокусу диалог (чат 2). «Сейчас» — ср 21.10.2026 14:00.
"""

from __future__ import annotations

import asyncio

import pytest

from app.contracts import Command, DialogState, Intent, ItemRef, Slots, TimeSpec
from app.nlu.turns import classify_turn

from .conftest import NOW, dt, parse, state


def turn(nlu, text: str, st: DialogState) -> tuple[Command, str]:
    c = asyncio.run(nlu.parse(text, st, NOW))
    ctx = nlu.context.answer(c.raw_text, st, NOW) is not None
    return c, classify_turn(c, st, NOW, nlu.dates, nlu.ents, from_context=ctx)


def after(nlu, first: str) -> DialogState:
    return state(last_command=parse(nlu, first))


# ---------- состояния ----------

CREATED = "поставь встречу с Олегом в пятницу в одиннадцать"
LISTED = "что у меня завтра"
SEARCHED = "найди всё про ТЗ"


def awaiting_time() -> DialogState:
    pend = Command(
        intent=Intent.CREATE,
        slots=Slots(
            title="Встреча с Иваном",
            when=TimeSpec(text="завтра", start=dt(2026, 10, 22), has_date=True),
        ),
        source="rules",
        raw_text="поставь встречу с Иваном завтра",
    )
    return state(pending=pend, awaiting="time")


def awaiting_move() -> DialogState:
    pend = Command(
        intent=Intent.UPDATE,
        slots=Slots(
            target=ItemRef(description="встречу"),
            new_when=TimeSpec(
                text="на воскресенье в 12", start=dt(2026, 10, 25, 12), has_time=True, has_date=True
            ),
        ),
        source="rules",
        raw_text="перенеси встречу на воскресенье в 12",
    )
    return state(pending=pend, awaiting="confirm_action")


def awaiting_delete() -> DialogState:
    pend = Command(
        intent=Intent.DELETE,
        slots=Slots(target=ItemRef(description="встречу с Анной")),
        source="rules",
        raw_text="удали встречу с Анной",
    )
    return state(pending=pend, awaiting="confirm_delete")


# ---------- 1. полная команда — всегда новая ----------


@pytest.mark.parametrize("first", [None, CREATED, LISTED, SEARCHED])
@pytest.mark.parametrize(
    "text,intent",
    [
        ("Что у меня в субботу?", Intent.LIST),
        ("что у меня сегодня?", Intent.LIST),
        ("а что было на прошлой неделе?", Intent.LIST),
        ("найди созвон с Орбитой", Intent.SEARCH),
        ("поставь встречу с Анной в понедельник в 10", Intent.CREATE),
    ],
)
def test_full_command_is_new(nlu, first: str | None, text: str, intent: Intent) -> None:
    st = after(nlu, first) if first else state()
    c, kind = turn(nlu, text, st)
    assert kind == "command" and c.intent == intent
    if intent == Intent.LIST:
        assert c.slots.query is None  # не уточнение прошлого поиска


@pytest.mark.parametrize("st_fn", [awaiting_time, awaiting_move])
def test_full_command_breaks_waiting(nlu, st_fn) -> None:
    c, kind = turn(nlu, "что у меня завтра?", st_fn())
    assert kind == "command" and c.intent == Intent.LIST


# ---------- 2. продолжение ----------


def test_wait_time_is_filled(nlu) -> None:
    c, kind = turn(nlu, "в три", awaiting_time())
    assert kind == "continuation" and c.intent == Intent.CREATE
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 15)


@pytest.mark.parametrize("text", ["Да, 21:15", "давай в 21:15"])
def test_wait_time_with_yes(nlu, text: str) -> None:
    c, kind = turn(nlu, text, awaiting_time())
    assert kind == "continuation" and c.slots.when
    assert c.slots.when.start == dt(2026, 10, 22, 21, 15)


@pytest.mark.parametrize("text", ["нет, в 11", "нет, давай в 11", "лучше в 11"])
def test_no_time_on_move_confirm_changes_proposal(nlu, text: str) -> None:
    c, kind = turn(nlu, text, awaiting_move())
    assert kind == "continuation" and c.intent == Intent.UPDATE
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 25, 11)


@pytest.mark.parametrize(
    "text,check",
    [
        ("нет, давай в двенадцать", lambda c: c.slots.new_when.start == dt(2026, 10, 23, 12)),
        ("сделай её каждую неделю", lambda c: c.slots.recurrence == "FREQ=WEEKLY"),
        ("пусть повторяется каждую неделю", lambda c: c.slots.recurrence == "FREQ=WEEKLY"),
        (
            "давай её в понедельник каждую неделю",
            lambda c: c.slots.recurrence and c.slots.new_when.start == dt(2026, 10, 26),
        ),
        ("и с Марией", lambda c: c.slots.participants == ["Мария"]),
    ],
)
def test_after_create_changes_that_item(nlu, text: str, check) -> None:
    c, kind = turn(nlu, text, after(nlu, CREATED))
    assert kind == "continuation"
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.pronoun
    assert check(c)


@pytest.mark.parametrize(
    "first,text,intent",
    [
        (LISTED, "а послезавтра?", Intent.LIST),
        (LISTED, "а встречи?", Intent.LIST),
        (SEARCHED, "Только прошедшие", Intent.SEARCH),
        ("когда я последний раз встречался с Марией", "а следующая когда?", Intent.SEARCH),
    ],
)
def test_after_list_or_search_refines_it(nlu, first: str, text: str, intent: Intent) -> None:
    c, kind = turn(nlu, text, after(nlu, first))
    assert kind == "continuation" and c.intent == intent


def test_only_past_filter_keeps_query(nlu) -> None:
    c, _ = turn(nlu, "Только прошедшие", after(nlu, SEARCHED))
    assert c.slots.tense == "past" and c.slots.query == "техническое задание"


def test_pronoun_after_search_targets_found(nlu) -> None:
    c, _ = turn(nlu, "перенеси его на час позже", after(nlu, "найди созвон с Орбитой"))
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.pronoun


# ---------- 3. да / нет ----------


@pytest.mark.parametrize("st_fn", [awaiting_move, awaiting_delete])
def test_yes_is_confirm(nlu, st_fn) -> None:
    c, kind = turn(nlu, "да", st_fn())
    assert (c.intent, kind) == (Intent.CONFIRM, "confirm")


@pytest.mark.parametrize("st_fn", [awaiting_move, awaiting_delete, awaiting_time])
@pytest.mark.parametrize("text", ["нет", "не надо", "нет, не надо"])
def test_no_alone_is_reject(nlu, st_fn, text: str) -> None:
    c, kind = turn(nlu, text, st_fn())
    assert (c.intent, kind) == (Intent.REJECT, "reject")


def test_no_time_on_delete_is_not_a_move(nlu) -> None:
    c, _ = turn(nlu, "нет, в 11", awaiting_delete())
    assert c.intent != Intent.UPDATE


# ---------- 4. из продолжения не создаём ----------


@pytest.mark.parametrize(
    "text",
    [
        "сделай её каждую неделю",
        "пусть повторяется каждую неделю",
        "лучше в воскресенье",
        "давай её в понедельник",
        "нет, давай в двенадцать",
    ],
)
def test_continuation_never_creates(nlu, text: str) -> None:
    c, _ = turn(nlu, text, after(nlu, CREATED))
    assert c.intent != Intent.CREATE
    assert c.slots.title not in ("Её", "Лучше", "Пусть повторяется")


def test_new_create_after_create_is_command(nlu) -> None:
    c, kind = turn(
        nlu, "и напомни мне в четверг вечером подготовить вопросы для Олега", after(nlu, CREATED)
    )
    assert kind == "command" and c.intent == Intent.CREATE


def test_putting_named_person_is_command(nlu) -> None:
    c, kind = turn(nlu, "ладно, поставь Нину на шесть вечера", after(nlu, "что у меня завтра?"))
    assert kind == "command" and c.intent == Intent.CREATE


# ---------- день из списка, косвенные просьбы ----------


@pytest.mark.parametrize(
    "first,text,day",
    [
        ("Что у меня завтра?", "добавь туда купить подарок", dt(2026, 10, 22)),
        ("Что у меня в понедельник?", "запиши на этот день оплатить налоги", dt(2026, 10, 26)),
    ],
)
def test_create_on_listed_day(nlu, first: str, text: str, day) -> None:
    c, kind = turn(nlu, text, after(nlu, first))
    assert kind == "command" and c.intent == Intent.CREATE
    assert c.slots.when and c.slots.when.start == day
    assert c.slots.title in ("Купить подарок", "Оплатить налоги")


def test_bare_time_after_create_is_continuation(nlu) -> None:
    c, kind = turn(nlu, "15:00", after(nlu, "через неделю сдать тестовое на хакатон, напомни"))
    assert kind == "continuation" and c.intent == Intent.UPDATE
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 28, 15)


@pytest.mark.parametrize(
    "text",
    [
        "было бы здорово если бы ты напомнил мне через час про обед",
        "я бы хотел чтобы ты мне напомнил завтра утром про лекарства",
    ],
)
def test_wish_is_create(nlu, text: str) -> None:
    c, kind = turn(nlu, text, state())
    assert kind == "command" and c.intent == Intent.CREATE and c.slots.when


@pytest.mark.parametrize("text", ["раз два три проверка связи", "сколько будет два плюс два"])
def test_nonsense_not_search(nlu, text: str) -> None:
    assert parse(nlu, text).intent in (Intent.SMALLTALK, Intent.UNKNOWN)


def test_gift_title_not_cut(nlu) -> None:
    assert parse(nlu, "поставь задачу купить подарок").slots.title == "Купить подарок"

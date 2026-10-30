"""Логи стенда 23.10 (49 реплик, main@73ab5d6): каждая фраза из разбора чата 0 — тест.

«Сейчас» — ср 21.10.2026 14:00 (как в golden_eval), поэтому «31» — 31 октября.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.contracts import Intent, ItemType
from app.nlu import parse_shift

from .conftest import NOW, dt, parse, state

THRESHOLD = 0.85
TODAY = dt(2026, 10, 21)

# ---------- 1. «все мои дела» — всё предстоящее ----------


@pytest.mark.parametrize(
    "text,type_",
    [
        ("покажи все мои дела", None),
        ("все задачи", ItemType.TASK),
        ("покажи вообще все", None),
        ("все что будет", None),
        ("покажи все встречи", ItemType.MEETING),
        ("что будет", None),
    ],
)
def test_all_upcoming(nlu, text: str, type_: ItemType | None) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.LIST and c.slots.type == type_
    w = c.slots.when
    assert w and w.is_period and w.start == TODAY and w.end == TODAY + timedelta(days=14)
    assert c.confidence >= THRESHOLD


def test_all_with_period_keeps_period(nlu) -> None:
    c = parse(nlu, "Покажи все встречи в ноябре")  # golden q18 — не «предстоящие 14 дней»
    assert c.slots.when and c.slots.when.start == dt(2026, 11, 1)


# ---------- 2. вежливая форма команды ----------


def test_polite_move(nlu) -> None:
    c = parse(nlu, "Можешь перенести, пожалуйста, встречу с Иваном на 12:00?")
    assert c.intent == Intent.UPDATE and c.slots.participants == ["Иван"]
    assert c.slots.new_when and c.slots.new_when.has_time and c.slots.new_when.start.hour == 12


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Можно удалить встречу с Еленой?", Intent.DELETE),
        ("не мог бы ты поставить созвон с Альфой завтра в 10?", Intent.CREATE),
        ("можешь найти договор с Максимом?", Intent.SEARCH),
        ("Будь добр, отметь, что я позвонил Ивану", Intent.COMPLETE),
    ],
)
def test_polite_other_verbs(nlu, text: str, intent: Intent) -> None:
    assert parse(nlu, text).intent == intent


# ---------- 3. вопрос с «что будет» — не создание ----------


@pytest.mark.parametrize(
    "text,start",
    [("на неделе что будет", dt(2026, 10, 19)), ("завтра что будет", dt(2026, 10, 22))],
)
def test_what_will_be_is_list(nlu, text: str, start: Any) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.LIST and c.slots.when and c.slots.when.start == start


# ---------- 4. «эту же / ту же / её» — последняя запись диалога ----------


@pytest.mark.parametrize(
    "text",
    [
        "перенеси эту же задачу еще на час позже",
        "перенеси ту же встречу ещё на час позже",
        "перенеси её же на час позже",
    ],
)
def test_same_item_pronoun(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.UPDATE
    assert c.slots.target and c.slots.target.pronoun
    assert c.slots.new_when and parse_shift(c.slots.new_when.text) == 60


# ---------- 5. последний день месяца, «на 31», «31-го», «31 числа» ----------


@pytest.mark.parametrize(
    "text,day",
    [
        ("в последний день месяца сдать отчёт", dt(2026, 10, 31)),
        ("в последний день следующего месяца сдать отчёт", dt(2026, 11, 30)),
        ("на 31 сдать отчёт", dt(2026, 10, 31)),
        ("31 числа сдать отчёт", dt(2026, 10, 31)),
        ("сдать отчёт 31-го", dt(2026, 10, 31)),
        ("сдать отчёт к 28", dt(2026, 10, 28, 23, 59)),
    ],
)
def test_day_of_month(nlu, text: str, day: Any) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.title == "Сдать отчёт"
    assert c.slots.when and c.slots.when.start == day


def test_on_19_in_update_is_still_time(nlu) -> None:  # QA-02 не сломан: «на 19» — час
    c = parse(nlu, "Перенеси встречу с Марией в субботу на 19")
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 24, 19)


# ---------- 6. чистые названия ----------


@pytest.mark.parametrize(
    "text,intent,type_,title",
    [
        (
            "нам будет сдать тестовое на хакатон, напомни пж",
            Intent.CREATE,
            ItemType.REMINDER,
            "Сдать тестовое на хакатон",
        ),
        (
            "задачу поставить починить часы перенеси её",
            Intent.CREATE,
            ItemType.TASK,
            "Починить часы",
        ),
        (
            "На 31 зарегистрировать команду",
            Intent.CREATE,
            ItemType.TASK,
            "Зарегистрировать команду",
        ),
        ("поставь, плиз, задачу купить хлеб", Intent.CREATE, ItemType.TASK, "Купить хлеб"),
    ],
)
def test_clean_titles(nlu, text: str, intent: Intent, type_: ItemType, title: str) -> None:
    c = parse(nlu, text)
    assert (c.intent, c.slots.type, c.slots.title) == (intent, type_, title)


def test_registration_on_31(nlu) -> None:
    c = parse(nlu, "На 31 зарегистрировать команду")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 31)


# ---------- 7. длительность и конец ----------


@pytest.mark.parametrize(
    "text,start,end,minutes",
    [
        (
            "встреча завтра с 13:30 до 15:30",
            dt(2026, 10, 22, 13, 30),
            dt(2026, 10, 22, 15, 30),
            120,
        ),
        ("встреча завтра в 10 на два часа", dt(2026, 10, 22, 10), dt(2026, 10, 22, 12), 120),
        (
            "встреча завтра в пол первого на два часа",
            dt(2026, 10, 22, 12, 30),
            dt(2026, 10, 22, 14, 30),
            120,
        ),
        (
            "встреча 5 ноября в 14:30 на полтора часа",
            dt(2026, 11, 5, 14, 30),
            dt(2026, 11, 5, 16),
            90,
        ),
    ],
)
def test_duration_and_end(nlu, text: str, start: Any, end: Any, minutes: int) -> None:
    c = parse(nlu, text)
    w = c.slots.when
    assert w and (w.start, w.end) == (start, end)
    assert c.slots.duration_min == minutes


@pytest.mark.parametrize(
    "text,start",
    [("встреча на 3 дня", dt(2026, 10, 21, 15)), ("встреча завтра в 3 дня", dt(2026, 10, 22, 15))],
)
def test_three_of_day_regression(nlu, text: str, start: Any) -> None:
    # «в 3 дня» = 15:00 — разбиралось верно, страж (не путать с длительностью «3 дня»)
    c = parse(nlu, text)
    assert c.slots.when and c.slots.when.start == start


# ---------- 8. «задачу» сказано — значит задача ----------


@pytest.mark.parametrize(
    "text",
    [
        "поставь задачу на вчера созвониться с Иваном",
        "добавь задачу провести планёрку в пятницу",
        "создай задачу встреча с юристом",
    ],
)
def test_explicit_task_wins_over_meeting_words(nlu, text: str) -> None:
    assert parse(nlu, text).slots.type == ItemType.TASK


def test_meeting_without_explicit_task_still_meeting(nlu) -> None:
    assert parse(nlu, "поставь созвон с Иваном завтра в 10").slots.type == ItemType.MEETING


# ---------- 9. опечатки недели, «скоро» ----------


def test_next_week_typo(nlu) -> None:
    c = parse(nlu, "что у меня на следующий неделе")
    assert c.intent == Intent.LIST and c.slots.when
    assert (c.slots.when.start, c.slots.when.end) == (dt(2026, 10, 26), dt(2026, 11, 2))


@pytest.mark.parametrize("text", ["что у меня скоро", "что у меня в ближайшее время"])
def test_soon_is_upcoming_days(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.LIST and c.slots.when and c.slots.when.is_period
    assert c.slots.when.start == TODAY and c.slots.when.end == TODAY + timedelta(days=7)


# ---------- 10. вопросы о возможностях — справка ----------


@pytest.mark.parametrize(
    "text",
    [
        "ты можешь записывать встречи?",
        "что ты умеешь?",
        "а ты умеешь напоминать?",
        "можешь переносить задачи?",
        "как тобой пользоваться",
    ],
)
def test_capability_questions_are_help(nlu, text: str) -> None:
    # интента help в контракте v1.2 нет: smalltalk — диалог отвечает списком умений
    assert parse(nlu, text).intent == Intent.SMALLTALK


def test_capability_vs_command(nlu) -> None:
    # совершенный вид — это просьба сделать, а не вопрос «умеешь ли»
    c = parse(nlu, "ты можешь записать встречу с Анной на завтра в 12?")
    assert c.intent == Intent.CREATE and c.slots.when
    assert c.slots.when.start == dt(2026, 10, 22, 12)


def test_now_fixture_sane() -> None:
    assert NOW.isoformat().startswith("2026-10-21T14:00") and state() is not None

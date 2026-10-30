"""Регрессия по живому тесту 23.10 (main@35252ad, STORAGE_IMPL=postgres).

1. «в часов 15» = «часов в 15» = 15:00;
2. слова времени («часов», «в районе», «около») не попадают в название;
3. «старую / прошлую / прошедшую встречу» — фильтр на прошлое, а не текстовый запрос;
4. мусорная фраза без сущностей в фолбэке → unknown (переспрос); мат и «, ,» — не в название;
5. остатки времени или даты в названии → уверенность ниже порога (решает LLM).
"""

from __future__ import annotations

import pytest

from app.contracts import Intent, ItemType
from app.nlu.llm.tools import CreateArgs, to_command
from app.nlu.router import sanitize_title

from .conftest import NOW, dt, parse

THRESHOLD = 0.85

# ---------- 1–2. разговорное время, чистое название ----------


@pytest.mark.parametrize(
    "text",
    [
        "Поставь встречу на завтра в часов 15",
        "Поставь встречу на завтра часов в 15",
        "Поставь встречу на завтра в районе 15",
        "Поставь встречу на завтра в районе 15 часов",
        "Поставь встречу завтра около 15 часов",
        "Поставь встречу завтра где-то в 15",
    ],
)
def test_colloquial_hour_and_clean_title(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    w = c.slots.when
    assert w and w.has_time and w.start == dt(2026, 10, 22, 15)
    assert c.slots.title == "Встреча"
    assert c.confidence >= THRESHOLD


def test_colloquial_hour_with_counterpart(nlu) -> None:
    c = parse(nlu, "Поставь созвон с Альфой на завтра часов в 11")
    assert c.slots.title == "Созвон с Альфой"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 11)


# ---------- 3. «старую» — фильтр на прошлое ----------


@pytest.mark.parametrize(
    "text,tense",
    [
        ("Найди встречу, старую", "past"),
        ("Старую встречу", "past"),
        ("найди прошедшие созвоны с Альфой", "past"),
        ("покажи давнюю задачу про договор", "past"),
        ("найди предстоящую встречу с Иваном", "future"),
    ],
)
def test_old_new_adjectives_are_tense(nlu, text: str, tense: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.SEARCH and c.slots.tense == tense
    q = c.slots.query or ""
    assert not any(w in q for w in ("стар", "прошедш", "давн", "предстоящ"))


def test_old_meeting_keeps_type_and_other_filters(nlu) -> None:
    c = parse(nlu, "найди старую встречу с Иваном по договору")
    assert c.slots.type == ItemType.MEETING and c.slots.participants == ["Иван"]
    assert c.slots.query == "договор" and c.slots.tense == "past"


# ---------- 4. мусор: unknown вместо поиска по «после дождичек» ----------


@pytest.mark.parametrize(
    "text",
    [
        "Привет, мне встречу, после дождичка, блядь, ещё что-нибудь",
        "ну это самое, ещё что-нибудь",
        "мне встречу",
    ],
)
def test_garbage_without_entities_is_unknown(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.UNKNOWN, c.slots


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("Встреча, , с Иваном", "Встреча, с Иваном"),
        ("Встреча с Иваном, блядь", "Встреча с Иваном"),
        ("Встреча часов", "Встреча"),
        ("в районе встреча", "Встреча"),
        (", , ,", ""),
        ("сука", ""),
        ("Купить 2 пакета молока", "Купить 2 пакета молока"),
    ],
)
def test_sanitize_title(raw: str, clean: str) -> None:
    assert sanitize_title(raw) == clean


def test_profanity_never_in_title_rules(nlu) -> None:
    c = parse(nlu, "поставь, блядь, встречу с Иваном завтра в 15")
    assert c.intent == Intent.CREATE
    assert c.slots.title == "Встреча с Иваном"


def test_profanity_never_in_title_llm(nlu) -> None:
    args = CreateArgs(type="meeting", title="встреча с Иваном, бля", when_text="завтра в 15")
    c = to_command("create_item", args, "встреча с Иваном завтра в 15", NOW, nlu.dates, nlu.ents)
    assert c.slots.title == "Встреча с Иваном"


def test_old_meeting_search_is_not_emptied(nlu) -> None:
    # «Старую встречу» — фильтр (tense), а не пустой поиск: unknown здесь был бы ошибкой
    assert parse(nlu, "Старую встречу").intent == Intent.SEARCH


# ---------- 5. время в названии → решает LLM ----------


@pytest.mark.parametrize(
    "text",
    [
        "поставь встречу на завтра в 15 часов вечера утром",  # лишнее время осталось в названии
        "добавь задачу созвониться в понедельник или во вторник",
    ],
)
def test_time_words_in_title_lower_confidence(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE
    assert c.confidence < THRESHOLD, c.slots.title


@pytest.mark.parametrize(
    "text",
    ["поставь задачу купить 2 пакета молока завтра", "Добавь встречу с командой завтра в 15:00"],
)
def test_numbers_are_not_time_words(nlu, text: str) -> None:
    assert parse(nlu, text).confidence >= THRESHOLD

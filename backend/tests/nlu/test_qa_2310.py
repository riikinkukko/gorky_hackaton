"""Находки QA 23.10 (qa/prompts/2026-10-23_chat1_nlu.md) на уровне NLU: QA-02, 04, 05, 18.

Сквозные версии — backend/tests/e2e (зона чата 7). Здесь — быстрые проверки слотов и
отрицательные случаи, чтобы правки не расползались.
"""

from __future__ import annotations

import pytest

from app.contracts import Intent, ItemType
from app.nlu import AsrNormalizer

from .conftest import dt, parse

# ---------- QA-02: «на 19» без минут при переносе ----------


@pytest.mark.parametrize(
    "text",
    [
        "Перенеси встречу с Марией в субботу на 19.",
        "Перенеси встречу с Марией в субботу на 19",
        "Перенеси встречу с Марией в субботу на 19 часов",
        "Перенеси встречу с Марией в субботу на семь вечера",
    ],
)
def test_update_hour_without_minutes(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.UPDATE and c.slots.participants == ["Мария"]
    nw = c.slots.new_when
    assert nw and nw.has_time and nw.start == dt(2026, 10, 24, 19)


# ---------- QA-04: аббревиатуры по буквам ----------


@pytest.mark.parametrize(
    "text,title",
    [
        ("Послезавтра в девять созвон с альфой по СРМ.", "Созвон с Альфой по CRM"),
        ("послезавтра в девять созвон с альфой по срм", "Созвон с Альфой по CRM"),
        ("послезавтра в девять созвон с альфой по си ар эм", "Созвон с Альфой по CRM"),
        ("добавь задачу подготовить тэзэ для вектора", "Подготовить ТЗ для Вектора"),
        ("Добавь задачу подготовить т з для Вектора.", "Подготовить ТЗ для Вектора"),
        ("добавь задачу отправить ка пэ Ольге", "Отправить КП Ольге"),
        ("напомни завтра в 10 отправить эс эм эс Ивану", "Отправить SMS Ивану"),
    ],
)
def test_spelled_abbreviation_in_title(nlu, text: str, title: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.title == title


def test_abbreviation_still_expands_in_search(nlu) -> None:
    assert parse(nlu, "найди всё про тэзэ").slots.query == "техническое задание"
    assert parse(nlu, "какие были напоминалки про срм").slots.query == "CRM"


@pytest.mark.parametrize(
    "text", ["купить трикотаж и тазик", "встреча в ТЗ-отделе", "смска от Ивана", "тзинь"]
)
def test_abbreviation_does_not_touch_words(text: str) -> None:
    # аббревиатура — только отдельными буквами или целым словом; части слов не трогаем
    assert AsrNormalizer().normalize(text).text == text


# ---------- QA-05: организация в именительном после «с» ----------


@pytest.mark.parametrize(
    "text,org",
    [
        ("Когда ближайший созвон с Альфа?", "Альфа"),
        ("когда ближайший созвон с альфа", "Альфа"),
        ("найди встречи с Вектор", "Вектор"),
        ("созвон с Орбита когда", "Орбита"),
    ],
)
def test_org_nominative_after_s(nlu, text: str, org: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.SEARCH
    assert c.slots.organization == org and c.slots.participants == []


@pytest.mark.parametrize(
    "text,person", [("встреча с Максимом", "Максим"), ("позвонить Анне", "Анна")]
)
def test_people_stay_people(nlu, text: str, person: str) -> None:
    c = parse(nlu, text)
    assert c.slots.participants == [person] and c.slots.organization is None


# ---------- QA-18: обрезанное «Перенеси…» ----------


@pytest.mark.parametrize(
    "text,new_start",
    [
        ("Неси встречу на субботу", dt(2026, 10, 24)),
        ("еси её на субботу", dt(2026, 10, 24)),
        ("Неси её на субботу", dt(2026, 10, 24)),
        ("неси встречу на субботу в 15", dt(2026, 10, 24, 15)),
        ("реси задачу на завтра", dt(2026, 10, 22)),
    ],
)
def test_truncated_move_is_update_with_low_confidence(nlu, text: str, new_start) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.UPDATE  # не создание «Неси встречу»
    assert c.confidence < 0.85  # диалог переспросит (SAFE_CONFIDENCE), а не выполнит молча
    assert c.slots.new_when and c.slots.new_when.start == new_start
    assert c.raw_text == text  # в логе и диалоге — то, что реально распознано


def test_truncated_move_keeps_meeting_type(nlu) -> None:
    assert parse(nlu, "Неси встречу на субботу").slots.type == ItemType.MEETING


@pytest.mark.parametrize("text", ["неси хлеб", "Перенеси встречу на субботу"])
def test_not_a_truncated_move(nlu, text: str) -> None:
    c = parse(nlu, text)
    if text.startswith("Перенеси"):
        assert c.intent == Intent.UPDATE and c.confidence >= 0.85  # полная фраза — как раньше
    else:
        assert c.intent != Intent.UPDATE


# ---------- QA-20: перечисление («и») против выбора («или») ----------


@pytest.mark.parametrize(
    "text,sep",
    [
        ("Две пары: одну пару в восемь, вторую в 14:40", " и "),
        ("Завтра две пары: в восемь и в 14:40", " и "),
        (
            "Мне надо завтра встретиться с Анной, может быть, в 5 часов вечера, может быть, в 6:00",
            " или ",
        ),
    ],
)
def test_enumeration_vs_choice(nlu, text: str, sep: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.when and c.slots.when.ambiguous
    assert sep in c.slots.when.text

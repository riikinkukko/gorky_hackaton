from __future__ import annotations

import pytest

from app.contracts import ItemType
from app.nlu.entities import default_normalizer

N = default_normalizer()


@pytest.mark.parametrize(
    "word,lemma",
    [
        ("Иваном", "Иван"),
        ("Ивану", "Иван"),
        ("ивану", "Иван"),
        ("Марией", "Мария"),
        ("Натальей", "Наталья"),
        ("Петром", "Пётр"),
        ("Сергею", "Сергей"),
    ],
)
def test_person_lemma(word: str, lemma: str) -> None:
    assert N.normalize_person(word) == lemma


def test_people_in_broken_data_titles() -> None:
    # падежи в данных организаторов сломаны: «Позвонить Иваном», «Подготовить вопросы для Натальей»
    assert N.people("Позвонить Иваном") == ["Иван"]
    assert N.people("Подготовить вопросы для Натальей") == ["Наталья"]
    assert N.people("Напомнить Анной про CRM") == ["Анна"]


def test_homonyms_need_capital() -> None:
    assert N.people("купить роман в магазине") == []
    assert N.people("встреча с Верой") == ["Вера"]


def test_unknown_word_not_name() -> None:
    assert N.people("что было по хакатону") == []


@pytest.mark.parametrize(
    "text,org",
    [
        ("Созвон с Стартом", "Старт"),
        ("созвоны со Стартом", "Старт"),
        ("встреча с Альфой", "Альфа"),
        ("для Вектора", "Вектор"),
        ("с Партнёром", "Партнёр"),
        ("с партнером", "Партнёр"),
    ],
)
def test_organizations(text: str, org: str) -> None:
    assert N.organizations_in(text) == [org]


def test_org_needs_context() -> None:
    assert N.organizations_in("пульс участился") == []


@pytest.mark.parametrize(
    "text,query",
    [
        ("Найди всё про ТЗ", "техническое задание"),
        ("найди демо по мобилке", "демо мобильное приложение"),
        ("планёрки по деньгам", "планёрка бюджет смета"),
        ("найди все КП", "коммерческое предложение"),
        ("напоминалки про си ар эм", "CRM"),
        ("когда звонил Сергею", "позвонить"),
    ],
)
def test_expand_query(text: str, query: str) -> None:
    assert N.expand_query(text) == query


@pytest.mark.parametrize(
    "text,tense",
    [
        ("Когда у меня была встреча", "past"),
        ("когда я последний раз звонил", "past"),
        ("я когда-то ставил задачу", "past"),
        ("когда следующая встреча", "future"),
        ("когда ближайший созвон", "future"),
        ("найди задачу про договор", "any"),
        ("обновить материалы по сайту", "any"),
    ],
)
def test_tense(text: str, tense: str) -> None:
    assert N.tense(text) == tense


@pytest.mark.parametrize(
    "text,typ",
    [
        ("демо по мобилке", ItemType.MEETING),
        ("напоминалки про CRM", ItemType.REMINDER),
        ("задачи на завтра", ItemType.TASK),
        ("подготовиться к встрече", None),
    ],
)
def test_item_type(text: str, typ: ItemType | None) -> None:
    assert N.item_type(text) == typ

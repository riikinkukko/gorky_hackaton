"""Нормализация текста для поиска. Без БД."""

import pytest

from app.storage.text import (
    edit_distance,
    extract_entities,
    fuzzy_ok,
    lemma,
    lemmas,
    lexicon,
    norm_person,
    query_group,
    search_text,
)


@pytest.mark.parametrize("form", ["Иваном", "Ивану", "ивана", "Иван"])
def test_person_forms(form):
    assert norm_person(form) == "иван"


def test_pymorphy_mistakes_are_overridden():
    assert lemma("Анне") == "анна"  # pymorphy3 даёт «анне»
    assert lemma("аналитике") == "аналитика"  # pymorphy3 даёт «аналитик»
    assert lemma("демо") == "демо"


def test_verbs_from_synonyms_yml():
    assert lemma("звонил") == lemma("Позвонить") == "позвонить"
    assert lemma("готовился") == lemma("Подготовиться") == "подготовиться"
    assert lemma("скинуть") == "отправить"
    assert lemma("напоминалки") == lemma("напоминай") == lemma("Напомнить")


def test_phrases_and_synonyms():
    st = search_text("Проверить смету по мобильному приложению").split()
    assert "мобильное_приложение" in st and "мобилка" in st and "смета" in st
    assert "тз" in search_text("Подготовить техническое задание").split()
    assert "встреча" not in search_text("Отправить итоги встречи Ивану").split()  # фраза


def test_expand_is_query_only():
    assert {"бюджет", "смета"} <= query_group("деньги")
    assert "деньги" not in search_text("Обсудить бюджет").split()


def test_lexicon_loaded():
    lex = lexicon()
    assert lex.source.endswith("synonyms.yml") and lex.types["созвон"] == "meeting"


@pytest.mark.parametrize(
    "q, expect",
    [
        ("найди всё про тэ зэ", ["тз"]),
        ("напоминалки про си ар эм", ["напомнить", "crm"]),
    ],
)
def test_spoken_abbreviations(q, expect):
    assert lemmas(q) == expect


@pytest.mark.parametrize(
    "q, persons, orgs",
    [
        ("когда у меня была встреча с и ваном", ["Иван"], []),
        ("созвон с арбитой когда", [], ["Орбита"]),
        ("созвоны со стартом", [], ["Старт"]),
        ("Найди встречу с Петром", ["Пётр"], []),
        ("найди презинтацию для максима", ["Максим"], []),
        ("старт проекта в понедельник", [], []),  # строчное «старт» без предлога — не компания
    ],
)
def test_entities(q, persons, orgs):
    e = extract_entities(q)
    assert (e.persons, e.organizations) == (persons, orgs)


def test_fuzzy():
    assert edit_distance("арбита", "орбита") == 1
    assert fuzzy_ok("презинтация", "презентация")
    assert not fuzzy_ok("сайт", "сайты")  # короткие слова — без нечёткости
    assert not fuzzy_ok("проверить", "провести")

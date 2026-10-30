"""Обогащение CSV: шаблоны, исправление падежей, вывод типа. Без БД."""

import csv
from collections import Counter
from pathlib import Path

import pytest

from app.contracts import ItemType
from app.storage.enrich import enrich

CSV = Path(__file__).resolve().parents[3] / "data" / "voice_assistant_events_1000.csv"


@pytest.mark.parametrize(
    "src, title, typ, person, org, topic",
    [
        ("Позвонить Иваном", "Позвонить Ивану", ItemType.TASK, ["Иван"], [], None),
        (
            "Отправить договор Марией",
            "Отправить договор Марии",
            ItemType.TASK,
            ["Мария"],
            [],
            "договор",
        ),
        (
            "Напомнить Анной про CRM",
            "Напомнить Анне про CRM",
            ItemType.REMINDER,
            ["Анна"],
            [],
            "CRM",
        ),
        (
            "Напомнить Дмитрием про мобильному приложению",
            "Напомнить Дмитрию про мобильное приложение",
            ItemType.REMINDER,
            ["Дмитрий"],
            [],
            "мобильное приложение",
        ),
        (
            "Подготовить вопросы для Натальей",
            "Подготовить вопросы для Натальи",
            ItemType.TASK,
            ["Наталья"],
            [],
            None,
        ),
        (
            "Обсудить договору с Алексеем",
            "Обсудить договор с Алексеем",
            ItemType.TASK,
            ["Алексей"],
            [],
            "договор",
        ),
        (
            "Обсудить аналитике с Еленой",
            "Обсудить аналитику с Еленой",
            ItemType.TASK,
            ["Елена"],
            [],
            "аналитика",
        ),
        (
            "Созвон с Стартом по CRM",
            "Созвон со Стартом по CRM",
            ItemType.MEETING,
            [],
            ["Старт"],
            "CRM",
        ),
        (
            "Подготовиться к встрече с Стартом",
            "Подготовиться к встрече со Стартом",
            ItemType.TASK,
            [],
            ["Старт"],
            None,
        ),
        ("Обсудить бюджет по бюджету", "Обсудить бюджет", ItemType.TASK, [], [], "бюджет"),
        (
            "Встреча с Иваном по презентации",
            "Встреча с Иваном по презентации",
            ItemType.MEETING,
            ["Иван"],
            [],
            "презентация",
        ),
        (
            "Провести планёрку по сайту",
            "Провести планёрку по сайту",
            ItemType.MEETING,
            [],
            [],
            "сайт",
        ),
    ],
)
def test_templates(src, title, typ, person, org, topic):
    e = enrich(src)
    assert (e.title, e.type, e.person, e.organization, e.topic) == (title, typ, person, org, topic)
    assert e.title_original == src
    assert (e.text_issue is None) == (src == title)


def test_demo_is_short_meeting():
    e = enrich("Демо проекта по мобильному приложению")
    assert e.type is ItemType.MEETING and e.duration_min == 30


def test_unknown_text_falls_back():
    e = enrich("Встреча с командой по новому продукту в офисе")
    assert e.type is ItemType.MEETING and e.title == e.title_original


@pytest.mark.skipif(not CSV.exists(), reason="нет CSV организаторов")
def test_whole_dataset_matches_analysis():
    """Цифры из «01_Анализ…»: 761/186/53 и 313 строк с исправлениями."""
    with CSV.open(encoding="utf-8-sig") as f:
        rows = [enrich(r["text"]) for r in csv.DictReader(f)]
    assert Counter(r.type for r in rows) == {
        ItemType.TASK: 761,
        ItemType.MEETING: 186,
        ItemType.REMINDER: 53,
    }
    assert sum(r.text_issue is not None for r in rows) == 313
    # после исправления в заголовках не осталось творительного падежа после глаголов-шаблонов
    bad = [
        r.title
        for r in rows
        if r.title.startswith(("Позвонить", "Отправить"))
        and r.title.endswith(("ом", "ем", "ой", "ей"))
    ]
    assert not bad, bad[:5]

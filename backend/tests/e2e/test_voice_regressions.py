"""Регрессия голосовых ошибок: транскрипт (как его пишет распознавание) → ожидаемая команда.

Два вида тестов:
* «держится» — транскрипт с типовой ошибкой распознавания, которую система уже переживает.
  Если тест упал — что-то сломали.
* ``xfail`` — подтверждённая находка QA (номер QA-xx, владелец, qa/REPORT.md). Нестрогий, чтобы
  починка в чужой ветке не роняла CI: когда владелец починит, тест покажет XPASS
  (``pytest tests/e2e -rxX``), и чат 7 снимет маркер — с этого момента это обычный тест-страж.

Транскрипты взяты из прогонов qa/run_voice.py. Пока живых записей нет, часть — симуляция
типовых ошибок GigaAM (qa/sim/hyp_v1.csv); дефект понимания в каждом случае воспроизводится
текстом, независимо от того, откуда пришёл транскрипт.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import say


def known(qa: str, owner: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=False, reason=f"{qa} ({owner}): {what}")


# ---------- держится: ошибки распознавания, которые уже не мешают ----------


@pytest.mark.parametrize(
    "transcript",
    [
        "Добавь встречу с командой завтра в 15:00",  # эталон
        "Добавь встречу с командой завтра в 15 00.",  # GigaAM: время через пробел
        "добавь встречу с командой завтра в 15.00",
    ],
)
def test_create_time_formats(client: TestClient, transcript: str) -> None:
    t = say(client, transcript, "e2e-time")
    assert (t.intent, t.action) == ("create_item", "created")
    assert t.item["type"] == "meeting" and t.item["title"] == "Встреча с командой"
    assert t.at() == "2026-10-22T15:00"


def test_conflict_with_lowercase_org_and_spaced_time(client: TestClient) -> None:
    t = say(client, "Поставь созвон с маяком 29 октября в 15 30.", "e2e-q10")
    assert (t.intent, t.action) == ("create_item", "conflict")
    assert {i["id"] for i in t.items} >= {697, 698}


def test_conflict_time_in_words(client: TestClient) -> None:
    t = say(
        client,
        "поставь созвон с маяком двадцать девятого октября в пятнадцать тридцать",
        "e2e-q10w",
    )
    assert (t.intent, t.action) == ("create_item", "conflict")


def test_search_missing_preposition(client: TestClient) -> None:
    t = say(client, "Когда у меня была встреча Иваном?", "e2e-q19")
    assert t.intent == "search_items"
    assert t.slots.get("participants") == ["Иван"]
    assert [i["id"] for i in t.items][:1] == [530]


def test_filler_and_digit_hour(client: TestClient) -> None:
    t = say(client, "Ну короче напомни это самое завтра в 8 отправить протокол Елене.", "e2e-q63")
    assert (t.intent, t.action) == ("create_item", "created")
    assert t.item["title"] == "Отправить протокол Елене"
    assert t.at() == "2026-10-22T08:00"


def test_time_words_without(client: TestClient) -> None:
    t = say(client, "Завтра без 15 10 созвон с пульсом.", "e2e-q40")
    assert (t.intent, t.action) == ("create_item", "created")
    assert t.at() == "2026-10-22T09:45"


def test_crm_spelled_in_search(client: TestClient) -> None:
    t = say(client, "какие были напоминалки про си ар эм", "e2e-q61")
    assert t.intent == "search_items" and t.slots.get("query") == "CRM"


def test_voice_equals_text_for_reference(client: TestClient) -> None:
    """Эталонная фраза через /ws/voice даёт то же, что через /api/chat."""
    text = "Когда у меня была встреча с Иваном?"
    v = say(client, text, "e2e-par-v")
    r = client.post(
        "/api/chat", json={"text": text, "session_id": "e2e-par-t"}, headers={"X-User-Id": "1"}
    ).json()
    assert v.transcript == text
    assert (v.intent, v.action) == (r["debug"]["intent"], r["action"])
    assert [i["id"] for i in v.items] == [i["id"] for i in r["items"]]


# ---------- находки QA-01…07: открытые — xfail, исправленные — стражи ----------


# QA-01 (чат 1) — исправлено в nlu/v3, страж
@pytest.mark.parametrize(
    ("transcript", "new_at"),
    [
        ("перенеси созвон с орбитой на шестнадцать тридцать", "2026-10-29T16:30"),
        ("перенеси встречу с марией в субботу на девятнадцать ноль ноль", "2026-10-24T19:00"),
    ],
)
def test_update_time_in_words(client: TestClient, transcript: str, new_at: str) -> None:
    t = say(client, transcript, "e2e-qa01")
    assert (t.intent, t.action) == ("update_item", "updated")
    assert t.at() == new_at


# QA-02 (чат 1) — исправлено в nlu/v4, страж
def test_update_hour_without_minutes(client: TestClient) -> None:
    t = say(client, "Перенеси встречу с Марией в субботу на 19.", "e2e-qa02")
    assert (t.intent, t.action) == ("update_item", "updated")
    assert t.at() == "2026-10-24T19:00"


# QA-03 (чат 1) — исправлено в nlu/v3, страж
def test_zero_zero_not_in_title(client: TestClient) -> None:
    t = say(client, "добавь встречу с командой завтра в пятнадцать ноль ноль", "e2e-qa03")
    assert t.action == "created"
    assert t.item["title"] == "Встреча с командой"
    assert t.at() == "2026-10-22T15:00"


# QA-04 (чат 1) — исправлено в nlu/v4, страж
@pytest.mark.parametrize(
    ("transcript", "title"),
    [
        ("Послезавтра в девять созвон с альфой по СРМ.", "Созвон с Альфой по CRM"),
        ("послезавтра в девять созвон с альфой по срм", "Созвон с Альфой по CRM"),
        ("добавь задачу подготовить тэзэ для вектора", "Подготовить ТЗ для Вектора"),
        ("Добавь задачу подготовить т з для Вектора.", "Подготовить ТЗ для Вектора"),
    ],
)
def test_spelled_abbreviations_in_title(client: TestClient, transcript: str, title: str) -> None:
    t = say(client, transcript, "e2e-qa04")
    assert t.action == "created"
    assert t.item["title"].lower() == title.lower()
    assert "CRM" in t.item["title"] or "ТЗ" in t.item["title"]


# QA-05 (чат 1) — исправлено в nlu/v4, страж
def test_org_nominative_after_s(client: TestClient) -> None:
    t = say(client, "Когда ближайший созвон с Альфа?", "e2e-qa05")
    assert t.intent == "search_items"
    assert t.slots.get("organization") == "Альфа"
    assert t.items, t.reply["text"]


# QA-06 (чат 1 / чат 2) — исправлено в nlu/v3 + dialog/v3, страж
def test_update_target_with_misheard_org(client: TestClient) -> None:
    t = say(client, "Перенеси созвон с арбитой на 16:30.", "e2e-qa06")
    assert t.slots.get("organization") == "Орбита"  # уже так
    assert (t.intent, t.action) == ("update_item", "updated")
    assert t.at() == "2026-10-29T16:30"


# QA-07 (чат 1) — исправлено в nlu/v3, страж
def test_entity_case_in_title(client: TestClient) -> None:
    t = say(client, "напомни мне завтра в десять утра позвонить ивану", "e2e-qa07")
    assert t.action == "created"
    assert t.item["title"] == "Позвонить Ивану"

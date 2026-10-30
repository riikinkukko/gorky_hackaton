"""Роутер на формулировках, которых нет в golden_eval (проверка обобщения)."""

from __future__ import annotations

import pytest

from app.contracts import Intent, ItemStatus, ItemType

from .conftest import dt, parse

IN = Intent

INTENTS = [
    # создание
    ("напомни через 20 минут выключить духовку", IN.CREATE),
    ("запланируй на пятницу в 16 созвон с Партнёром", IN.CREATE),
    ("завтра в 9 у меня собеседование", IN.CREATE),
    ("мне надо до пятницы согласовать смету с Анной", IN.CREATE),
    ("добавь купить хлеб", IN.CREATE),
    ("купить хлеб", IN.CREATE),
    ("в субботу в 12 день рождения Ани", IN.CREATE),
    ("каждую пятницу в 17 отчёт руководителю", IN.CREATE),
    ("не забудь мне завтра напомнить про врача", IN.CREATE),
    ("поставь задачу изменить договор", IN.CREATE),  # «изменить» — не перенос
    ("напомни закрыть сделку с Вектором", IN.CREATE),  # «закрыть» — не выполнено
    # списки
    ("что у меня по планам на четверг", IN.LIST),
    ("какие у меня дела в понедельник", IN.LIST),
    ("расскажи что запланировано на завтра", IN.LIST),
    ("я свободен завтра в три?", IN.LIST),
    ("покажи невыполненные задачи", IN.LIST),
    ("что было позавчера", IN.LIST),
    ("что у меня", IN.LIST),
    # поиск
    ("есть что-нибудь на этой неделе с Альфой?", IN.SEARCH),
    ("когда я последний раз встречался с Ольгой", IN.SEARCH),
    ("найди созвоны с Севером в прошлом месяце", IN.SEARCH),
    ("поищи договор с Максимом", IN.SEARCH),
    ("что мы обсуждали с Натальей", IN.SEARCH),
    # изменение / выполнение / удаление
    ("перенеси созвон с Альфой с пятницы на понедельник", IN.UPDATE),
    ("передвинь встречу на 18:00", IN.UPDATE),
    ("поменяй время встречи с Анной на 11", IN.UPDATE),
    ("переименуй её в Встреча с инвесторами", IN.UPDATE),
    ("удали все напоминания на завтра", IN.DELETE),
    ("отмени встречу с Еленой", IN.DELETE),
    ("я позвонил Ивану", IN.COMPLETE),
    ("задачу про смету можно закрыть", IN.COMPLETE),
    ("отметь последнюю как сделанную", IN.COMPLETE),
    # служебное
    ("спасибо", IN.SMALLTALK),
    ("кто ты", IN.SMALLTALK),
    ("ага", IN.CONFIRM),
    ("не надо", IN.REJECT),
    ("верни как было", IN.UNDO),
    ("включи музыку", IN.UNKNOWN),
    ("ммм", IN.UNKNOWN),
    ("", IN.UNKNOWN),
]


@pytest.mark.parametrize("text,intent", INTENTS, ids=[t or "<empty>" for t, _ in INTENTS])
def test_intent(nlu, text, intent) -> None:
    assert parse(nlu, text).intent == intent


def test_reminder_slots(nlu) -> None:
    c = parse(nlu, "напомни через 20 минут выключить духовку")
    assert c.slots.type == ItemType.REMINDER
    assert c.slots.title == "Выключить духовку"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 21, 14, 20)


def test_meeting_with_org(nlu) -> None:
    c = parse(nlu, "запланируй на пятницу в 16 созвон с Партнёром")
    assert c.slots.type == ItemType.MEETING and c.slots.organization == "Партнёр"
    assert c.slots.title == "Созвон с Партнёром"


def test_deadline_dative_weekday(nlu) -> None:
    c = parse(nlu, "нужно купить подарок маме к пятнице")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23, 23, 59)
    assert c.slots.title == "Купить подарок маме"


def test_reminder_evening_hour(nlu) -> None:
    c = parse(nlu, "поставь напоминание на 7 вечера выпить таблетки")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 21, 19)
    assert c.slots.title == "Выпить таблетки"


def test_duration(nlu) -> None:
    c = parse(nlu, "Поставь встречу с Анной завтра в 11 на 45 минут")
    assert c.slots.duration_min == 45
    assert c.slots.title == "Встреча с Анной"


def test_list_status(nlu) -> None:
    c = parse(nlu, "покажи невыполненные задачи")
    assert c.slots.status == ItemStatus.OPEN and c.slots.type == ItemType.TASK


def test_search_meeting_verb(nlu) -> None:
    c = parse(nlu, "когда я последний раз встречался с Ольгой")
    assert c.slots.type == ItemType.MEETING
    assert c.slots.participants == ["Ольга"] and c.slots.tense == "past"


def test_search_prep_not_type(nlu) -> None:
    # «готовился к встрече» — это задачи «Подготовиться к встрече с …», а не встречи
    c = parse(nlu, "Когда я последний раз готовился к встрече с Вектором?")
    assert c.slots.type is None and c.slots.organization == "Вектор"
    assert c.slots.query == "подготовиться"


def test_search_asr_noise(nlu) -> None:
    assert parse(nlu, "когда у меня была встреча с и ваном").slots.participants == ["Иван"]
    assert parse(nlu, "созвон с арбитой когда").slots.organization == "Орбита"
    assert parse(nlu, "найди презинтацию для максима").slots.query == "презентация"
    assert parse(nlu, "найди всё про тэ зэ").slots.query == "техническое задание"


def test_update_split_target_and_new_time(nlu) -> None:
    c = parse(nlu, "перенеси созвон с Альфой с пятницы на понедельник")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23)  # где запись сейчас
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 26)  # куда переносим
    assert c.slots.organization == "Альфа"


def test_update_on_eleven_is_time(nlu) -> None:
    c = parse(nlu, "поменяй время встречи с Анной на 11")
    assert c.slots.new_when and c.slots.new_when.has_time and c.slots.new_when.start.hour == 11
    assert c.slots.participants == ["Анна"]


def test_rename(nlu) -> None:
    c = parse(nlu, "переименуй её в Встреча с инвесторами")
    assert c.slots.target and c.slots.target.pronoun
    assert c.slots.new_title == "Встреча с инвесторами"


def test_shift(nlu) -> None:
    from app.nlu import parse_shift

    c = parse(nlu, "Сдвинь планёрку по интеграции 18 ноября на час позже")
    assert c.slots.new_when and c.slots.new_when.start is None
    assert parse_shift(c.slots.new_when.text) == 60
    assert parse_shift("на 30 минут раньше") == -30


def test_complete_verb_to_infinitive(nlu) -> None:
    c = parse(nlu, "Отметь, что смету по мобильному приложению я проверил")
    assert (
        c.slots.target and c.slots.target.description == "проверить смету по мобильному приложению"
    )
    assert c.slots.status == ItemStatus.DONE


def test_invalid_date_is_ambiguous(nlu) -> None:
    c = parse(nlu, "Что у меня 31 ноября?")
    assert c.intent == IN.LIST and c.slots.when and c.slots.when.ambiguous


def test_fallback_marks_source(nlu) -> None:
    # без LLM неуверенные правила помечаются как fallback
    c = parse(nlu, "созвоны со Стартом")
    assert c.intent == IN.SEARCH and c.source == "fallback"


def test_factories_v11() -> None:
    from app.nlu import build_date_resolver, build_nlu

    class S:  # как pydantic-settings: атрибуты в нижнем регистре
        llm_provider = "rules"

    dates = build_date_resolver(S())
    nlu = build_nlu(S(), dates)
    assert nlu.llm is None and nlu.dates is dates


def test_undo_word(nlu) -> None:
    assert parse(nlu, "отмени").intent == IN.UNDO  # POST /api/undo и голосом
    assert parse(nlu, "отмени последнее действие").intent == IN.UNDO


def test_update_new_when_has_date(nlu) -> None:
    c = parse(nlu, "Перенеси созвон с Орбитой на 16:30")
    assert c.slots.new_when and c.slots.new_when.has_time and not c.slots.new_when.has_date
    c = parse(nlu, "Перенеси встречу с Марией в субботу на 19:00")
    assert c.slots.new_when and c.slots.new_when.has_date  # день взят из цели


@pytest.mark.parametrize(
    "text",
    [
        "Когда я последний раз звонил Сергею?",
        "Когда я в последний раз звонил Ивану?",
        "Когда я последний раз готовился к встрече с Вектором?",
        "Когда была последняя планёрка по деньгам?",
        "когда последний раз встречались с Анной",
        "когда в последний раз был созвон с Альфой",
    ],
)
def test_last_time_is_past(nlu, text) -> None:  # q22 / q76 / q82 / q84
    c = parse(nlu, text)
    assert c.intent == IN.SEARCH and c.slots.tense == "past"


def test_invalid_date_list_keeps_ambiguous(nlu) -> None:  # q75
    c = parse(nlu, "Что у меня 31 ноября?")
    assert c.intent == IN.LIST
    assert c.slots.when and c.slots.when.ambiguous and c.slots.when.start is None

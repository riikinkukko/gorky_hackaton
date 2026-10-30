"""Состязательные реплики чата 9 (qa/adversarial, 24.10): корни провалов на уровне NLU.

«Сейчас» — пт 23.10.2026 15:00 (freeze_now набора). Сквозная проверка — `make adversarial`.
1. вопрос никогда не создание («кие планы на завтра», «чё там у меня на завтра»);
2. вежливые обёртки не попадают в название;
3. самопоправки: значение после «нет / ой нет / то есть / хотя нет»;
4. разговорное время: «полчетвёртого», «без двадцати семь», «в четверть восьмого»;
5. цель правки в прошедшем времени: «ту встречу, что была», «была на понедельник»;
6. мат и паразиты в начале фразы;
+ «поправить»: сдвиги «на полчаса пораньше», «на пару дней», перенос без «перенеси»,
  «сделал / позвонил уже» — выполнено, две команды в фразе, «оттуда / туда же».
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contracts import Command, DialogState, Intent, ItemType, TimeSpec
from app.nlu import parse_shift

from .conftest import MSK, dt, parse, state

FRI = datetime(2026, 10, 23, 15, 0, tzinfo=MSK)


def p(nlu, text: str, st: DialogState | None = None) -> Command:
    return parse(nlu, text, st, now=FRI)


def after(nlu, first: str, second: str) -> Command:
    c1 = p(nlu, first)
    return p(nlu, second, state(last_command=c1))


# ---------- 1. вопрос — список, не создание ----------


@pytest.mark.parametrize(
    "text,day",
    [
        ("кие планы на завтра", dt(2026, 10, 24)),
        ("чё там у меня на завтра", dt(2026, 10, 24)),
        ("то у меня сегодня", dt(2026, 10, 23)),
        ("акие встречи в понедельник", dt(2026, 10, 26)),
    ],
)
def test_question_is_list(nlu, text: str, day: datetime) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.LIST and c.slots.when and c.slots.when.start == day


def test_show_meetings_week(nlu) -> None:
    c = p(nlu, "кажи встречи на неделю")
    assert c.intent == Intent.LIST and c.slots.type == ItemType.MEETING


# ---------- 2. вежливые обёртки ----------


@pytest.mark.parametrize(
    "text",
    [
        "можно встречу с командой завтра в десять",
        "не могли бы вы поставить встречу с командой завтра в десять",
        "было бы неплохо встречу с командой завтра в десять",
    ],
)
def test_polite_wrapper_not_in_title(nlu, text: str) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.title == "Встреча с командой"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 24, 10)


# ---------- 3. самопоправки ----------


@pytest.mark.parametrize(
    "text,start",
    [
        ("встреча с Иваном в среду нет в четверг в десять", dt(2026, 10, 29, 10)),
        ("созвон с Альфой завтра в девять ой нет в десять", dt(2026, 10, 24, 10)),
        ("встреча с Олегом завтра в семь то есть в восемь вечера", dt(2026, 10, 24, 20)),
    ],
)
def test_self_correction_takes_value_after_marker(nlu, text: str, start: datetime) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.when and c.slots.when.start == start
    assert c.slots.when.ambiguous is False


def test_self_correction_in_shift(nlu) -> None:
    c = p(nlu, "сдвинь созвон с Орбитой на час позже хотя нет на два")
    assert c.intent == Intent.UPDATE and c.slots.organization == "Орбита"
    assert c.slots.new_when and parse_shift(c.slots.new_when.text) == 120


def test_self_correction_replaces_noun(nlu) -> None:
    c = p(nlu, "отметь смету выполненной а нет подожди акт")
    assert c.intent == Intent.COMPLETE and c.slots.target
    assert c.slots.target.description == "акт"


def test_self_correction_in_update_time(nlu) -> None:
    c = p(nlu, "перенеси звонок Анне на десять нет на одиннадцать")
    assert c.intent == Intent.UPDATE and c.slots.new_when and c.slots.new_when.start
    assert c.slots.new_when.start.hour == 11


# ---------- 4. разговорное время ----------


@pytest.mark.parametrize(
    "text,hour,minute",
    [
        ("встреча с Иваном завтра в полчетвёртого", 15, 30),
        ("встреча с Иваном завтра без двадцати семь вечера", 18, 40),
        ("встреча с Иваном завтра в четверть восьмого вечера", 19, 15),
        ("встреча с Иваном завтра в полдевятого вечера", 20, 30),
    ],
)
def test_colloquial_time(nlu, text: str, hour: int, minute: int) -> None:
    c = p(nlu, text)
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 24, hour, minute)


# ---------- 5. цель правки в прошедшем времени ----------


@pytest.mark.parametrize(
    "text,intent,desc",
    [
        ("удали ту встречу с Марией что была", Intent.DELETE, "встречу с Марией"),
        ("отметь старую задачу про смету выполненной", Intent.COMPLETE, "задачу про смету"),
        ("задача про акт была на понедельник перенеси на вторник", Intent.UPDATE, "задача про акт"),
    ],
)
def test_past_wording_stripped_from_target(nlu, text: str, intent: Intent, desc: str) -> None:
    c = p(nlu, text)
    assert c.intent == intent and c.slots.target and c.slots.target.description == desc
    assert c.slots.tense == "any"


def test_was_on_monday_is_date_key(nlu) -> None:
    c = p(nlu, "задача про акт была на понедельник перенеси на вторник")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 26)  # ближайший, не прошедший
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 27)


def test_which_was_on_third(nlu) -> None:
    c = p(nlu, "звонок Анне который был на третье перенеси на четвёртое")
    assert c.slots.participants == ["Анна"] and c.slots.when
    assert c.slots.when.start == dt(2026, 11, 3)
    assert c.slots.target and c.slots.target.description == "позвонить Анне"


def test_yesterdays_task(nlu) -> None:
    c = p(nlu, "вчерашнюю задачу отметь сделанной")
    assert c.intent == Intent.COMPLETE and c.slots.when
    assert c.slots.when.start == dt(2026, 10, 22)


def test_that_will_be_tomorrow_is_not_question(nlu) -> None:
    c = p(nlu, "та встреча с Марией что будет завтра перенеси на десять вечера")
    assert c.intent == Intent.UPDATE and c.slots.participants == ["Мария"]
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 24, 22)


# ---------- 6. мат и паразиты в начале ----------


@pytest.mark.parametrize(
    "text,intent",
    [
        ("бля что у меня завтра", Intent.LIST),
        ("слышь двинь встречу с Марией на часик попозже", Intent.UPDATE),
        ("кароч этот созвон с Альфой отменяется", Intent.DELETE),
        ("ну это самое убери нахрен звонок Анне", Intent.DELETE),
    ],
)
def test_lead_slang(nlu, text: str, intent: Intent) -> None:
    assert p(nlu, text).intent == intent


# ---------- сдвиги ----------


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("на час позже", 60),
        ("на 30 минут раньше", -30),
        ("на полчаса пораньше", -30),
        ("на часик попозже", 60),
        ("на пару дней", 2 * 1440),
        ("на недельку", 7 * 1440),
        ("на два часа позже", 120),
        ("на сорок пять минут раньше", -45),
    ],
)
def test_parse_shift_colloquial(text: str, minutes: int) -> None:
    assert parse_shift(text) == minutes


@pytest.mark.parametrize("text", ["на час", "на неделе", "на 19", "на полчаса"])
def test_not_a_shift(text: str) -> None:
    assert parse_shift(text) is None


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("перенеси созвон с Альфой на полчаса пораньше", -30),
        ("на час позже сдвинь созвон с Альфой", 60),
        ("сдвинь звонок Анне на пару дней", 2880),
        ("отложи звонок Анне на недельку", 7 * 1440),
        ("перенеси её на 30 минут раньше", -30),  # не «30-е число»
    ],
)
def test_update_shift(nlu, text: str, minutes: int) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.UPDATE and c.slots.new_when
    assert c.slots.new_when.start is None and parse_shift(c.slots.new_when.text) == minutes
    assert c.slots.target and (c.slots.target.description or c.slots.target.pronoun)


def test_date_before_verb_keeps_target(nlu) -> None:
    c = p(nlu, "на воскресенье встречу с Марией сдвинь")
    assert c.intent == Intent.UPDATE and c.slots.participants == ["Мария"]
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 25)


def test_vague_new_date_is_ambiguous(nlu) -> None:
    c = p(nlu, "перенеси смету на после праздников")
    assert c.intent == Intent.UPDATE and c.slots.target
    assert c.slots.target.description == "смету"
    assert c.slots.new_when and c.slots.new_when.ambiguous


def test_friday_on_friday_is_next_week(nlu) -> None:
    c = p(nlu, "перенеси созвон с Альфой на пятницу")
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 30)


# ---------- перенос без «перенеси», вежливость ----------


@pytest.mark.parametrize(
    "text,new_start",
    [
        ("было бы удобнее встретиться с Марией в воскресенье", dt(2026, 10, 25)),
        ("мне не очень удобно в субботу с Марией давай в воскресенье", dt(2026, 10, 25)),
        ("а нельзя ли перенести встречу с Марией на воскресенье", dt(2026, 10, 25)),
    ],
)
def test_implicit_move(nlu, text: str, new_start: datetime) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.UPDATE and c.slots.participants == ["Мария"]
    assert c.slots.new_when and c.slots.new_when.start == new_start


def test_davai_time_move(nlu) -> None:
    c = p(nlu, "встречу с Марией давай на без четверти восемь вечера")
    assert c.intent == Intent.UPDATE and c.slots.new_when
    assert c.slots.new_when.has_time and c.slots.new_when.start
    assert (c.slots.new_when.start.hour, c.slots.new_when.start.minute) == (19, 45)


@pytest.mark.parametrize(
    "text,intent",
    [
        ("будьте любезны удалите подготовку акта", Intent.DELETE),
        ("давай наверное уберём созвон с Альфой", Intent.DELETE),
        ("отменилась встреча с Марией в субботу", Intent.DELETE),
        ("перекинь созвон с Орбитой на четыре", Intent.UPDATE),
        ("чё-то не успеваю двинь отчёт на понедельник", Intent.UPDATE),
        ("сделай как было", Intent.UNDO),
        ("меть смету выполненной", Intent.COMPLETE),
        ("отово смета", Intent.COMPLETE),
        ("дали созвон с альфой", Intent.DELETE),
    ],
)
def test_more_verbs(nlu, text: str, intent: Intent) -> None:
    assert p(nlu, text).intent == intent


# ---------- «сделал» — выполнено ----------


@pytest.mark.parametrize(
    "text,desc",
    [
        ("всё сделал с итогами для Максима", "сделать с итогами для Максима"),
        ("смета уже проверена", "смета"),
        ("кажется смету я уже проверил", "проверить смету"),
        ("позвонил уже", "позвонить"),
    ],
)
def test_done_statement_is_complete(nlu, text: str, desc: str) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.COMPLETE and c.slots.target
    assert c.slots.target.description == desc


@pytest.mark.parametrize("text", ["я забыл позвонить Ивану", "когда я позвонил Ивану?"])
def test_not_done_statement(nlu, text: str) -> None:
    assert p(nlu, text).intent != Intent.COMPLETE


def test_target_noun_matches_title_verbs(nlu) -> None:
    # названия в данных — «Позвонить Анне», «Подготовить акт»: резолвер сравнивает основы
    assert p(nlu, "перенеси звонок Анне на завтра").slots.target.description == "позвонить Анне"  # type: ignore[union-attr]
    assert p(nlu, "удали подготовку акта").slots.target.description == "подготовить акта"  # type: ignore[union-attr]


# ---------- две команды ----------


@pytest.mark.parametrize(
    "text,intent",
    [
        ("перенеси созвон с Орбитой на пятницу и удали звонок Анне", Intent.UPDATE),
        ("удали встречу с Марией и поставь новую на воскресенье в двенадцать", Intent.DELETE),
        ("перенеси созвон с Альфой на понедельник а звонок Анне на вторник", Intent.UPDATE),
        ("смету проверил и протокол Елене отправил", Intent.COMPLETE),
    ],
)
def test_first_of_two_commands(nlu, text: str, intent: Intent) -> None:
    c = p(nlu, text)
    assert c.intent == intent
    assert "Анн" not in (c.slots.target.description or "") if c.slots.target else True


def test_two_targets_one_date(nlu) -> None:
    c = p(nlu, "перенеси отчёт на согласование и созвон с Орбитой на пятницу")
    assert c.intent == Intent.UPDATE and c.slots.new_when
    assert c.slots.new_when.start == dt(2026, 10, 30)


# ---------- контекст ----------


@pytest.mark.parametrize(
    "second,hour,day",
    [("ой нет на одиннадцать", 11, 24), ("а лучше в воскресенье", None, 25)],
)
def test_correction_after_create(nlu, second: str, hour: int | None, day: int) -> None:
    c = after(nlu, "Напомни завтра в десять позвонить Ивану", second)
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.pronoun
    nw = c.slots.new_when
    assert nw and nw.start and nw.start.day == day
    if hour is not None:
        assert nw.start.hour == hour


@pytest.mark.parametrize("second", ["всё купил", "позвонил уже"])
def test_done_after_create(nlu, second: str) -> None:
    c = after(nlu, "Поставь задачу купить продукты завтра в 10", second)
    assert c.intent == Intent.COMPLETE


def test_from_there(nlu) -> None:
    c = after(nlu, "Что у меня в субботу?", "перенеси встречу оттуда на воскресенье")
    assert c.intent == Intent.UPDATE and c.slots.when and c.slots.when.start == dt(2026, 10, 24)
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 25)


def test_to_there(nlu) -> None:
    c = after(nlu, "Что у меня в понедельник?", "перенеси встречу с Иваном туда же на одиннадцать")
    nw = c.slots.new_when
    assert c.intent == Intent.UPDATE and nw and nw.start == dt(2026, 10, 26, 11)
    assert isinstance(nw, TimeSpec) and nw.has_date


# ---------- найти старое ----------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("что было три дня назад", dt(2026, 10, 20), dt(2026, 10, 21)),
        ("что было неделю назад", dt(2026, 10, 16), dt(2026, 10, 17)),
        ("что было в позапрошлую пятницу", dt(2026, 10, 9), None),
        ("что было в прошлом году в декабре", dt(2025, 12, 1), dt(2026, 1, 1)),
    ],
)
def test_past_dates(nlu, text: str, start: datetime, end: datetime | None) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.LIST and c.slots.when and c.slots.when.start == start
    if end is not None:
        assert c.slots.when.end == end


def test_recently_in_past_question(nlu) -> None:
    c = p(nlu, "что было на днях по презентации")
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 16)


@pytest.mark.parametrize(
    "text,who",
    [
        ("найди встречу с Иваном ой нет с Марией", ["Мария"]),
        ("что было с Вектором а нет с Орбитой", []),
    ],
)
def test_self_correction_entity(nlu, text: str, who: list[str]) -> None:
    c = p(nlu, text)
    assert c.intent == Intent.SEARCH and c.slots.participants == who
    assert not c.slots.query or "нет" not in c.slots.query


def test_search_not_complete(nlu) -> None:
    assert p(nlu, "кажется я ставил задачу про смету можешь найти").intent == Intent.SEARCH


@pytest.mark.parametrize("text", ["найди", "найди найди найди"])
def test_empty_search_is_unknown(nlu, text: str) -> None:
    assert p(nlu, text).intent == Intent.UNKNOWN

"""Регрессия по живому тесту на стенде 22.10 (документ «10_Разбор_живого_теста_22_10»).

Фразы — дословные транскрипты. «Сейчас» — среда 21.10.2026 14:00 (как в golden_eval),
поэтому «пятница, 23 октября» — действительно пятница.

Соглашения для диалога (чат 2), которые проверяются здесь:
- target.description == "__last__" — «последнюю задачу/запись/встречу»;
- target.description == "__all__"  — «все встречи», «все завтрашние задачи», «отмени всё»;
  тип и период — в slots.type / slots.when; диалог обязан переспросить перед удалением;
- when.ambiguous и text с « или » — во фразе несколько вариантов времени
  («может быть, в 5, может быть, в 6») или несколько записей («две пары»);
- «в обед» при создании — when = 12:00–14:00, ambiguous=True: диалог предлагает слот.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import Intent, ItemType
from app.nlu import parse_shift
from app.nlu.llm.tools import CreateArgs, to_command

from .conftest import NOW, dt, parse, state

THRESHOLD = 0.85


# ---------- 1. мусор, приветствия, неуверенность, тип «встреча» ----------


def test_greeting_meeting_tomorrow_evening(nlu) -> None:
    c = parse(nlu, "Привет, поставь мне встречу на 7 вечера на завтра")
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    assert c.slots.title == "Встреча"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 19) and c.slots.when.has_time


def test_garbage_tail_and_idiom(nlu) -> None:
    c = parse(
        nlu,
        "Привет, запланируй мне встречу на 8 часов вечера, послезавтра, "
        "после дождичка в четверг, ну ещё что-нибудь",
    )
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23, 20)
    assert c.slots.title == "Встреча"
    assert c.confidence < THRESHOLD  # необъяснённый хвост → LLM посмотрит


def test_two_time_options_are_ambiguous(nlu) -> None:
    c = parse(
        nlu,
        "Мне надо завтра, наверное, встретиться с каким-то очень важным человеком, "
        "может быть, в 5 часов вечера, может быть, в 6:00",
    )
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    w = c.slots.when
    assert w and w.ambiguous and w.start and w.start.date() == dt(2026, 10, 22).date()
    assert " или " in w.text
    assert c.slots.title and "наверное" not in c.slots.title and "может" not in c.slots.title
    assert c.confidence < THRESHOLD


def test_two_items_in_one_phrase_are_ambiguous(nlu) -> None:
    c = parse(nlu, "Две пары: одну пару в восемь, вторую в 14:40")
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    assert c.slots.when and c.slots.when.ambiguous
    assert c.confidence < THRESHOLD


@pytest.mark.parametrize(
    "text",
    [
        "Привет, поставь мне встречу на 7 вечера на завтра",
        "завтра в 10 созвон с Альфой",
        "пара по английскому в 8 утра завтра",
        "надо завтра встретиться с Анной в 12",
    ],
)
def test_meeting_word_anywhere_gives_meeting(nlu, text: str) -> None:
    assert parse(nlu, text).slots.type == ItemType.MEETING


@pytest.mark.parametrize(
    "text",
    [
        "подготовиться к встрече с Вектором завтра",
        "отправить итоги встречи Ивану в пятницу",
        # 23.10 (логи стенда): сказано «задачу» — это задача, даже если внутри «встреча»
        "поставь задачу, у меня встреча с Анной в 12 завтра",
    ],
)
def test_meeting_word_under_preposition_is_task(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.TASK


def test_clean_phrase_keeps_high_confidence(nlu) -> None:
    c = parse(nlu, "Добавь встречу с командой завтра в 15:00")
    assert c.confidence >= THRESHOLD and c.slots.title == "Встреча с командой"


# ---------- 2. время из ветки LLM и ответы на уточнение ----------


def test_pair_tomorrow_morning_by_rules(nlu) -> None:
    c = parse(nlu, "пара по английскому в 8 утра завтра")
    assert c.intent == Intent.CREATE and c.source == "rules"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 8) and c.slots.when.has_time
    assert c.slots.title == "Пара по английскому"


@pytest.mark.parametrize(
    "when_text",
    ["в 8 утра завтра", "8 утра завтра", "завтра 8:00", "завтра в 8.00", "2026-10-22T08:00", None],
)
def test_llm_when_text_reaches_resolver(nlu, when_text: str | None) -> None:
    # None — LLM забыл when_text: время берётся из самой фразы правилами
    args = CreateArgs(type="meeting", title="Пара по английскому", when_text=when_text)
    c = to_command(
        "create_item", args, "пара по английскому в 8 утра завтра", NOW, nlu.dates, nlu.ents
    )
    assert c.slots.when and c.slots.when.has_time, c.slots.when
    assert c.slots.when.start == dt(2026, 10, 22, 8)


def test_llm_time_without_date_keeps_date_from_phrase(nlu) -> None:
    args = CreateArgs(type="meeting", title="Пара", when_text="8 утра")
    c = to_command("create_item", args, "пара завтра в 8 утра", NOW, nlu.dates, nlu.ents)
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 8)


@pytest.mark.parametrize(
    "answer,hour", [("8.00", 8), ("8:00", 8), ("10:00", 10), ("в 8 утра", 8), ("восемь", 8)]
)
def test_time_answer_formats(nlu, answer: str, hour: int) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    c = parse(nlu, answer, state(pending=first, awaiting="time"))
    assert c.intent == Intent.CREATE and c.slots.when and c.slots.when.has_time
    assert c.slots.when.start == dt(2026, 10, 22, hour)


# ---------- 3. части дня как период ----------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("Какой план на вечер", dt(2026, 10, 21, 17), dt(2026, 10, 21, 23)),
        ("Какой план на сегодняшний вечер", dt(2026, 10, 21, 17), dt(2026, 10, 21, 23)),
        ("что у меня сегодня вечером", dt(2026, 10, 21, 17), dt(2026, 10, 21, 23)),
        ("Что у меня на утро", dt(2026, 10, 22, 6), dt(2026, 10, 22, 12)),  # сегодняшнее прошло
        ("что на завтрашнее утро", dt(2026, 10, 22, 6), dt(2026, 10, 22, 12)),
        ("какие планы на завтрашний вечер", dt(2026, 10, 22, 17), dt(2026, 10, 22, 23)),
    ],
)
def test_daypart_period_list(nlu, text: str, start: Any, end: Any) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.LIST, c.slots
    w = c.slots.when
    assert w and w.is_period and (w.start, w.end) == (start, end)


# ---------- 4. продолжения через last_command ----------


def test_follow_up_this_week_then_next(nlu) -> None:
    first = parse(nlu, "Что у меня сегодня?")
    week = parse(nlu, "а на этой неделе?", state(last_command=first))
    assert week.intent == Intent.LIST and week.slots.when
    assert week.slots.when.start == dt(2026, 10, 19)
    nxt = parse(nlu, "на следующей?", state(last_command=week))
    assert nxt.intent == Intent.LIST and nxt.slots.when
    assert (nxt.slots.when.start, nxt.slots.when.end) == (dt(2026, 10, 26), dt(2026, 11, 2))
    prev = parse(nlu, "а на прошлой?", state(last_command=week))
    assert prev.slots.when and prev.slots.when.start == dt(2026, 10, 12)


# ---------- 5. полночь ----------


@pytest.mark.parametrize(
    "text",
    ["Сделаем на сегодня запись в 12:00 ночи", "поставь на сегодня в полночь проверить сервер"],
)
def test_midnight_today_is_end_of_today(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.CREATE
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 0) and c.slots.when.has_time


# ---------- 6. вне темы и просьба о совете ----------


@pytest.mark.parametrize(
    "text", ["Какой сейчас курс рубля?", "какой курс доллара", "расскажи анекдот"]
)
def test_out_of_scope(nlu, text: str) -> None:
    assert parse(nlu, text).intent == Intent.UNKNOWN


def test_advice_lunch_is_create_with_window(nlu) -> None:
    c = parse(nlu, "Нужна встреча завтра примерно в обед, что посоветуешь?")
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    w = c.slots.when
    assert w and w.ambiguous
    assert (w.start, w.end) == (dt(2026, 10, 22, 12), dt(2026, 10, 22, 14))


# ---------- 7. цели для update / delete ----------


def test_last_task_shift_one_day(nlu) -> None:
    c = parse(nlu, "Перенеси последнюю задачу на один день вперёд")
    assert c.intent == Intent.UPDATE
    assert c.slots.target and c.slots.target.description == "__last__"
    assert c.slots.new_when and c.slots.new_when.start is None
    assert parse_shift(c.slots.new_when.text) == 24 * 60


@pytest.mark.parametrize("text", ["Удали последнюю запись", "удали последнюю встречу"])
def test_last_record_delete(nlu, text: str) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.DELETE and c.slots.target
    assert c.slots.target.description == "__last__"


def test_two_dates_first_target_second_new(nlu) -> None:
    c = parse(nlu, "Перенеси встречу с 23 октября на 8:00 на 24 октября 8:00")
    assert c.intent == Intent.UPDATE and c.slots.type == ItemType.MEETING
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23, 8)
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 24, 8)


def test_target_described_by_date_clause(nlu) -> None:
    c = parse(nlu, "Перенеси задачу, которая на пятницу, 23 октября, 8:00, на субботу 9:00")
    assert c.intent == Intent.UPDATE
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 23, 8)
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 24, 9)


def test_new_time_tomorrows_day(nlu) -> None:
    c = parse(nlu, "Перенеси встречу на 19:00 завтрашнего дня")
    assert c.intent == Intent.UPDATE
    nw = c.slots.new_when
    assert nw and nw.start == dt(2026, 10, 22, 19) and nw.has_date and nw.has_time
    assert c.slots.when is None  # «завтрашнего дня» — новое время, не цель


# ---------- 8. массовые действия ----------


@pytest.mark.parametrize(
    "text,type_,start",
    [
        ("отмени все завтрашние встречи", ItemType.MEETING, dt(2026, 10, 22)),
        ("отмени все встречи", ItemType.MEETING, None),
        ("удали все задачи на пятницу", ItemType.TASK, dt(2026, 10, 23)),
        ("Отмени все", None, None),
    ],
)
def test_delete_all(nlu, text: str, type_: ItemType | None, start: Any) -> None:
    c = parse(nlu, text)
    assert c.intent == Intent.DELETE and c.slots.target
    assert c.slots.target.description == "__all__"
    assert c.slots.type == type_
    assert (c.slots.when.start if c.slots.when else None) == start


def test_cancel_all_while_awaiting_is_reject(nlu) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    assert parse(nlu, "Отмени все", state(pending=first, awaiting="time")).intent == Intent.REJECT


@pytest.mark.parametrize(
    "text,intent", [("отмени", Intent.UNDO), ("отмени встречу с Еленой", Intent.DELETE)]
)
def test_single_cancel_unchanged(nlu, text: str, intent: Intent) -> None:
    c = parse(nlu, text)
    assert c.intent == intent
    if c.slots.target:
        assert c.slots.target.description not in ("__all__", "__last__")


@pytest.mark.parametrize(
    "ref,mark,type_",
    [("latest", "__last__", ItemType.TASK), ("all", "__all__", ItemType.MEETING)],
)
def test_llm_target_marks(nlu, ref: str, mark: str, type_: ItemType) -> None:
    from app.nlu.llm.tools import TargetOnlyArgs

    desc = "последнюю задачу" if ref == "latest" else "все встречи"
    args = TargetOnlyArgs.model_validate({"target": {"ref": ref, "description": desc}})
    c = to_command("delete_item", args, desc, NOW, nlu.dates, nlu.ents)
    assert c.slots.target and c.slots.target.description == mark
    assert c.slots.type == type_

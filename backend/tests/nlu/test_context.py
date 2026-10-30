"""Уточняющий диалог и контекст: ответы на вопросы, подтверждения, follow-up."""

from __future__ import annotations

from app.contracts import Intent, ItemType

from .conftest import dt, parse, state


def test_time_answer_merges_with_pending_date(nlu) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    assert first.slots.when and not first.slots.when.has_time
    c = parse(nlu, "в три", state(pending=first, awaiting="time"))
    assert c.intent == Intent.CREATE and c.slots.type == ItemType.MEETING
    assert c.slots.title == "Встреча с Иваном"
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 22, 15) and c.slots.when.has_time


def test_bare_number_answer(nlu) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    for ans in ("15", "пятнадцать тридцать", "давай в 4"):
        c = parse(nlu, ans, state(pending=first, awaiting="time"))
        assert c.intent == Intent.CREATE and c.slots.when and c.slots.when.has_time, ans
    c = parse(nlu, "пятнадцать тридцать", state(pending=first, awaiting="time"))
    assert c.slots.when.start == dt(2026, 10, 22, 15, 30)


def test_datetime_answer_for_reminder(nlu) -> None:
    first = parse(nlu, "Напомни про договор")
    c = parse(nlu, "в понедельник утром", state(pending=first, awaiting="date"))
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 26, 9)


def test_new_command_while_awaiting(nlu) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    c = parse(nlu, "Что у меня сегодня?", state(pending=first, awaiting="time"))
    assert c.intent == Intent.LIST


def test_cancel_while_awaiting(nlu) -> None:
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    assert parse(nlu, "отмена", state(pending=first, awaiting="time")).intent == Intent.REJECT
    assert parse(nlu, "отмена").intent == Intent.UNDO  # без сценария — откат последнего действия


def test_conflict_alternative_time(nlu) -> None:
    first = parse(nlu, "Поставь созвон с Маяком 29 октября в 15:30")
    st = state(pending=first, awaiting="confirm_conflict")
    c = parse(nlu, "давай на 16:30", st)
    assert c.intent == Intent.CREATE and c.slots.when.start == dt(2026, 10, 29, 16, 30)
    assert parse(nlu, "всё равно на 15:30", st).intent == Intent.CONFIRM
    assert parse(nlu, "нет", st).intent == Intent.REJECT


def test_confirm_delete(nlu) -> None:
    first = parse(nlu, "Удали созвон с Альфой первого ноября")
    st = state(pending=first, awaiting="confirm_delete")
    assert parse(nlu, "да, удаляй", st).intent == Intent.CONFIRM
    assert parse(nlu, "нет, оставь", st).intent == Intent.REJECT


def test_title_answer(nlu) -> None:
    first = parse(nlu, "Напомни")
    assert first.intent == Intent.CREATE and first.slots.title is None
    c = parse(nlu, "позвонить маме", state(pending=first, awaiting="title"))
    assert c.slots.title == "Позвонить маме" and c.slots.type == ItemType.REMINDER


def test_target_ordinal(nlu) -> None:
    first = parse(nlu, "Перенеси встречу с Марией на час раньше")
    c = parse(nlu, "вторую", state(pending=first, awaiting="target", last_item_ids=[693, 797, 852]))
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.item_id == 797


def test_follow_up_period(nlu) -> None:
    first = parse(nlu, "Какие встречи на этой неделе?")
    c = parse(nlu, "А на следующей?", state(last_command=first))
    assert c.intent == Intent.LIST and c.slots.type == ItemType.MEETING
    assert c.slots.when and c.slots.when.start == dt(2026, 10, 26)


def test_follow_up_next(nlu) -> None:
    first = parse(nlu, "Когда у меня была встреча с Иваном?")
    c = parse(nlu, "А следующая?", state(last_command=first))
    assert c.intent == Intent.SEARCH and c.slots.tense == "future"
    assert c.slots.participants == ["Иван"] and c.slots.type == ItemType.MEETING


def test_follow_up_other_person(nlu) -> None:
    first = parse(nlu, "Когда у меня была встреча с Иваном?")
    c = parse(nlu, "а с Марией?", state(last_command=first))
    assert c.intent == Intent.SEARCH and c.slots.participants == ["Мария"]


def test_correction_keeps_date(nlu) -> None:
    first = parse(nlu, "Напомни мне в пятницу в 10 утра позвонить Ивану")
    c = parse(nlu, "нет, в 11", state(last_command=first))
    assert c.intent == Intent.UPDATE and c.slots.target and c.slots.target.pronoun
    assert c.slots.new_when and c.slots.new_when.start == dt(2026, 10, 23, 11)

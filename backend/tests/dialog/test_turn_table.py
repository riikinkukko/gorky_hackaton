"""Таблица «состояние диалога × тип реплики» (dialog/v7): по тесту на каждую клетку.

Типы реплик (turns.py):
- full          — «Что у меня сегодня?»: выполняется как новая, снимает открытый вопрос;
- continuation  — «нет, давай в двенадцать» / «в 15» / «нет, в 11»: применяется к фокусу,
                  никогда не создаёт запись;
- confirm       — «да»;
- reject        — «нет».

Команды NLU здесь такие, какие он отдаёт сейчас — с его ошибками: продолжение приходит
как создание задачи «Нет», «нет, в 11» — как REJECT без времени. Диалог обязан справиться.
«Сейчас» — ср, 21.10.2026 14:00.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest

from app.contracts import Command, Intent, Item, ItemRef, ItemType, Slots, TimeSpec
from tests.dialog.conftest import MSK, Harness
from tests.dialog.phrases import build_nlu
from tests.dialog.scripted_nlu import at, on_day, time_only

M, T, R = ItemType.MEETING, ItemType.TASK, ItemType.REMINDER


def c(intent: Intent, conf: float = 0.92, **slots: object) -> Command:
    return Command(
        intent=intent, source="rules", raw_text="", confidence=conf, slots=Slots(**slots)
    )  # type: ignore[arg-type]


def dt(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=MSK)


# ---------- фразы четырёх типов ----------

FULL = "Что у меня сегодня?"
CONT = "нет, давай в двенадцать"
YES = "да"
NO = "нет"


def nlu_for_table():
    n = build_nlu()
    add = n.add
    # continuation: NLU принимает за создание задачи «Нет» на сегодня 12:00 (has_date=False)
    add(CONT, lambda now, s: c(Intent.CREATE, conf=0.5, type=T, title="Нет",
                               when=time_only(now, 12, text="в двенадцать")))  # fmt: skip
    add("нет, в 11", c(Intent.REJECT, conf=0.95))
    add("давай в 17", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 17, text="в 17")))
    add("в 15", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 15, text="в 15")))
    # сценарии состояний
    add(
        "поставь встречу с Олегом в пятницу в одиннадцать",
        lambda now, s: c(Intent.CREATE, type=M, title="Встреча с Олегом", participants=["Олег"],
                         when=at(now, 2, 11, text="в пятницу в одиннадцать")),
    )  # fmt: skip
    add(
        "найди созвон с Орбитой",
        c(Intent.SEARCH, type=M, organization="Орбита", query="созвон"),
    )
    add(
        "Встречу перенеси на воскресенье в 12",
        lambda now, s: c(Intent.UPDATE, type=M, target=ItemRef(description="Встречу"),
                         new_when=at(now, 4, 12, text="на воскресенье в 12")),
    )  # fmt: skip
    add(
        "Отмени все завтрашние встречи",
        lambda now, s: c(Intent.DELETE, type=M, target=ItemRef(description="__all__"),
                         when=on_day(now, 1, "завтрашние")),
    )  # fmt: skip
    add(
        "Напомни сдать отчёт в пятницу",
        lambda now, s: c(Intent.CREATE, type=R, title="Сдать отчёт",
                         when=on_day(now, 2, "в пятницу")),
    )  # fmt: skip
    return n


async def add_item(h: Harness, type_: ItemType, title: str, when: datetime) -> Item:
    meet = type_ == M
    return await h.repo.create(
        Item(user_id=1, type=type_, title=title, starts_at=when if meet else None,
             ends_at=when + timedelta(hours=1) if meet else None, due_at=None if meet else when)
    )  # fmt: skip


# ---------- состояния: функция приводит Harness в нужное состояние ----------

Setup = Callable[[Harness], Awaitable[dict]]


async def s_none(h: Harness) -> dict:
    return {}


async def s_created(h: Harness) -> dict:
    r = await h.say("поставь встречу с Олегом в пятницу в одиннадцать")
    assert r.action == "created"
    return {"item": r.items[0].id}


async def s_list(h: Harness) -> dict:
    r = await h.say("Что у меня в субботу?")
    assert r.action == "listed"
    return {}


async def s_search(h: Harness) -> dict:
    r = await h.say("найди созвон с Орбитой")
    assert r.action == "found"
    return {}


async def s_await_time(h: Harness) -> dict:
    r = await h.say("Поставь встречу с Иваном завтра")
    assert h.state.awaiting == "time", r.text
    return {}


async def s_await_title(h: Harness) -> dict:
    r = await h.say("Поставь встречу завтра в 12")
    assert h.state.awaiting == "title", r.text
    return {}


async def s_await_target(h: Harness) -> dict:
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    h.state.last_item_ids = []
    r = await h.say("Перенеси встречу завтра на 17:00")
    assert h.state.awaiting == "target", r.text
    return {}


async def s_confirm_action(h: Harness) -> dict:
    await h.say("Что у меня в субботу?")
    r = await h.say("Встречу перенеси на воскресенье в 12")
    assert h.state.awaiting == "confirm_action", r.text
    return {"item": 693}


async def s_confirm_conflict(h: Harness) -> dict:
    r = await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    assert h.state.awaiting == "confirm_conflict", r.text
    return {}


async def s_confirm_delete(h: Harness) -> dict:
    item = (await h.say("Напомни мне завтра в 10 утра позвонить Ивану")).items[0]
    r = await h.say("Удали напоминание позвонить Ивану на завтра")
    assert h.state.awaiting == "confirm_delete", r.text
    return {"item": item.id}


async def s_confirm_many(h: Harness) -> dict:
    a = await add_item(h, M, "Созвон с Альфой", dt(22, 10))
    b = await add_item(h, M, "Встреча с Иваном", dt(22, 13))
    r = await h.say("Отмени все завтрашние встречи")
    assert h.state.awaiting == "confirm_many", r.text
    return {"items": [a.id, b.id]}


async def s_time_optional(h: Harness) -> dict:
    r = await h.say("Напомни сдать отчёт в пятницу")
    assert h.state.awaiting == "time_optional", r.text
    return {"item": r.items[0].id}


STATES: dict[str, Setup] = {
    "none": s_none,
    "created": s_created,
    "list": s_list,
    "search": s_search,
    "await_time": s_await_time,
    "await_title": s_await_title,
    "await_target": s_await_target,
    "confirm_action": s_confirm_action,
    "confirm_conflict": s_confirm_conflict,
    "confirm_delete": s_confirm_delete,
    "confirm_many": s_confirm_many,
    "time_optional": s_time_optional,
}


@pytest.fixture
def h() -> Harness:
    from app.nlu import build_date_resolver

    harness = Harness(nlu_for_table())
    harness.orch.dialog.dates = build_date_resolver(None)  # как в рантайме
    return harness


def titles(h: Harness) -> set[str]:
    return {i.title for i in h.repo.all(1)}


# ---------- 1. Полная команда: всегда новая, снимает вопрос, ничего не меняет ----------


@pytest.mark.parametrize("state", list(STATES))
async def test_full_command_is_new_in_every_state(h, state):
    ctx = await STATES[state](h)
    before = titles(h)
    r = await h.say(FULL)
    assert r.action == "listed" and h.ids(r) == [688, 689], (state, r.text)
    assert h.state.awaiting is None and h.state.pending is None
    assert titles(h) >= before - {""}  # ничего не удалено
    if "item" in ctx and state == "confirm_delete":
        assert await h.repo.get(1, ctx["item"]) is not None


# ---------- 2. Продолжение: к фокусу; никогда не создаёт запись ----------


async def _no_new_titles(h: Harness, before: set[str]) -> None:
    new = titles(h) - before
    assert not ({"Нет", "Её", "Лучше", "Пусть повторяется"} & new), new


async def test_cont_none_asks_and_never_creates(h):
    before = titles(h)
    r = await h.say(CONT)
    assert r.action == "clarify" and r.text.startswith("Не понял, к какой записи")
    assert titles(h) == before


async def test_cont_after_create_changes_that_record(h):
    ctx = await s_created(h)
    before = titles(h)
    r = await h.say(CONT)
    assert r.action == "updated", r.text
    assert (await h.repo.get(1, ctx["item"])).starts_at == dt(23, 12)  # дата та же, время новое
    await _no_new_titles(h, before)


@pytest.mark.parametrize("state", ["list", "search"])
async def test_cont_after_list_or_search_never_creates(h, state):
    await STATES[state](h)
    before = titles(h)
    r = await h.say(CONT)
    assert r.action != "created", r.text
    await _no_new_titles(h, before)


async def test_cont_await_time_answers_question(h):
    await s_await_time(h)
    r = await h.say("в 15")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 15)


async def test_cont_await_title_does_not_create_garbage(h):
    await s_await_title(h)
    before = titles(h)
    r = await h.say(CONT)
    assert r.action == "clarify" and h.state.awaiting == "title"
    await _no_new_titles(h, before)


async def test_cont_await_target_keeps_choice(h):
    await s_await_target(h)
    r = await h.say(CONT)
    assert r.action == "clarify" and h.state.awaiting == "target"


async def test_cont_confirm_action_changes_proposed_time(h):
    """«нет, в 11» на «Перенести … на вс, 12:00?» — поправка, а не отказ."""
    ctx = await s_confirm_action(h)
    r = await h.say("нет, в 11")
    assert r.action == "updated", r.text
    assert (await h.repo.get(1, ctx["item"])).starts_at == dt(25, 11)


async def test_cont_confirm_conflict_takes_named_time(h):
    """«давай в 17» на конфликт — время 17:00, а не предложенные 16:30."""
    await s_confirm_conflict(h)
    r = await h.say("давай в 17")
    assert r.action == "created" and r.items[0].starts_at == dt(29, 17)


async def test_cont_confirm_delete_keeps_record(h):
    ctx = await s_confirm_delete(h)
    r = await h.say(CONT)
    assert r.action == "clarify"
    assert await h.repo.get(1, ctx["item"]) is not None


async def test_cont_confirm_many_keeps_records(h):
    ctx = await s_confirm_many(h)
    r = await h.say(CONT)
    assert r.action == "clarify"
    assert all([await h.repo.get(1, i) is not None for i in ctx["items"]])


async def test_cont_time_optional_moves_reminder(h):
    ctx = await s_time_optional(h)
    r = await h.say("в 15")
    assert r.action == "updated"
    assert (await h.repo.get(1, ctx["item"])).due_at == dt(23, 15)


# ---------- 3. Подтверждение ----------


async def test_yes_none(h):
    r = await h.say(YES)
    assert r.action == "none" and "нечего подтверждать" in r.text


@pytest.mark.parametrize("state", ["created", "list", "search"])
async def test_yes_without_question_changes_nothing(h, state):
    await STATES[state](h)
    before = titles(h)
    r = await h.say(YES)
    assert r.action == "none" and titles(h) == before


async def test_yes_await_time_keeps_question(h):
    await s_await_time(h)
    r = await h.say(YES)
    assert r.action == "clarify" and h.state.awaiting == "time"


async def test_yes_await_title_keeps_question(h):
    await s_await_title(h)
    r = await h.say(YES)
    assert r.action == "clarify" and h.state.awaiting == "title"


async def test_yes_await_target_keeps_question(h):
    await s_await_target(h)
    r = await h.say(YES)
    assert r.action == "clarify" and h.state.awaiting == "target"


async def test_yes_confirm_action_applies(h):
    ctx = await s_confirm_action(h)
    r = await h.say(YES)
    assert r.action == "updated" and (await h.repo.get(1, ctx["item"])).starts_at == dt(25, 12)


async def test_yes_confirm_conflict_takes_suggestion(h):
    await s_confirm_conflict(h)
    r = await h.say(YES)
    assert r.action == "created" and r.items[0].starts_at == dt(29, 16, 30)


async def test_yes_confirm_delete_deletes(h):
    ctx = await s_confirm_delete(h)
    r = await h.say(YES)
    assert r.action == "deleted" and await h.repo.get(1, ctx["item"]) is None


async def test_yes_confirm_many_applies_all(h):
    ctx = await s_confirm_many(h)
    r = await h.say(YES)
    assert r.action == "deleted"
    assert all([await h.repo.get(1, i) is None for i in ctx["items"]])


async def test_yes_time_optional_keeps_time(h):
    ctx = await s_time_optional(h)
    r = await h.say(YES)
    assert r.text == "Хорошо, оставляю так."
    assert (await h.repo.get(1, ctx["item"])).due_at == dt(23, 10)


# ---------- 4. Отказ ----------


@pytest.mark.parametrize("state", ["none", "created", "list", "search"])
async def test_no_without_question_changes_nothing(h, state):
    await STATES[state](h)
    before = titles(h)
    r = await h.say(NO)
    assert r.action == "none" and titles(h) == before


@pytest.mark.parametrize("state", ["await_time", "await_title", "await_target"])
async def test_no_cancels_clarification(h, state):
    await STATES[state](h)
    before = titles(h)
    r = await h.say(NO)
    assert r.action == "none" and r.text == "Хорошо, отменил."
    assert h.state.awaiting is None and titles(h) == before


async def test_no_confirm_action_keeps(h):
    ctx = await s_confirm_action(h)
    r = await h.say(NO)
    assert r.text == "Хорошо, ничего не меняю."
    assert (await h.repo.get(1, ctx["item"])).starts_at == dt(24, 20, 45)


async def test_no_confirm_conflict_cancels(h):
    await s_confirm_conflict(h)
    r = await h.say(NO)
    assert r.action == "none" and not [i for i in h.repo.all(1) if i.title == "Созвон с Маяком"]


async def test_no_confirm_delete_keeps(h):
    ctx = await s_confirm_delete(h)
    r = await h.say(NO)
    assert r.text == "Хорошо, не удаляю." and await h.repo.get(1, ctx["item"]) is not None


async def test_no_confirm_many_keeps(h):
    ctx = await s_confirm_many(h)
    r = await h.say(NO)
    assert r.text == "Хорошо, не удаляю."
    assert all([await h.repo.get(1, i) is not None for i in ctx["items"]])


async def test_no_time_optional_keeps_reminder(h):
    ctx = await s_time_optional(h)
    r = await h.say(NO)
    assert r.text == "Хорошо, оставляю так."
    assert await h.repo.get(1, ctx["item"]) is not None


# ---------- правило 1: полная команда сбрасывает контекст поиска ----------


async def test_full_list_after_search_does_not_inherit_query(h):
    """«Что у меня в субботу?» после поиска: NLU перенёс query — диалог его не наследует."""
    h.nlu.add("Найди всё про ТЗ", c(Intent.SEARCH, query="техническое задание"))
    h.nlu.add(
        "Что у меня в субботу? (после поиска)",
        lambda now, s: c(Intent.SEARCH, query="техническое задание",
                         when=on_day(now, 3, "в субботу")),
    )  # fmt: skip
    await h.say("Найди всё про ТЗ")
    r = await h.say("Что у меня в субботу? (после поиска)")
    assert r.action == "listed" and h.ids(r) == [691, 692, 693], r.text
    assert r.debug["intent"] == "list_items" and r.debug["grounded_from"] == "search_items"


# ---------- правило 4: из продолжения не создаётся запись ----------


@pytest.mark.parametrize(
    "phrase,bad_title",
    [
        ("сделай её каждую неделю", "Её"),
        ("лучше в пятницу", "Лучше"),
        ("пусть повторяется", "Пусть повторяется"),
    ],
)
async def test_continuation_titles_never_created(h, phrase, bad_title):
    await s_created(h)
    h.nlu.add(phrase, lambda now, s, t=bad_title: c(Intent.CREATE, type=T, title=t,
                                                    recurrence="FREQ=WEEKLY" if "повтор" in phrase
                                                    or "кажд" in phrase else None,
                                                    when=on_day(now, 2, "в пятницу")
                                                    if "пятниц" in phrase else None))  # fmt: skip
    r = await h.say(phrase)
    assert r.action != "created", r.text
    assert bad_title not in titles(h)


async def test_repeat_continuation_makes_series(h):
    ctx = await s_created(h)
    h.nlu.add(
        "сделай её каждую неделю",
        c(Intent.CREATE, type=T, title="Её", recurrence="FREQ=WEEKLY"),
    )
    r = await h.say("сделай её каждую неделю")
    assert r.action == "updated"
    assert (await h.repo.get(1, ctx["item"])).rrule == "FREQ=WEEKLY;BYDAY=FR"
    assert r.text.startswith("Теперь «Встреча с Олегом» повторяется: каждую пятницу")


async def test_participant_continuation_adds_person(h):
    ctx = await s_created(h)
    h.nlu.add("и с Марией", c(Intent.CREATE, type=M, participants=["Мария"]))
    r = await h.say("и с Марией")
    assert r.action == "updated"
    item = await h.repo.get(1, ctx["item"])
    assert "Мария" in item.person and item.title == "Встреча с Олегом и Марией"


async def test_list_period_continuation(h):
    """«а послезавтра?» после списка — тот же список на другой день (NLU отдал создание)."""
    await h.say("Что у меня сегодня?")
    h.nlu.add("а послезавтра?", lambda now, s: c(Intent.CREATE, type=T, title="А",
                                                 when=on_day(now, 2, "послезавтра")))  # fmt: skip
    r = await h.say("а послезавтра?")
    assert r.action == "listed" and r.text.startswith("В пятницу")


async def test_bare_name_with_verb_is_full_command(h):
    """«ладно, поставь Нину на шесть вечера» — создание, а не правка последней записи."""
    await s_created(h)
    h.nlu.add(
        "ладно, поставь Нину на шесть вечера",
        lambda now, s: c(Intent.CREATE, type=M, title="Встреча с Ниной", participants=["Нина"],
                         when=at(now, 1, 18, text="на шесть вечера")),
    )  # fmt: skip
    r = await h.say("ладно, поставь Нину на шесть вечера")
    assert r.action == "created" and r.items[0].title == "Встреча с Ниной"


def test_classify_examples():
    from app.contracts import DialogState
    from app.dialog.turns import classify_turn

    st = DialogState(session_id="x", user_id=1)

    def k(text: str, intent: Intent = Intent.UNKNOWN, **slots: object) -> str:
        return classify_turn(
            Command(intent=intent, source="rules", raw_text=text, slots=Slots(**slots)),
            st,  # type: ignore[arg-type]
        )

    assert k("Что у меня сегодня?", Intent.LIST) == "full"
    assert k("Найди встречу с Иваном", Intent.SEARCH) == "full"
    assert k("перенеси её на пятницу", Intent.UPDATE) == "full"
    assert k("нет, давай в двенадцать") == "continuation"
    assert k("нет, в 11", Intent.REJECT) == "continuation"
    assert k("давай её в понедельник") == "continuation"
    assert k("пусть повторяется") == "continuation"
    assert k("сделай её каждую неделю") == "continuation"
    assert k("и с Марией", participants=["Мария"]) == "continuation"
    assert k("а послезавтра?") == "continuation"
    assert k("в три") == "continuation"
    assert k("да", Intent.CONFIRM) == "confirm"
    assert k("ну да, давай") == "confirm"
    assert k("нет", Intent.REJECT) == "reject"
    assert k("нет, не надо") == "reject"
    assert k("отмени", Intent.UNDO) == "full"
    _ = TimeSpec


# ---------- случаи из adversarial (вторая порция v7) ----------


async def test_time_optional_bare_time_as_create(h):
    """«15:00» на «сказать другое время?»: NLU отдал создание без названия — это ответ."""
    ctx = await s_time_optional(h)
    h.nlu.add("15:00", lambda now, s: c(Intent.CREATE, conf=0.5, type=T,
                                        when=time_only(now, 15, text="15:00")))  # fmt: skip
    r = await h.say("15:00")
    assert r.action == "updated", r.text
    assert (await h.repo.get(1, ctx["item"])).due_at == dt(23, 15)


async def test_created_then_list_like_continuation_edits_record(h):
    """«в субботу в 12» сразу после создания: NLU отдал список — правим созданную встречу."""
    ctx = await s_created(h)
    before = titles(h)
    sat = "в субботу в 12"
    h.nlu.add("давай в субботу в 12", lambda now, s: c(Intent.LIST, when=at(now, 3, 12, text=sat)))
    r = await h.say("давай в субботу в 12")
    assert r.action == "updated", r.text
    assert (await h.repo.get(1, ctx["item"])).starts_at == dt(24, 12)
    assert r.debug["intent"] == "update_item" and r.debug["nlu_intent"] == "list_items"
    assert titles(h) == before


async def test_search_refined_by_tense(h):
    """«а прошлые?» после поиска — тот же поиск, только прошлое; ничего не создаётся."""
    await add_item(h, M, "Созвон с Орбитой", dt(14, 10))
    await s_search(h)
    before = titles(h)
    h.nlu.add("а прошлые?", c(Intent.CREATE, conf=0.4, type=T, title="Прошлые"))
    r = await h.say("а прошлые?")
    assert r.action == "found", r.text
    assert titles(h) == before


def test_tense_refinement_vs_time():
    from app.dialog.turns import continuation_of

    def cont(text: str):
        return continuation_of(Command(intent=Intent.UNKNOWN, source="rules", raw_text=text), text)

    assert cont("а следующая?").tense == "future"
    assert cont("а прошлые?").tense == "past"
    assert cont("давай на следующую неделю").tense is None

"""Регрессии живого теста 23.10: текст конфликта, перенос рядом со своим старым местом,
поиск переносимой записи по точному времени. «Сейчас» — ср, 21.10.2026 14:00 МСК."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contracts import Command, Intent, Item, ItemRef, ItemType, Slots
from tests.dialog.conftest import MSK, Harness
from tests.dialog.phrases import build_nlu
from tests.dialog.scripted_nlu import at

M, T = ItemType.MEETING, ItemType.TASK


def c(intent: Intent, **slots: object) -> Command:
    return Command(intent=intent, source="rules", raw_text="", slots=Slots(**slots))  # type: ignore[arg-type]


def dt(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=MSK)


async def add(h: Harness, type_: ItemType, title: str, when: datetime) -> Item:
    meeting = type_ == ItemType.MEETING
    return await h.repo.create(
        Item(
            user_id=1,
            type=type_,
            title=title,
            starts_at=when if meeting else None,
            ends_at=when + timedelta(hours=1) if meeting else None,
            due_at=None if meeting else when,
        )
    )


@pytest.fixture
def h() -> Harness:
    n = build_nlu()
    # как отдаёт NLU чата 1 (сверено на настоящем NLU)
    n.add(
        "Поставь встречу с Иваном завтра в 10:00",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Иваном",
            participants=["Иван"],
            when=at(now, 1, 10, text="завтра в 10:00"),
        ),
    )
    n.add(
        "Перенеси встречу с завтра 9:45 на 10:00",
        lambda now, s: c(
            Intent.UPDATE,
            type=M,
            target=ItemRef(description="встречу"),
            when=at(now, 1, 9, 45, "с завтра 9:45"),
            new_when=at(now, 1, 10, text="на 10:00"),
        ),
    )
    return Harness(n, seed=False)


async def test_conflict_names_time_of_the_other_item(h):
    """Было: «Завтра в 10:00 у вас уже есть «Проверить смету»», а смета в 10:45."""
    await add(h, T, "Проверить смету", dt(22, 10, 45))
    r = await h.say("Поставь встречу с Иваном завтра в 10:00")
    assert r.action == "conflict"
    assert r.text.startswith("Завтра в 10:45 у вас «Проверить смету». Поставить на ")
    assert r.text.endswith("или всё равно на 10:00?")


async def test_conflict_with_several_names_each_time(h):
    await add(h, M, "Встреча с Олегом", dt(22, 9, 45))
    await add(h, T, "Проверить смету", dt(22, 10, 45))
    r = await h.say("Поставь встречу с Иваном завтра в 10:00")
    assert r.text.startswith(
        "Завтра на это время уже есть: в 09:45 «Встреча с Олегом», в 10:45 «Проверить смету»."
    )


async def test_move_does_not_offer_own_old_slot(h):
    """Было: перенос 9:45 → 10:00 → «Поставить на 09:45 или всё равно на 10:00?»."""
    m = await add(h, M, "Встреча с Олегом", dt(22, 9, 45))
    await add(h, T, "Проверить смету", dt(22, 10, 45))
    r = await h.say("Перенеси встречу с завтра 9:45 на 10:00")
    assert r.action == "conflict"
    assert m.id not in h.ids(r)  # сама запись — не пересечение
    assert "09:45" not in r.text and "09:30" not in r.text
    assert (
        r.text
        == "Завтра в 10:45 у вас «Проверить смету». Поставить на 11:00 или всё равно на 10:00?"
    )
    r = await h.say("да")
    assert r.action == "updated" and (await h.repo.get(1, m.id)).starts_at == dt(22, 11)


async def test_move_force_original_request(h):
    m = await add(h, M, "Встреча с Олегом", dt(22, 9, 45))
    await add(h, T, "Проверить смету", dt(22, 10, 45))
    await h.say("Перенеси встречу с завтра 9:45 на 10:00")
    r = await h.say("Всё равно 15:30")  # «всё равно» → запрошенные 10:00
    assert r.action == "updated" and (await h.repo.get(1, m.id)).starts_at == dt(22, 10)


async def test_target_exact_time_wins_over_neighbour(h):
    """Было: «Нашёл несколько подходящих» — рядом в окне ±30 мин стояла встреча на 10:00."""
    m = await add(h, M, "Встреча с Олегом", dt(22, 9, 45))
    await add(h, M, "Созвон с Альфой", dt(22, 10))
    r = await h.say("Перенеси встречу с завтра 9:45 на 10:00")
    assert "Какую именно" not in r.text
    # переносится именно 9:45; на 10:00 стоит созвон — это конфликт, а не выбор цели
    assert r.action == "conflict"
    assert r.text.startswith("Завтра в 10:00 у вас «Созвон с Альфой».")
    assert h.state.pending.slots.target.item_id == m.id


# ============ 4. Напоминание с датой без времени — 10:00 без пустого вопроса ============


@pytest.fixture
def r4(h: Harness) -> Harness:
    from tests.dialog.scripted_nlu import on_day, time_only

    n = h.nlu
    n.add(
        "Через неделю нам нужно сдать тестовое, напомни пж",
        lambda now, s: c(
            Intent.CREATE,
            type=ItemType.REMINDER,
            title="Сдать тестовое",
            when=on_day(now, 7, "через неделю"),
        ),
    )
    n.add(
        "Напомни сегодня позвонить маме",
        lambda now, s: c(
            Intent.CREATE,
            type=ItemType.REMINDER,
            title="Позвонить маме",
            when=on_day(now, 0, "сегодня"),
        ),
    )
    n.add("в 15", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 15, text="в 15")))
    n.add("Другое время", c(Intent.UNKNOWN))
    n.add("Отменить", c(Intent.UNDO))
    n.add(
        "Поставь встречу завтра",
        lambda now, s: c(Intent.CREATE, type=M, title="Встреча", when=on_day(now, 1, "завтра")),
    )
    return h


async def test_reminder_without_time_defaults_to_10(r4):
    r = await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")
    assert r.action == "created"
    assert r.text == "Напомню сдать тестовое в ср, 28 октября, в 10:00 — сказать другое время?"
    assert r.items[0].due_at == dt(28, 10)
    assert r.suggestions == ["Другое время", "Отменить"]


async def test_default_time_can_be_changed_by_answer(r4):
    item = (await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")).items[0]
    r = await r4.say("в 15")
    assert r.action == "updated"
    assert (await r4.repo.get(1, item.id)).due_at == dt(28, 15)


async def test_default_time_other_time_button(r4):
    item = (await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")).items[0]
    r = await r4.say("Другое время")
    assert r.action == "clarify" and r.text == "Во сколько напомнить?"
    await r4.say("в 15")
    assert (await r4.repo.get(1, item.id)).due_at == dt(28, 15)


async def test_default_time_cancel_button_removes_reminder(r4):
    item = (await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")).items[0]
    r = await r4.say("Отменить")
    assert r.action == "undone"
    assert await r4.repo.get(1, item.id) is None


async def test_default_time_keeps_on_yes_and_new_command_passes(r4):
    item = (await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")).items[0]
    r = await r4.say("да")
    assert r.text == "Хорошо, оставляю так."
    await r4.say("Через неделю нам нужно сдать тестовое, напомни пж")
    r = await r4.say("Что у меня сегодня?")  # новая команда, а не ответ
    assert r.action == "listed" and r4.state.awaiting is None
    assert (await r4.repo.get(1, item.id)).due_at == dt(28, 10)


async def test_today_after_10_uses_next_hour(r4):
    r = await r4.say("Напомни сегодня позвонить маме")  # сейчас 14:00
    assert r.items[0].due_at == dt(21, 15)
    assert "в 15:00 — сказать другое время?" in r.text


async def test_meeting_without_time_still_asks(r4):
    r = await r4.say("Поставь встречу завтра")
    assert r.action == "clarify" and r.text == "На какое время поставить встречу?"


# ============ Логи стенда 23.10 (вечер) ============


@pytest.fixture
def s6() -> Harness:
    from tests.dialog.scripted_nlu import on_day, period, time_only

    n = build_nlu()
    R = ItemType.REMINDER
    add_ = n.add
    # 1. «Что» как создание — так бывало на стенде
    add_("Что", c(Intent.CREATE, type=T, title="Что"))
    add_(
        "Что в пятницу",
        lambda now, s: c(Intent.CREATE, type=R, title="Что", when=on_day(now, 2, "в пятницу")),
    )
    # 2. ответ на «Когда напомнить?» NLU принимает за поиск / встречу
    add_("Напомни сдать отчёт", c(Intent.CREATE, type=R, title="Сдать отчёт"))
    add_("в последний день месяца", c(Intent.SEARCH, query="день месяц"))
    add_(
        "в последний день месяца нужно сдать отчёт",
        c(Intent.CREATE, type=T, title="В последний день месяца сдать отчёт"),
    )
    add_(
        "завтра в обед",
        lambda now, s: c(Intent.CREATE, type=M, when=at(now, 1, 13, text="завтра в обед")),
    )
    # 3. прошлое
    add_(
        "Поставь задачу купить хлеб на вчера",
        lambda now, s: c(
            Intent.CREATE, type=T, title="Купить хлеб", when=on_day(now, -1, "на вчера")
        ),
    )
    add_(
        "Поставь встречу сегодня в 9 с Иваном",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Иваном",
            when=at(now, 0, 9, text="сегодня в 9"),
        ),
    )
    add_(
        "Напомни вчера позвонить маме",
        lambda now, s: c(
            Intent.CREATE, type=R, title="Позвонить маме", when=on_day(now, -1, "вчера")
        ),
    )
    # 4. продолжение списка
    add_(
        "Что у меня на следующей неделе?",
        lambda now, s: c(Intent.LIST, when=period(now, 5, 11, "на следующей неделе")),
    )
    add_("а потом что", c(Intent.LIST, tense="future"))
    add_("дальше", c(Intent.UNKNOWN))
    add_(
        "а на этой неделе?",
        lambda now, s: c(Intent.LIST, when=period(now, -2, 4, "на этой неделе")),
    )
    # 5. название напоминания
    add_(
        "Нам будет сдать тестовое на хакатон через неделю, напомни пж",
        lambda now, s: c(
            Intent.CREATE,
            type=R,
            title="Нам сдать тестовое на хакатон , напомни пж",
            when=on_day(now, 7, "через неделю"),
        ),
    )
    # 6. длительность
    add_(
        "Сколько длится встреча с Марией по интеграции?",
        c(Intent.SEARCH, type=M, participants=["Мария"], query="длиться интеграция"),
    )
    add_(
        "Сколько длится проверка сметы?",
        c(Intent.SEARCH, query="длиться смета"),
    )
    add_("в 15", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 15, text="в 15")))
    return Harness(n)


async def test_empty_title_is_not_created(s6):
    """«Что» — не название: не спрашиваем время и не создаём, а спрашиваем, что записать."""
    before = len(s6.repo.all(1))
    r = await s6.say("Что")
    assert r.action == "clarify" and r.text.startswith("Не понял, что записать.")
    s6.state = type(s6.state)(session_id="s2", user_id=1)
    r = await s6.say("Что в пятницу")
    assert r.action == "clarify" and r.text.startswith("Не понял, что записать.")
    assert len(s6.repo.all(1)) == before


async def test_answer_with_time_words_keeps_type(s6):
    r = await s6.say("Напомни сдать отчёт")
    assert r.text == "Когда напомнить сдать отчёт?"
    r = await s6.say("в последний день месяца")
    assert r.action == "created" and r.items[0].type == "reminder"
    assert r.items[0].title == "Сдать отчёт"
    assert r.items[0].due_at == dt(31, 10)


async def test_answer_as_titleless_meeting_keeps_type(s6):
    await s6.say("Напомни сдать отчёт")
    r = await s6.say("завтра в обед")  # NLU: create meeting без названия
    assert r.action == "created" and r.items[0].type == "reminder"
    assert "встреч" not in r.text


async def test_month_end_in_title_becomes_date(s6):
    r = await s6.say("в последний день месяца нужно сдать отчёт")
    assert r.action == "created"
    assert r.items[0].title == "Сдать отчёт" and r.items[0].due_at == dt(31, 0)


async def test_create_in_past_day_asks(s6):
    r = await s6.say("Поставь задачу купить хлеб на вчера")
    assert r.action == "clarify"
    assert r.text == "Вчера уже прошло — поставить на сегодня?"
    r = await s6.say("да")
    assert r.action == "created" and r.items[0].due_at == dt(21, 0)


async def test_create_in_past_time_today_asks(s6):
    r = await s6.say("Поставь встречу сегодня в 9 с Иваном")  # сейчас 14:00
    assert r.text == "Сегодня в 09:00 уже прошло — поставить на завтра, 09:00?"
    r = await s6.say("нет")
    assert r.action == "none"
    assert not [i for i in s6.repo.all(1) if i.title == "Встреча с Иваном"]


async def test_reminder_in_past_asks_not_silent_today(s6):
    r = await s6.say("Напомни вчера позвонить маме")
    assert r.text == "Вчера уже прошло — поставить на сегодня?"
    r = await s6.say("да")
    assert r.action == "created" and r.items[0].due_at == dt(21, 15)  # сегодня, через час


async def test_list_continuation(s6):
    r = await s6.say("Что у меня на следующей неделе?")
    first = s6.ids(r)
    shown = list(s6.state.last_item_ids)
    assert len(shown) == 3
    r = await s6.say("а потом что")
    assert r.action == "listed" and r.text.startswith("Дальше:")
    nxt = s6.ids(r)
    assert nxt and not set(nxt) & set(shown)
    assert nxt == first[3 : 3 + len(nxt)]  # ровно следующая порция
    r = await s6.say("дальше")
    assert not set(s6.ids(r)) & set(nxt)
    while r.items:
        r = await s6.say("дальше")
    assert r.text == "Это всё на следующей неделе."


async def test_new_period_is_not_continuation(s6):
    await s6.say("Что у меня на следующей неделе?")
    r = await s6.say("а на этой неделе?")
    assert r.text.startswith("На этой неделе у вас")


async def test_reminder_title_is_clean_in_reply(s6):
    r = await s6.say("Нам будет сдать тестовое на хакатон через неделю, напомни пж")
    assert r.items[0].title == "Сдать тестовое на хакатон"
    assert r.text == (
        "Напомню сдать тестовое на хакатон в ср, 28 октября, в 10:00 — сказать другое время?"
    )


async def test_duration_of_meeting(s6):
    r = await s6.say("Сколько длится встреча с Марией по интеграции?")
    assert r.text == "«Встреча с Марией по интеграции» (сб, 24 октября) длится 1 час: 20:45–21:45."


async def test_duration_not_set_for_task(s6):
    r = await s6.say("Сколько длится проверка сметы?")
    assert "длительность не указана" in r.text

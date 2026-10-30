"""Регрессии живого теста 22.10 (docs: «10_Разбор_живого_теста_22_10»): выбор цели,
«последняя», массовые действия, подтверждение рискованных действий, окно и варианты времени.

Command'ы ниже — формат, который диалог ждёт от NLU (чат 1, nlu/v3), в рамках контракта v1.2:
- цель по дате/времени — slots.when (как «встречу с Марией в субботу»), новое время — new_when;
- «последнюю задачу» — target.description="__last__";
- «все встречи на завтра» — target.description="__all__" + type + when (день или период);
- «в обед / утром / вечером» — when.ambiguous=True, start (+ end) = окно;
- «в 5 или в 6» — when.ambiguous=True, в when.text есть «или / либо / может быть».
«Сейчас» — ср, 21.10.2026 14:00 МСК.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contracts import Command, Intent, Item, ItemRef, ItemType, Slots, TimeSpec
from tests.dialog.conftest import MSK, Harness
from tests.dialog.phrases import build_nlu
from tests.dialog.scripted_nlu import at, on_day, time_only

M, T = ItemType.MEETING, ItemType.TASK


def c(intent: Intent, conf: float = 0.95, **slots: object) -> Command:
    return Command(
        intent=intent, source="rules", raw_text="", confidence=conf, slots=Slots(**slots)
    )  # type: ignore[arg-type]


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
def live() -> Harness:
    n = build_nlu()
    add_ = n.add

    # 1. выбор цели по дате/времени
    add_(
        "Перенеси встречу с 23 октября на 8:00 на 24 октября 8:00",
        lambda now, s: c(
            Intent.UPDATE,
            type=M,
            target=ItemRef(description="встречу"),
            when=at(now, 2, 8, text="23 октября на 8:00"),
            new_when=at(now, 3, 8, text="24 октября 8:00"),
        ),
    )
    add_(
        "Перенеси встречу на 19:00 завтрашнего дня на послезавтра на 22:30",
        lambda now, s: c(
            Intent.UPDATE,
            type=M,
            target=ItemRef(description="встречу"),
            when=at(now, 1, 19, text="на 19:00 завтрашнего дня"),
            new_when=at(now, 2, 22, 30, "на послезавтра на 22:30"),
        ),
    )
    add_(
        "Перенеси задачу, которая на пятницу, 23 октября, 8:00, на субботу",
        lambda now, s: c(
            Intent.UPDATE,
            type=T,
            target=ItemRef(description="задачу"),
            when=at(now, 2, 8, text="на пятницу, 23 октября, 8:00"),
            new_when=on_day(now, 3, "на субботу"),
        ),
    )
    # 2. «последняя»
    add_(
        "Перенеси последнюю задачу на один день вперёд",
        c(
            Intent.UPDATE,
            type=T,
            target=ItemRef(description="__last__"),
            new_when=TimeSpec(text="на один день вперёд"),
        ),
    )
    add_(
        "Добавь задачу купить подарок завтра в 18",
        lambda now, s: c(
            Intent.CREATE, type=T, title="Купить подарок", when=at(now, 1, 18, text="завтра в 18")
        ),
    )
    # 3. массовые действия
    add_(
        "Отмени все завтрашние встречи",
        lambda now, s: c(
            Intent.DELETE,
            type=M,
            target=ItemRef(description="__all__"),
            when=on_day(now, 1, "завтрашние"),
        ),
    )
    add_(
        "Отмени все встречи",
        c(Intent.DELETE, type=M, target=ItemRef(description="__all__")),
    )
    add_("Отмени все", c(Intent.DELETE, target=ItemRef(description="__all__")))
    add_(
        "Отметь все задачи на сегодня выполненными",
        lambda now, s: c(
            Intent.COMPLETE,
            type=T,
            target=ItemRef(description="__all__"),
            when=on_day(now, 0, "на сегодня"),
        ),
    )
    add_(
        "Перенеси все встречи на завтра на час позже",
        lambda now, s: c(
            Intent.UPDATE,
            type=M,
            target=ItemRef(description="__all__"),
            when=on_day(now, 1, "на завтра"),
            new_when=TimeSpec(text="на час позже"),
        ),
    )
    # 4. безопасность: «Удали эту задачу» → ASR срезал «У» → «Дали эту задачу»
    add_(
        "Дали эту задачу",
        c(
            Intent.COMPLETE,
            conf=0.6,
            type=T,
            target=ItemRef(pronoun=True, description="эту задачу"),
        ),
    )
    add_(
        "Отметь эту задачу выполненной",
        c(Intent.COMPLETE, type=T, target=ItemRef(pronoun=True, description="эту задачу")),
    )
    add_(
        "Перенеси её на завтра",
        lambda now, s: c(
            Intent.UPDATE, target=ItemRef(pronoun=True), new_when=on_day(now, 1, "на завтра")
        ),
    )
    # 5. окно «в обед»
    add_(
        "Нужна встреча завтра в обед, что посоветуешь?",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча",
            when=TimeSpec(
                text="завтра в обед",
                start=dt(22, 12),
                has_time=True,
                has_date=True,
                ambiguous=True,
            ),
        ),
    )
    add_("Да, 12:00", c(Intent.CONFIRM))
    add_("Да, 13:00", c(Intent.CONFIRM))
    add_(
        "Напомни вечером позвонить маме",
        lambda now, s: c(
            Intent.CREATE,
            type=ItemType.REMINDER,
            title="Позвонить маме",
            when=TimeSpec(
                text="вечером",
                start=dt(21, 18),
                end=dt(21, 21),
                has_time=True,
                has_date=False,
                ambiguous=True,
            ),
        ),
    )
    # 6. два варианта времени
    add_(
        "Поставь встречу с Олегом завтра, может быть, в 5 часов вечера, может быть, в 6:00",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Олегом",
            participants=["Олег"],
            when=TimeSpec(
                text="завтра, может быть, в 5 часов вечера, может быть, в 6:00",
                start=dt(22, 17),
                end=dt(22, 18),
                has_time=True,
                has_date=True,
                ambiguous=True,
            ),
        ),
    )
    add_(
        "Созвон с Вектором завтра в 5 или в 6",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Созвон с Вектором",
            organization="Вектор",
            when=TimeSpec(
                text="завтра в 5 или в 6",
                start=dt(22, 17),
                has_time=True,
                has_date=True,
                ambiguous=True,
            ),
        ),
    )
    add_("17:00", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 17, text="17:00")))
    add_("18:00", lambda now, s: c(Intent.UNKNOWN, when=time_only(now, 18, text="18:00")))
    return Harness(n)


# ====================== 1. Выбор цели по дате и времени ======================


async def test_target_by_date_and_time_not_by_word(live):
    m = await add(live, M, "Пара по английскому", dt(23, 8))
    await add(live, M, "Встреча с Анной", dt(23, 15))  # тот же день, другое время
    r = await live.say("Перенеси встречу с 23 октября на 8:00 на 24 октября 8:00")
    assert r.action == "updated", r.text
    assert live.ids(r) == [m.id]
    assert r.text == "Перенёс «Пара по английскому» на сб, 24 октября, 08:00."


async def test_target_date_tomorrow_not_today(live):
    await add(live, M, "Встреча сегодня вечером", dt(21, 19))  # сегодня, то же время
    m = await add(live, M, "Ужин с партнёрами", dt(22, 19))
    r = await live.say("Перенеси встречу на 19:00 завтрашнего дня на послезавтра на 22:30")
    assert r.action == "updated", r.text
    assert live.ids(r) == [m.id]
    assert r.text.endswith("на пт, 23 октября, 22:30.")


async def test_target_task_by_date_time(live):
    t = await add(live, T, "Сдать лабораторную", dt(23, 8))
    r = await live.say("Перенеси задачу, которая на пятницу, 23 октября, 8:00, на субботу")
    assert r.action == "updated" and live.ids(r) == [t.id]
    assert r.text.endswith("на сб, 24 октября, 08:00.")


async def test_target_time_window_is_30_minutes(live):
    """Встреча в 08:20 попадает в «на 8:00», в 09:00 — уже нет (кандидат один)."""
    near = await add(live, M, "Планёрка", dt(23, 8, 20))
    await add(live, M, "Обход", dt(23, 9))
    r = await live.say("Перенеси встречу с 23 октября на 8:00 на 24 октября 8:00")
    assert live.ids(r) == [near.id]


# ====================== 2. «Последняя» ======================


async def test_last_task_is_last_created_in_session(live):
    created = (await live.say("Добавь задачу купить подарок завтра в 18")).items[0]
    await live.say("Что у меня сегодня?")  # контекст сменился — «последняя» всё равно созданная
    r = await live.say("Перенеси последнюю задачу на один день вперёд")
    assert r.action == "updated" and live.ids(r) == [created.id]
    assert r.text == "Перенёс «Купить подарок» на пт, 23 октября, 18:00."


async def test_last_task_without_history_is_nearest_upcoming(live):
    r = await live.say("Перенеси последнюю задачу на один день вперёд")
    assert r.action == "updated" and live.ids(r) == [690]  # чт 22.10 09:15
    assert r.text.endswith("на пт, 23 октября, 09:15.")


# ====================== 3. Массовые действия ======================


async def test_delete_all_tomorrow_meetings_confirm_and_undo_all(live):
    ms = [
        await add(live, M, "Созвон с Альфой", dt(22, 10)),
        await add(live, M, "Встреча с Иваном", dt(22, 13)),
        await add(live, M, "Демо для Пульса", dt(22, 16)),
    ]
    r = await live.say("Отмени все завтрашние встречи")
    assert r.action == "clarify" and live.state.awaiting == "confirm_many"
    assert r.text.startswith("Удалить 3 встречи на завтра?\n• чт, 22 октября, 10:00 — Созвон")
    assert r.suggestions == ["Да", "Нет"]
    assert {i.id for i in r.items} == {m.id for m in ms}

    r = await live.say("да")
    assert r.action == "deleted"
    assert r.text == "Удалил 3 встречи. Если передумаете — скажите «отмени»."
    assert all([await live.repo.get(1, m.id) is None for m in ms])
    assert await live.repo.get(1, 690) is not None  # задача на завтра не тронута

    r = await live.say("отмени")
    assert r.action == "undone" and r.text == "Вернул 3 записи."
    assert all([await live.repo.get(1, m.id) is not None for m in ms])
    assert (await live.say("отмени")).text == "Отменять нечего."


async def test_delete_all_rejected(live):
    await add(live, M, "Созвон с Альфой", dt(22, 10))
    await live.say("Отмени все завтрашние встречи")
    r = await live.say("нет")
    assert r.text == "Хорошо, не удаляю."
    assert len(await live.repo.list_range(1, dt(22, 0), dt(23, 0), M)) == 1


async def test_delete_all_without_period_shows_count(live):
    r = await live.say("Отмени все встречи")
    assert r.action == "clarify"
    upcoming = [
        i for i in live.repo.all(1) if i.type == M and i.starts_at and i.starts_at >= live.now
    ]
    assert r.text.startswith(f"Удалить все предстоящие встречи — {len(upcoming)} встреч")
    assert "…и ещё" in r.text and len(r.items) == 3
    r = await live.say("нет")
    assert len([i for i in live.repo.all(1) if i.type == M]) >= len(upcoming)


async def test_delete_everything_needs_confirmation(live):
    r = await live.say("Отмени все")
    assert r.action == "clarify" and r.text.startswith("Удалить все предстоящие записи —")
    before = len(live.repo.all(1))
    await live.say("нет")
    assert len(live.repo.all(1)) == before


async def test_complete_all_today_tasks(live):
    r = await live.say("Отметь все задачи на сегодня выполненными")
    assert r.text.startswith("Отметить выполненными 1 задачу на сегодня?")
    r = await live.say("да")
    assert r.action == "completed" and live.ids(r) == [689]
    r = await live.say("отмени")
    assert r.action == "undone"


async def test_shift_all_tomorrow_meetings(live):
    a = await add(live, M, "Созвон с Альфой", dt(22, 10))
    b = await add(live, M, "Встреча с Иваном", dt(22, 13))
    r = await live.say("Перенеси все встречи на завтра на час позже")
    assert r.text.startswith("Перенести 2 встречи на завтра на час позже?")
    await live.say("да")
    assert (await live.repo.get(1, a.id)).starts_at == dt(22, 11)
    assert (await live.repo.get(1, b.id)).starts_at == dt(22, 14)
    r = await live.say("отмени")
    assert r.text == "Вернул как было: 2 записи."
    assert (await live.repo.get(1, a.id)).starts_at == dt(22, 10)


async def test_nothing_to_mass_delete(live):
    r = await live.say("Отмени все завтрашние встречи")
    assert r.action == "none" and r.text == "Завтра встреч нет."


# ====================== 4. Безопасность опасных действий ======================


async def test_low_confidence_pronoun_complete_asks(live):
    """Живой случай: «Дали эту задачу» — нельзя молча отмечать выполненной."""
    await live.say("Добавь задачу купить подарок завтра в 18")
    r = await live.say("Дали эту задачу")
    assert r.action == "clarify" and live.state.awaiting == "confirm_action"
    assert r.text == "Отметить выполненной «Купить подарок» (чт, 22 октября, 18:00)?"
    r = await live.say("нет")
    assert r.text == "Хорошо, ничего не меняю."
    assert (await live.repo.get(1, r.items[0].id if r.items else 1001)).status == "open"


async def test_pronoun_with_several_in_context_asks(live):
    await live.say("Что у меня сегодня?")  # в контексте 688 (встреча) и 689 (задача)
    r = await live.say("Отметь эту задачу выполненной")
    assert r.action == "clarify"
    assert r.text.startswith("Отметить выполненной «Подготовиться к встрече с Пульсом»")
    r = await live.say("да")
    assert r.action == "completed" and live.ids(r) == [689]


async def test_pronoun_update_with_several_in_context_asks(live):
    await live.say("Что у меня сегодня?")
    r = await live.say("Перенеси её на завтра")
    # две записи в контексте и нет типа — выбор, не молчаливый перенос
    assert r.action == "clarify"
    assert live.state.awaiting in ("target", "confirm_action")


async def test_confident_single_context_acts_without_question(live):
    created = (await live.say("Добавь задачу купить подарок завтра в 18")).items[0]
    r = await live.say("Отметь эту задачу выполненной")
    assert r.action == "completed" and live.ids(r) == [created.id]


# ====================== 5. Окно «в обед / вечером» ======================


async def test_lunch_window_creates_at_usual_time(live):
    """QA-20: день и время («в обед») названы — не переспрашиваем, ставим на 13:00."""
    r = await live.say("Нужна встреча завтра в обед, что посоветуешь?")
    assert r.action == "created", r.text
    assert r.items[0].starts_at == dt(22, 13)


async def test_lunch_window_busy_offers_free_slot_in_window(live):
    await add(live, M, "Обед с командой", dt(22, 13))
    r = await live.say("Нужна встреча завтра в обед, что посоветуешь?")
    assert r.action == "clarify"
    assert r.text == "Завтра в 13:00 занято. Свободно 12:00–13:00 — поставить?"
    r = await live.say("Да, 12:00")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 12)


async def test_lunch_window_full_suggests_nearest(live):
    await add(live, M, "Обед", dt(22, 12))
    await add(live, M, "Ревью", dt(22, 13))
    r = await live.say("Нужна встреча завтра в обед, что посоветуешь?")
    assert r.text.startswith("Завтра в это время всё занято. Ближайшее свободное — ")


async def test_evening_window_with_explicit_end(live):
    r = await live.say("Напомни вечером позвонить маме")
    assert r.action == "created" and r.items[0].due_at == dt(21, 19)


# ====================== 6. Два варианта времени ======================


async def test_two_options_from_start_end(live):
    r = await live.say(
        "Поставь встречу с Олегом завтра, может быть, в 5 часов вечера, может быть, в 6:00"
    )
    assert r.action == "clarify"
    assert r.text == "Во сколько поставить: 17:00 или 18:00?"
    assert r.suggestions == ["17:00", "18:00"]
    r = await live.say("18:00")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 18)


async def test_two_options_via_date_resolver(live):
    class Dates:
        def resolve(self, text, now, mode="point"):
            for word, hour in (("5", 17), ("6", 18)):
                if word in text:
                    return TimeSpec(
                        text=text, start=now.replace(hour=hour, minute=0), has_time=True
                    )
            return TimeSpec(text=text)

    live.orch.dialog.dates = Dates()
    r = await live.say("Созвон с Вектором завтра в 5 или в 6")
    assert r.suggestions == ["17:00", "18:00"]
    r = await live.say("17:00")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 17)


# ============ dialog/v4: QA-18 (обрезанное начало), QA-20 (лишние вопросы) ============


@pytest.fixture
def v4(live: Harness) -> Harness:
    add_ = live.nlu.add
    # так NLU видит обрезанные «Перенеси…» / «Удали…» — как создание
    add_(
        "Неси встречу на субботу",
        lambda now, s: c(
            Intent.CREATE, type=M, title="Неси встречу", when=on_day(now, 3, "на субботу")
        ),
    )
    add_(
        "еси её на субботу",
        lambda now, s: c(Intent.CREATE, type=T, title="Еси её", when=on_day(now, 3, "на субботу")),
    )
    add_("дали эту задачу", c(Intent.CREATE, type=T, title="Дали эту задачу"))
    add_(
        "Несите отчёт завтра",  # настоящее слово, не обрывок — создаём как есть
        lambda now, s: c(
            Intent.CREATE, type=T, title="Несите отчёт", when=on_day(now, 1, "завтра")
        ),
    )
    add_(
        "Поставь встречу с Олегом завтра в 15:00",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Олегом",
            participants=["Олег"],
            when=at(now, 1, 15, text="завтра в 15:00"),
        ),
    )
    add_(
        "Завтра две пары: в восемь и в 14:40",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Две восемь и",
            when=TimeSpec(
                text="Завтра в 14:40 или в восемь",  # NLU переписал «и» в «или»
                start=dt(22, 14, 40),
                end=dt(22, 8),
                has_time=True,
                has_date=True,
                ambiguous=True,
            ),
        ),
    )
    add_(
        "Поставь встречу завтра, непонятно во сколько",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча",
            when=TimeSpec(text="завтра", start=dt(22, 0), has_date=True, ambiguous=True),
        ),
    )
    return live


async def test_truncated_reschedule_does_not_create(v4):
    await v4.say("Поставь встречу с Олегом завтра в 15:00")
    before = len(v4.repo.all(1))
    r = await v4.say("Неси встречу на субботу")
    assert r.action == "clarify" and r.debug["intent"] == "update_item"
    assert r.debug["repaired_from"] == "create_item"
    assert r.text == "Перенести «Встреча с Олегом» на сб, 24 октября, 15:00?"
    assert len(v4.repo.all(1)) == before
    r = await v4.say("да")
    assert r.action == "updated" and r.items[0].starts_at == dt(24, 15)


async def test_truncated_pronoun_reschedule(v4):
    await v4.say("Поставь встречу с Олегом завтра в 15:00")
    r = await v4.say("еси её на субботу")
    assert r.action == "clarify" and r.text.startswith("Перенести «Встреча с Олегом»")
    r = await v4.say("нет")
    assert r.text == "Хорошо, ничего не меняю."


async def test_truncated_delete_needs_confirmation(v4):
    await v4.say("Добавь задачу купить подарок завтра в 18")
    r = await v4.say("дали эту задачу")
    assert r.action == "clarify" and r.debug["intent"] == "delete_item"
    assert r.text.startswith("Удалить задачу «Купить подарок»")


async def test_real_word_is_not_treated_as_truncation(v4):
    r = await v4.say("Несите отчёт завтра")
    assert r.action == "created" and r.items[0].title == "Несите отчёт"


async def test_two_events_with_and_created_both(v4):
    r = await v4.say("Завтра две пары: в восемь и в 14:40")
    assert r.action == "created"
    assert [i.starts_at for i in r.items] == [dt(22, 8), dt(22, 14, 40)]
    assert r.text.startswith("Записал 2 встречи:")
    r = await v4.say("отмени")
    assert r.text == "Отменил создание: 2 записи."


async def test_ambiguous_time_with_date_asks_only_time(v4):
    r = await v4.say("Поставь встречу завтра, непонятно во сколько")
    assert r.action == "clarify"
    assert r.text == "На какое время поставить встречу?"  # не «На какой день и время»

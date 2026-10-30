"""Страховка названия перед созданием (dialog/v6) и вежливая просьба с конфликтом.

Диалог не создаёт запись с мусорным названием, что бы ни вернул NLU: пустое, слово-вопрос,
«нет …», мат, дата/время в названии, обрывок, повтор. Служебное начало («можно», «давай»,
«туда», «запишите») срезается, если после него остаётся содержание.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contracts import Command, Intent, Item, ItemType, Slots
from app.dialog.normalize import guard_title
from tests.dialog.conftest import MSK, Harness
from tests.dialog.phrases import build_nlu
from tests.dialog.scripted_nlu import at

M, T = ItemType.MEETING, ItemType.TASK
ASK = "Не понял, что записать. Скажите, например: «завтра в 10 позвонить Ивану»."


@pytest.mark.parametrize(
    "title",
    [
        "", "Что", "что?", "чё", "кие", "Какие", "Нет не", "Нет, не надо", "Давай", "Туда",
        "Можно", "Одну такую же", "Встреча с", "Встреча встреча встреча", "Бля", "Хуйня какая-то",
        "Встреча хотя нет на тридцать первое", "Созвон с клиентом н в вечера",
        "Встреча в 14:40 обед",
        "Позвонить завтра", "Отчёт в пятницу",
    ],
)  # fmt: skip
def test_guard_rejects(title):
    assert guard_title(title) is None


@pytest.mark.parametrize(
    "title,clean",
    [
        ("Можно встречу с командой", "Встреча с командой"),
        ("Туда встречу с Анной", "Встреча с Анной"),
        ("Давай созвон с Альфой", "Созвон с Альфой"),
        ("Запишите встречу с заказчиком", "Встреча с заказчиком"),
        ("Это сделай созвон с этими как их Вектором", "Созвон с Вектором"),
        ("Созвон с клиентом ну ты понял", "Созвон с клиентом"),
        ("Ёлки палки созвон с Анной", "Созвон с Анной"),
        ("Бля чуть не забыл встреча с Иваном", "Встреча с Иваном"),
        ("Встреча", "Встреча"),
        ("Встреча 14:40", "Встреча"),
        ("13:30 тренировка по волейболу", "Тренировка по волейболу"),
        ("День рождения мамы", "День рождения мамы"),
        ("Подготовить ТЗ для Вектора", "Подготовить ТЗ для Вектора"),
        ("Созвон с Альфой по CRM", "Созвон с Альфой по CRM"),
    ],
)
def test_guard_cleans_service_start(title, clean):
    assert guard_title(title) == clean


def c(intent: Intent, **slots: object) -> Command:
    return Command(intent=intent, source="fallback", raw_text="", slots=Slots(**slots))  # type: ignore[arg-type]


def dt(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=MSK)


@pytest.fixture
def g() -> Harness:
    n = build_nlu()
    for phrase, title in [
        ("кие завтра в 10", "кие"),
        ("что завтра в 10", "Что"),
        ("а нет не надо завтра в 10", "Нет не"),
        ("бля завтра в 10", "Бля"),
        ("встреча с завтра в 10", "Встреча с"),
        ("встреча хотя нет на тридцать первое в 10", "Встреча хотя нет на тридцать первое"),
        ("можно встречу с командой завтра в 10", "Можно встречу с командой"),
        ("поставь туда встречу с Анной завтра в 12", "Туда встречу с Анной"),
    ]:
        n.add(
            phrase,
            lambda now, s, title=title: c(
                Intent.CREATE, type=M, title=title, when=at(now, 1, 10, text="завтра в 10")
            ),
        )
    n.add(
        "не могли бы вы поставить встречу с Иваном на завтра в десять",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Иваном",
            participants=["Иван"],
            when=at(now, 1, 10, text="на завтра в десять"),
        ),
    )
    n.add("позвонить Ивану", c(Intent.UNKNOWN))
    return Harness(n)


@pytest.mark.parametrize(
    "phrase",
    [
        "кие завтра в 10",
        "что завтра в 10",
        "а нет не надо завтра в 10",
        "бля завтра в 10",
        "встреча с завтра в 10",
        "встреча хотя нет на тридцать первое в 10",
    ],
)
async def test_garbage_title_is_not_created(g, phrase):
    before = len(g.repo.all(1))
    r = await g.say(phrase)
    assert r.action == "clarify" and r.text == ASK
    assert len(g.repo.all(1)) == before


async def test_answer_to_title_question_creates(g):
    await g.say("что завтра в 10")
    r = await g.say("позвонить Ивану")
    assert r.action == "created" and r.items[0].title == "Позвонить Ивану"
    assert r.items[0].starts_at == dt(22, 10)


@pytest.mark.parametrize(
    "phrase,title",
    [
        ("можно встречу с командой завтра в 10", "Встреча с командой"),
        ("поставь туда встречу с Анной завтра в 12", "Встреча с Анной"),
    ],
)
async def test_service_start_is_cut_and_created(g, phrase, title):
    r = await g.say(phrase)
    assert r.action == "created" and r.items[0].title == title


# ---------- вежливая просьба + конфликт: «да» создаёт без второго вопроса ----------


async def _smeta(g: Harness) -> Item:
    return await g.repo.create(
        Item(user_id=1, type=T, title="Проверить смету", due_at=dt(22, 10, 45))
    )


async def test_polite_conflict_then_yes_creates_once(g):
    await _smeta(g)
    r = await g.say("не могли бы вы поставить встречу с Иваном на завтра в десять")
    assert r.action == "conflict"
    assert r.text.startswith("Завтра в 10:45 у вас «Проверить смету». Поставить на 09:45")
    assert r.debug["draft"]["title"] == "Встреча с Иваном"  # что собирались создать
    r = await g.say("да")
    assert r.action == "created", r.text
    assert r.items[0].title == "Встреча с Иваном" and r.items[0].starts_at == dt(22, 9, 45)
    assert g.state.awaiting is None
    meetings = [i for i in g.repo.all(1) if i.title == "Встреча с Иваном"]
    assert len(meetings) == 1


async def test_polite_conflict_then_force_keeps_requested_time(g):
    await _smeta(g)
    await g.say("не могли бы вы поставить встречу с Иваном на завтра в десять")
    r = await g.say("Всё равно 15:30")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 10)
    assert g.state.awaiting is None


async def test_polite_conflict_then_filler_yes(g):
    await _smeta(g)
    await g.say("не могли бы вы поставить встречу с Иваном на завтра в десять")
    r = await g.say("ну да, давай")
    assert r.action == "created" and g.state.awaiting is None


async def test_cancel_after_clarification_phrase(g):
    g.nlu.add(
        "Поставь встречу с Иваном завтра",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Иваном",
            when=at(now, 1, 0, text="завтра").model_copy(update={"has_time": False}),
        ),
    )
    g.nlu.add("а нет не надо", c(Intent.CREATE, type=T, title="Нет не"))
    r = await g.say("Поставь встречу с Иваном завтра")
    assert r.action == "clarify"
    r = await g.say("а нет не надо")
    assert r.action == "none" and r.text == "Хорошо, отменил."
    assert not [i for i in g.repo.all(1) if "Нет" in i.title]
    _ = timedelta


# ---------- подстраховка времени по сказанному (record_meeting) ----------


@pytest.fixture
def sp() -> Harness:
    from app.contracts import TimeSpec
    from tests.dialog.scripted_nlu import on_day, period

    n = build_nlu()
    add_ = n.add
    # самоисправление: NLU взял первое время / дату
    add_(
        "созвон завтра в три нет в четыре",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Созвон",
            when=TimeSpec(text="завтра в три нет в четыре", start=dt(22, 15), end=dt(22, 16),
                          has_time=True, has_date=True, ambiguous=True),
        ),
    )  # fmt: skip
    add_(
        "встреча с Иваном в среду ой нет в четверг в десять",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Иваном ой нет в четверг",
            when=at(now, 7, 10, text="в среду"),
        ),
    )
    add_(
        "встреча с Олегом то есть с Игорем завтра в двенадцать",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Олегом то есть с Игорем",
            when=at(now, 1, 12, text="завтра в двенадцать"),
        ),
    )
    # разговорное время
    add_(
        "встреча с Иваном в понедельник полчетвёртого",
        lambda now, s: c(
            Intent.CREATE, type=M, title="Встреча с Иваном", when=on_day(now, 5, "в понедельник")
        ),
    )
    add_(
        "созвон завтра без четверти двенадцать",
        lambda now, s: c(Intent.CREATE, type=M, title="Созвон", when=on_day(now, 1, "завтра")),
    )
    # «на выходных» — два дня, не окно внутри дня
    add_(
        "на выходных встреча с Сергеем",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Встреча с Сергеем",
            when=TimeSpec(text="на выходных", start=dt(24, 0), end=dt(26, 0),
                          has_date=True, ambiguous=True),
        ),
    )  # fmt: skip
    # контекст списка
    add_(
        "Покажи встречи на понедельник",
        lambda now, s: c(Intent.LIST, type=M, when=on_day(now, 5, "на понедельник")),
    )
    add_(
        "добавь ещё созвон с Альфой в три",
        lambda now, s: c(
            Intent.CREATE,
            type=M,
            title="Созвон с Альфой",
            when=at(now, 0, 15, text="в три").model_copy(update={"has_date": False}),
        ),
    )
    _ = period
    h = Harness(n, seed=False)
    from app.nlu import build_date_resolver

    h.orch.dialog.dates = build_date_resolver(None)  # как в рантайме: настоящий DateResolver
    return h


async def test_self_correction_time(sp):
    r = await sp.say("созвон завтра в три нет в четыре")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 16)


async def test_self_correction_date_and_title(sp):
    r = await sp.say("встреча с Иваном в среду ой нет в четверг в десять")
    assert r.action == "created", r.text
    assert r.items[0].title == "Встреча с Иваном"


async def test_self_correction_person(sp):
    r = await sp.say("встреча с Олегом то есть с Игорем завтра в двенадцать")
    assert r.items[0].title == "Встреча с Игорем"


async def test_half_past_colloquial(sp):
    r = await sp.say("встреча с Иваном в понедельник полчетвёртого")
    assert r.action == "created" and r.items[0].starts_at == dt(26, 15, 30)


async def test_quarter_to_colloquial(sp):
    r = await sp.say("созвон завтра без четверти двенадцать")
    assert r.action == "created" and r.items[0].starts_at == dt(22, 11, 45)


async def test_weekend_is_not_a_day_window(sp):
    r = await sp.say("на выходных встреча с Сергеем")
    assert r.action == "clarify" and r.text == "На какой день и время поставить встречу?"


async def test_time_only_after_day_list_uses_that_day(sp):
    await sp.say("Покажи встречи на понедельник")
    r = await sp.say("добавь ещё созвон с Альфой в три")
    assert r.action == "created" and r.items[0].starts_at == dt(26, 15)


def test_cut_correction_keeps_normal_titles():
    from app.dialog.normalize import cut_correction

    assert cut_correction("Проверить нет ли ошибок") == "Проверить нет ли ошибок"
    assert cut_correction("Встреча с Олегом то есть с Игорем") == "Встреча с Игорем"
    assert cut_correction("Созвон с Вектором ой в смысле в среду") == "Созвон с Вектором"


@pytest.mark.parametrize(
    "title,clean",
    [
        ("Не забыть оплатить интернет", "Оплатить интернет"),
        ("Надо бы не забыть купить подарок маме", "Купить подарок маме"),
    ],
)
def test_guard_keeps_not_forget_tasks(title, clean):
    """Регрессия record_task rt-044 / rt-092: «не забыть …» — дело, а не отказ «нет»."""
    assert guard_title(title) == clean

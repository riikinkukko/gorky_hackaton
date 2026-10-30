"""Юнит-тесты domain: русский язык, сборка времени, повторы, конфликты."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contracts import TimeSpec
from app.domain import recurrence, ru
from app.domain.service import ItemService
from app.domain.timeutil import combine
from tests.dialog.conftest import CSV, MSK, NOW
from tests.dialog.fake_storage import InMemoryItemRepository, NaiveSearchEngine, load_csv


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "запись"),
        (2, "записи"),
        (5, "записей"),
        (11, "записей"),
        (21, "запись"),
        (22, "записи"),
        (103, "записи"),
        (112, "записей"),
    ],
)
def test_plural(n, expected):
    assert ru.plural(n, ru.RECORD) == expected


@pytest.mark.parametrize(
    "name,instr",
    [
        ("Иван", "Иваном"),
        ("Мария", "Марией"),
        ("Анна", "Анной"),
        ("Сергей", "Сергеем"),
        ("Наталья", "Натальей"),
        ("Пётр", "Петром"),
        ("Альфа", "Альфой"),
        ("Игорь", "Игорем"),
        ("Маяк", "Маяком"),
    ],
)
def test_name_instr(name, instr):
    assert ru.name_instr(name) == instr


def test_with_prep():
    assert ru.with_prep("Стартом") == "со Стартом"
    assert ru.with_prep("Иваном") == "с Иваном"


def test_fmt_dt():
    assert (
        ru.fmt_dt(datetime(2026, 10, 22, 10, 0, tzinfo=MSK), True, NOW) == "чт, 22 октября, 10:00"
    )
    assert (
        ru.fmt_dt(datetime(2025, 4, 4, 18, 0, tzinfo=MSK), True, NOW) == "пт, 4 апреля 2025, 18:00"
    )
    assert ru.fmt_dt(datetime(2026, 10, 23, tzinfo=MSK), False, NOW) == "пт, 23 октября"


def test_day_phrase():
    d = NOW.date()
    assert ru.day_phrase(d, NOW) == "сегодня"
    assert ru.day_phrase(d + timedelta(days=1), NOW) == "завтра"
    assert ru.day_phrase(d + timedelta(days=3), NOW) == "в субботу"
    assert ru.day_phrase(d + timedelta(days=6), NOW) == "во вторник"
    assert ru.day_phrase(d + timedelta(days=12), NOW) == "2 ноября"


def test_stem_matches_broken_cases():
    assert ru.stem("Иваном") == ru.stem("Ивану") == ru.stem("Иван")
    assert ru.stem("смету") == ru.stem("смета")
    assert ru.stem("Марией") == ru.stem("Мария")


def test_combine_time_only_keeps_base_date():
    base = datetime(2026, 10, 22, tzinfo=MSK)
    spec = TimeSpec(text="в три", start=NOW.replace(hour=15), has_time=True)
    assert combine(base, False, spec, NOW) == (datetime(2026, 10, 22, 15, tzinfo=MSK), True)


def test_combine_date_only_keeps_base_time():
    base = datetime(2026, 10, 24, 19, tzinfo=MSK)
    spec = TimeSpec(text="на завтра", start=datetime(2026, 10, 22, tzinfo=MSK))
    assert combine(base, True, spec, NOW) == (datetime(2026, 10, 22, 19, tzinfo=MSK), True)


def test_combine_today_explicit():
    base = datetime(2026, 10, 24, 19, tzinfo=MSK)
    spec = TimeSpec(text="сегодня в 18", start=NOW.replace(hour=18), has_time=True, has_date=True)
    assert combine(base, True, spec, NOW)[0] == NOW.replace(hour=18)


def test_recurrence_describe_and_first():
    assert recurrence.describe("FREQ=WEEKLY;BYDAY=MO") == "каждый понедельник"
    assert recurrence.describe("FREQ=WEEKLY;BYDAY=WE") == "каждую среду"
    assert recurrence.describe("FREQ=DAILY") == "каждый день"
    assert recurrence.describe("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "по будням"
    first = recurrence.first_occurrence("FREQ=WEEKLY;BYDAY=WE", NOW.replace(hour=10), NOW)
    assert first == datetime(2026, 10, 28, 10, tzinfo=MSK)  # сегодняшние 10:00 уже прошли


def _service() -> ItemService:
    repo = InMemoryItemRepository(load_csv(CSV, 1, NOW))
    return ItemService(repo, NaiveSearchEngine(repo))


@pytest.mark.asyncio
async def test_conflict_and_suggest_q10():
    s = _service()
    start = datetime(2026, 10, 29, 15, 30, tzinfo=MSK)
    conflicts = await s.conflicts.check(1, start, start + timedelta(hours=1))
    assert sorted(i.id for i in conflicts) == [697, 698]
    sugg = await s.conflicts.suggest(1, start, start + timedelta(hours=1), NOW)
    assert sugg == datetime(2026, 10, 29, 16, 30, tzinfo=MSK)


@pytest.mark.asyncio
async def test_demo_default_duration_30():
    s = _service()
    item = s.build(1, "meeting", "Демо проекта", NOW + timedelta(days=1), True, NOW)  # type: ignore[arg-type]
    assert (item.ends_at - item.starts_at) == timedelta(minutes=30)


@pytest.mark.asyncio
async def test_suggest_never_in_past():
    s = _service()
    start = NOW.replace(hour=8)  # 08:00 сегодня занято (688), и всё до 14:00 — прошлое
    sugg = await s.conflicts.suggest(1, start, start + timedelta(hours=1), NOW)
    assert sugg is not None and sugg >= NOW


def test_time_only_flag_is_trusted():
    """v1.1: «в три» без has_date к дате записи не привязывается к «сегодня»."""
    base = datetime(2026, 10, 24, 20, 45, tzinfo=MSK)
    spec = TimeSpec(text="на 19:00", start=NOW.replace(hour=19), has_time=True, has_date=False)
    assert combine(base, True, spec, NOW)[0] == datetime(2026, 10, 24, 19, tzinfo=MSK)


@pytest.mark.asyncio
async def test_list_period_is_half_open():
    s = _service()
    day = datetime(2026, 10, 21, tzinfo=MSK)
    items = await s.list_period(1, day, day + timedelta(days=1))
    assert [i.id for i in items] == [688, 689]
    assert await s.next_after(1, day + timedelta(days=1)) is not None

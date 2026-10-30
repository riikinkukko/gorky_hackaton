"""ItemRepository на живой PostgreSQL."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.contracts import ActionRecord, Item, ItemStatus, ItemType
from app.storage.repository import SqlItemRepository, expand_occurrences

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)
pytestmark = pytest.mark.asyncio


def new_item(user_id: int, **kw) -> Item:
    base = dict(user_id=user_id, type=ItemType.MEETING, title="Встреча с командой", source="voice")
    return Item(**{**base, **kw})


async def test_import_kept_ids_and_fields(sm, demo_user):
    repo = SqlItemRepository(sm)
    it = await repo.get(demo_user, 530)
    assert it and it.title == "Встреча с Иваном по презентации" and it.person == ["Иван"]
    assert it.starts_at == datetime(2026, 3, 5, 15, 45, tzinfo=MSK)
    assert it.ends_at - it.starts_at == timedelta(minutes=60)
    assert it.status is ItemStatus.UNKNOWN and it.source == "import"
    fixed = await repo.get(demo_user, 2)
    assert (fixed.title, fixed.title_original) == (
        "Отправить договор Ивану",
        "Отправить договор Иваном",
    )
    assert fixed.due_at and fixed.starts_at is None


async def test_crud_soft_delete_restore(sm, demo_user):
    repo = SqlItemRepository(sm)
    start = datetime(2026, 10, 22, 15, 0, tzinfo=MSK)
    it = await repo.create(new_item(demo_user, starts_at=start, ends_at=start + timedelta(hours=1)))
    assert it.id > 1000 and it.created_at.tzinfo is not None
    it2 = await repo.update(it.model_copy(update={"title": "Встреча с Иваном", "person": ["Иван"]}))
    assert it2.title == "Встреча с Иваном" and it2.updated_at >= it.updated_at
    await repo.soft_delete(demo_user, it.id)
    assert await repo.get(demo_user, it.id) is None
    await repo.restore(demo_user, it.id)
    assert (await repo.get(demo_user, it.id)).title == "Встреча с Иваном"
    assert await repo.get(demo_user + 999, it.id) is None  # чужой пользователь
    await repo.soft_delete(demo_user, it.id)


async def test_naive_datetime_rejected(sm, demo_user):
    with pytest.raises(ValueError):
        naive = datetime(2026, 10, 22, 15)
        await SqlItemRepository(sm).create(new_item(demo_user, starts_at=naive))


@pytest.mark.parametrize(
    "start, end, typ, ids",
    [
        ("2026-10-21", "2026-10-22", None, [688, 689]),  # q11
        ("2026-10-21", "2026-10-22", ItemType.MEETING, [688]),  # q13
        ("2026-10-22", "2026-10-23", ItemType.TASK, [690]),  # q14
        ("2026-10-19", "2026-10-26", None, [687, 688, 689, 690, 691, 692, 693, 694]),  # q15
        ("2026-10-26", "2026-11-02", None, [695, 696, 697, 698, 699, 700, 701, 702]),  # q16
        ("2026-11-01", "2026-12-01", ItemType.MEETING, [701, 712, 717, 719, 722, 724]),  # q18
        ("2026-10-23", "2026-10-24", None, []),  # q66 пустая пятница
    ],
)
async def test_list_range_golden(sm, demo_user, start, end, typ, ids):
    s, e = (datetime.fromisoformat(x).replace(tzinfo=MSK) for x in (start, end))
    got = [
        i.id
        for i in await SqlItemRepository(sm).list_range(demo_user, s, e, typ)
        if i.source == "import"
    ]
    assert got == ids


@pytest.mark.parametrize(
    "start, minutes, ids",
    [
        ("2026-10-29T15:30", 60, {697, 698}),  # q10: задача + созвон с Орбитой
        ("2026-11-01T15:00", 30, {701}),  # q49: демо 15:00–15:30 и созвон 15:15
        ("2026-10-24T20:00", 60, {693}),  # q50: встреча с Марией в 20:45
        ("2026-10-22T12:00", 60, set()),  # q35: свободно
        ("2026-10-29T16:30", 60, set()),  # q53: после созвона 15:30–16:30 — смежно
    ],
)
async def test_overlaps_golden(sm, demo_user, start, minutes, ids):
    s = datetime.fromisoformat(start).replace(tzinfo=MSK)
    got = await SqlItemRepository(sm).find_overlaps(demo_user, s, s + timedelta(minutes=minutes))
    assert {i.id for i in got if i.source == "import"} == ids


async def test_recurring_overlap_and_expand(sm, demo_user):
    repo = SqlItemRepository(sm)
    mon = datetime(2026, 10, 26, 10, 0, tzinfo=MSK)
    it = await repo.create(
        new_item(
            demo_user,
            title="Планёрка по CRM",
            starts_at=mon,
            ends_at=mon + timedelta(hours=1),
            rrule="FREQ=WEEKLY;BYDAY=MO",
        )
    )
    nov9 = datetime(2026, 11, 9, 10, 30, tzinfo=MSK)
    hits = await repo.find_overlaps(demo_user, nov9, nov9 + timedelta(minutes=30))
    occ = [h for h in hits if h.id == it.id]
    assert occ and occ[0].starts_at == datetime(2026, 11, 9, 10, 0, tzinfo=MSK)
    occs = expand_occurrences(it, mon, mon + timedelta(days=28))
    assert [o[0].day for o in occs] == [26, 2, 9, 16]
    in_list = await repo.list_range(demo_user, nov9 - timedelta(days=1), nov9 + timedelta(days=1))
    assert it.id in {i.id for i in in_list}  # серия отдаётся для развёртки доменом
    await repo.soft_delete(demo_user, it.id)


async def test_action_log(sm, demo_user):
    repo = SqlItemRepository(sm)
    it = await repo.get(demo_user, 691)
    done = it.model_copy(update={"status": ItemStatus.DONE})
    aid = await repo.log_action(
        ActionRecord(
            session_id="s1", user_id=demo_user, kind="complete", item_id=691, before=it, after=done
        )
    )
    a = await repo.get_action(demo_user, aid)
    assert a and a.id == aid and a.kind == "complete" and not a.undone
    assert a.before == it and a.after.status is ItemStatus.DONE  # снимки — Item
    assert a.created_at.tzinfo is not None
    await repo.mark_undone(demo_user, aid)
    assert (await repo.get_action(demo_user, aid)).undone
    assert await repo.get_action(demo_user + 999, aid) is None  # чужой журнал не виден
    with pytest.raises(KeyError):
        await repo.mark_undone(demo_user, 10**9)


async def test_create_ignores_client_id_and_dates(sm, demo_user):
    repo = SqlItemRepository(sm)
    old = datetime(2000, 1, 1, tzinfo=MSK)
    it = await repo.create(
        Item(
            id=530,
            user_id=demo_user,
            type=ItemType.TASK,
            title="Купить билеты",
            created_at=old,
            updated_at=old,
        )
    )
    assert it.id not in (0, 530) and it.created_at.year >= 2026
    await repo.soft_delete(demo_user, it.id)

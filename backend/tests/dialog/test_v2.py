"""dialog/v2: q89, q90, q75, «последний раз», undo без карточки, слова-паразиты."""

from __future__ import annotations

from datetime import timedelta

from app.contracts import Intent, TimeSpec
from tests.dialog.scripted_nlu import cmd, on_day


async def test_q89_nearest_future_target_without_question(h):
    r = await h.say("Перенеси созвон с Орбитой на 16:30")
    assert r.action == "updated", r.text
    assert h.ids(r) == [698]
    assert r.text == "Перенёс «Созвон с Орбитой по презентации» на чт, 29 октября, 16:30."


async def test_q89_asks_when_two_future_at_same_time(h):
    a = (await h.say("Добавь встречу с командой завтра в 15:00")).items[0]
    b = await h.repo.create(a.model_copy(update={"id": 0, "title": "Встреча с командой дизайна"}))
    h.state.last_item_ids = []
    h.nlu.add(
        "Перенеси встречу с командой на 17",
        lambda now, s: cmd(
            Intent.UPDATE,
            target=__import__("app.contracts", fromlist=["ItemRef"]).ItemRef(
                description="встречу с командой"
            ),
            new_when=TimeSpec(text="на 17", start=now.replace(hour=17, minute=0), has_time=True),
        ),
    )
    r = await h.say("Перенеси встречу с командой на 17")
    assert r.action == "clarify" and {a.id, b.id} <= set(h.ids(r))


async def test_q90_relative_shift_on_known_date(h):
    r = await h.say("Сдвинь планёрку по интеграции 18 ноября на час позже")
    assert r.action == "updated", r.text
    assert h.ids(r) == [717]
    it = await h.repo.get(1, 717)
    assert it.starts_at.strftime("%Y-%m-%d %H:%M") == "2026-11-18 12:30"
    assert it.ends_at - it.starts_at == timedelta(hours=1)


async def test_q75_invalid_date_asks(h):
    r = await h.say("Что у меня 31 ноября?")
    assert r.action == "clarify"
    assert r.text == "В ноябре 30 дней. Показать 30 ноября или 1 декабря?"
    assert r.suggestions == ["30 ноября", "1 декабря"]
    r = await h.say("30 ноября")
    assert r.action == "listed"
    assert r.text.startswith("30 ноября у вас")


async def test_undo_create_returns_no_items(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    r = await h.say("отмени")
    assert r.action == "undone" and r.items == []


async def test_last_time_answers_latest_by_engine_order(h):
    """Гибридный поиск: порядок верный, score не монотонен (как у Postgres-поиска)."""

    class Hybrid:
        async def search(self, user_id, slots, now, limit=20):
            ids = {217: 1.0968, 141: 1.0984, 17: 1.1, 506: 0.41}
            return [(await h.repo.get(1, i), sc) for i, sc in ids.items()]

    h.orch.dialog.service.search_engine = Hybrid()
    r = await h.say("Когда я последний раз звонил Сергею?")
    assert h.ids(r)[0] == 217
    assert r.text.startswith("Последняя — вт, 19 ноября 2024, 15:45")


# ---------- паузы и слова-паразиты ----------
# NLU чата 1 сам чистит «ну», «э-э», «короче». Диалог должен выдержать то, что осталось:
# ответ на уточнение без when (DateResolver разбирает) и мусорный ответ без потери вопроса.


async def test_filler_answer_resolved_by_date_resolver(h):
    class Dates:
        def resolve(self, text, now, mode="point"):
            if "три" in text:
                return TimeSpec(text=text, start=now.replace(hour=15, minute=0), has_time=True)
            return TimeSpec(text=text)

    h.orch.dialog.dates = Dates()
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("ээ… ну давай в три, наверное")
    assert r.action == "created"
    assert r.text.endswith("чт, 22 октября, 15:00.")


async def test_filler_only_keeps_question(h):
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("эм… ну…")
    assert r.action == "clarify" and h.state.awaiting == "time"
    r = await h.say("в три")
    assert r.action == "created"


async def test_filler_yes_confirms_conflict(h):
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    r = await h.say("ну да, давай")
    assert r.action == "created" and r.text.endswith("16:30.")


async def test_filler_choice_ordinal(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    h.state.last_item_ids = []
    await h.say("Перенеси встречу завтра на 17:00")
    second = h.state.last_item_ids[1]
    r = await h.say("эм, вторую")
    assert h.ids(r) == [second]


async def test_list_answer_after_invalid_date_via_new_list(h):
    await h.say("Что у меня 31 ноября?")
    h.nlu.add(
        "Покажи 1 декабря", lambda now, s: cmd(Intent.LIST, when=on_day(now, 41, "1 декабря"))
    )
    r = await h.say("Покажи 1 декабря")
    assert r.action == "listed" and h.state.awaiting is None


# ---------- звуки-заминки и паузы (strip_hesitations / clean_title) ----------


def test_strip_hesitations():
    from app.dialog.normalize import strip_hesitations as s

    assert s("Э-э… покажи все встречи в ноябре") == "покажи все встречи в ноябре"
    assert s("Напомни … мне завтра в 10") == "Напомни мне завтра в 10"
    assert s("эм, ну да") == "ну да"
    assert s("ммм... в три") == "в три"
    assert s("Поставь встречу с Эммой") == "Поставь встречу с Эммой"  # имя не трогаем
    assert s("…") == "…"  # пусто после очистки → исходный текст


def test_clean_title():
    from app.dialog.normalize import clean_title as c

    assert c("… позвонить Ивану") == "Позвонить Ивану"
    assert c("… мне позвонить Ивану") == "Позвонить Ивану"
    assert c("…, созвон с Вектором") == "Созвон с Вектором"
    assert c("… : подготовить отчёт") == "Подготовить отчёт"
    assert c("задачу купить билеты") == "Купить билеты"
    assert c("… встречу с командой") == "Встреча с командой"


async def test_hesitation_reaches_nlu_clean(h):
    r = await h.say("Э-э… что у меня сегодня?")
    assert r.action == "listed" and h.ids(r) == [688, 689]
    assert r.debug["text_clean"] == "что у меня сегодня?"


def test_hesitation_keeps_spelled_abbreviations():
    from app.dialog.normalize import strip_hesitations as s

    assert s("какие были напоминалки про си ар эм") == "какие были напоминалки про си ар эм"
    assert s("эм какие были напоминалки") == "какие были напоминалки"
    assert s("напомни, хм, завтра") == "напомни, завтра"
    assert s("про си ар эм, пожалуйста") == "про си ар эм, пожалуйста"

"""Сценарные тесты диалога: фразы из ТЗ и golden_eval (q01–q34)."""

from __future__ import annotations

import pytest

from app.contracts import ItemStatus

pytestmark = pytest.mark.asyncio


# ====================== Создание (ТЗ §1–2, q01–q07) ======================


async def test_q01_reminder_tomorrow_10(h):
    r = await h.say("Напомни мне завтра в 10 утра позвонить Ивану")
    assert r.action == "created"
    assert r.text == "Напомню: позвонить Ивану — чт, 22 октября, 10:00."
    item = r.items[0]
    assert item.type == "reminder" and item.due_at.isoformat() == "2026-10-22T10:00:00+03:00"
    assert "Отменить" in r.suggestions


async def test_q02_task_friday_without_time(h):
    r = await h.say("В пятницу нужно отправить отчет")
    assert r.action == "created"
    assert r.text == "Добавил задачу «Отправить отчёт» — пт, 23 октября."
    assert r.items[0].has_time is False


async def test_q03_task_until_end_of_week(h):
    r = await h.say("Поставь задачу купить билеты до конца недели")
    assert r.text == "Добавил задачу «Купить билеты» — до вс, 25 октября, 23:59."


async def test_q04_meeting_tomorrow_15(h):
    r = await h.say("Добавь встречу с командой завтра в 15:00")
    assert r.action == "created"
    assert r.text == "Записал встречу «Встреча с командой» — чт, 22 октября, 15:00."
    it = r.items[0]
    assert (it.ends_at - it.starts_at).total_seconds() == 3600  # длительность по умолчанию


async def test_q05_meeting_thursday_11(h):
    r = await h.say("В четверг в 11 у меня встреча с дизайнером")
    assert r.text == "Записал встречу «Встреча с дизайнером» — чт, 22 октября, 11:00."


async def test_q06_in_two_hours(h):
    r = await h.say("Через два часа напомни проверить почту")
    assert r.text == "Напомню: проверить почту — ср, 21 октября, 16:00."


async def test_q07_day_after_tomorrow(h):
    r = await h.say("Послезавтра в девять созвон с Альфой по CRM")
    assert r.text == "Записал встречу «Созвон с Альфой по CRM» — пт, 23 октября, 09:00."


async def test_created_item_persists_and_is_listed(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    r = await h.say("Какие задачи у меня на завтра?")
    assert h.ids(r) == [690]  # встреча — не задача
    r = await h.say("Покажи мои встречи на сегодня")
    assert h.ids(r) == [688]


# ====================== Уточнения (q08, q09) ======================


async def test_q08_meeting_without_time_asks_and_accepts_answer(h):
    r = await h.say("Поставь встречу с Иваном завтра")
    assert r.action == "clarify"
    assert r.text == "На какое время поставить встречу?"
    assert h.state.awaiting == "time"

    r = await h.say("в три")
    assert r.action == "created"
    assert r.text == "Записал встречу «Встреча с Иваном» — чт, 22 октября, 15:00."
    assert h.state.awaiting is None and h.state.pending is None
    assert r.items[0].person == ["Иван"]


async def test_q08_cancel_during_clarification(h):
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("отмена")
    assert r.text == "Хорошо, отменил."
    assert h.state.awaiting is None
    before = len(h.repo.all(1))
    r = await h.say("в три")  # больше не ответ на вопрос: продолжение без фокуса (v7)
    assert r.action == "clarify" and r.text.startswith("Не понял, к какой записи")
    assert len(h.repo.all(1)) == before


async def test_q08_new_command_abandons_question(h):
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("Что у меня сегодня?")
    assert r.action == "listed" and h.ids(r) == [688, 689]
    assert h.state.awaiting is None


async def test_q08_unclear_answer_reasks(h):
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("не знаю")
    assert r.action == "clarify" and "Не понял время" in r.text
    r = await h.say("в три")
    assert r.action == "created"


async def test_q09_reminder_asks_date_then_defaults_time(h):
    r = await h.say("Напомни про договор")
    assert r.text == "Когда напомнить про договор?"
    assert h.state.awaiting == "date"
    r = await h.say("завтра")  # дата есть, времени нет — 10:00 без пустого вопроса
    assert r.action == "created"
    assert r.text == "Напомню про договор в чт, 22 октября, в 10:00 — сказать другое время?"
    r = await h.say("в три")
    assert r.action == "updated" and r.text.endswith("чт, 22 октября, 15:00.")


async def test_meeting_title_generated_from_participant_then_asks_date(h):
    r = await h.say("Поставь встречу с Марией")
    assert r.text == "На какой день и время поставить встречу?"
    r = await h.say("завтра в три")
    assert r.text == "Записал встречу «Встреча с Марией» — чт, 22 октября, 15:00."


async def test_meeting_without_title_asks_title(h):
    r = await h.say("Поставь встречу завтра в 12")
    assert r.text == "Как назвать встречу?"
    r = await h.say("Синк по релизу")
    assert r.text == "Записал встречу «Синк по релизу» — чт, 22 октября, 12:00."


# ====================== Конфликт (q10) ======================


async def test_q10_conflict_offers_free_slot_and_yes_accepts(h):
    r = await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    assert r.action == "conflict"
    assert sorted(h.ids(r)) == [697, 698]
    assert "Поставить на 16:30 или всё равно на 15:30?" in r.text
    assert r.suggestions[:2] == ["16:30", "Всё равно 15:30"]
    r = await h.say("да")
    assert r.text == "Записал встречу «Созвон с Маяком» — чт, 29 октября, 16:30."


async def test_q10_conflict_force_original_time(h):
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    r = await h.say("Всё равно 15:30")
    assert r.action == "created"
    assert r.text.endswith("чт, 29 октября, 15:30.")


async def test_q10_conflict_button_with_time(h):
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    r = await h.say("16:30")
    assert r.text.endswith("чт, 29 октября, 16:30.")


async def test_q10_conflict_other_time_and_cancel(h):
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    r = await h.say("в 17")
    assert r.action == "created" and r.text.endswith("17:00.")
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    r = await h.say("нет")
    assert r.text == "Хорошо, отменил."


# ====================== Списки (ТЗ §3, q11–q18) ======================


@pytest.mark.parametrize("phrase", ["Что у меня сегодня?", "Какие планы на сегодня?"])
async def test_q11_q12_today(h, phrase):
    r = await h.say(phrase)
    assert r.action == "listed"
    assert h.ids(r) == [688, 689]
    assert r.text.startswith("Сегодня у вас 2 записи:")
    assert "08:00 — Встреча с Марией по презентации" in r.text


async def test_q13_meetings_today(h):
    r = await h.say("Есть ли у меня встречи сегодня?")
    assert h.ids(r) == [688]
    assert r.text.startswith("Сегодня у вас 1 встреча:")


async def test_q14_tasks_tomorrow(h):
    r = await h.say("Какие задачи у меня на завтра?")
    assert h.ids(r) == [690]
    assert r.text.startswith("Завтра у вас 1 задача:")


async def test_q15_this_week_long_list_is_summarised(h):
    r = await h.say("Что запланировано на эту неделю?")
    assert h.ids(r) == [687, 688, 689, 690, 691, 692, 693, 694]
    assert r.text.startswith("На этой неделе у вас 8 записей: 2 встречи и 6 задач. Ближайшие три:")
    assert "чт, 22 октября, 09:15 — Подготовить вопросы для Максимом" in r.text
    assert h.state.last_item_ids == [690, 691, 692]
    assert "Только встречи" in r.suggestions


async def test_q16_next_week(h):
    r = await h.say("А на следующей неделе?")
    assert h.ids(r) == [695, 696, 697, 698, 699, 700, 701, 702]
    assert r.text.startswith("На следующей неделе у вас 8 записей")


async def test_q17_saturday(h):
    r = await h.say("Что у меня в субботу?")
    assert h.ids(r) == [691, 692, 693]
    assert r.text.startswith("В субботу у вас 3 записи:")


async def test_q18_meetings_in_november(h):
    r = await h.say("Покажи все встречи в ноябре")
    assert h.ids(r) == [701, 712, 717, 719, 722, 724]
    assert r.text.startswith("В ноябре у вас 6 встреч.")


async def test_empty_day_suggests_nearest_event(h):
    r = await h.say("Что у меня в пятницу?")
    assert r.text == (
        "В пятницу ничего не запланировано. Ближайшая — «Проверить смету по мобильному "
        "приложению», сб, 24 октября, 10:45."
    )
    assert h.ids(r) == [691]


async def test_empty_typed_day(h):
    r = await h.say("Какие встречи в пятницу?")
    assert r.text.startswith("В пятницу встреч нет. Ближайшая — «Встреча с Марией по интеграции»")


# ====================== Поиск (ТЗ §4, q19–q30) ======================


async def test_q19_when_was_meeting_with_ivan(h):
    r = await h.say("Когда у меня была встреча с Иваном?")
    assert r.action == "found"
    assert h.ids(r) == [530, 429, 309]
    assert r.text.startswith("Последняя — чт, 5 марта, 15:45: «Встреча с Иваном по презентации».")
    assert "27 сентября 2025" in r.text


async def test_q20_broad_search_is_grouped(h):
    r = await h.say("Найди задачу про презентацию")
    assert r.text.startswith("Нашёл ")
    assert "по запросу «презентация»" in r.text and "Уточнить" in r.text
    assert len(h.state.last_item_ids) == 3


async def test_q21_search_past_contract(h):
    r = await h.say("Я когда-то ставил задачу по договору, найди её")
    assert "Последние три" in r.text
    assert all(i.starts_at is None or i.starts_at < h.now for i in r.items)


async def test_q22_last_call_to_sergey(h):
    r = await h.say("Когда я последний раз звонил Сергею?")
    assert h.ids(r)[0] == 217
    assert "вт, 19 ноября 2024, 15:45" in r.text


async def test_q30_nothing_found(h):
    r = await h.say("Найди встречу с Петром")
    assert r.action == "found" and r.items == []
    assert r.text == "Не нашёл встреч с Петром."


# ====================== Изменение, контекст, выполнение (q31–q33) ======================


async def test_q31_move_meeting_with_maria_on_saturday(h):
    r = await h.say("Перенеси встречу с Марией в субботу на 19:00")
    assert r.action == "updated"
    assert h.ids(r) == [693]
    assert r.text == "Перенёс «Встреча с Марией по интеграции» на сб, 24 октября, 19:00."
    item = await h.repo.get(1, 693)
    assert item.starts_at.hour == 19 and (item.ends_at - item.starts_at).total_seconds() == 3600


async def test_q32_pronoun_uses_last_item(h):
    await h.say("Перенеси встречу с Марией в субботу на 19:00")
    r = await h.say("А её перенеси на завтра")
    assert h.ids(r) == [693]
    assert r.text == "Перенёс «Встреча с Марией по интеграции» на чт, 22 октября, 19:00."


async def test_q32_pronoun_after_create(h):
    await h.say("Напомни мне завтра в 10 утра позвонить Ивану")
    r = await h.say("А её перенеси на завтра")  # дата та же — время сохраняется
    assert r.text.endswith("чт, 22 октября, 10:00.")


async def test_pronoun_without_context(h):
    r = await h.say("А её перенеси на завтра")
    assert r.action == "clarify" and "о какой записи" in r.text


async def test_update_without_new_time_asks(h):
    await h.say("Перенеси встречу с Марией в субботу на 19:00")
    r = await h.say("Перенеси её")
    assert r.text == "На когда перенести?"
    r = await h.say("завтра")
    assert r.text.endswith("чт, 22 октября, 19:00.")


async def test_update_target_not_found(h):
    r = await h.say("Перенеси встречу с Олегом на завтра")
    assert r.action == "none" and r.text.startswith("Не нашёл запись")


async def test_multiple_candidates_ask_to_choose(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    h.state.last_item_ids = []  # без контекста — иначе выберется последняя упомянутая
    r = await h.say("Перенеси встречу завтра на 17:00")
    assert r.action == "clarify" and h.state.awaiting == "target"
    assert "1." in r.text and "2." in r.text
    r = await h.say("с дизайнером")
    assert r.text == "Перенёс «Встреча с дизайнером» на чт, 22 октября, 17:00."


async def test_choose_by_ordinal(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    h.state.last_item_ids = []
    await h.say("Перенеси встречу завтра на 17:00")
    _first, second = h.state.last_item_ids
    r = await h.say("вторую")
    assert h.ids(r) == [second]


async def test_update_conflict_then_yes(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    r = await h.say("Перенеси встречу с командой на 11")
    assert r.action == "conflict" and "дизайнером" in r.text
    r = await h.say("да")
    assert r.action == "updated" and "12:00" in r.text


async def test_q33_complete_task(h):
    r = await h.say("Отметь, что смету по мобильному приложению я проверил")
    assert r.action == "completed"
    assert h.ids(r) == [691]
    assert (await h.repo.get(1, 691)).status == ItemStatus.DONE
    r = await h.say("отмени")
    assert r.action == "undone"
    assert (await h.repo.get(1, 691)).status == ItemStatus.UNKNOWN


# ====================== Удаление и undo (q34) ======================


async def test_q34_delete_with_confirmation_and_undo(h):
    created = (await h.say("Напомни мне завтра в 10 утра позвонить Ивану")).items[0]
    r = await h.say("Удали напоминание позвонить Ивану на завтра")
    assert r.action == "clarify" and h.state.awaiting == "confirm_delete"
    assert r.text == "Удалить напоминание «Позвонить Ивану» — чт, 22 октября, 10:00?"
    r = await h.say("да")
    assert r.action == "deleted"
    assert await h.repo.get(1, created.id) is None
    r = await h.say("отмени")
    assert r.action == "undone" and r.text == "Вернул «Позвонить Ивану»."
    assert await h.repo.get(1, created.id) is not None


async def test_q34_delete_rejected(h):
    created = (await h.say("Напомни мне завтра в 10 утра позвонить Ивану")).items[0]
    await h.say("Удали напоминание позвонить Ивану на завтра")
    r = await h.say("нет")
    assert r.text == "Хорошо, не удаляю."
    assert await h.repo.get(1, created.id) is not None


async def test_undo_create(h):
    created = (await h.say("Добавь встречу с командой завтра в 15:00")).items[0]
    r = await h.say("отмени")
    assert r.text == "Отменил: «Встреча с командой» больше нет в списке."
    assert await h.repo.get(1, created.id) is None
    r = await h.say("отмени")
    assert r.text == "Отменять нечего."


async def test_undo_update_restores_time(h):
    await h.say("Перенеси встречу с Марией в субботу на 19:00")
    await h.say("отмени")
    item = await h.repo.get(1, 693)
    assert item.starts_at.hour == 20 and item.starts_at.minute == 45


# ====================== Повторы ======================


async def test_recurring_meeting_created_and_expanded(empty):
    r = await empty.say("Каждый понедельник в 10 планёрка")
    assert r.action == "created"
    assert r.text == (
        "Записал повторяющуюся встречу «Планёрка» — каждый понедельник, 10:00. "
        "Первый раз — пн, 26 октября, 10:00."
    )
    r = await empty.say("А на следующей неделе?")
    assert [i.starts_at.day for i in r.items] == [26]
    r = await empty.say("Что у меня 2 ноября?")
    assert r.text.startswith("2 ноября у вас 1 запись:")
    r = await empty.say("Покажи все встречи в ноябре")
    assert [i.starts_at.day for i in r.items] == [2, 9, 16, 23, 30]


# ====================== Прочее ======================


async def test_confirm_without_pending(h):
    r = await h.say("да")
    assert r.action == "none" and "нечего подтверждать" in r.text


async def test_unknown_and_smalltalk(h):
    assert (await h.say("абракадабра")).action == "none"
    assert "задачами" in (await h.say("привет")).text


async def test_debug_contains_intent_and_latency(h):
    r = await h.say("Что у меня сегодня?")
    assert r.debug["intent"] == "list_items" and "latency_ms" in r.debug


async def test_q23_demo_mobile_synonym_expanded_by_nlu(h):
    r = await h.say("Найди демо по мобилке")
    assert r.text.startswith("Нашёл 8 встреч")


async def test_date_resolver_fallback_when_nlu_gives_no_time():
    """Если NLU не разобрал ответ «в три», диалог сам спрашивает DateResolver."""
    from app.contracts import TimeSpec
    from tests.dialog.conftest import Harness
    from tests.dialog.phrases import build_nlu

    class Dates:
        def resolve(self, text, now, mode="point"):
            return TimeSpec(text=text, start=now.replace(hour=16, minute=0), has_time=True)

    h = Harness(build_nlu())
    h.orch.dialog.dates = Dates()
    await h.say("Поставь встречу с Иваном завтра")
    r = await h.say("к четырём")  # ScriptedNLU не знает фразу → UNKNOWN без when
    assert r.text == "Записал встречу «Встреча с Иваном» — чт, 22 октября, 16:00."


async def test_factory_matches_contract(h):
    from app.contracts import Orchestrator

    assert isinstance(h.orch.handle, object)
    orch: Orchestrator = h.orch  # mypy проверяет совместимость с протоколом
    assert (await orch.handle("Что у меня сегодня?", h.state, h.now)).action == "listed"


# ====================== v1.2: last_command ======================


async def test_last_command_after_list(h):
    await h.say("Что запланировано на эту неделю?")
    assert h.state.last_command is not None
    assert h.state.last_command.intent == "list_items"


async def test_last_command_not_written_on_clarify(h):
    await h.say("Что у меня сегодня?")
    await h.say("Поставь встречу с Иваном завтра")  # clarify
    assert h.state.last_command.intent == "list_items"


async def test_last_command_is_assembled_after_clarify(h):
    await h.say("Поставь встречу с Иваном завтра")
    await h.say("в три")
    last = h.state.last_command
    assert last.intent == "create_item" and last.slots.title == "Встреча с Иваном"
    assert last.slots.when.start.isoformat() == "2026-10-22T15:00:00+03:00"


async def test_last_command_on_cancel_is_reject(h):
    await h.say("Поставь встречу с Иваном завтра")
    await h.say("отмена")
    assert h.state.last_command.intent == "reject"


# ====================== Регрессии из прогона eval с настоящим NLU ======================


async def test_new_command_during_conflict_question_is_not_a_time_answer(h):
    await h.say("Поставь созвон с Маяком 29 октября в 15:30")
    assert h.state.awaiting == "confirm_conflict"
    r = await h.say("Что у меня сегодня?")
    assert r.action == "listed" and h.ids(r) == [688, 689]
    assert h.state.awaiting is None


async def test_new_command_during_target_choice(h):
    await h.say("Добавь встречу с командой завтра в 15:00")
    await h.say("В четверг в 11 у меня встреча с дизайнером")
    h.state.last_item_ids = []
    await h.say("Перенеси встречу завтра на 17:00")
    assert h.state.awaiting == "target"
    r = await h.say("Что у меня сегодня?")
    assert r.action == "listed" and h.state.awaiting is None


async def test_search_keeps_engine_relevance_and_names(h):
    """q22: движок отдаёт все «Позвонить …», лучший уровень — «Позвонить Сергеем»."""
    from app.contracts import Slots

    class OrSearch:
        async def search(self, user_id, slots, now, limit=20):
            hits = []
            for it in h.repo.all(user_id):
                if slots.tense == "past" and it.due_at and it.due_at >= now:
                    continue
                low = it.title.lower()
                score = ("позвон" in low) + ("серге" in low)
                if score:
                    hits.append((it, float(score)))
            return sorted(hits, key=lambda p: -p[1])[:limit]

    h.orch.dialog.service.search_engine = OrSearch()
    r = await h.say("Когда я последний раз звонил Сергею?")
    assert h.ids(r)[:3] == [217, 141, 17]
    assert r.text.startswith("Последняя — вт, 19 ноября 2024, 15:45: «Позвонить Сергеем».")
    assert "Анной" not in r.text
    _ = Slots


async def test_search_by_organization_when_engine_ignores_it(h):
    """q24/q29: движок не смотрит на organization — диалог повторяет запрос с именем."""
    from app.contracts import Slots

    class QueryOnly:
        async def search(self, user_id, slots: Slots, now, limit=20):
            if not slots.query:
                return []
            words = [w[:5].lower() for w in slots.query.split()]
            return [
                (it, 1.0) for it in h.repo.all(user_id) if any(w in it.title.lower() for w in words)
            ][:limit]

    h.orch.dialog.service.search_engine = QueryOnly()
    h.nlu.add(
        "Что у нас было с Вектором?",
        __import__("tests.dialog.scripted_nlu", fromlist=["cmd"]).cmd(
            "search_items", organization="Вектор", tense="past"
        ),
    )
    r = await h.say("Что у нас было с Вектором?")
    assert set(h.ids(r)) <= {49, 93, 179, 241, 277, 326, 412, 428, 507, 518, 652}
    assert len(r.items) >= 3

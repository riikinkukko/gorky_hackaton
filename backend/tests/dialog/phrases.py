"""Фразы из ТЗ и golden_eval → Command, которую должен вернуть NLU (чат 1).

«Сейчас» в тестах — ср, 21.10.2026 14:00 МСК: завтра = чт 22.10, пятница = 23.10,
суббота = 24.10, воскресенье = 25.10, следующая неделя = 26.10–01.11.
"""

from __future__ import annotations

from datetime import timedelta

from app.contracts import Intent, ItemRef, ItemType, TimeSpec
from tests.dialog.scripted_nlu import ScriptedNLU, at, cmd, on_day, period, time_only

C, L, S, U = Intent.CREATE, Intent.LIST, Intent.SEARCH, Intent.UPDATE
M, T, R = ItemType.MEETING, ItemType.TASK, ItemType.REMINDER


def build_nlu() -> ScriptedNLU:
    n = ScriptedNLU()
    add = n.add

    # ---------- создание (ТЗ, q01–q07) ----------
    add(
        "Напомни мне завтра в 10 утра позвонить Ивану",
        lambda now, s: cmd(
            C,
            type=R,
            title="Позвонить Ивану",
            participants=["Иван"],
            when=at(now, 1, 10, text="завтра в 10 утра"),
        ),
    )
    add(
        "В пятницу нужно отправить отчет",
        lambda now, s: cmd(C, type=T, title="Отправить отчёт", when=on_day(now, 2, "в пятницу")),
    )
    add(
        "Поставь задачу купить билеты до конца недели",
        lambda now, s: cmd(
            C, type=T, title="Купить билеты", when=at(now, 4, 23, 59, "до конца недели")
        ),
    )
    add(
        "Добавь встречу с командой завтра в 15:00",
        lambda now, s: cmd(
            C, type=M, title="Встреча с командой", when=at(now, 1, 15, text="завтра в 15:00")
        ),
    )
    add(
        "В четверг в 11 у меня встреча с дизайнером",
        lambda now, s: cmd(
            C, type=M, title="Встреча с дизайнером", when=at(now, 1, 11, text="в четверг в 11")
        ),
    )
    add(
        "Через два часа напомни проверить почту",
        lambda now, s: cmd(
            C,
            type=R,
            title="Проверить почту",
            when=TimeSpec(
                text="через два часа", start=now + timedelta(hours=2), has_time=True, has_date=True
            ),
        ),
    )
    add(
        "Послезавтра в девять созвон с Альфой по CRM",
        lambda now, s: cmd(
            C,
            type=M,
            title="Созвон с Альфой по CRM",
            organization="Альфа",
            when=at(now, 2, 9, text="послезавтра в девять"),
        ),
    )

    # ---------- уточнения (q08, q09) ----------
    add(
        "Поставь встречу с Иваном завтра",
        lambda now, s: cmd(
            C,
            type=M,
            title="Встреча с Иваном",
            participants=["Иван"],
            when=on_day(now, 1, "завтра"),
        ),
    )
    add("Поставь встречу с Марией", lambda now, s: cmd(C, type=M, participants=["Мария"]))
    add("Напомни про договор", lambda now, s: cmd(C, type=R, title="Про договор"))
    add(
        "Поставь встречу завтра в 12",
        lambda now, s: cmd(C, type=M, when=at(now, 1, 12, text="завтра в 12")),
    )
    add("в три", lambda now, s: cmd(Intent.UNKNOWN, when=time_only(now, 15, text="в три")))
    add("в 10", lambda now, s: cmd(Intent.UNKNOWN, when=time_only(now, 10, text="в 10")))
    add("завтра", lambda now, s: cmd(Intent.UNKNOWN, when=on_day(now, 1, "завтра")))
    add(
        "завтра в три", lambda now, s: cmd(Intent.UNKNOWN, when=at(now, 1, 15, text="завтра в три"))
    )
    add("Синк по релизу", cmd(Intent.UNKNOWN))
    add("не знаю", cmd(Intent.UNKNOWN))

    # ---------- конфликт (q10) ----------
    add(
        "Поставь созвон с Маяком 29 октября в 15:30",
        lambda now, s: cmd(
            C,
            type=M,
            title="Созвон с Маяком",
            organization="Маяк",
            when=at(now, 8, 15, 30, "29 октября в 15:30"),
        ),
    )
    add("16:30", lambda now, s: cmd(Intent.UNKNOWN, when=time_only(now, 16, 30, "16:30")))
    add("Всё равно 15:30", lambda now, s: cmd(Intent.UNKNOWN, when=time_only(now, 15, 30, "15:30")))
    add("в 17", lambda now, s: cmd(Intent.UNKNOWN, when=time_only(now, 17, text="в 17")))

    # ---------- списки (ТЗ, q11–q18) ----------
    today = lambda now, s, **kw: cmd(L, when=on_day(now, 0, "сегодня"), **kw)  # noqa: E731
    add("Что у меня сегодня?", lambda now, s: today(now, s))
    add("Какие планы на сегодня?", lambda now, s: today(now, s))
    add("Покажи мои встречи на сегодня", lambda now, s: today(now, s, type=M))
    add("Есть ли у меня встречи сегодня?", lambda now, s: today(now, s, type=M))
    add(
        "Какие задачи у меня на завтра?",
        lambda now, s: cmd(L, type=T, when=on_day(now, 1, "на завтра")),
    )
    add(
        "Что запланировано на эту неделю?",
        lambda now, s: cmd(L, when=period(now, -2, 4, "на эту неделю")),
    )
    add(
        "А на следующей неделе?",
        lambda now, s: cmd(L, when=period(now, 5, 11, "на следующей неделе")),
    )
    add("Что у меня в субботу?", lambda now, s: cmd(L, when=on_day(now, 3, "в субботу")))
    add("Что у меня в пятницу?", lambda now, s: cmd(L, when=on_day(now, 2, "в пятницу")))
    add("Какие встречи в пятницу?", lambda now, s: cmd(L, type=M, when=on_day(now, 2, "в пятницу")))
    add(
        "Покажи все встречи в ноябре",
        lambda now, s: cmd(L, type=M, when=period(now, 11, 40, "в ноябре")),
    )
    add("Что у меня 2 ноября?", lambda now, s: cmd(L, when=on_day(now, 12, "2 ноября")))

    # ---------- поиск (ТЗ, q19–q30) ----------
    add("Найди задачу про презентацию", cmd(S, query="презентация"))
    add("Когда у меня была встреча с Иваном?", cmd(S, type=M, participants=["Иван"], tense="past"))
    add("Я когда-то ставил задачу по договору, найди её", cmd(S, query="договор", tense="past"))
    add(
        "Когда я последний раз звонил Сергею?",
        cmd(S, query="позвонить", participants=["Сергей"], tense="past"),
    )
    add("Найди встречу с Петром", cmd(S, type=M, participants=["Пётр"]))
    add("Найди демо по мобилке", cmd(S, type=M, query="демо мобильное приложение"))

    # ---------- изменение, контекст, выполнение, удаление (q31–q34) ----------
    add(
        "Перенеси встречу с Марией в субботу на 19:00",
        lambda now, s: cmd(
            U,
            type=M,
            participants=["Мария"],
            when=on_day(now, 3, "в субботу"),
            target=ItemRef(description="встречу с Марией"),
            new_when=time_only(now, 19, text="на 19:00"),
        ),
    )
    add(
        "А её перенеси на завтра",
        lambda now, s: cmd(U, target=ItemRef(pronoun=True), new_when=on_day(now, 1, "на завтра")),
    )
    add("Перенеси её", cmd(U, target=ItemRef(pronoun=True)))
    add(
        "Отметь, что смету по мобильному приложению я проверил",
        cmd(Intent.COMPLETE, target=ItemRef(description="смету по мобильному приложению")),
    )
    add(
        "Удали напоминание позвонить Ивану на завтра",
        lambda now, s: cmd(
            Intent.DELETE,
            type=R,
            when=on_day(now, 1, "на завтра"),
            target=ItemRef(description="позвонить Ивану"),
        ),
    )
    add(
        "Перенеси встречу завтра на 17:00",
        lambda now, s: cmd(
            U,
            type=M,
            when=on_day(now, 1, "завтра"),
            target=ItemRef(description="встречу"),
            new_when=time_only(now, 17, text="на 17:00"),
        ),
    )
    add(
        "Перенеси встречу с Олегом на завтра",
        lambda now, s: cmd(
            U,
            type=M,
            participants=["Олег"],
            target=ItemRef(description="встречу с Олегом"),
            new_when=on_day(now, 1, "на завтра"),
        ),
    )
    add(
        "Перенеси встречу с командой на 11",
        lambda now, s: cmd(
            U,
            type=M,
            target=ItemRef(description="встречу с командой"),
            new_when=time_only(now, 11, text="на 11"),
        ),
    )
    add("вторую", cmd(Intent.UNKNOWN))
    add("с дизайнером", cmd(Intent.UNKNOWN))

    # ---------- повторы ----------
    add(
        "Каждый понедельник в 10 планёрка",
        lambda now, s: cmd(
            C,
            type=M,
            title="Планёрка",
            recurrence="FREQ=WEEKLY;BYDAY=MO",
            when=time_only(now, 10, text="в 10"),
        ),
    )

    # ---------- служебные ----------
    add("да", cmd(Intent.CONFIRM))
    add("нет", cmd(Intent.REJECT))
    add("отмена", cmd(Intent.REJECT))
    add("отмени", cmd(Intent.UNDO))
    add("привет", cmd(Intent.SMALLTALK))
    add_v2_phrases(n)
    return n


def add_v2_phrases(n: ScriptedNLU) -> None:
    """dialog/v2: Command'ы как их отдаёт NLU чата 1 (сверено с трассой eval-real)."""
    add = n.add
    add(
        "Перенеси созвон с Орбитой на 16:30",
        lambda now, s: cmd(
            U,
            type=M,
            organization="Орбита",
            query="созвон",
            target=ItemRef(description="созвон с Орбитой"),
            new_when=time_only(now, 16, 30, "на 16:30"),
        ),
    )
    add(
        "Сдвинь планёрку по интеграции 18 ноября на час позже",
        lambda now, s: cmd(
            U,
            type=M,
            when=period(now, 28, 28, "18 ноября"),
            query="планёрка интеграция",
            target=ItemRef(description="планёрку по интеграции"),
            new_when=TimeSpec(text="на час позже"),
        ),
    )
    add(
        "Что у меня 31 ноября?",
        cmd(L, when=TimeSpec(text="31 ноября", has_date=True, ambiguous=True)),
    )
    add("30 ноября", lambda now, s: cmd(Intent.UNKNOWN, when=on_day(now, 40, "30 ноября")))

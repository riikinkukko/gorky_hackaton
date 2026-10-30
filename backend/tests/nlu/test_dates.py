"""Юнит-тесты RuleDateResolver. «Сейчас» всегда передаётся параметром."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.nlu.dates import RuleDateResolver, has_explicit_date, remove_spans

MSK = ZoneInfo("Europe/Moscow")
WED = datetime(2026, 10, 21, 14, 0, tzinfo=MSK)  # среда, как в golden_eval
FRI_MORNING = datetime(2026, 10, 23, 9, 0, tzinfo=MSK)  # пятница утро
FRI_EVENING = datetime(2026, 10, 23, 20, 0, tzinfo=MSK)  # пятница вечер
LATE = datetime(2026, 10, 21, 23, 30, tzinfo=MSK)  # почти полночь
NEW_YEAR = datetime(2026, 12, 30, 12, 0, tzinfo=MSK)  # конец года
SUN = datetime(2026, 10, 25, 12, 0, tzinfo=MSK)  # воскресенье

R = RuleDateResolver()


def d(y: int, m: int, day: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, day, h, mi, tzinfo=MSK)


# (фраза, now, ожидаемый start, has_time)
POINT_CASES = [
    # --- дни ---
    ("сегодня", WED, d(2026, 10, 21), False),
    ("завтра", WED, d(2026, 10, 22), False),
    ("послезавтра", WED, d(2026, 10, 23), False),
    ("на завтра", WED, d(2026, 10, 22), False),
    ("вчера", WED, d(2026, 10, 20), False),
    # --- дни недели ---
    ("в пятницу", WED, d(2026, 10, 23), False),
    ("в понедельник", WED, d(2026, 10, 26), False),
    ("во вторник", WED, d(2026, 10, 27), False),
    ("в среду", WED, d(2026, 10, 21), False),  # сегодня среда, день не прошёл
    ("в среду в 10", WED, d(2026, 10, 28, 10), True),  # 10:00 уже прошло → следующая среда
    ("в среду в 16:00", WED, d(2026, 10, 21, 16), True),  # ещё не прошло → сегодня
    ("в пятницу в 11", FRI_MORNING, d(2026, 10, 23, 11), True),
    ("в пятницу в 11", FRI_EVENING, d(2026, 10, 30, 11), True),
    ("в следующую пятницу", WED, d(2026, 10, 30), False),
    ("в эту субботу", WED, d(2026, 10, 24), False),
    ("в воскресенье", WED, d(2026, 10, 25), False),
    ("в понедельник", SUN, d(2026, 10, 26), False),
    ("в пятницу на следующей неделе", WED, d(2026, 10, 30), False),
    # --- время ---
    ("завтра в 10 утра", WED, d(2026, 10, 22, 10), True),
    ("завтра в 15:00", WED, d(2026, 10, 22, 15), True),
    ("в четверг в 11", WED, d(2026, 10, 22, 11), True),
    ("завтра в 3", WED, d(2026, 10, 22, 15), True),  # политика: 1–7 → вечер
    ("завтра в 5", WED, d(2026, 10, 22, 17), True),
    ("завтра в 8", WED, d(2026, 10, 22, 8), True),
    ("завтра в 7 утра", WED, d(2026, 10, 22, 7), True),
    ("завтра в 3 часа дня", WED, d(2026, 10, 22, 15), True),
    ("завтра в 9 вечера", WED, d(2026, 10, 22, 21), True),
    ("завтра в 12", WED, d(2026, 10, 22, 12), True),
    ("послезавтра в девять", WED, d(2026, 10, 23, 9), True),
    ("завтра в девять тридцать", WED, d(2026, 10, 22, 9, 30), True),
    ("завтра в 10.30", WED, d(2026, 10, 22, 10, 30), True),
    ("завтра в 10 часов 15 минут", WED, d(2026, 10, 22, 10, 15), True),
    ("завтра в половине третьего", WED, d(2026, 10, 22, 14, 30), True),
    ("завтра без пятнадцати три", WED, d(2026, 10, 22, 14, 45), True),
    ("завтра в полдень", WED, d(2026, 10, 22, 12), True),
    ("в три", WED, d(2026, 10, 21, 15), True),  # время без даты, ещё не прошло
    ("в 10", WED, d(2026, 10, 22, 10), True),  # уже прошло → завтра
    ("в 11 вечера", LATE, d(2026, 10, 22, 23), True),
    ("в два ночи", WED, d(2026, 10, 22, 2), True),
    # --- части суток ---
    ("завтра утром", WED, d(2026, 10, 22, 9), True),
    ("завтра днём", WED, d(2026, 10, 22, 13), True),
    ("сегодня вечером", WED, d(2026, 10, 21, 19), True),
    ("вечером", WED, d(2026, 10, 21, 19), True),
    ("завтра вечером в 8", WED, d(2026, 10, 22, 20), True),
    ("утром в 7", WED, d(2026, 10, 22, 7), True),
    # --- относительное ---
    ("через два часа", WED, d(2026, 10, 21, 16), True),
    ("через 15 минут", WED, d(2026, 10, 21, 14, 15), True),
    ("через полчаса", WED, d(2026, 10, 21, 14, 30), True),
    ("через час", WED, d(2026, 10, 21, 15), True),
    ("через полтора часа", WED, d(2026, 10, 21, 15, 30), True),
    ("через два часа", LATE, d(2026, 10, 22, 1, 30), True),  # переход через полночь
    ("через 3 дня", WED, d(2026, 10, 24), False),
    ("через неделю", WED, d(2026, 10, 28), False),
    ("через месяц", WED, d(2026, 11, 21), False),
    ("через неделю в 10", WED, d(2026, 10, 28, 10), True),
    # --- дедлайны ---
    ("до конца недели", WED, d(2026, 10, 25, 23, 59), True),
    ("до конца недели", SUN, d(2026, 10, 25, 23, 59), True),
    ("до конца дня", WED, d(2026, 10, 21, 23, 59), True),
    ("до конца месяца", WED, d(2026, 10, 31, 23, 59), True),
    ("до пятницы", WED, d(2026, 10, 23, 23, 59), False),
    # --- явные даты ---
    ("29 октября в 15:30", WED, d(2026, 10, 29, 15, 30), True),
    ("5 марта", WED, d(2027, 3, 5), False),  # прошло в этом году → следующий
    ("5 марта 2026 года", WED, d(2026, 3, 5), False),
    ("двадцать девятого октября", WED, d(2026, 10, 29), False),
    ("первого ноября в 10", WED, d(2026, 11, 1, 10), True),
    ("29.10", WED, d(2026, 10, 29), False),
    ("29.10.2027", WED, d(2027, 10, 29), False),
    ("15-го числа", WED, d(2026, 11, 15), False),
    ("2 января", NEW_YEAR, d(2027, 1, 2), False),
    ("завтра", NEW_YEAR, d(2026, 12, 31), False),
    ("послезавтра", NEW_YEAR, d(2027, 1, 1), False),
]


@pytest.mark.parametrize(
    "text,now,start,has_time", POINT_CASES, ids=[f"{c[0]}@{c[1]:%a%H}" for c in POINT_CASES]
)
def test_point(text: str, now: datetime, start: datetime, has_time: bool) -> None:
    spec = R.resolve(text, now, "point")
    assert spec.start == start, spec
    assert spec.has_time is has_time
    assert spec.start.tzinfo is not None


# (фраза, now, start, end) — end исключающий
PERIOD_CASES = [
    ("сегодня", WED, d(2026, 10, 21), d(2026, 10, 22)),
    ("на сегодня", WED, d(2026, 10, 21), d(2026, 10, 22)),
    ("завтра", WED, d(2026, 10, 22), d(2026, 10, 23)),
    ("в субботу", WED, d(2026, 10, 24), d(2026, 10, 25)),
    ("на эту неделю", WED, d(2026, 10, 19), d(2026, 10, 26)),
    ("на этой неделе", WED, d(2026, 10, 19), d(2026, 10, 26)),
    ("на неделе", WED, d(2026, 10, 19), d(2026, 10, 26)),
    ("на следующей неделе", WED, d(2026, 10, 26), d(2026, 11, 2)),
    ("на следующую неделю", SUN, d(2026, 10, 26), d(2026, 11, 2)),
    ("на прошлой неделе", WED, d(2026, 10, 12), d(2026, 10, 19)),
    ("в ноябре", WED, d(2026, 11, 1), d(2026, 12, 1)),
    ("в марте", WED, d(2027, 3, 1), d(2027, 4, 1)),
    ("в этом месяце", WED, d(2026, 10, 1), d(2026, 11, 1)),
    ("в следующем месяце", NEW_YEAR, d(2027, 1, 1), d(2027, 2, 1)),
    ("на выходных", WED, d(2026, 10, 24), d(2026, 10, 26)),
    ("на ближайшие 3 дня", WED, d(2026, 10, 21), d(2026, 10, 24)),
    ("завтра утром", WED, d(2026, 10, 22, 6), d(2026, 10, 22, 12)),
    ("до конца недели", WED, WED, d(2026, 10, 26, 0, 0)),
]


@pytest.mark.parametrize("text,now,start,end", PERIOD_CASES, ids=[c[0] for c in PERIOD_CASES])
def test_period(text: str, now: datetime, start: datetime, end: datetime) -> None:
    spec = R.resolve(text, now, "period")
    assert spec.is_period
    assert (spec.start, spec.end) == (start, end)


def test_week_in_point_mode_is_ambiguous() -> None:
    spec = R.resolve("на следующей неделе", WED, "point")
    assert spec.is_period and spec.ambiguous
    assert spec.start == d(2026, 10, 26)


def test_past_tense_prefers_past_weekday() -> None:
    res = R.extract("что было в пятницу", WED, "period")
    assert res.spec and res.spec.start == d(2026, 10, 16)


def test_past_tense_prefers_past_month() -> None:
    res = R.extract("какие встречи были в марте", WED, "period")
    assert res.spec and res.spec.start == d(2026, 3, 1)


def test_unparsed_text_is_ambiguous() -> None:
    spec = R.resolve("когда-нибудь потом", WED)
    assert spec.start is None and spec.ambiguous


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError):
        R.resolve("завтра", datetime(2026, 10, 21, 14, 0))


def test_interval() -> None:
    spec = R.resolve("завтра с 10 до 12", WED)
    assert spec.start == d(2026, 10, 22, 10) and spec.end == d(2026, 10, 22, 12)


def test_recurrence_weekly() -> None:
    res = R.extract("каждый понедельник в 10 планёрка", WED)
    assert res.recurrence == "FREQ=WEEKLY;BYDAY=MO"
    assert res.spec and res.spec.start == d(2026, 10, 26, 10)


def test_recurrence_daily_and_weekdays() -> None:
    assert R.extract("каждый день в 9 зарядка", WED).recurrence == "FREQ=DAILY"
    assert R.extract("по будням в 10 стендап", WED).recurrence == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"


def test_numbers_not_hours() -> None:
    # «в 2 задачах» не должно стать временем, а «через 3 дня» — не 3 часа
    assert R.extract("напиши про 2 задачи", WED).spec is None
    spec = R.resolve("через 3 дня", WED)
    assert spec.has_time is False


@pytest.mark.parametrize(
    "text,remainder",
    [
        ("Напомни мне завтра в 10 утра позвонить Ивану", "Напомни мне позвонить Ивану"),
        ("Добавь встречу с командой завтра в 15:00", "Добавь встречу с командой"),
        ("В четверг в 11 у меня встреча с дизайнером", "у меня встреча с дизайнером"),
        ("Поставь задачу купить билеты до конца недели", "Поставь задачу купить билеты"),
        ("Через два часа напомни проверить почту", "напомни проверить почту"),
    ],
)
def test_spans_removal(text: str, remainder: str) -> None:
    res = R.extract(text, WED)
    assert remove_spans(text, res.spans) == remainder


def test_has_explicit_date() -> None:
    assert has_explicit_date("на 19:00", WED) is False
    assert has_explicit_date("на субботу 19:00", WED) is True
    assert has_explicit_date("на завтра", WED) is True


@pytest.mark.parametrize(
    "text,has_date,has_time",
    [
        ("в три", False, True),
        ("в 15:00", False, True),
        ("вечером", False, True),
        ("завтра", True, False),
        ("в пятницу", True, False),
        ("29 октября", True, False),
        ("завтра в 10", True, True),
        ("через два часа", True, True),
        ("на следующей неделе", True, False),
    ],
)
def test_has_date_v11(text: str, has_date: bool, has_time: bool) -> None:
    spec = R.resolve(text, WED)
    assert (spec.has_date, spec.has_time) == (has_date, has_time)


@pytest.mark.parametrize(
    "text", ["31 ноября", "Что у меня 31 ноября?", "30 февраля в 10", "на 31.11", "31.04.2027"]
)
def test_invalid_date_is_ambiguous_not_shifted(text: str) -> None:
    # q75: несуществующую дату не сдвигаем на 1 декабря и не подменяем сегодняшним днём
    for mode in ("point", "period"):
        spec = R.resolve(text, WED, mode)  # type: ignore[arg-type]
        assert spec.ambiguous and spec.has_date
        assert spec.start is None and spec.end is None

"""Детерминированный резолвер относительных дат и времени для русского языка.

LLM возвращает только фразу времени («в пятницу в 11»), абсолютную дату считает этот модуль.
«Сейчас» всегда приходит параметром `now` (tz-aware).

Политики (документ 01, раздел 6):
- «в пятницу» — ближайшая будущая пятница; если сегодня пятница и время не прошло
  (или не указано) — сегодня.
- «в 3», «в 5» без «утра/вечера» → 15:00, 17:00 (если час 1–7). «в 10 утра» → 10:00.
- «утром» 09:00, «днём» 13:00, «вечером» 19:00, «ночью» 23:00.
- «до конца недели» → воскресенье 23:59; «на следующей неделе» → период пн–вс
  (для создания — ambiguous).
- «через N часов/минут» — от `now`.
- Время без даты: сегодня, если ещё не прошло, иначе завтра.

Соглашение о периодах: `end` — ИСКЛЮЧАЮЩАЯ граница (полуинтервал [start, end)),
как и у встреч [starts_at, ends_at). «Сегодня» = [00:00 сегодня, 00:00 завтра).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.contracts import TimeSpec

from .text import NormText, normalize

Mode = Literal["point", "period"]

MONTHS_GEN = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
MONTHS_PREP = {
    "январе": 1,
    "феврале": 2,
    "марте": 3,
    "апреле": 4,
    "мае": 5,
    "июне": 6,
    "июле": 7,
    "августе": 8,
    "сентябре": 9,
    "октябре": 10,
    "ноябре": 11,
    "декабре": 12,
}
MONTHS_NOM = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}
WEEKDAYS = {
    "понедельник": 0,
    "понедельника": 0,
    "понедельникам": 0,
    "вторник": 1,
    "вторника": 1,
    "вторникам": 1,
    "среда": 2,
    "среду": 2,
    "среды": 2,
    "средам": 2,
    "четверг": 3,
    "четверга": 3,
    "четвергам": 3,
    "пятница": 4,
    "пятницу": 4,
    "пятницы": 4,
    "пятницам": 4,
    "суббота": 5,
    "субботу": 5,
    "субботы": 5,
    "субботам": 5,
    "воскресенье": 6,
    "воскресенья": 6,
    "воскресеньям": 6,
    "понедельнику": 0,
    "вторнику": 1,
    "среде": 2,
    "четвергу": 3,
    "пятнице": 4,
    "субботе": 5,
    "воскресенью": 6,
}
RRULE_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

DAYPART_TIME = {"утро": 9, "день": 13, "обед": 12, "до_обеда": 12, "вечер": 19, "ночь": 23}
# окна для списков («что у меня на вечер») и для «в обед» при создании (ambiguous)
DAYPART_WINDOW = {
    "утро": (6, 12), "день": (12, 17), "обед": (12, 14), "вечер": (17, 23), "ночь": (0, 6),
    "до_обеда": (0, 13),
}  # fmt: skip
DAYPART_WORDS = {
    "утром": "утро",
    "утра": "утро",
    "с утра": "утро",
    "утро": "утро",
    "утречком": "утро",
    "днем": "день",
    "дня": "день",
    "в обед": "обед",
    "ближе к вечеру": "вечер",
    "под вечер": "вечер",
    "к вечеру": "вечер",
    "до обеда": "до_обеда",
    "ближе к обеду": "обед",
    "к обеду": "обед",
    "в обеденное время": "обед",
    "после обеда": "день",
    "вечером": "вечер",
    "вечера": "вечер",
    "вечер": "вечер",
    "вечерком": "вечер",
    "ночью": "ночь",
    "ночи": "ночь",
}

_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MG = "|".join(MONTHS_GEN)
_MP = "|".join(MONTHS_PREP)
_MN = "|".join(MONTHS_NOM)
# слова, после которых число — не час: «в 3 дня», «в 5 минут» и т.п.
# («дня» как часть суток — отдельно)
_NOT_HOUR_NEXT = re.compile(
    r"^(?:минут\w*|мин|секунд\w*|дн(?:я|ей)|недел\w*|месяц\w*|год\w*|лет|раз\w*|числа|" + _MG + r"|"
    r"человек\w*|штук\w*|рубл\w*|процент\w*|страниц\w*)\b"
)

# Прилагательные «о прошлом»: «старую встречу», «прошедшие», «давнюю», «ранние».
# Формы перечислены явно, чтобы не задеть «Старт» (компания) и «ранним утром» (часть дня).
PAST_ADJ = (
    r"стар(?:ый|ая|ое|ые|ую|ого|ой|ом|ых|ым|ыми|ому)|прошедш\w*|давн[\w-]*|"
    r"ранн(?:ий|яя|ее|ие|его|ей|ем|их|им|ими|юю|ему)(?!\s+(?:утр|вечер|дн[её]м|ноч))|"
    r"прежн\w*|минувш\w*"
)
_PAST_MARKERS = re.compile(
    r"(?<!\S)(был|была|было|были|прошл\w*|вчера|позавчера|ставил\w*|звонил\w*|встречал\w*|"
    r"созванивал\w*|обсуждал\w*|отправлял\w*|делал\w*|проводил\w*|писал\w*|" + PAST_ADJ + r")(?!\S)"
)


def _r(p: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\S)(?:" + p + r")(?!\S)")


# ---------- результат разбора ----------


@dataclass
class DateParse:
    spec: TimeSpec | None
    has_date: bool = False
    spans: list[tuple[int, int]] = field(default_factory=list)  # позиции в ИСХОДНОМ тексте
    deadline: bool = False
    recurrence: str | None = None

    @property
    def found(self) -> bool:
        return self.spec is not None


@dataclass
class _Parts:
    day: date | None = None
    hour: int | None = None
    minute: int = 0
    hour_qualified: bool = False  # «утра/вечера» или 24ч формат — политику pm не применяем
    daypart: str | None = None
    delta: timedelta | None = None  # «через 2 часа»
    period: tuple[date, date] | None = None  # [start, end)
    period_kind: str | None = None  # week | month | weekend | year | days
    week_offset: int | None = None  # для «в пятницу на следующей неделе»
    weekday: int | None = None
    weekday_mod: str | None = None  # this | next | prev | None
    deadline: datetime | None = None
    deadline_day: bool = False  # «до пятницы»
    explicit_time_end: datetime | None = None
    invalid: bool = False  # «31 ноября»
    spans: list[tuple[int, int]] = field(default_factory=list)  # позиции в нормализованной строке
    taken: list[tuple[int, int]] = field(default_factory=list)

    def free(self, s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in self.taken)

    def take(self, s: int, e: int) -> None:
        self.taken.append((s, e))
        self.spans.append((s, e))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _apply_pm(hour: int, qualified: bool, daypart: str | None) -> int:
    if daypart == "утро":
        return 0 if hour == 12 else hour
    if daypart in ("день", "вечер"):
        return hour + 12 if 1 <= hour < 12 else hour
    if daypart == "ночь":
        if hour == 12:
            return 0
        return hour + 12 if 6 <= hour < 12 else hour
    if not qualified and 1 <= hour < 8:
        return hour + 12
    return hour


class RuleDateResolver:
    """Реализация протокола `DateResolver` из контракта + разбор дат внутри целой фразы."""

    # ----- публичное API -----

    def resolve(self, text: str, now: datetime, mode: Mode = "point") -> TimeSpec:
        res = self.extract(text, now, mode)
        if res.spec is None:
            iso = _parse_iso(text, now)  # LLM иногда возвращает вычисленную дату вместо фразы
            if iso is not None:
                return iso
            return TimeSpec(text=text, ambiguous=bool(text.strip()))
        return res.spec

    def extract(
        self, text: str, now: datetime, mode: Mode = "point", prefer_past: bool | None = None
    ) -> DateParse:
        """Находит выражения времени в произвольной фразе, возвращает TimeSpec и их позиции."""
        if now.tzinfo is None:
            raise ValueError("now должен быть tz-aware")
        nt = normalize(text)
        s = nt.text
        if prefer_past is None:
            prefer_past = bool(_PAST_MARKERS.search(s))
        p = _Parts()
        recurrence = self._recurrence(s, p, now)
        self._relative(s, p)
        self._deadlines(s, p, now)
        self._periods(s, p, now, prefer_past)
        self._day_words(s, p, now)
        self._weekdays(s, p)
        self._explicit_dates(s, p, now, mode, prefer_past)
        self._times(s, p)
        self._dayparts(s, p)
        spec, has_date = self._combine(p, now, mode, prefer_past)
        spans = self._orig_spans(nt, p.spans)
        if spec is not None:
            spec = spec.model_copy(
                update={"text": " ".join(text[a:b] for a, b in spans) or text, "has_date": has_date}
            )
        return DateParse(
            spec=spec,
            has_date=has_date,
            spans=spans,
            deadline=p.deadline is not None or p.deadline_day,
            recurrence=recurrence,
        )

    # ----- отдельные правила -----

    def _recurrence(self, s: str, p: _Parts, now: datetime) -> str | None:
        m = _r(r"(?:каждый|каждую|каждое) (" + _WD + r")|по (" + _WD + r")").search(s)
        if m:
            wd = WEEKDAYS[m.group(1) or m.group(2)]
            p.weekday = wd
            p.take(m.start(), m.end())
            return f"FREQ=WEEKLY;BYDAY={RRULE_DAYS[wd]}"
        m = _r(r"по будням|каждый будний день|в будни").search(s)
        if m:
            p.take(m.start(), m.end())
            return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        m = _r(r"каждый день|ежедневно|каждое утро|каждый вечер").search(s)
        if m:
            p.take(m.start(), m.end())
            if "утро" in m.group(0):
                p.daypart = "утро"
            elif "вечер" in m.group(0):
                p.daypart = "вечер"
            return "FREQ=DAILY"
        m = _r(r"каждую неделю|еженедельно|раз в неделю").search(s)
        if m:
            p.take(m.start(), m.end())
            return "FREQ=WEEKLY"
        m = _r(r"каждый месяц|ежемесячно|раз в месяц").search(s)
        if m:
            p.take(m.start(), m.end())
            return "FREQ=MONTHLY"
        m = _r(r"каждые (\d+) (?:дня|дней)").search(s)
        if m:
            p.take(m.start(), m.end())
            return f"FREQ=DAILY;INTERVAL={int(m.group(1))}"
        return None

    def _relative(self, s: str, p: _Parts) -> None:
        pat = _r(
            r"через (?:(\d+|пол|полтора|полторы|пару|пары|пара|несколько) )?"
            r"(минуту|минуты|минут|мин|час|часа|часов|день|дня|дней|неделю|недели|недель|"
            r"месяц|месяца|месяцев|год|года|лет)"
            r"(?: (\d+) (?:минут|минуты|мин))?"
        )
        # «три дня назад», «неделю назад», «пару недель назад» — день в прошлом
        ago = _r(
            r"(?:(\d+|пару|пары|пара|несколько) )?(день|дня|дней|неделю|недели|недель) назад"
        ).search(s)
        if ago and p.free(ago.start(), ago.end()):
            raw = ago.group(1)
            k = 2 if raw in ("пару", "пары", "пара") else 3 if raw == "несколько" else int(raw or 1)
            days = k * (7 if ago.group(2).startswith("недел") else 1)
            p.delta = -timedelta(days=days)
            p.take(ago.start(), ago.end())
            return
        for m in pat.finditer(s):
            if not p.free(m.start(), m.end()):
                continue
            n_raw, unit, extra_min = m.group(1), m.group(2), m.group(3)
            if n_raw in ("полтора", "полторы"):
                n = 1.5
            elif n_raw in ("пару", "пары", "пара"):
                n = 2.0
            elif n_raw == "несколько":
                n = 3.0
            elif n_raw == "пол":
                n = 0.5
            elif n_raw:
                n = float(n_raw)
            else:
                n = 1.0
            if unit.startswith("мин"):
                p.delta = timedelta(minutes=n)
            elif unit.startswith("час"):
                p.delta = timedelta(hours=n, minutes=int(extra_min or 0))
            elif unit.startswith(("д", "ден")):
                p.delta = timedelta(days=n)
            elif unit.startswith("недел"):
                p.delta = timedelta(weeks=n)
            elif unit.startswith("месяц"):
                p.delta = timedelta(days=30 * n)  # уточняется в _combine
                p.period_kind = f"months:{int(n)}"
            else:
                p.delta = timedelta(days=365 * n)
                p.period_kind = f"years:{int(n)}"
            p.take(m.start(), m.end())
            return

    def _deadlines(self, s: str, p: _Parts, now: datetime) -> None:
        m = _r(
            r"(?:до|к) конц[ау] (дня|недели|месяца|года)|(?:до"
            r"|к) (?:вечера|ночи) (сегодня|сегодняшнего дня)"
        ).search(s)
        if not m:
            return
        unit = m.group(1) or "дня"
        today = now.date()
        if unit == "дня":
            d = today
        elif unit == "недели":
            d = _monday(today) + timedelta(days=6)
        elif unit == "месяца":
            d = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        else:
            d = date(today.year, 12, 31)
        p.deadline = datetime.combine(d, time(23, 59), tzinfo=now.tzinfo)
        p.take(m.start(), m.end())

    def _periods(self, s: str, p: _Parts, now: datetime, prefer_past: bool) -> None:
        today = now.date()
        mon = _monday(today)
        week = _r(
            r"(?:(?:на|в|за|по) )?(?:(эт(?:ой|у|а|от|ом)|текущ\w*|следующ\w*|будущ\w*|"
            r"прошл\w*|предыдущ\w*) )?недел(?:е|ю|я|и)"
        )
        bare = _r(r"(?:на|в|за) (следующей|этой|прошлой|будущей)").search(s)
        if (
            bare
            and p.free(bare.start(), bare.end())
            and re.match(r"\s*(?:[?!.,]|$)", s[bare.end() :])
        ):
            mod = bare.group(1)
            off = 1 if mod.startswith(("след", "буд")) else -1 if mod.startswith("прошл") else 0
            start = mon + timedelta(weeks=off)  # «на следующей?» — неделя по умолчанию
            p.period = (start, start + timedelta(days=7))
            p.period_kind = "week"
            p.week_offset = off
            p.take(bare.start(), bare.end())
        for mw in week.finditer(s):
            if not p.free(mw.start(), mw.end()):
                continue
            mod = mw.group(1) or ""
            off = (
                1
                if mod.startswith(("след", "буд"))
                else -1
                if mod.startswith(("прошл", "предыд"))
                else 0
            )
            if not mod and not mw.group(0).startswith(("на ", "в ", "за ", "по ")):
                continue  # голое «неделя» — не период
            start = mon + timedelta(weeks=off)
            p.period = (start, start + timedelta(days=7))
            p.period_kind = "week"
            p.week_offset = off
            p.take(mw.start(), mw.end())
            break
        m = _r(
            r"(?:на|в|за) (эт(?:ом|от)|текущ(?:ем|ий)|"
            r"следующ(?:ем|ий)|будущ(?:ем|ий)|прошл(?:ом|ый)|"
            r"предыдущ(?:ем|ий)) месяц(?:е)?"
        ).search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            mod = m.group(1)
            off = (
                1
                if mod.startswith(("след", "буд"))
                else -1
                if mod.startswith(("прошл", "предыд"))
                else 0
            )
            y, mo = today.year, today.month + off
            y += (mo - 1) // 12
            mo = (mo - 1) % 12 + 1
            start = date(y, mo, 1)
            p.period = (start, _add_month(start))
            p.period_kind = "month"
            p.take(m.start(), m.end())
        m = _r(
            r"(?:(?:в|за) )?("  # «ноябре в встречи какие» — порядок слов, предлог отдельно
            + _MP
            + r")(?: (\d{4})(?: года| году| г)?)?|(?:на|за|весь|в) ("
            + _MN
            + r")(?: (\d{4}))?"
        ).search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            mo = MONTHS_PREP[m.group(1)] if m.group(1) else MONTHS_NOM[m.group(3)]
            if m.group(4):
                m_year = m.group(4)
            else:
                m_year = m.group(2)
            ry = _r(
                r"(?:в|на|за) (эт(?:ом|от)|следующ(?:ем|ий)|позапрошл(?:ом|ый)|прошл(?:ом|ый)) "
                r"год(?:у)?"
            ).search(s)
            if not m_year and ry and p.free(ry.start(), ry.end()):
                # «в прошлом году в декабре» — месяц указанного года
                w = ry.group(1)
                off = (
                    1
                    if w.startswith("след")
                    else -2
                    if w.startswith("позап")
                    else (-1 if w.startswith("прошл") else 0)
                )
                m_year = str(today.year + off)
                p.take(ry.start(), ry.end())
            if m_year:
                y = int(m_year)
            elif prefer_past:
                y = today.year if mo <= today.month else today.year - 1
            else:
                y = today.year if mo >= today.month else today.year + 1
            start = date(y, mo, 1)
            p.period = (start, _add_month(start))
            p.period_kind = "month"
            p.take(m.start(), m.end())
        m = _r(r"(?:в|на|за) (эт(?:ом|от)|следующ(?:ем|ий)|прошл(?:ом|ый)) год(?:у)?").search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            off = (
                1 if m.group(1).startswith("след") else -1 if m.group(1).startswith("прошл") else 0
            )
            start = date(today.year + off, 1, 1)
            p.period = (start, date(start.year + 1, 1, 1))
            p.period_kind = "year"
            p.take(m.start(), m.end())
        m = _r(
            r"(?:(?:на|в|за|по) )?(?:(эти|этих|следующие|следующих|прошлые|прошлых) )?"
            r"выходн(?:ые|ых)"
        ).search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            mod = m.group(1) or ""
            sat = mon + timedelta(days=5)
            if mod.startswith("след"):
                sat += timedelta(weeks=1)
            elif mod.startswith("прошл") or (prefer_past and today < sat):
                sat -= timedelta(weeks=1)
            p.period = (sat, sat + timedelta(days=2))
            p.period_kind = "weekend"
            p.take(m.start(), m.end())
        m = _r(
            r"(?:(?:на|в|за) )?(?:ближайшие|следующие) (\d+) (?:дня|дней)|(?:на|в) ближайшие дни|"
            r"(скоро|в ближайшее время|на днях|в ближайшие дни|в скором времени)"
        ).search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            n = int(m.group(1)) if m.group(1) else (7 if m.group(2) else 3)
            p.period = (today, today + timedelta(days=n))
            if prefer_past and m.group(2) == "на днях":  # «что было на днях» — прошедшая неделя
                p.period = (today - timedelta(days=7), today + timedelta(days=1))
            p.period_kind = "days"
            p.take(m.start(), m.end())
        m = _r(r"(?:за|в) последние (\d+) (?:дня|дней)").search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            n = int(m.group(1))
            p.period = (today - timedelta(days=n - 1), today + timedelta(days=1))
            p.period_kind = "days"
            p.take(m.start(), m.end())

    def _day_words(self, s: str, p: _Parts, now: datetime) -> None:
        adj = _r(
            r"(?:(?:на|к|до|с|в|за) )?(сегодняшн|завтрашн|послезавтрашн|вчерашн|позавчерашн)\w*"
            r"(?: (вечер\w*|утр\w*|ноч\w*|дн(?:я|ем)|день|обед\w*))?"
        ).search(s)
        if adj and p.free(adj.start(), adj.end()):
            shift = {"сегодняшн": 0, "завтрашн": 1, "послезавтрашн": 2, "вчерашн": -1,
                     "позавчерашн": -2}[adj.group(1)]  # fmt: skip
            p.day = now.date() + timedelta(days=shift)
            part = adj.group(2) or ""
            for stem, dp in (
                ("вечер", "вечер"),
                ("утр", "утро"),
                ("ноч", "ночь"),
                ("обед", "обед"),
            ):
                if part.startswith(stem):
                    p.daypart = dp
            if adj.group(0).startswith(("до ", "к ")):
                p.deadline_day = True
            p.take(adj.start(), adj.end())
            return
        m = _r(
            r"(?:(?:на|к|до|с|в) )?(сегодня|сегодняшний день|завтра|послезавтра|вчера|позавчера)"
        ).search(s)
        if not m or not p.free(m.start(), m.end()):
            return
        word = m.group(1)
        shift = {
            "сегодня": 0,
            "сегодняшний день": 0,
            "завтра": 1,
            "послезавтра": 2,
            "вчера": -1,
            "позавчера": -2,
        }[word]
        p.day = now.date() + timedelta(days=shift)
        if m.group(0).startswith(("до ", "к ")):
            p.deadline_day = True
        p.take(m.start(), m.end())

    def _weekdays(self, s: str, p: _Parts) -> None:
        pat = _r(
            r"(?:(в|во|на|к|до|по|с|со|ко) )?(?:(эт(?:у|от|о|"
            r"а)|следующ(?:ую|ий|ее|ая)|ближайш(?:ую|ий|ее|ая)|"
            r"будущ(?:ую|ий|ее)|позапрошл(?:ую|ый|ое|ая)|прошл(?:ую|ый|ое|ая)|"
            r"предыдущ(?:ую|ий)) )?(" + _WD + r")"
        )
        for m in pat.finditer(s):
            if not p.free(m.start(), m.end()):
                continue
            prep, mod, wd = m.group(1), m.group(2) or "", m.group(3)
            p.weekday = WEEKDAYS[wd]
            p.weekday_mod = (
                "next"
                if mod.startswith(("след", "буд"))
                else "prev2"
                if mod.startswith("позапрошл")
                else "prev"
                if mod.startswith(("прошл", "предыд"))
                else "this"
                if mod.startswith("эт")
                else None
            )
            if prep in ("до", "к", "ко"):
                p.deadline_day = True
            p.take(m.start(), m.end())
            return

    def _explicit_dates(
        self, s: str, p: _Parts, now: datetime, mode: Mode, prefer_past: bool
    ) -> None:
        today = now.date()
        m = _r(
            r"(?:(?:на|к|до|с|по|в) )?(\d{1,2}) (" + _MG + r")(?: (\d{4})(?: года| г)?)?"
        ).search(s)
        if m and p.free(m.start(), m.end()):
            d, mo = int(m.group(1)), MONTHS_GEN[m.group(2)]
            y = int(m.group(3)) if m.group(3) else None
            dd = _safe_date(y or today.year, mo, d)
            if dd is None:
                p.invalid = True
                p.take(m.start(), m.end())
                return
            if dd:
                if y is None:
                    if prefer_past and dd > today:
                        dd = _safe_date(today.year - 1, mo, d) or dd
                    elif not prefer_past and dd < today:
                        dd = _safe_date(today.year + 1, mo, d) or dd
                p.day = dd
                if m.group(0).startswith(("до ", "к ")):
                    p.deadline_day = True
                p.take(m.start(), m.end())
                return
        m = _r(r"(?:(?:на|к|до|с|по) )?(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?").search(s)
        if m and p.free(m.start(), m.end()) and not s[: m.start()].endswith("в "):
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else None
            if y is not None and y < 100:
                y += 2000
            if 1 <= mo <= 12 and 1 <= d <= 31:
                dd = _safe_date(y or today.year, mo, d)
                if dd is None:  # «31.11»: дата названа, но её нет — переспросить, не сдвигать
                    p.invalid = True
                    p.take(m.start(), m.end())
                    return
                if dd:
                    if y is None and not prefer_past and dd < today:
                        dd = _safe_date(today.year + 1, mo, d) or dd
                    p.day = dd
                    p.take(m.start(), m.end())
                    return
        # «с 26 по 30», «с 26 числа до 30» — день месяца; «с 10 до 12» — интервал времени (не тут)
        m = _r(
            r"с (\d{1,2})(?: числа)? по (\d{1,2})(?: числа)?(?: (" + _MG + r"))?|"
            r"с (\d{1,2}) числа до (\d{1,2})(?: числа)?(?: (" + _MG + r"))?"
        ).search(s)
        if m and m.group(4):
            m_groups = (m.group(4), m.group(5), m.group(6))
        elif m:
            m_groups = (m.group(1), m.group(2), m.group(3))
        if m and p.free(m.start(), m.end()) and p.period is None:
            g1, g2, g3 = m_groups
            mo = MONTHS_GEN[g3] if g3 else today.month
            a = _safe_date(today.year, mo, int(g1))
            b = _safe_date(today.year, mo, int(g2))
            if a and b and a < today and not prefer_past and not g3:
                nm = _add_month(date(today.year, today.month, 1))
                a, b = _safe_date(nm.year, nm.month, a.day), _safe_date(nm.year, nm.month, b.day)
            if a and b and a <= b:
                p.period = (a, b + timedelta(days=1))
                p.period_kind = "days"
                p.take(m.start(), m.end())
                return
        m = _r(
            r"(?:в )?(начале|середине|конце) (?:(этого|следующего) )?(месяца|" + _MG + r")"
        ).search(s)
        if m and p.free(m.start(), m.end()) and p.period is None:
            if m.group(3) == "месяца":
                first = date(today.year, today.month, 1)
                if (m.group(2) or "").startswith("след"):
                    first = _add_month(first)
            else:
                mo = MONTHS_GEN[m.group(3)]
                y = today.year if mo >= today.month or prefer_past else today.year + 1
                first = date(y, mo, 1)
            last = calendar.monthrange(first.year, first.month)[1]
            lo, hi = {"начале": (1, 10), "середине": (11, 20), "конце": (21, last)}[m.group(1)]
            p.period = (first.replace(day=lo), first.replace(day=hi) + timedelta(days=1))
            p.period_kind = "days"
            p.take(m.start(), m.end())
            return
        m = _r(
            r"(?:(?:в|на|к|до) )?(?:сам(?:ый|ом) )?последн(?:ий|ем|его|ему) (?:день|число|числа) "
            r"(?:(этого|текущего|следующего|прошлого) )?месяца"
        ).search(s)
        if m and p.free(m.start(), m.end()):
            mod = m.group(1) or ""
            off = 1 if mod.startswith("след") else -1 if mod.startswith("прошл") else 0
            first = date(today.year, today.month, 1)
            for _ in range(abs(off)):
                first = _add_month(first) if off > 0 else (first - timedelta(days=1)).replace(day=1)
            p.day = first.replace(day=calendar.monthrange(first.year, first.month)[1])
            if m.group(0).startswith(("до ", "к ")):
                p.deadline_day = True
            p.take(m.start(), m.end())
            return
        m = _r(r"(?:(?:на|к|до|с|в) )?(\d{1,2}) (?:числа|число)|(?:на|к|до) (2[5-9]|3[01])").search(
            s
        )
        if m and p.free(m.start(), m.end()):
            d = int(m.group(1) or m.group(2))
            dd = _safe_date(today.year, today.month, d)
            if dd and dd < today and not prefer_past:
                nm = _add_month(date(today.year, today.month, 1))
                dd = _safe_date(nm.year, nm.month, d)
            if dd is None:
                p.invalid = True  # «31 числа» в месяце из 30 дней, «32-го» — переспросить
            if dd or p.invalid:
                p.day = dd
                if m.group(0).startswith(("до ", "к ")):
                    p.deadline_day = True
                p.take(m.start(), m.end())

    def _times(self, s: str, p: _Parts) -> None:
        # «с 10 до 12» / «с 10:00 до 11:30» — интервал
        m: re.Match[str] | None = _r(
            r"с (\d{1,2})(?:[:.](\d{2}))? до (\d{1,2})(?:[:.](\d{2}))?(?: (утра|дня|вечера))?"
        ).search(s)
        if m and p.free(m.start(), m.end()):
            q = m.group(5)
            dp = DAYPART_WORDS.get(q) if q else None
            h1 = _apply_pm(int(m.group(1)), bool(m.group(2)) and int(m.group(1)) >= 8, dp)
            h2 = _apply_pm(int(m.group(3)), bool(m.group(4)) and int(m.group(3)) >= 8, dp)
            if h2 < h1 and h2 + 12 <= 23:
                h2 += 12
            if 0 <= h1 <= 23 and 0 <= h2 <= 24:
                p.hour, p.minute, p.hour_qualified = h1, int(m.group(2) or 0), True
                p.explicit_time_end = datetime(2000, 1, 1, h2 % 24, int(m.group(4) or 0))
                p.take(m.start(), m.end())
                return
        # «в половине третьего» → 2:30 (+ политика pm)
        m = _r(r"(?:в |к |около )?(?:половине|пол) (\d{1,2})").search(s)
        if m and p.free(m.start(), m.end()) and 1 <= int(m.group(1)) <= 12:
            h = int(m.group(1)) - 1 or 12
            p.hour, p.minute = h, 30
            p.take(m.start(), m.end())
            self._qualifier_after(s, m.end(), p)
            return
        # «в четверть восьмого» → 7:15, «без четверти восемь» → 7:45
        m = _r(r"(?:в |к |около )?четверть (\d{1,2})").search(s)
        if m and p.free(m.start(), m.end()) and 1 <= int(m.group(1)) <= 12:
            p.hour, p.minute = int(m.group(1)) - 1 or 12, 15
            p.take(m.start(), m.end())
            self._qualifier_after(s, m.end(), p)
            return
        m = _r(r"(?:в |к )?без четверти (\d{1,2})").search(s)
        if m and p.free(m.start(), m.end()) and 1 <= int(m.group(1)) <= 12:
            p.hour, p.minute = int(m.group(1)) - 1 or 12, 45
            p.take(m.start(), m.end())
            self._qualifier_after(s, m.end(), p)
            return
        # «без пятнадцати три» → 2:45
        m = _r(r"(?:в |к )?без (\d{1,2})(?: минут)? (\d{1,2})").search(s)
        if m and p.free(m.start(), m.end()) and int(m.group(1)) < 60 and 1 <= int(m.group(2)) <= 12:
            h = int(m.group(2)) - 1 or 12
            p.hour, p.minute = h, 60 - int(m.group(1))
            p.take(m.start(), m.end())
            self._qualifier_after(s, m.end(), p)
            return
        m = _r(r"(?:в |к |на |около |часам к )?(полдень|полночь)").search(s)
        if m and p.free(m.start(), m.end()):
            p.hour, p.minute, p.hour_qualified = (
                (12, 0, True) if m.group(1) == "полдень" else (0, 0, True)
            )
            p.take(m.start(), m.end())
            return
        # «в 15:00», «на 19:00», «в 10.30», «в 3:30»
        m = _r(
            r"(?:(?:в|к|на|около|до|с|часам к) )?(\d{1,2})[:.](\d{2})(?: (утра|дня|вечера|ночи))?"
        ).search(s)
        if m and p.free(m.start(), m.end()) and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
            h = int(m.group(1))
            if m.group(3):
                p.daypart = DAYPART_WORDS[m.group(3)]
            p.hour, p.minute = h, int(m.group(2))
            p.hour_qualified = h == 0 or h >= 8 or bool(m.group(3))
            p.take(m.start(), m.end())
            return
        # «в 9 30» (из «в девять тридцать»), «в 10 часов 15 минут»
        m = _r(
            r"(?:в|к|около) (\d{1,2})(?: часов| часа| час)? "
            r"(\d{2})(?: минут\w*)?(?: (утра|дня|вечера|ночи))?"
        ).search(s)
        if (
            m
            and p.free(m.start(), m.end())
            and int(m.group(1)) <= 23
            and 0 <= int(m.group(2)) <= 59
        ):
            rest = s[m.end() :].lstrip()
            if not _NOT_HOUR_NEXT.match(rest):
                p.hour, p.minute = int(m.group(1)), int(m.group(2))
                if m.group(3):
                    p.daypart = DAYPART_WORDS[m.group(3)]
                p.take(m.start(), m.end())
                return
        # «в 10 утра», «в 3», «к 5 часам», «в 7 вечера», «в 10 часов»
        # разговорное: «в часов 15», «часов в 15», «в районе 15», «где-то в 15» (живой тест 23.10)
        pat = _r(
            r"(?:часов в|часам к|в часов|в районе|в районе часов|где-то в|где-то около|"
            r"в|к|около|до|с) (\d{1,2})(?: (?:час|часа|часов|часам))?(?: (утра|дня|вечера|ночи))?"
        )
        for mh in pat.finditer(s):
            if not p.free(mh.start(), mh.end()):
                continue
            h = int(mh.group(1))
            if h > 24:
                continue
            rest = s[mh.end() :].lstrip()
            if not mh.group(2) and _NOT_HOUR_NEXT.match(rest):
                continue
            p.hour, p.minute = h % 24, 0
            if mh.group(2):
                p.daypart = DAYPART_WORDS[mh.group(2)]
            p.hour_qualified = h >= 8 or h == 0
            p.take(mh.start(), mh.end())
            return
        # «8 утра», «7 вечера» без предлога; «3 дня» сюда не берём — это «три дня»
        m = _r(
            r"(?:(?:на|в|к|около|часам к) )?(\d{1,2})(?: (?:час|часа|часов))? (утра|вечера|ночи)"
        ).search(s)
        if m and p.free(m.start(), m.end()) and 0 <= int(m.group(1)) <= 12:
            p.hour, p.minute = int(m.group(1)), 0
            p.daypart = DAYPART_WORDS[m.group(2)]
            p.take(m.start(), m.end())
            return
        # «встречу добавь … на одиннадцать» — «на N» без единиц — это час
        m = _r(r"на (\d{1,2})").search(s)
        if m and p.free(m.start(), m.end()) and 1 <= int(m.group(1)) <= 23:
            rest = s[m.end() :].lstrip()
            if not re.match(
                r"(?:час|минут|мин|дн|день|недел|месяц|год|лет|человек|раз|числ|персон|штук|врем|"
                + _MG
                + r")",
                rest,
            ):
                p.hour, p.minute = int(m.group(1)), 0
                p.hour_qualified = int(m.group(1)) >= 8
                p.take(m.start(), m.end())
                self._qualifier_after(s, m.end(), p)
                return
        # «на 19» в контексте переноса: только с «часов»
        m = _r(r"на (\d{1,2})(?: (?:час|часа|часов))? (утра|дня|вечера|ночи)").search(s)
        if m and p.free(m.start(), m.end()) and int(m.group(1)) <= 12:
            p.hour, p.minute = int(m.group(1)), 0
            p.daypart = DAYPART_WORDS[m.group(2)]
            p.take(m.start(), m.end())

    def _qualifier_after(self, s: str, pos: int, p: _Parts) -> None:
        m = re.match(r" (утра|дня|вечера|ночи)(?!\S)", s[pos:])
        if m:
            p.daypart = DAYPART_WORDS[m.group(1)]
            p.take(pos, pos + m.end())

    def _dayparts(self, s: str, p: _Parts) -> None:
        if p.daypart:
            return
        pat = _r(
            r"(утром|утречком|с утра|днем|в обед|в обеденное время|после обеда|"
            r"вечером|вечерком|ночью|ближе к вечеру|под вечер|к вечеру|до обеда|ближе к обеду|"
            r"к обеду)|(?:на|к) (вечер|утро|ночь|обед)"
        )
        for m in pat.finditer(s):
            if not p.free(m.start(), m.end()):
                continue
            p.daypart = (
                DAYPART_WORDS[m.group(1)]
                if m.group(1)
                else {"вечер": "вечер", "утро": "утро", "ночь": "ночь", "обед": "обед"}[m.group(2)]
            )
            if m.group(1) == "после обеда" and p.hour is None:
                p.hour = 15
                p.hour_qualified = True
            p.take(m.start(), m.end())
            return

    # ----- сборка -----

    def _combine(
        self, p: _Parts, now: datetime, mode: Mode, prefer_past: bool
    ) -> tuple[TimeSpec | None, bool]:
        tz = now.tzinfo
        today = now.date()
        if p.invalid:
            return TimeSpec(text="", ambiguous=True), True
        nothing = (
            p.day is None
            and p.hour is None
            and p.daypart is None
            and p.delta is None
            and p.period is None
            and p.weekday is None
            and p.deadline is None
        )
        if nothing:
            return None, False

        def dt(d: date, h: int = 0, mi: int = 0) -> datetime:
            return datetime.combine(d, time(h, mi), tzinfo=tz)

        hour: int | None = None
        if p.hour is not None:
            hour = _apply_pm(p.hour, p.hour_qualified, p.daypart)
        elif p.daypart == "обед" and mode == "point":
            # «примерно в обед»: точного времени нет — окно 12–14, диалог предложит слот
            base_day = p.day or (
                self._weekday_date(p.weekday, p.weekday_mod, now, None, 0, prefer_past)
                if p.weekday is not None
                else (today if now.hour < 14 else today + timedelta(days=1))
            )
            h1, h2 = DAYPART_WINDOW["обед"]
            return TimeSpec(
                text="", start=dt(base_day, h1), end=dt(base_day, h2), ambiguous=True
            ), (p.day is not None or p.weekday is not None)
        elif p.daypart and mode == "point":
            hour = DAYPART_TIME[p.daypart]
        if hour == 0 and p.minute == 0 and p.day == today and not prefer_past:
            p.day = today + timedelta(days=1)  # «сегодня в 12 ночи» — наступающая полночь

        # дедлайн «до конца недели»
        if p.deadline is not None and p.day is None and p.weekday is None:
            if mode == "period":
                return TimeSpec(
                    text="",
                    start=now,
                    end=p.deadline + timedelta(minutes=1),
                    has_time=False,
                    is_period=True,
                ), True
            return TimeSpec(text="", start=p.deadline, has_time=True), True

        # «через 2 часа» / «через 3 дня»
        if p.delta is not None:
            if p.period_kind and p.period_kind.startswith(("months", "years")):
                n = int(p.period_kind.split(":")[1]) or 1
                base = _add_months_keep_day(
                    today, n if p.period_kind.startswith("months") else 12 * n
                )
            elif p.delta < timedelta(days=1) and p.delta.days == 0 and p.delta.seconds > 0:
                start = (now + p.delta).replace(second=0, microsecond=0)
                return TimeSpec(text="", start=start, has_time=True), True
            else:
                base = today + timedelta(days=p.delta.days)
            if hour is not None:
                return TimeSpec(text="", start=dt(base, hour, p.minute), has_time=True), True
            if mode == "period":
                return TimeSpec(
                    text="", start=dt(base), end=dt(base + timedelta(days=1)), is_period=True
                ), True
            return TimeSpec(text="", start=dt(base), has_time=False), True

        # день недели (с учётом «на следующей неделе»)
        day = p.day
        if day is None and p.weekday is not None:
            if p.week_offset is not None:
                day = _monday(today) + timedelta(weeks=p.week_offset, days=p.weekday)
                p.period = None
            else:
                day = self._weekday_date(p.weekday, p.weekday_mod, now, hour, p.minute, prefer_past)

        # период без конкретного дня
        if day is None and p.period is not None:
            a, b = p.period
            if mode == "point" and p.period_kind == "week" and p.week_offset == 0 and hour is None:
                # «на этой неделе» при создании ≈ «до конца недели»? — нет, уточняем день
                pass
            return TimeSpec(
                text="",
                start=dt(a),
                end=dt(b),
                has_time=False,
                is_period=True,
                ambiguous=(mode == "point"),
            ), True

        # время без даты
        if day is None:
            if hour is None and p.daypart and mode == "period":
                h1, h2 = DAYPART_WINDOW[p.daypart]
                start = dt(today, h1)
                if h2 <= now.hour and not prefer_past and p.daypart != "до_обеда":
                    start += timedelta(days=1)
                return TimeSpec(
                    text="", start=start, end=start + timedelta(hours=h2 - h1), is_period=True
                ), False
            if hour is None:
                return None, False
            cand = dt(today, hour, p.minute)
            if cand <= now and not prefer_past:
                cand += timedelta(days=1)
            return TimeSpec(text="", start=cand, end=self._end(cand, p), has_time=True), False

        # есть день
        if hour is not None:
            start = dt(day, hour, p.minute)
            return TimeSpec(text="", start=start, end=self._end(start, p), has_time=True), True
        if p.deadline_day and mode == "point":
            return TimeSpec(text="", start=dt(day, 23, 59), has_time=False), True
        if mode == "period":
            if p.daypart:
                h1, h2 = DAYPART_WINDOW[p.daypart]
                start = dt(day, h1)
                return TimeSpec(
                    text="", start=start, end=start + timedelta(hours=h2 - h1), is_period=True
                ), True
            return TimeSpec(
                text="", start=dt(day), end=dt(day + timedelta(days=1)), is_period=True
            ), True
        return TimeSpec(text="", start=dt(day), has_time=False), True

    @staticmethod
    def _end(start: datetime, p: _Parts) -> datetime | None:
        if p.explicit_time_end is None:
            return None
        end = start.replace(hour=p.explicit_time_end.hour, minute=p.explicit_time_end.minute)
        if end <= start:
            end += timedelta(days=1)
        return end

    @staticmethod
    def _weekday_date(
        wd: int, mod: str | None, now: datetime, hour: int | None, minute: int, prefer_past: bool
    ) -> date:
        today = now.date()
        if mod == "next":
            return _monday(today) + timedelta(weeks=1, days=wd)
        if mod == "prev":
            return _monday(today) - timedelta(weeks=1) + timedelta(days=wd)
        if mod == "prev2":  # «в позапрошлую пятницу»
            return _monday(today) - timedelta(weeks=2) + timedelta(days=wd)
        diff = (wd - today.weekday()) % 7
        if prefer_past:
            back = (today.weekday() - wd) % 7
            if back == 0 and hour is not None and (hour, minute) > (now.hour, now.minute):
                back = 7
            return today - timedelta(days=back)
        if diff == 0 and hour is not None and (hour, minute) <= (now.hour, now.minute):
            diff = 7
        return today + timedelta(days=diff)

    @staticmethod
    def _orig_spans(nt: NormText, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out = sorted(nt.orig_span(a, b) for a, b in spans)
        merged: list[tuple[int, int]] = []
        for a, b in out:
            if a == b:
                continue
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        return merged


def _parse_iso(text: str, now: datetime) -> TimeSpec | None:
    """«2026-10-22T08:00», «2026-10-22 08:00», «2026-10-22» → TimeSpec."""
    t = text.strip()
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?", t
    ):
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    has_time = len(t) > 10
    d = d.replace(tzinfo=now.tzinfo) if d.tzinfo is None else d.astimezone(now.tzinfo)
    return TimeSpec(text=t, start=d, has_time=has_time, has_date=True)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _add_month(d: date) -> date:
    return date(d.year + (d.month // 12), d.month % 12 + 1, 1)


def _add_months_keep_day(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Вырезает найденные фрагменты из текста, схлопывая пробелы."""
    out, pos = [], 0
    for a, b in sorted(spans):
        out.append(text[pos:a])
        pos = max(pos, b)
    out.append(text[pos:])
    return re.sub(r"\s{2,}", " ", "".join(out)).strip(" ,")


def has_explicit_date(text: str, now: datetime) -> bool:
    """Есть ли во фразе дата (а не только время). Нужно диалогу при переносе «на 19:00»."""
    return RuleDateResolver().extract(text, now).has_date

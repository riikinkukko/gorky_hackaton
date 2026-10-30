"""Русский язык для ответов: даты, числительные, падежи, грубый стемминг.

Без внешних зависимостей: ответы должны строиться даже без pymorphy3.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.contracts import ItemType

WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# «в понедельник», «в среду»: винительный падеж
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
# «каждый понедельник», «каждую среду», «каждое воскресенье»
EVERY_WEEKDAY = [
    "каждый понедельник",
    "каждый вторник",
    "каждую среду",
    "каждый четверг",
    "каждую пятницу",
    "каждую субботу",
    "каждое воскресенье",
]
MONTHS_GEN = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
MONTHS_PREP = [
    "январе",
    "феврале",
    "марте",
    "апреле",
    "мае",
    "июне",
    "июле",
    "августе",
    "сентябре",
    "октябре",
    "ноябре",
    "декабре",
]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ("запись", "записи", "записей")) -> "записей"."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def count(n: int, forms: tuple[str, str, str]) -> str:
    return f"{n} {plural(n, forms)}"


RECORD = ("запись", "записи", "записей")

TYPE_WORDS: dict[ItemType, dict[str, object]] = {
    ItemType.MEETING: {
        "nom": "встреча",
        "acc": "встречу",
        "gen_pl": "встреч",
        "forms": ("встреча", "встречи", "встреч"),
    },
    ItemType.TASK: {
        "nom": "задача",
        "acc": "задачу",
        "gen_pl": "задач",
        "forms": ("задача", "задачи", "задач"),
    },
    ItemType.REMINDER: {
        "nom": "напоминание",
        "acc": "напоминание",
        "gen_pl": "напоминаний",
        "forms": ("напоминание", "напоминания", "напоминаний"),
    },
}


def type_word(t: ItemType | None, case: str) -> str:
    if t is None:
        return {"nom": "запись", "acc": "запись", "gen_pl": "записей"}[case]
    return str(TYPE_WORDS[t][case])


_ACC_FORMS: dict[ItemType | None, tuple[str, str, str]] = {
    ItemType.MEETING: ("встречу", "встречи", "встреч"),
    ItemType.TASK: ("задачу", "задачи", "задач"),
    ItemType.REMINDER: ("напоминание", "напоминания", "напоминаний"),
    None: ("запись", "записи", "записей"),
}


def type_forms_acc(t: ItemType | None) -> tuple[str, str, str]:
    """Для «удалить 1 задачу / 3 задачи / 5 задач» (винительный)."""
    return _ACC_FORMS[t]


def type_plural(t: ItemType | None) -> str:
    """«все встречи», «все задачи», «все записи»."""
    return _ACC_FORMS[t][1]


def type_forms(t: ItemType | None) -> tuple[str, str, str]:
    if t is None:
        return RECORD
    forms = TYPE_WORDS[t]["forms"]
    assert isinstance(forms, tuple)
    return forms


# ---------- даты ----------


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def fmt_date(d: date, now: datetime, weekday: bool = True) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if d.year != now.year:
        s += f" {d.year}"
    if weekday:
        s = f"{WEEKDAYS_SHORT[d.weekday()]}, {s}"
    return s


def fmt_dt(dt: datetime, has_time: bool, now: datetime) -> str:
    """«чт, 22 октября, 10:00»; без времени — «пт, 23 октября»; другой год — с годом."""
    dt = dt.astimezone(now.tzinfo)
    s = fmt_date(dt.date(), now)
    if has_time:
        s += f", {fmt_time(dt)}"
    return s


def day_phrase(d: date, now: datetime) -> str:
    """Как назвать день в ответе: «сегодня», «завтра», «в субботу», «5 ноября»."""
    today = now.date()
    if d == today:
        return "сегодня"
    if d == today - timedelta(days=1):
        return "вчера"
    if d == today - timedelta(days=2):
        return "позавчера"
    if d == today + timedelta(days=1):
        return "завтра"
    if today < d <= today + timedelta(days=6):
        prep = "во" if d.weekday() == 1 else "в"
        return f"{prep} {WEEKDAYS_ACC[d.weekday()]}"
    return fmt_date(d, now, weekday=False)


def period_phrase(start: datetime, end: datetime, now: datetime) -> str:
    """Название периода: «сегодня», «на этой неделе», «в ноябре», «с 3 по 9 ноября»."""
    s, e = start.astimezone(now.tzinfo), end.astimezone(now.tzinfo)
    last = (e - timedelta(microseconds=1)).date() if e.time() == datetime.min.time() else e.date()
    first = s.date()
    if first == last:
        return day_phrase(first, now)
    week_start = now.date() - timedelta(days=now.weekday())
    if first == week_start and last == week_start + timedelta(days=6):
        return "на этой неделе"
    if first == week_start + timedelta(days=7) and last == week_start + timedelta(days=13):
        return "на следующей неделе"
    if first.day == 1 and first.month == last.month and (last + timedelta(days=1)).day == 1:
        s_ = f"в {MONTHS_PREP[first.month - 1]}"
        return s_ + (f" {first.year}" if first.year != now.year else "")
    if first.month == last.month:
        return f"с {first.day} по {fmt_date(last, now, weekday=False)}"
    return f"с {fmt_date(first, now, weekday=False)} по {fmt_date(last, now, weekday=False)}"


def capitalize(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# ---------- падежи имён ----------

_HUSHING = set("жшчщц")


_IRREGULAR_INSTR = {
    "пётр": "Петром",
    "петр": "Петром",
    "павел": "Павлом",
    "лев": "Львом",
    "любовь": "Любовью",
}


def name_instr(name: str) -> str:
    """Творительный падеж для имён и названий: «с Иваном», «с Марией», «с Альфой»."""
    w = name.strip()
    if not w:
        return w
    if w.lower() in _IRREGULAR_INSTR:
        return _IRREGULAR_INSTR[w.lower()]
    low = w.lower()
    if low.endswith("ия"):
        return w[:-1] + "ей"
    if low.endswith("ья"):
        return w[:-1] + "ей"
    if low.endswith("я"):
        return w[:-1] + "ей"
    if low.endswith("а"):
        return w[:-1] + ("ей" if len(low) > 1 and low[-2] in _HUSHING else "ой")
    if low.endswith("й"):
        return w[:-1] + "ем"
    if low.endswith("ь"):
        return w[:-1] + "ем"
    if low[-1] in "оеиуюэы":  # несклоняемые
        return w
    return w + ("ем" if low[-1] in _HUSHING else "ом")


def join_names(names: list[str], case: str = "instr") -> str:
    forms = [name_instr(n) if case == "instr" else n for n in names]
    if len(forms) <= 1:
        return "".join(forms)
    return ", ".join(forms[:-1]) + " и " + forms[-1]


def with_prep(name_instr_form: str) -> str:
    """«с Иваном», но «со Стартом», «со Светланой»."""
    low = name_instr_form.lower()
    if len(low) > 1 and low[0] in "сзшж" and low[1] not in "аеёиоуыэюя":
        return f"со {name_instr_form}"
    return f"с {name_instr_form}"


# ---------- грубый стемминг для сопоставления ----------

_ENDINGS = sorted(
    [
        "иями",
        "ями",
        "ами",
        "ией",
        "ией",
        "ого",
        "его",
        "ому",
        "ему",
        "ыми",
        "ими",
        "ов",
        "ев",
        "ой",
        "ей",
        "ом",
        "ем",
        "ам",
        "ям",
        "ах",
        "ях",
        "ую",
        "юю",
        "ая",
        "яя",
        "ое",
        "ее",
        "ые",
        "ие",
        "ый",
        "ий",
        "ия",
        "ья",
        "ью",
        "а",
        "я",
        "у",
        "ю",
        "е",
        "ы",
        "и",
        "о",
        "й",
        "ь",
    ],
    key=len,
    reverse=True,
)

_TRAILING = set("аеиоуыэюяйь")
_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def stem(word: str) -> str:
    w = norm(word)
    for end in _ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 3:
            w = w[: -len(end)]
            break
    while len(w) > 3 and w[-1] in _TRAILING:
        w = w[:-1]
    return w


def stems(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text)]

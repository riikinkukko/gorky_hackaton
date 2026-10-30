"""Нормализация текста.

1. ``normalize_for_tts`` — Silero не читает цифры, латиницу и сокращения: «чт, 22 октября, 10:00»
   превращаем в «четверг, двадцать второе октября, десять часов».
2. ``normalize_for_wer`` — приводим эталон и гипотезу к одному виду перед подсчётом WER:
   GigaAM-e2e пишет «в 15:30», человек говорит «в пятнадцать тридцать».
"""

from __future__ import annotations

import re

_UNITS_M = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = [
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
_TENS = [
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
]
_HUNDREDS = [
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
]

# порядковые, средний род, им. п. («двадцать второе октября») и род. п. («двадцать второго октября»)
_ORD_NOM = {
    1: "первое",
    2: "второе",
    3: "третье",
    4: "четвёртое",
    5: "пятое",
    6: "шестое",
    7: "седьмое",
    8: "восьмое",
    9: "девятое",
    10: "десятое",
    11: "одиннадцатое",
    12: "двенадцатое",
    13: "тринадцатое",
    14: "четырнадцатое",
    15: "пятнадцатое",
    16: "шестнадцатое",
    17: "семнадцатое",
    18: "восемнадцатое",
    19: "девятнадцатое",
    20: "двадцатое",
    30: "тридцатое",
}
_ORD_GEN = {
    k: (v[:-2] + "ого") if not v.endswith("тье") else v[:-2] + "ьего" for k, v in _ORD_NOM.items()
}
_ORD_GEN[3] = "третьего"

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
_MONTH_RE = "|".join(MONTHS_GEN)

_WEEKDAYS = {
    "пн": "понедельник",
    "вт": "вторник",
    "ср": "среда",
    "чт": "четверг",
    "пт": "пятница",
    "сб": "суббота",
    "вс": "воскресенье",
}
_WEEKDAYS_ACC = {"среда": "среду", "пятница": "пятницу", "суббота": "субботу"}
_ORD_GEN.update(
    {
        40: "сорокового",
        50: "пятидесятого",
        60: "шестидесятого",
        70: "семидесятого",
        80: "восьмидесятого",
        90: "девяностого",
    }
)

# как произносить сокращения и латиницу, которые встречаются в ответах
_SPELL = {
    "CRM": "си-ар-эм",
    "ТЗ": "тэ-зэ",
    "ICS": "ай-си-эс",
    "SMS": "эс-эм-эс",
    "PDF": "пэ-дэ-эф",
    "API": "эй-пи-ай",
    "ID": "ай-ди",
    "IT": "ай-ти",
    "HR": "эйч-ар",
    "КП": "ка-пэ",
    "ИП": "и-пэ",
    "ООО": "о-о-о",
    "МФЦ": "эм-эф-цэ",
    "UX": "ю-экс",
    "MVP": "эм-ви-пи",
    "OK": "окей",
    "ok": "окей",
}
_LAT_LETTERS = {
    "A": "эй",
    "B": "би",
    "C": "си",
    "D": "ди",
    "E": "и",
    "F": "эф",
    "G": "джи",
    "H": "эйч",
    "I": "ай",
    "J": "джей",
    "K": "кей",
    "L": "эл",
    "M": "эм",
    "N": "эн",
    "O": "о",
    "P": "пи",
    "Q": "кью",
    "R": "ар",
    "S": "эс",
    "T": "ти",
    "U": "ю",
    "V": "ви",
    "W": "дабл-ю",
    "X": "экс",
    "Y": "уай",
    "Z": "зед",
}
_CYR_LETTERS = {
    "А": "а",
    "Б": "бэ",
    "В": "вэ",
    "Г": "гэ",
    "Д": "дэ",
    "Е": "е",
    "Ж": "жэ",
    "З": "зэ",
    "И": "и",
    "К": "ка",
    "Л": "эль",
    "М": "эм",
    "Н": "эн",
    "О": "о",
    "П": "пэ",
    "Р": "эр",
    "С": "эс",
    "Т": "тэ",
    "У": "у",
    "Ф": "эф",
    "Х": "ха",
    "Ц": "цэ",
    "Ч": "чэ",
    "Ш": "ша",
    "Э": "э",
    "Ю": "ю",
    "Я": "я",
}
_FEMININE_NEXT = re.compile(r"^(минут|недел|задач|встреч|запис|секунд|тысяч|штук)", re.I)


def number_to_words(n: int, feminine: bool = False) -> str:
    """Целое 0..999 999 999 → слова (именительный падеж)."""
    if n < 0:
        return "минус " + number_to_words(-n, feminine)
    if n == 0:
        return "ноль"
    parts: list[str] = []
    for value, forms, fem in (
        (1_000_000, ("миллион", "миллиона", "миллионов"), False),
        (1_000, ("тысяча", "тысячи", "тысяч"), True),
    ):
        if n >= value:
            q, n = divmod(n, value)
            parts.append(_below_thousand(q, fem))
            parts.append(plural(q, *forms))
    if n:
        parts.append(_below_thousand(n, feminine))
    return " ".join(p for p in parts if p)


def _below_thousand(n: int, feminine: bool) -> str:
    units = _UNITS_F if feminine else _UNITS_M
    words = []
    h, rest = divmod(n, 100)
    if h:
        words.append(_HUNDREDS[h])
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        t, u = divmod(rest, 10)
        if t:
            words.append(_TENS[t])
        if u:
            words.append(units[u])
    return " ".join(words)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def ordinal_day(n: int, genitive: bool = False) -> str:
    table = _ORD_GEN if genitive else _ORD_NOM
    if n in table:
        return table[n]
    tens, unit = divmod(n, 10)
    return f"{_TENS[tens]} {table[unit]}"


def year_genitive(year: int) -> str:
    """2026 → «две тысячи двадцать шестого» (для «… года»)."""
    head, tail = divmod(year, 100)
    if tail == 0:
        special = {1000: "тысячного", 2000: "двухтысячного", 3000: "трёхтысячного"}
        return special.get(year, number_to_words(year))
    tens, unit = divmod(tail, 10)
    if tail < 20 or unit == 0:
        last = ordinal_day(tail, genitive=True)
    else:
        last = f"{_TENS[tens]} {_ORD_GEN[unit]}"
    prefix = number_to_words(head * 100) if head else ""
    return f"{prefix} {last}".strip()


def time_to_words(h: int, m: int) -> str:
    hour = number_to_words(h)
    if m == 0:
        return f"{hour} {plural(h, 'час', 'часа', 'часов')}" if h else "полночь"
    minute = number_to_words(m, feminine=True)
    return f"{hour} ноль {minute}" if m < 10 else f"{hour} {minute}"


def _spell_abbr(token: str) -> str:
    if token in _SPELL:
        return _SPELL[token]
    if token.isascii():
        return "-".join(_LAT_LETTERS.get(ch.upper(), ch) for ch in token)
    return "-".join(_CYR_LETTERS.get(ch, ch.lower()) for ch in token)


def normalize_for_tts(text: str) -> str:
    t = re.sub(r"\s*[—–]\s*", ", ", text).replace("«", "").replace("»", "")

    def _wd(m: re.Match) -> str:
        prep, day = m.group(1) or "", _WEEKDAYS[m.group(2).lower()]
        return prep + (
            _WEEKDAYS_ACC.get(day, day) if prep.strip().lower() in {"на", "в", "во"} else day
        )

    t = re.sub(r"(\b(?:на|в|во)\s+)?(?<!\w)(пн|вт|ср|чт|пт|сб|вс)\.?(?=,|\s|$)", _wd, t, flags=re.I)

    # даты: «22 октября» → «двадцать второе октября»; после предлога «до/с/от/после» — род. п.
    def _date(m: re.Match) -> str:
        prep, day, month = m.group(1) or "", int(m.group(2)), m.group(3)
        gen = prep.strip().lower() in {"до", "с", "со", "от", "после", "около", "к"}
        return f"{prep}{ordinal_day(day, genitive=gen)} {month}"

    t = re.sub(
        rf"(\b(?:до|с|со|от|после|около|к|на|в)\s+)?\b(\d{{1,2}})\s+({_MONTH_RE})",
        _date,
        t,
        flags=re.I,
    )
    # время 10:00, 9:05
    t = re.sub(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        lambda m: time_to_words(int(m.group(1)), int(m.group(2))),
        t,
    )
    t = re.sub(r"\b(\d{4})\s*(?:г\.|года)", lambda m: year_genitive(int(m.group(1))) + " года", t)
    t = t.replace("%", " процентов").replace("№", "номер ")

    # оставшиеся числа: согласуем род со следующим словом («2 встречи» → «две встречи»)
    def _num(m: re.Match) -> str:
        nxt = t[m.end() : m.end() + 12].lstrip()
        return number_to_words(int(m.group(0)), feminine=bool(_FEMININE_NEXT.match(nxt)))

    t = re.sub(r"\b\d{1,9}\b", _num, t)
    # аббревиатуры (2–4 заглавные) и латиница
    t = re.sub(r"\b[A-ZА-ЯЁ]{2,5}\b", lambda m: _spell_abbr(m.group(0)), t)
    t = re.sub(
        r"\b[A-Za-z]+\b", lambda m: _SPELL.get(m.group(0), _spell_abbr(m.group(0).upper())), t
    )
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"\s+([,.!?])", r"\1", t).strip()


def split_for_tts(text: str, limit: int = 700) -> list[str]:
    """Silero плохо переносит очень длинные строки — режем по предложениям."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= limit:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                chunks.append(cur)
            while len(s) > limit:
                cut = s.rfind(",", 0, limit)
                cut = cut if cut > 0 else limit
                chunks.append(s[: cut + 1].strip())
                s = s[cut + 1 :]
            cur = s.strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


# ---------------------------------------------------------------- WER

_CANON = [
    (r"\bси[\s-]*ар[\s-]*эм\b|\bсиарэм\b|\bсрм\b|\bцрм\b", "crm"),
    (r"\bтэ[\s-]*зэ\b|\bтеза\b", "тз"),
    (r"\bпланерк", "планерк"),
]


def normalize_for_wer(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(
        rf"\b(\d{{1,2}})\s+({_MONTH_RE})",
        lambda m: f"{ordinal_day(int(m.group(1)), True)} {m.group(2)}",
        t,
    )
    t = re.sub(
        r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",
        lambda m: (
            f"{number_to_words(int(m.group(1)))} "
            + (
                "ноль ноль"
                if m.group(2) == "00"
                else time_to_words(0, int(m.group(2))).split(" ", 1)[1]
                if int(m.group(2)) < 10
                else number_to_words(int(m.group(2)), True)
            )
        ),
        t,
    )

    def _num(m: re.Match) -> str:
        nxt = t[m.end() : m.end() + 12].lstrip()
        return number_to_words(int(m.group(0)), feminine=bool(_FEMININE_NEXT.match(nxt)))

    t = re.sub(r"\b\d{1,9}\b", _num, t)
    t = t.replace("ё", "е")
    for pattern, repl in _CANON:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

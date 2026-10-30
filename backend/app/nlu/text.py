"""Нормализация текста для правил: токены с исходными позициями и числительные словами → цифры.

Правила (даты, роутер) работают по нормализованной строке, а вырезать найденное
нужно из исходного текста. Поэтому каждый нормализованный токен помнит свой
диапазон в оригинале, а `NormText.orig_span()` переводит позиции обратно.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------- числительные ----------

_UNITS = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одну": 1,
    "одного": 1,
    "одной": 1,
    "одному": 1,
    "два": 2,
    "две": 2,
    "двух": 2,
    "двум": 2,
    "три": 3,
    "трех": 3,
    "трем": 3,
    "четыре": 4,
    "четырех": 4,
    "четырем": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
    "семь": 7,
    "семи": 7,
    "восемь": 8,
    "восьми": 8,
    "девять": 9,
    "девяти": 9,
}
_TEENS = {
    "десять": 10,
    "десяти": 10,
    "одиннадцать": 11,
    "одиннадцати": 11,
    "двенадцать": 12,
    "двенадцати": 12,
    "тринадцать": 13,
    "тринадцати": 13,
    "четырнадцать": 14,
    "четырнадцати": 14,
    "пятнадцать": 15,
    "пятнадцати": 15,
    "шестнадцать": 16,
    "шестнадцати": 16,
    "семнадцать": 17,
    "семнадцати": 17,
    "восемнадцать": 18,
    "восемнадцати": 18,
    "девятнадцать": 19,
    "девятнадцати": 19,
}
_TENS = {
    "двадцать": 20,
    "двадцати": 20,
    "тридцать": 30,
    "тридцати": 30,
    "сорок": 40,
    "сорока": 40,
    "пятьдесят": 50,
    "пятидесяти": 50,
}

_ORD_STEMS = {
    "перв": 1,
    "втор": 2,
    "трет": 3,
    "четверт": 4,
    "пят": 5,
    "шест": 6,
    "седьм": 7,
    "восьм": 8,
    "девят": 9,
    "десят": 10,
    "одиннадцат": 11,
    "двенадцат": 12,
    "тринадцат": 13,
    "четырнадцат": 14,
    "пятнадцат": 15,
    "шестнадцат": 16,
    "семнадцат": 17,
    "восемнадцат": 18,
    "девятнадцат": 19,
    "двадцат": 20,
    "тридцат": 30,
}
_ORD_RE = re.compile(
    r"^(" + "|".join(sorted(_ORD_STEMS, key=len, reverse=True)) + r")"
    r"(?:ье|ья|ьего|ьему|ий|ого|ое|ому|ый|ая|ой|ую|ым|ом|ые|ых|ь?им|ьем)$"
)


def _ordinal(word: str) -> int | None:
    m = _ORD_RE.match(word)
    if not m:
        return None
    # «пятница», «десятки» и пр. сюда не попадут: окончания строго порядковые
    return _ORD_STEMS[m.group(1)]


def word_number(word: str) -> tuple[int, str] | None:
    """Одно слово → (значение, вид): вид 'units'|'teens'|'tens'|'ord'."""
    if word in _UNITS:
        return _UNITS[word], "units"
    if word in _TEENS:
        return _TEENS[word], "teens"
    if word in _TENS:
        return _TENS[word], "tens"
    o = _ordinal(word)
    if o is not None:
        return o, "ord"
    return None


# ---------- опечатки STT в словах дат ----------

_DATE_VOCAB = [
    "сегодня",
    "завтра",
    "послезавтра",
    "вчера",
    "позавчера",
    "понедельник",
    "вторник",
    "среду",
    "четверг",
    "пятницу",
    "субботу",
    "воскресенье",
    "понедельника",
    "вторника",
    "среды",
    "четверга",
    "пятницы",
    "субботы",
    "воскресенья",
    "января",
    "февраля",
    "марта",
    "апреля",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
    "январе",
    "феврале",
    "марте",
    "апреле",
    "июне",
    "июле",
    "августе",
    "сентябре",
    "октябре",
    "ноябре",
    "декабре",
    "неделе",
    "неделю",
    "выходных",
    "утром",
    "вечером",
    "половине",
    "полдень",
    "полночь",
]
_morph = None


def _known(word: str) -> bool:
    global _morph
    if _morph is None:
        import pymorphy3

        _morph = pymorphy3.MorphAnalyzer()
    return bool(_morph.word_is_known(word))


def _lev(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fix_date_typo(word: str) -> str:
    """«севодня» → «сегодня», «завтро» → «завтра». Только для слов, которых нет в словаре."""
    if len(word) < 5 or not word.isalpha() or word in _DATE_VOCAB or _known(word):
        return word
    limit = 1 if len(word) < 8 else 2
    best, best_d = word, limit + 1
    for v in _DATE_VOCAB:
        d = _lev(word, v, limit)
        if d < best_d:
            best, best_d = v, d
    return best if best_d <= limit else word


_COLLOQUIAL = {
    "полчасика": "30 минут", "полчасок": "30 минут", "часик": "час", "часика": "часа",
    "часиков": "часов", "минутку": "минуту", "минуток": "минут", "выхи": "выходные",
    "выхах": "выходных", "утречка": "утра", "утречком": "утром", "вечерочком": "вечером",
}  # fmt: skip

_MONTH_GEN = {
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
    "октября", "ноября", "декабря",
}  # fmt: skip

# ---------- токенизация ----------

_TOKEN_RE = re.compile(
    r"\d{1,2}[:.]\d{2}(?:[.]\d{2,4})?"  # 15:00, 10.30, 29.10, 29.10.2026
    r"|\d+-(?:го|е|ое|ого|му|м)\b"
    r"|\d+"
    r"|[а-яёa-z]+(?:-[а-яёa-z]+)*"  # слова, «когда-то», «по-моему»
    r"|[?!.,;:]",
    re.IGNORECASE,
)


@dataclass
class Token:
    norm: str
    orig_start: int
    orig_end: int


@dataclass
class NormText:
    original: str
    tokens: list[Token] = field(default_factory=list)
    text: str = ""
    _starts: list[int] = field(default_factory=list)  # позиция токена в text

    def orig_span(self, start: int, end: int) -> tuple[int, int]:
        """Диапазон в нормализованной строке → диапазон в оригинале."""
        first = last = None
        for i, s in enumerate(self._starts):
            e = s + len(self.tokens[i].norm)
            if e > start and s < end:
                if first is None:
                    first = i
                last = i
        if first is None or last is None:
            return (0, 0)
        return self.tokens[first].orig_start, self.tokens[last].orig_end

    def orig(self, start: int, end: int) -> str:
        a, b = self.orig_span(start, end)
        return self.original[a:b]


def simple_norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def normalize(text: str) -> NormText:
    raw = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
    toks: list[Token] = []
    i = 0
    while i < len(raw):
        w, s, e = raw[i]
        lw = simple_norm(w)
        # «полчетвертого», «полдевятого» → «половине 4», «половине 9»
        mpol = re.fullmatch(r"пол([а-я]+(?:ого|его))", lw)
        if mpol and (o := _ordinal(mpol.group(1))) is not None:
            toks.append(Token("половине", s, e))
            toks.append(Token(str(o), e, e))
            i += 1
            continue
        # разговорное: «полчасика» → 30 минут, «часик» → час, «выхи» → выходные
        if lw in _COLLOQUIAL:
            for k, word in enumerate(_COLLOQUIAL[lw].split()):
                toks.append(Token(word, s if k == 0 else e, e))
            i += 1
            continue
        # «15-го», «29-е» → «15»
        mnum = re.fullmatch(r"(\d+)-(?:го|е|ое|ого|му|м)", lw)
        if mnum:
            toks.append(Token(mnum.group(1), s, e))
            after = simple_norm(raw[i + 1][0]) if i + 1 < len(raw) else ""
            if after != "числа" and after not in _MONTH_GEN:
                toks.append(Token("числа", e, e))  # «31-го» = «31 числа»
            i += 1
            continue
        if lw == "полчаса":
            toks.append(Token("30", s, e))
            toks.append(Token("минут", e, e))
            i += 1
            continue
        wn = word_number(lw)
        if wn is None:
            toks.append(Token(fix_date_typo(lw), s, e))
            i += 1
            continue
        val, kind = wn
        end = e
        j = i + 1
        prev_norm = toks[-1].norm if toks else ""
        if kind == "tens" and j < len(raw) and prev_norm != "без":  # «без двадцати семь» = 6:40
            nxt = word_number(simple_norm(raw[j][0]))
            if nxt and nxt[1] in ("units", "ord") and nxt[0] < 10:
                val += nxt[0]
                end = raw[j][2]
                j += 1
        # «две тысячи двадцать шестого» → год 2026
        if len(toks) >= 2 and toks[-1].norm in ("тысячи", "тысяча") and toks[-2].norm == "2":
            year_start = toks[-2].orig_start
            del toks[-2:]
            toks.append(Token(str(2000 + val), year_start, end))
            i = j
            continue
        toks.append(Token(str(val), s, end))
        # «двадцать седьмого», «на тридцатое» без месяца → день месяца («27 числа»);
        # не после «половине / четверть / без» — там порядковое означает час
        last_word = simple_norm(raw[j - 1][0])
        is_day_ord = (kind == "ord" or word_number(last_word) is not None) and bool(
            re.search(r"(?:ого|ое|ье|его|ее)$", last_word)
        )
        after = simple_norm(raw[j][0]) if j < len(raw) else ""
        if (
            is_day_ord
            and _ordinal(last_word) is not None
            and prev_norm not in ("половине", "пол", "четверть", "без")
            and after != "числа"
            and after not in _MONTH_GEN
        ):
            toks.append(Token("числа", end, end))
        i = j
    nt = NormText(original=text, tokens=toks)
    pos = 0
    parts = []
    for t in toks:
        nt._starts.append(pos)
        parts.append(t.norm)
        pos += len(t.norm) + 1
    nt.text = " ".join(parts)
    return nt

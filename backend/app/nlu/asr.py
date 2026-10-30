"""Нормализация транскрипта распознавания (GigaAM) до разбора.

Типичные ошибки STT и что с ними делаем:

| Ошибка STT | Пример | Исправление |
|---|---|---|
| время цифрами через пробел | «в 15 00», «в 3 30» | «в 15:00», «в 3:30» |
| время словами с нулями | «пятнадцать ноль ноль», «девять ноль пять» | «15:00», «9:05» |
| время через точку или дефис | «15.00», «15-00» | «15:00» |
| заминки | «э-э», «эм», «ну короче», «это самое» | удаляются |
| слово разорвано | «с и ваном», «в лектором» | «с Иваном», «Вектором» |
| сущность искажена | «с арбитой», «презинтацию» | «с Орбитой», «презентацию» |
| пропущен предлог | «созвон вектором» | «созвон с Вектором» |
| сущность строчными | «встреча с марией» | «встреча с Марией» |

Сопоставление идёт со словарём сущностей пользователя: имена, организации, темы.
Базовый словарь — из synonyms.yml (люди и организации из CSV организаторов, темы),
поверх него — сущности из записей пользователя (`EntitySource`, см. `RepoEntitySource`).

Сравнение — по фонетическому ключу для русского (оглушение, аканье, иканье) с порогом
Левенштейна: 0 для коротких слов, 1 для слов от 5 букв, 2 — от 9. Словарные слова
(«лектор», «вектор» как слово) не трогаем, кроме склейки разорванного слова.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .entities import EntityNormalizer, default_normalizer
from .text import _lev, simple_norm, word_number

log = logging.getLogger("app.nlu.asr")

# ---------- фонетический ключ ----------

_DEVOICE = str.maketrans({"б": "п", "в": "ф", "г": "к", "д": "т", "ж": "ш", "з": "с"})
_VOWELS = str.maketrans({"о": "а", "е": "и", "ё": "и", "э": "и", "я": "и", "ы": "и", "ю": "у"})


def phonetic_key(word: str) -> str:
    """Грубый фонетический ключ русского слова: «арбитой» и «орбитой» → «арпитай».

    Оглушение согласных, редукция гласных (о→а, е/э/я/ы→и, ю→у), без ь/ъ и удвоений.
    """
    w = simple_norm(word).replace("ь", "").replace("ъ", "").replace("й", "и")
    w = w.translate(_DEVOICE).translate(_VOWELS)
    out: list[str] = []
    for ch in w:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _limit(n: int) -> int:
    return 0 if n < 5 else 1 if n < 9 else 2


# ---------- словарь сущностей ----------


@dataclass
class KnownEntities:
    """Сущности пользователя в именительном падеже: «Иван», «Орбита», «презентация»."""

    persons: set[str] = field(default_factory=set)
    organizations: set[str] = field(default_factory=set)
    topics: set[str] = field(default_factory=set)

    def merged(self, other: KnownEntities) -> KnownEntities:
        return KnownEntities(
            self.persons | other.persons,
            self.organizations | other.organizations,
            self.topics | other.topics,
        )


class EntitySource(Protocol):
    async def known_entities(self, user_id: int) -> KnownEntities: ...


class RepoEntitySource:
    """Сущности из записей пользователя через репозиторий, с кэшем на `ttl` секунд.

    Если у репозитория есть `known_entities(user_id) -> dict[str, list[str]]` (предложение
    чату 3: один SELECT DISTINCT по person/organization/topic), используем его. Иначе —
    метод контракта `list_range` за широкий период (записи без даты туда не попадут).
    """

    _LO = datetime(2000, 1, 1, tzinfo=UTC)
    _HI = datetime(2100, 1, 1, tzinfo=UTC)

    def __init__(self, repo: Any, ttl: float = 300.0) -> None:
        self.repo = repo
        self.ttl = ttl
        self._cache: dict[int, tuple[float, KnownEntities]] = {}

    async def known_entities(self, user_id: int) -> KnownEntities:
        hit = self._cache.get(user_id)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        ents = await self._load(user_id)
        self._cache[user_id] = (time.monotonic(), ents)
        return ents

    def invalidate(self, user_id: int | None = None) -> None:
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    async def _load(self, user_id: int) -> KnownEntities:
        direct = getattr(self.repo, "known_entities", None)
        if callable(direct):
            d = await direct(user_id)
            return KnownEntities(
                set(d.get("person", [])), set(d.get("organization", [])), set(d.get("topic", []))
            )
        items = await self.repo.list_range(user_id, self._LO, self._HI)
        ents = KnownEntities()
        for it in items:
            ents.persons.update(p for p in it.person if p)
            ents.organizations.update(o for o in it.organization if o)
            if it.topic:
                ents.topics.add(it.topic)
        return ents


@dataclass(frozen=True)
class _Form:
    form: str  # словоформа в нижнем регистре: «орбитой»
    canon: str  # каноническое имя: «Орбита»
    kind: str  # person | organization | topic
    key: str  # фонетический ключ формы


class _Lexicon:
    """Все словоформы сущностей → (каноническая форма, вид), с индексом по ключу."""

    def __init__(self, ents: KnownEntities, ents_norm: EntityNormalizer) -> None:
        morph = ents_norm.morph
        self.by_form: dict[str, _Form] = {}
        self.by_key: dict[str, list[_Form]] = {}
        for kind, names in (
            ("person", ents.persons),
            ("organization", ents.organizations),
            ("topic", ents.topics),
        ):
            for name in names:
                words = name.split()
                if not words or len(words) > 2:
                    continue
                head = words[-1] if kind == "topic" else words[0]
                if len(head) < 3:
                    continue
                forms = {simple_norm(head)}
                parses = morph.parse(head.lower())
                p = next((x for x in parses if "Name" in x.tag), None) if kind == "person" else None
                p = p or next((x for x in parses if x.tag.POS == "NOUN"), None)
                if p is not None:
                    forms |= {simple_norm(f.word) for f in p.lexeme}
                for f in forms:
                    if len(words) == 2 and kind == "topic":
                        continue  # «мобильное приложение»: двухсловные темы — через synonyms.yml
                    form = _Form(f, name, kind, phonetic_key(f))
                    self.by_form.setdefault(f, form)
                    self.by_key.setdefault(form.key, []).append(form)

    def exact(self, word: str) -> _Form | None:
        return self.by_form.get(simple_norm(word))

    def fuzzy(self, word: str, limit: int | None = None) -> _Form | None:
        """Ближайшая форма по фонетическому ключу; None при неоднозначности."""
        w = simple_norm(word)
        if len(w) < 4:
            return None
        key = phonetic_key(w)
        lim = _limit(len(w)) if limit is None else min(limit, _limit(len(w)))
        best: list[tuple[int, _Form]] = []
        for k, forms in self.by_key.items():
            if abs(len(k) - len(key)) > lim:
                continue
            d = _lev(key, k, lim)
            if d <= lim:
                # ключ совпал — уточняем по буквам, чтобы выбрать падежную форму
                for f in forms:
                    best.append((d * 10 + min(_lev(w, f.form, 3), 3), f))
        if not best:
            return None
        best.sort(key=lambda x: x[0])
        top = [f for s, f in best if s == best[0][0]]
        canon = {(f.canon, f.form) for f in top}
        return top[0] if len(canon) == 1 else None


# ---------- время ----------

_TIME_PREP = r"(?:в|к|на|с|до|около|часам к|после)"
_HOUR_MIN_SPACE = re.compile(
    r"(?<![\w:.])(" + _TIME_PREP + r"\s+)(\d{1,2})\s+(\d{2})(?![\w:.])(?!\s*(?:"
    r"января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"числа|минут|мин|час|часа|часов|дня|дней|человек|раз|рубл))",
    re.IGNORECASE,
)
_CTX = (
    r"сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|"
    r"встреча|встречу|созвон|демо|планерка|планерку|планёрка|планёрку|совещание|звонок|"
    r"утра|дня|вечера|ночи|января|февраля|марта|апреля|мая|июня|июля|августа|сентября|"
    r"октября|ноября|декабря|числа"
)
# «встреча 15 00», «завтра 15 00», «… 15 00» в конце: голое «N 00» — время только рядом
# со словом даты или встречи («купить 2 00 пакета» не трогаем)
_BARE_00_AFTER = re.compile(r"(^|\b(?:" + _CTX + r")\s+)(\d{1,2})\s+00(?![\w:.])", re.IGNORECASE)
_BARE_00_BEFORE = re.compile(
    r"(?<![\w:.,])(\d{1,2})\s+00(?=\s+(?:" + _CTX + r")\b|\s*[,.!?]?\s*$)", re.IGNORECASE
)
_DOT_DASH = re.compile(r"(?<![\w.])([01]?\d|2[0-3])[.\-](00|15|30|45)(?![\w.])")
_HOURS_MIN_WORDS = re.compile(r"(?<!\w)(\d{1,2}):(\d{2}) (?:минут\w*)", re.IGNORECASE)

_NOL = {"ноль", "нуль"}


def _num(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    wn = word_number(simple_norm(word))
    return wn[0] if wn and wn[1] in ("units", "teens", "tens") else None


def _words_time(text: str) -> tuple[str, list[str]]:
    """«пятнадцать ноль ноль» → «15:00», «девять ноль пять» → «9:05»,
    «в девять сорок пять» → «в 9:45».

    Склеиваем только когда второе число — минуты: «ноль ноль», «ноль N», 10–59 после часа
    с предлогом времени. «пять человек», «в два раза» не трогаем.
    """
    tokens = re.findall(r"\S+|\s+", text)
    words = [(i, t) for i, t in enumerate(tokens) if not t.isspace()]
    fixes: list[str] = []
    out = list(tokens)
    j = 0
    while j < len(words):
        i0, w0 = words[j]
        h = _num(w0)
        if h is None or not 0 <= h <= 23:
            j += 1
            continue
        prev = simple_norm(words[j - 1][1]) if j > 0 else ""
        hw = 1  # слов в часе: «двадцать один» — два
        if (
            h == 20
            and j + 1 < len(words)
            and (u := _num(words[j + 1][1])) is not None
            and 1 <= u <= 3
        ):
            h, hw = 20 + u, 2
        j += hw - 1
        nxt = [simple_norm(t) for _, t in words[j + 1 : j + 4]]
        minutes: int | None = None
        used = 0
        if len(nxt) >= 2 and nxt[0] in _NOL and nxt[1] in _NOL:
            minutes, used = 0, 2
        elif len(nxt) >= 2 and nxt[0] in _NOL and (m := _num(nxt[1])) is not None and m < 10:
            minutes, used = m, 2
        elif nxt and prev in ("в", "к", "на", "с", "до", "около"):
            # «в девять тридцать», «в девять сорок пять»; tens+units уже склеены word_number
            m1 = _num(nxt[0])
            if m1 is not None and 10 <= m1 <= 59 and not w0.isdigit():
                minutes, used = m1, 1
                if m1 % 10 == 0 and m1 >= 20 and len(nxt) >= 2:
                    m2 = _num(nxt[1])
                    if m2 is not None and 1 <= m2 <= 9:
                        minutes, used = m1 + m2, 2
        if minutes is None:
            j += 1
            continue
        last_idx = words[j + used][0]
        src = "".join(tokens[i0 : last_idx + 1])
        rep = f"{h}:{minutes:02d}"
        out[i0] = rep
        for k in range(i0 + 1, last_idx + 1):
            out[k] = ""
        fixes.append(f"{src} → {rep}")
        j += used + 1
    return "".join(out), fixes


def normalize_time(text: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    t, f = _words_time(text)
    fixes += f

    def sub(rx: re.Pattern[str], repl: Any, s: str) -> str:
        def _r(m: re.Match[str]) -> str:
            new = repl(m)
            if new != m.group(0):
                fixes.append(f"{m.group(0)} → {new}")
            return str(new)

        return rx.sub(_r, s)

    t = sub(
        _HOUR_MIN_SPACE,
        lambda m: (
            f"{m.group(1)}{m.group(2)}:{m.group(3)}"
            if int(m.group(2)) <= 23 and int(m.group(3)) <= 59
            else m.group(0)
        ),
        t,
    )
    t = sub(
        _BARE_00_AFTER,
        lambda m: f"{m.group(1)}{m.group(2)}:00" if int(m.group(2)) <= 23 else m.group(0),
        t,
    )
    t = sub(
        _BARE_00_BEFORE, lambda m: f"{m.group(1)}:00" if int(m.group(1)) <= 23 else m.group(0), t
    )
    t = sub(_DOT_DASH, lambda m: f"{m.group(1)}:{m.group(2)}", t)
    t = sub(_HOURS_MIN_WORDS, lambda m: f"{m.group(1)}:{m.group(2)}", t)
    return t, fixes


# ---------- аббревиатуры, произнесённые по буквам (QA-04) ----------

_ACRONYMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE), canon)
    for p, canon in (
        (
            r"(?<![\w-])(?:си[\s-]?ар[\s-]?эм|с[\s-]?р[\s-]?м|ц[\s-]?р[\s-]?м|эс[\s-]?эр[\s-]?эм)(?![\w-])",
            "CRM",
        ),
        (r"(?<![\w-])(?:тэ[\s-]?зэ|т[\s-]з|тз)(?![\w-])", "ТЗ"),
        (r"(?<![\w-])(?:ка[\s-]?пэ|кп|кэпэшк\w*|капэшк\w*)(?![\w-])", "КП"),
        (r"(?<![\w-])(?:эс[\s-]?эм[\s-]?эс|смс)(?![\w-])", "SMS"),
        (r"(?<![\w-])(?:пэ[\s-]?дэ[\s-]?эф|пи[\s-]?ди[\s-]?эф|пдф)(?![\w-])", "PDF"),
        (r"(?<![\w-])(?:эйч[\s-]?ар)(?![\w-])", "HR"),
        (r"(?<![\w-])(?:эй[\s-]?пи[\s-]?ай)(?![\w-])", "API"),
    )
]


def normalize_acronyms(text: str) -> tuple[str, list[str]]:
    """«по срм» → «по CRM», «тэзэ», «т з» → «ТЗ»: в названии записи остаётся канон."""
    fixes: list[str] = []
    for rx, canon in _ACRONYMS:

        def _sub(m: re.Match[str], canon: str = canon) -> str:
            if m.group(0) != canon:
                fixes.append(f"{m.group(0)} → {canon}")
            return canon

        text = rx.sub(_sub, text)
    return text, fixes


# ---------- заминки ----------

_HESITATION = re.compile(
    r"(?<![\w-])(?:э+(?:-э+)*|эм+|мм+|хм+|а-а+|ну короче|короче говоря|это самое|так сказать|"
    r"как бы|в общем)(?![\w-])[,.…]*",
    re.IGNORECASE,
)


# названия букв: «си ар эм» (CRM), «тэ зэ» (ТЗ) — «эм» после них не заминка
_LETTER_NAMES = {
    "а", "бэ", "вэ", "гэ", "дэ", "е", "зэ", "и", "ка", "эл", "эм", "эн", "о", "пэ", "эр", "эс",
    "тэ", "у", "эф", "ха", "цэ", "че", "ша", "си", "ар", "ай", "би", "ди", "джи", "ви",
}  # fmt: skip


def strip_hesitations(text: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def drop(m: re.Match[str]) -> str:
        before = text[: m.start()].split()
        if m.group(0).lower().startswith("эм") and before and before[-1].lower() in _LETTER_NAMES:
            return m.group(0)  # часть аббревиатуры
        found.append(m.group(0).strip(" ,.…"))
        return " "

    t = _HESITATION.sub(drop, text)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"^[\s,.…]+", "", re.sub(r"\s{2,}", " ", t)).strip()
    if not t:
        return text, []
    return t, [f"заминка «{x}»" for x in found if x]


# ---------- сущности ----------

_WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
_PREP_WITH = {"с", "со"}
_MEETING_HEADS = {
    # именительный/винительный: «созвон вектором» → «созвон с Вектором». Родительный
    # («итоги встречи иваном») не берём: там пропущен не «с», а дательный падеж.
    "встреча", "встречу", "созвон", "созвоны", "демо", "звонок", "планерка", "планерку",
    "совещание", "обсудить", "обсуждение", "переговоры", "встретиться", "созвониться",
    "встречаться", "договориться",
}  # fmt: skip
_SHORT_SPLIT = {"в", "и", "с", "о", "у", "а", "к", "на", "по", "за", "не", "до", "от"}


@dataclass
class AsrResult:
    original: str
    text: str
    fixes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def as_debug(self) -> dict[str, Any]:
        return {"asr_original": self.original, "asr_text": self.text, "asr_fixes": self.fixes}


class AsrNormalizer:
    """Нормализует транскрипт: заминки, время, искажённые сущности. Быстро: ~0,2 мс на фразу."""

    def __init__(self, normalizer: EntityNormalizer | None = None) -> None:
        self.ents = normalizer or default_normalizer()
        self.base = KnownEntities(
            persons=set(self.ents.known_people.values()),
            organizations=set(self.ents.organizations.values()),
            topics={c for _, c in self.ents.phrases if " " not in c and len(c) >= 4},
        )
        self._lex_cache: dict[frozenset[str], _Lexicon] = {}
        self._base_lex = self._lexicon(self.base)

    def _lexicon(self, ents: KnownEntities) -> _Lexicon:
        key = frozenset(
            [f"p:{x}" for x in ents.persons]
            + [f"o:{x}" for x in ents.organizations]
            + [f"t:{x}" for x in ents.topics]
        )
        lex = self._lex_cache.get(key)
        if lex is None:
            if len(self._lex_cache) > 64:
                self._lex_cache.clear()
            lex = self._lex_cache[key] = _Lexicon(ents, self.ents)
        return lex

    def normalize(self, text: str, user_entities: KnownEntities | None = None) -> AsrResult:
        lex = self._lexicon(self.base.merged(user_entities)) if user_entities else self._base_lex
        fixes: list[str] = []
        t, f = normalize_acronyms(text)  # до заминок: «эм» в «си ар эм» — буква
        fixes += f
        t, f = strip_hesitations(t)
        fixes += f
        t, f = normalize_time(t)
        fixes += f
        t, f = self._entities(t, lex)
        fixes += f
        return AsrResult(original=text, text=t, fixes=fixes)

    # ----- сущности -----

    def _known_word(self, w: str) -> bool:
        return bool(self.ents.morph.word_is_known(w.lower()))

    def _styled(self, form: _Form, like: str) -> str:
        """Форма сущности с заглавной для имён и организаций."""
        word = form.form
        if form.kind in ("person", "organization"):
            canon = form.canon
            if canon[:1].isupper():
                word = word[:1].upper() + word[1:]
            if "ё" in canon.lower() and "е" in word:
                # «Партнёром»: восстанавливаем ё по канону
                word = word.replace("е", "ё", 1) if canon.lower().count("ё") == 1 else word
        return word

    def _entities(self, text: str, lex: _Lexicon) -> tuple[str, list[str]]:
        spans = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text)]
        repl: list[tuple[int, int, str, str]] = []  # start, end, new, why
        i = 0
        while i < len(spans):
            s, e, w = spans[i]
            low = simple_norm(w)
            prev = simple_norm(spans[i - 1][2]) if i > 0 else ""
            # 1) разорванное слово: «и ваном» → «Иваном», «в лектором» → «Вектором»
            if low in _SHORT_SPLIT and i + 1 < len(spans):
                s2, e2, w2 = spans[i + 1]
                if text[e:s2].strip() == "":
                    glued = low + simple_norm(w2)
                    form = lex.exact(glued) or (
                        lex.fuzzy(glued, limit=1) if not self._known_word(glued) else None
                    )
                    # вторая половина сама похожа на сущность («с орбетой») — предлог не трогаем
                    nxt_is_entity = lex.exact(w2) is not None or (
                        not self._known_word(w2) and lex.fuzzy(w2) is not None
                    )
                    if form and form.kind != "topic" and not nxt_is_entity:
                        # «в лектором»: склейка — сущность, а «лектором» сам по себе — нет
                        new = self._styled(form, w2)
                        if form.kind == "organization" and prev not in _PREP_WITH:
                            new = self._with_prep(new, prev, form)
                        repl.append((s, e2, new, f"склейка «{w} {w2}» → {form.canon}"))
                        i += 2
                        continue
            # 2) точное совпадение формы: регистр и пропущенный предлог
            form = lex.exact(w)
            if form is not None and form.kind != "topic":
                new = self._styled(form, w)
                if form.kind == "organization" and not self._org_context(prev, form, w):
                    i += 1
                    continue  # «север», «старт», «пульс» как обычные слова
                if prev not in _PREP_WITH:
                    new = self._with_prep(new, prev, form)
                if new != w:
                    repl.append((s, e, new, f"«{w}» → {form.canon}"))
                i += 1
                continue
            # 3) нечёткое совпадение для незнакомых слов: «арбитой» → «Орбитой»
            if len(low) >= 4 and not self._known_word(low):
                form = lex.fuzzy(low)
                if form is not None:
                    new = self._styled(form, w) if form.kind != "topic" else form.form
                    if form.kind == "organization" and prev not in _PREP_WITH:
                        new = self._with_prep(new, prev, form)
                    repl.append((s, e, new, f"«{w}» ≈ {form.canon}"))
            i += 1
        if not repl:
            return text, []
        out, pos = [], 0
        for s, e, new, _ in repl:
            out.append(text[pos:s])
            out.append(new)
            pos = e
        out.append(text[pos:])
        return "".join(out), [why for *_, why in repl]

    def _org_context(self, prev: str, form: _Form, w: str) -> bool:
        """Слово-организация вне контекста — обычное слово: «на север», «старт продаж»."""
        if w[:1].isupper() or prev in _PREP_WITH | {"для", "у", "от", "к", "ко"}:
            return True
        tag = self._case_of(form.form)
        return prev in _MEETING_HEADS and tag == "ablt"

    def _case_of(self, form: str) -> str | None:
        for p in self.ents.morph.parse(form):
            if p.tag.POS == "NOUN":
                return p.tag.case
        return None

    def _with_prep(self, new: str, prev: str, form: _Form) -> str:
        """«созвон вектором» → «созвон с Вектором»: творительный после слова-встречи."""
        if prev in _MEETING_HEADS and self._case_of(form.form) == "ablt":
            return "с " + new
        return new


def merge_known(*items: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for it in items:
        out.update(x for x in it if x)
    return out

"""Нормализация русского текста для импорта и поиска.

Одна и та же функция `lemmas()` применяется к заголовкам записей и к запросам,
поэтому «Позвонить Иваном» и «звонил Ивану» дают одинаковые токены.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymorphy3

# ---------- Лексикон предметной области ----------

# Имена: именительный, родительный, дательный, винительный, творительный.
PERSON_FORMS: dict[str, tuple[str, str, str, str, str]] = {
    "Алексей": ("Алексей", "Алексея", "Алексею", "Алексея", "Алексеем"),
    "Анна": ("Анна", "Анны", "Анне", "Анну", "Анной"),
    "Дмитрий": ("Дмитрий", "Дмитрия", "Дмитрию", "Дмитрия", "Дмитрием"),
    "Елена": ("Елена", "Елены", "Елене", "Елену", "Еленой"),
    "Иван": ("Иван", "Ивана", "Ивану", "Ивана", "Иваном"),
    "Максим": ("Максим", "Максима", "Максиму", "Максима", "Максимом"),
    "Мария": ("Мария", "Марии", "Марии", "Марию", "Марией"),
    "Наталья": ("Наталья", "Натальи", "Наталье", "Наталью", "Натальей"),
    "Ольга": ("Ольга", "Ольги", "Ольге", "Ольгу", "Ольгой"),
    "Сергей": ("Сергей", "Сергея", "Сергею", "Сергея", "Сергеем"),
}
NOM, GEN, DAT, ACC, INS = range(5)

# Организации: именительный и творительный (в данных встречаются только они).
ORG_FORMS: dict[str, tuple[str, str]] = {
    "Альфа": ("Альфа", "Альфой"),
    "Вектор": ("Вектор", "Вектором"),
    "Маяк": ("Маяк", "Маяком"),
    "Орбита": ("Орбита", "Орбитой"),
    "Партнёр": ("Партнёр", "Партнёром"),
    "Пульс": ("Пульс", "Пульсом"),
    "Север": ("Север", "Севером"),
    "Старт": ("Старт", "Стартом"),
}

# Темы: именительный (каноническая форма) → дательный/предложный, как в данных.
TOPIC_FORMS: dict[str, str] = {
    "CRM": "CRM",
    "аналитика": "аналитике",
    "бюджет": "бюджету",
    "договор": "договору",
    "интеграция": "интеграции",
    "мобильное приложение": "мобильному приложению",
    "новый продукт": "новому продукту",
    "презентация": "презентации",
    "сайт": "сайту",
    "хакатон": "хакатону",
}
# Винительный падеж темы — для «Обсудить …» и «Напомнить … про …».
TOPIC_ACC: dict[str, str] = {
    "CRM": "CRM",
    "аналитика": "аналитику",
    "бюджет": "бюджет",
    "договор": "договор",
    "интеграция": "интеграцию",
    "мобильное приложение": "мобильное приложение",
    "новый продукт": "новый продукт",
    "презентация": "презентацию",
    "сайт": "сайт",
    "хакатон": "хакатон",
}

# Объекты (документы и действия): винительный → именительный.
OBJECT_FORMS: dict[str, str] = {
    "акт": "акт",
    "договор": "договор",
    "итоги встречи": "итоги встречи",
    "коммерческое предложение": "коммерческое предложение",
    "отчёт": "отчёт",
    "презентацию": "презентация",
    "протокол": "протокол",
    "смету": "смета",
    "техническое задание": "техническое задание",
}

# ---------- Лемматизация ----------

# Ошибки pymorphy3 на словах предметной области (морфология, не синонимы).
LEMMA_OVERRIDES: dict[str, str] = {
    "аналитике": "аналитика",
    "аналитику": "аналитика",
    "аналитикой": "аналитика",
    "аналитик": "аналитика",
    "демо": "демо",
    "деньгам": "деньги",
    "деньги": "деньги",
    "денег": "деньги",
    "деньгами": "деньги",
    "срм": "crm",
    "црм": "crm",
    "тз": "тз",
    "кп": "кп",
    "итоги": "итог",
    "сроки": "срок",
}

# Устойчивые сочетания (пара лемм → токен). Заполняется из phrases в synonyms.yml.
PHRASES: dict[tuple[str, str], str] = {}

# Слова, которые не несут смысла для поиска (служебные, команды, «задача» как тип).
STOPWORDS: set[str] = set(
    """
    а и или но в во на по с со к ко у о об от до для из за про при над под без через же ли бы не
    я мы ты вы он она они оно мой моя мои наш наша наши свой себя меня мне нас нам вас
    что кто когда где какой какая какие который это этот эта тот та все всё весь вся всего
    был быть была было были будет есть нет ещё еще уже последний раз когда-то когда-нибудь
    найти найди найдите покажи показать показывать искать поискать напомни вспомнить подсказать
    скажи сказать дать дай мочь можно нужно надо должный ставить поставить ставил
    задача задачка дело запись событие штука вещь всё-таки там тут вообще хотеть
    её ее его их связать связанный касаться ну короче слушай давай самый
    """.split()
)

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[А-Яа-яЁё]+)?")


@lru_cache(maxsize=1)
def _morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def fold(s: str) -> str:
    """Нижний регистр и ё → е — для сравнения."""
    return s.lower().replace("ё", "е")


# Все падежные формы известных имён и организаций → лемма (pymorphy3 путает, напр. «Анне»).
_FORM_LEMMA: dict[str, str] = {
    **{fold(form): fold(n) for n, forms in PERSON_FORMS.items() for form in forms},
    **{fold(form): fold(n) for n, forms in ORG_FORMS.items() for form in forms},
}


@lru_cache(maxsize=50_000)
def _base_lemma(word: str) -> str:
    w = fold(word)
    if w in _FORM_LEMMA:
        return _FORM_LEMMA[w]
    if w in LEMMA_OVERRIDES:
        return LEMMA_OVERRIDES[w]
    if re.fullmatch(r"[a-z0-9]+", w):
        return w
    return fold(_morph().parse(w)[0].normal_form)


@lru_cache(maxsize=50_000)
def lemma(word: str) -> str:
    """Лемма с приведением глагола к форме из заголовков (verbs в synonyms.yml)."""
    base = _base_lemma(word)
    return lexicon().verbs.get(base, base)


# Аббревиатуры, произнесённые по буквам (так их отдаёт распознавание речи).
SPOKEN_ABBR: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bт[эе]\s*з[эе]\b", re.I), "ТЗ"),
    (re.compile(r"\b(си|эс)\s*ар\s*эм\b", re.I), "CRM"),
    (re.compile(r"\b(си|эс)\s*эр\s*эм\b", re.I), "CRM"),
    (re.compile(r"\bка\s*п[эе]\b", re.I), "КП"),
]


def normalize_spoken(text: str) -> str:
    for rx, repl in SPOKEN_ABBR:
        text = rx.sub(repl, text)
    return text


def words(text: str) -> list[str]:
    """Слова исходного текста; склеивает имя, разбитое распознаванием («и ваном» → «иваном»)."""
    ws = _WORD_RE.findall(normalize_spoken(text))
    out: list[str] = []
    i = 0
    while i < len(ws):
        if i + 1 < len(ws) and len(ws[i]) <= 2 and fold(ws[i] + ws[i + 1]) in _FORM_LEMMA:
            out.append(ws[i] + ws[i + 1])
            i += 2
            continue
        out.append(ws[i])
        i += 1
    return out


def lemmas(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Леммы со склейкой устойчивых сочетаний из словаря синонимов."""
    lexicon()  # фразы словаря должны быть в PHRASES до разбора
    return _join(text, lemma, drop_stopwords)


def _join(text: str, lem: Callable[[str], str], drop_stopwords: bool) -> list[str]:
    raw = [lem(w) for w in words(text)]
    out: list[str] = []
    i = 0
    while i < len(raw):
        if i + 1 < len(raw) and (raw[i], raw[i + 1]) in PHRASES:
            out.append(PHRASES[(raw[i], raw[i + 1])])
            i += 2
            continue
        out.append(raw[i])
        i += 1
    if drop_stopwords:
        out = [t for t in out if t not in STOPWORDS]
    return out


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Расстояние Дамерау—Левенштейна (с ранним выходом выше cap)."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def fuzzy_ok(a: str, b: str) -> bool:
    """Похожи ли два слова с учётом опечатки: 1 правка от 5 букв, 2 — от 9."""
    n = min(len(a), len(b))
    if n < 5:
        return False
    return edit_distance(a, b, 2) <= (2 if n >= 9 else 1)


# ---------- Словарь синонимов: backend/app/nlu/synonyms.yml (ведёт чат 1) ----------
# Секции: phrases (канон: [варианты] — симметричная группа), expand (слово запроса →
# что ещё искать, одностороннее), verbs (разговорный глагол → глагол из заголовков),
# types, organizations, people. После правки словаря: python -m app.cli reembed.

SYNONYMS_PATH = Path(
    # `or`: пустая строка в .env (SYNONYMS_PATH=) — тоже «не задано», а не путь «.»
    os.getenv("SYNONYMS_PATH") or Path(__file__).resolve().parents[1] / "nlu" / "synonyms.yml"
)
# Заголовки напоминаний начинаются с «Напомнить», а в речи — «напоминалка», «напоминай».
_REMINDER_VERB = "напомнить"
_REMINDER_ASPECTS = ("напоминать",)


@dataclass(frozen=True)
class Lexicon:
    source: str
    groups: dict[str, frozenset[str]] = field(default_factory=dict)  # токен → группа
    expand: dict[str, frozenset[str]] = field(default_factory=dict)  # только для запроса
    verbs: dict[str, str] = field(default_factory=dict)  # лемма → глагол из заголовков
    types: dict[str, str] = field(default_factory=dict)  # токен → тип записи
    organizations: tuple[str, ...] = ()
    people: tuple[str, ...] = ()


_EMPTY = Lexicon(source="none")
_LOADING = {"on": False}


def _phrase_token(text: str) -> str:
    return "_".join(fold(text).split())


def _tokens_raw(text: str) -> list[str]:
    return [_base_lemma(w) for w in words(text)]


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    """Словарь чата 1. Нет файла — работаем без синонимов (с предупреждением в логе)."""
    if _LOADING["on"]:
        return _EMPTY
    if not SYNONYMS_PATH.exists():
        logging.getLogger(__name__).warning("нет %s: поиск без синонимов", SYNONYMS_PATH)
        return _EMPTY
    import yaml

    _LOADING["on"] = True
    try:
        return _parse(yaml.safe_load(SYNONYMS_PATH.read_text(encoding="utf-8")) or {})
    finally:
        _LOADING["on"] = False


def _parse(data: dict[str, Any]) -> Lexicon:
    groups: list[set[str]] = []
    for key, variants in (data.get("phrases") or {}).items():
        key_toks = _tokens_raw(str(key))
        canon = key_toks[0] if len(key_toks) == 1 else _phrase_token(str(key))
        group = {canon}
        for v in [key, *(variants or [])]:
            toks = _tokens_raw(str(v))
            if len(toks) == 1:
                group.add(toks[0])
            elif len(toks) == 2:
                PHRASES.setdefault((toks[0], toks[1]), canon)
            # фразы из 3+ слов не поддерживаются: в данных таких нет
        groups.append(group)

    index: dict[str, frozenset[str]] = {}
    for g in groups:
        merged = frozenset(g).union(*(index[t] for t in g if t in index))
        for t in merged:
            index[t] = merged

    def tok(text: str) -> str:
        toks = _tokens_raw(text)
        if len(toks) == 1:
            return toks[0]
        return PHRASES.get((toks[0], toks[1]), _phrase_token(text)) if len(toks) == 2 else ""

    expand = {
        tok(str(k)): frozenset(tok(str(v)) for v in (vs or []))
        for k, vs in (data.get("expand") or {}).items()
    }
    verbs: dict[str, str] = {}
    for canon_verb, variants in (data.get("verbs") or {}).items():
        target = _base_lemma(str(canon_verb))
        for v in [canon_verb, *(variants or [])]:
            verbs.setdefault(_base_lemma(str(v)), target)
    types: dict[str, str] = {}
    for typ, variants in (data.get("types") or {}).items():
        for v in variants or []:
            types[_base_lemma(str(v))] = str(typ)
            if typ == "reminder":
                verbs.setdefault(_base_lemma(str(v)), _REMINDER_VERB)
    for v in _REMINDER_ASPECTS:
        verbs.setdefault(v, _REMINDER_VERB)
    return Lexicon(
        source=str(SYNONYMS_PATH),
        groups=index,
        expand=expand,
        verbs=verbs,
        types=types,
        organizations=tuple(map(str, data.get("organizations") or ())),
        people=tuple(map(str, data.get("people") or ())),
    )


def synonym_group(token: str) -> frozenset[str]:
    """Симметричная группа (phrases)."""
    return lexicon().groups.get(token, frozenset({token}))


def query_group(token: str) -> frozenset[str]:
    """Что искать по слову запроса: группа + односторонние расширения (expand)."""
    lex = lexicon()
    extra = lex.expand.get(token, frozenset())
    return synonym_group(token).union(*(synonym_group(t) for t in extra))


def expand(tokens: list[str]) -> list[str]:
    """Токены + все их синонимы, без дублей, порядок сохранён."""
    seen: dict[str, None] = {}
    for t in tokens:
        for s in sorted(synonym_group(t)):
            seen.setdefault(s, None)
        seen.setdefault(t, None)
    return list(seen)


def search_text(*parts: str | None) -> str:
    """Нормализованный текст для FTS/триграмм: леммы + синонимы."""
    toks: list[str] = []
    for p in parts:
        if p:
            toks.extend(lemmas(p))
    return " ".join(expand(toks))


# ---------- Сущности в свободном тексте (страховка, если NLU их не выделил) ----------

_PERSON_BY_FOLD = {fold(n): n for n in PERSON_FORMS}
_ORG_BY_FOLD = {fold(n): n for n in ORG_FORMS}


@dataclass
class Entities:
    persons: list[str]
    organizations: list[str]
    rest: list[str]  # леммы, не ставшие сущностями


_ENTITY_PREPS = {"с", "со", "для", "у", "к", "ко", "от", "и"}
_ORG_FORMS_FOLDED = {fold(form): n for n, forms in ORG_FORMS.items() for form in forms}
_PERSON_FORMS_FOLDED = {fold(form): n for n, forms in PERSON_FORMS.items() for form in forms}


def _fuzzy_lookup(word: str, table: dict[str, str]) -> str | None:
    w = fold(word)
    hits = {n for form, n in table.items() if fuzzy_ok(w, form)}
    return hits.pop() if len(hits) == 1 else None


def extract_entities(
    text: str, extra_orgs: set[str] | None = None, extra_persons: set[str] | None = None
) -> Entities:
    """Имена и компании из свободного текста.

    Компания: известное имя с заглавной буквы или после предлога («со стартом» из STT);
    допускается одна опечатка («с арбитой» → Орбита). Имя: известная форма в любом регистре,
    одна опечатка, либо слово с заглавной буквы, которое pymorphy3 помечает как Name («Петром»).
    """
    lex = lexicon()
    known_orgs = dict(_ORG_BY_FOLD)
    for o in (*lex.organizations, *(extra_orgs or ())):
        known_orgs.setdefault(fold(lemma(o)), o)
    known_persons = dict(_PERSON_BY_FOLD)
    for p in (*lex.people, *(extra_persons or ())):
        known_persons[fold(lemma(p))] = p
    persons: list[str] = []
    orgs: list[str] = []
    rest: list[str] = []
    ws = words(text)
    for k, word in enumerate(ws):
        lem = lemma(word)
        after_prep = k > 0 and fold(ws[k - 1]) in _ENTITY_PREPS
        if lem in known_orgs and (word[:1].isupper() or after_prep):
            orgs.append(known_orgs[lem])
            continue
        if lem in known_persons:
            persons.append(known_persons[lem])
            continue
        if lem not in STOPWORDS and (word[:1].isupper() or after_prep):
            if org := _fuzzy_lookup(word, _ORG_FORMS_FOLDED):
                orgs.append(org)
                continue
            if person := _fuzzy_lookup(word, _PERSON_FORMS_FOLDED):
                persons.append(person)
                continue
        if word[:1].isupper() and lem not in STOPWORDS:
            parse = _morph().parse(word)[0]
            if "Name" in parse.tag:
                persons.append(parse.normal_form.capitalize())
                continue
        rest.append(word)
    return Entities(
        persons=list(dict.fromkeys(persons)),
        organizations=list(dict.fromkeys(orgs)),
        rest=lemmas(" ".join(rest)),
    )


def norm_person(name: str) -> str:
    """Каноническое имя для фильтра: «иваном» → «иван» (fold)."""
    return fold(lemma(name))

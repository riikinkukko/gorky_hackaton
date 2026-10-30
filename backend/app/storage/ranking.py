"""Логика поиска, общая для PostgreSQL (HybridSearchEngine) и памяти (InMemorySearchEngine).

Хранилища отличаются только тем, откуда берутся кандидаты: PostgreSQL отбирает их индексами
(FTS + триграммы + вектор), память перебирает все записи. Всё остальное — разбор запроса,
фильтры, покрытие, уровни, порядок, «не нашёл» — здесь, в одном месте, без SQLAlchemy.
Поэтому одинаковые запросы на одинаковых данных дают одинаковую выдачу
(проверяет tests/storage/test_search_parity.py).

Алгоритм:
1. Время запроса: Slots.tense; если NLU оставил «any», а в запросе есть «старую»,
   «прошедшие», «давнюю», «будущие»… — берётся оно (страховка). Эти слова — не тема.
2. Фильтры: type, participants, organization, status, tense, период (when). Имена и компании,
   которые NLU не выделил, достаются из текста.
3. Смысловые группы: леммы запроса без служебных слов и слов времени, каждая с синонимами.
4. Покрытие = доля групп, найденных в записи; ниже половины лучшего — отсекается. Совпало
   только слово-тип («встреча»), а предмет нет — «не нашёл». Ни одна группа не совпала, но
   названы человек или компания — всё по ним.
5. Порядок: уровень (покрытие − штраф за несовпавший тип) → время (past — свежие первыми,
   future — ближайшие, any — ближайшие к «сейчас»). Лучший уровень из ≥ 5 записей — без
   нижних уровней.

score — уровень релевантности: равный у одинаково подходящих, вниз по списку не растёт.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.contracts import Item, ItemStatus, Slots
from app.storage.text import (
    extract_entities,
    fold,
    fuzzy_ok,
    lemma,
    lexicon,
    norm_person,
    query_group,
    search_text,
)

Tense = Literal["past", "future", "any"]

# Штраф за тип, не совпавший с подсказкой из запроса. Меньше шага покрытия (1/число групп),
# поэтому никогда не поднимает запись над записью с бо́льшим покрытием.
TYPE_MISS_PENALTY = 0.01
# Если записей лучшего уровня не меньше этого числа, остальные уровни не отдаются.
TOP_TIER_ALONE = 5
# Балл, когда смысловых слов нет (только фильтры) и когда совпали лишь человек/компания.
SCORE_FILTER_ONLY = 1.0
SCORE_STRUCTURE_ONLY = 0.5

# Слова-типы: если тип уже задан фильтром, они не участвуют в покрытии.
TYPE_WORDS = {"встреча", "митинг", "напомнить", "задача", "созвон"}
# Слова, задающие время запроса (леммы). Не тема: в покрытии не участвуют.
PAST_WORDS = {
    "старый", "прошлый", "прошедший", "прошедшее", "давний", "давно", "давным-давно",
    "ранний", "ранее", "прежний", "былой", "предыдущий", "минувший", "недавний", "недавно",
}  # fmt: skip
FUTURE_WORDS = {"будущий", "предстоящий", "грядущий", "следующий", "ближайший"}
# Время разбирает NLU (Slots.when / tense); в тексте эти слова только мешают покрытию.
TIME_WORDS = (
    set(
        """
    год неделя месяц день сутки сегодня завтра вчера послезавтра позавчера утро вечер ночь
    час минута понедельник вторник среда четверг пятница суббота воскресенье выходной
    январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь
    этот последний назад скоро
    """.split()
    )
    | PAST_WORDS
    | FUTURE_WORDS
)


# ---------- документ: запись + поисковые поля ----------


@dataclass(frozen=True)
class SearchFields:
    person_norm: tuple[str, ...]
    organization_norm: tuple[str, ...]
    search_text: str


def make_fields(
    title: str, topic: str | None, obj: str | None, person: Iterable[str], org: Iterable[str]
) -> SearchFields:
    """Поисковые поля записи. PostgreSQL хранит их в items, память считает на лету."""
    person, org = list(person), list(org)
    return SearchFields(
        person_norm=tuple(norm_person(p) for p in person),
        organization_norm=tuple(fold(o) for o in org),
        search_text=search_text(title, topic, obj, " ".join(person), " ".join(org)),
    )


def search_fields(item: Item) -> SearchFields:
    return make_fields(item.title, item.topic, item.object, item.person, item.organization)


@dataclass(frozen=True)
class Doc:
    item: Item
    fields: SearchFields

    @property
    def at(self) -> datetime | None:
        return self.item.starts_at or self.item.due_at


# ---------- разбор запроса ----------


@dataclass
class SearchPlan:
    """Разобранный запрос — удобно смотреть в debug."""

    persons: list[str] = field(default_factory=list)  # нормализованные (fold)
    organizations: list[str] = field(default_factory=list)
    groups: list[frozenset[str]] = field(default_factory=list)
    raw_words: list[str] = field(default_factory=list)
    type_hint: str | None = None
    tense: Tense = "any"  # итоговое время: из Slots или из слов запроса

    @property
    def has_structure(self) -> bool:
        return bool(self.persons or self.organizations)


def tense_of_words(tokens: Iterable[str]) -> Tense:
    toks = set(tokens)
    past, future = bool(toks & PAST_WORDS), bool(toks & FUTURE_WORDS)
    if past == future:
        return "any"
    return "past" if past else "future"


def _type_hint(tokens: list[str]) -> str | None:
    """Мягкий приоритет типа, если тип не задан: слова-типы из types в synonyms.yml."""
    types = {**lexicon().types, "напомнить": "reminder"}
    return next((types[t] for t in tokens if t in types), None)


def _merge_expanded(groups: list[frozenset[str]]) -> list[frozenset[str]]:
    """NLU раскрывает «деньги» прямо в запрос («бюджет смета»); это одно понятие, а не два.

    Группы, целиком входящие в цель одного правила expand из synonyms.yml, сливаются в «или».
    """
    targets = [frozenset().union(*(query_group(t) for t in v)) for v in lexicon().expand.values()]
    out: list[frozenset[str]] = []
    used: set[int] = set()
    for i, g in enumerate(groups):
        if i in used:
            continue
        merged = g
        for tgt in targets:
            if g <= tgt:
                for j in range(i + 1, len(groups)):
                    if j not in used and groups[j] <= tgt:
                        merged |= groups[j]
                        used.add(j)
        out.append(merged)
    return out


def plan_query(slots: Slots) -> SearchPlan:
    text = slots.query or slots.title or ""
    ents = extract_entities(text)
    persons = [norm_person(p) for p in (slots.participants or ents.persons)]
    org_src = [slots.organization] if slots.organization else ents.organizations
    orgs = [fold(lemma(o)) for o in org_src if o]
    tokens = ents.rest
    # имена/компании из слотов, которые остались в тексте, — не смысловые группы
    drop = set(persons) | set(orgs) | TIME_WORDS
    if slots.type is not None:
        drop |= TYPE_WORDS
    groups: list[frozenset[str]] = []
    for t in tokens:
        if t in drop or len(t) < 2:
            continue
        g = query_group(t)
        if g not in groups:
            groups.append(g)
    return SearchPlan(
        persons=list(dict.fromkeys(persons)),
        organizations=list(dict.fromkeys(orgs)),
        groups=_merge_expanded(groups),
        raw_words=tokens,
        type_hint=None if slots.type else _type_hint(tokens),
        tense=slots.tense if slots.tense != "any" else tense_of_words(tokens),
    )


def is_constrained(slots: Slots, plan: SearchPlan) -> bool:
    """Есть ли хоть одно условие. Пустой запрос без фильтров → пустой ответ."""
    return bool(plan.groups or plan.has_structure or slots.type or slots.when or slots.status) or (
        plan.tense != "any"
    )


def period(slots: Slots) -> tuple[datetime, datetime] | None:
    if slots.when and slots.when.start:
        return slots.when.start, slots.when.end or (slots.when.start + timedelta(days=1))
    return None


def passes_filters(doc: Doc, slots: Slots, plan: SearchPlan, now: datetime) -> bool:
    """Фильтры в Python. HybridSearchEngine._filters — их SQL-двойник (держать в синхроне)."""
    it = doc.item
    if slots.type is not None and it.type != slots.type:
        return False
    if slots.status is not None:
        if it.status != slots.status:
            return False
    elif it.status == ItemStatus.CANCELLED:
        return False
    if plan.persons and not set(plan.persons) & set(doc.fields.person_norm):
        return False
    if plan.organizations and not set(plan.organizations) & set(doc.fields.organization_norm):
        return False
    at = doc.at
    if plan.tense == "past" and not (at is not None and at < now):
        return False
    if plan.tense == "future" and not (at is not None and at >= now):
        return False
    rng = period(slots)
    return rng is None or (at is not None and rng[0] <= at < rng[1])


# ---------- ранжирование ----------


def coverage(text: str, groups: list[frozenset[str]]) -> float:
    if not groups:
        return 1.0
    doc = text.split()
    docset = set(doc)
    hit = 0
    for g in groups:
        if any(
            t in docset
            or (len(t) >= 6 and any(d.startswith(t[:6]) for d in doc))
            or any(fuzzy_ok(t, d) for d in doc)
            for t in g
        ):
            hit += 1
    return hit / len(groups)


def order(
    docs: Iterable[Doc], cov: dict[int, float], plan: SearchPlan, now: datetime
) -> list[tuple[Item, float]]:
    def time_key(d: Doc) -> float:
        at = d.at
        if at is None:
            return float("inf")
        if plan.tense == "past":
            return -at.timestamp()
        if plan.tense == "future":
            return at.timestamp()
        delta = (at - now).total_seconds()
        return abs(delta) - (0.5 if delta >= 0 else 0.0)

    def score(d: Doc) -> float:
        miss = plan.type_hint is not None and d.item.type.value != plan.type_hint
        return round(cov.get(d.item.id, 0.0) - TYPE_MISS_PENALTY * miss, 4)

    ordered = sorted(docs, key=lambda d: (-score(d), time_key(d), d.item.id))
    return [(d.item, score(d)) for d in ordered]


def rank_all(docs: Iterable[Doc], plan: SearchPlan, now: datetime, score: float) -> list:
    """Все записи, прошедшие фильтры, одним уровнем — по времени."""
    docs = list(docs)
    return order(docs, {d.item.id: score for d in docs}, plan, now)


def rank_matches(
    candidates: Iterable[Doc], plan: SearchPlan, now: datetime
) -> list[tuple[Item, float]] | None:
    """Ранжировать кандидатов (уже прошедших фильтры) по смысловым группам.

    None — ни одна группа не совпала, но названы человек/компания: вызывающий отдаёт все
    записи, прошедшие фильтры (rank_all c SCORE_STRUCTURE_ONLY).
    """
    docs = {d.item.id: d for d in candidates}
    cov = {i: coverage(d.fields.search_text, plan.groups) for i, d in docs.items()}
    hits = {i: c for i, c in cov.items() if c > 0}
    if not hits:
        return None if plan.has_structure else []
    matched = [
        g for g in plan.groups if any(coverage(docs[i].fields.search_text, [g]) for i in hits)
    ]
    unmatched = [g for g in plan.groups if g not in matched]
    if unmatched and all(g & TYPE_WORDS for g in matched) and not plan.has_structure:
        # совпало только слово-тип («встреча»), а предмет («с дизайнером») — нет: не нашли
        return []
    best = max(hits.values())
    keep = [docs[i] for i, c in hits.items() if c >= best / 2]
    ranked = order(keep, cov, plan, now)
    top = ranked[0][1]
    if sum(1 for _, sc in ranked if sc == top) >= TOP_TIER_ALONE:
        # Лучших совпадений хватает на весь ответ: нижние уровни не нужны и только
        # мешают потребителю, который сортирует выдачу по времени.
        ranked = [(i, sc) for i, sc in ranked if sc == top]
    return ranked

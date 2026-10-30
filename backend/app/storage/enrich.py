"""Обогащение строк CSV организаторов: тип, сущности, исправленный заголовок.

Данные сгенерированы по ~25 шаблонам, у 31% строк сломаны падежи
(«Позвонить Иваном»). Разбираем шаблонами — это детерминированно и проверяемо;
неизвестный шаблон не теряется: тип выводится по первому слову, сущности — словарём.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

from app.contracts import Item, ItemStatus, ItemType
from app.storage.text import (
    ACC,
    DAT,
    GEN,
    INS,
    NOM,
    OBJECT_FORMS,
    ORG_FORMS,
    PERSON_FORMS,
    TOPIC_ACC,
    TOPIC_FORMS,
    fold,
)

# Обратные словари: форма → каноническое имя.
_PERSON_INS = {f[INS]: n for n, f in PERSON_FORMS.items()}
_PERSON_ANY = {fold(form): n for n, f in PERSON_FORMS.items() for form in f}
_ORG_INS = {f[1]: n for n, f in ORG_FORMS.items()}
_TOPIC_DAT = {v: k for k, v in TOPIC_FORMS.items()}

_P = "|".join(map(re.escape, _PERSON_INS))
_O = "|".join(map(re.escape, _ORG_INS))
_T = "|".join(sorted(map(re.escape, _TOPIC_DAT), key=len, reverse=True))
_OBJ = "|".join(sorted(map(re.escape, OBJECT_FORMS), key=len, reverse=True))

DEFAULT_DURATION_MIN = {"meeting": 60, "demo": 30}


@dataclass
class Enriched:
    type: ItemType
    title: str
    title_original: str
    person: list[str] = field(default_factory=list)
    organization: list[str] = field(default_factory=list)
    topic: str | None = None
    object: str | None = None
    text_issue: str | None = None  # что исправили; None — исходник был корректен
    duration_min: int | None = None  # для встреч


def _issue(original: str, fixed: str, kind: str) -> str | None:
    return kind if original != fixed else None


def enrich(text: str) -> Enriched:
    t = " ".join(text.split())

    # --- встречи ---
    if m := re.fullmatch(rf"Встреча с ({_P}) по ({_T})", t):
        p, tp = _PERSON_INS[m[1]], _TOPIC_DAT[m[2]]
        return Enriched(ItemType.MEETING, t, t, [p], [], tp, None, None, 60)
    if m := re.fullmatch(rf"Созвон с ({_O}) по ({_T})", t):
        o, tp = _ORG_INS[m[1]], _TOPIC_DAT[m[2]]
        prep = "со" if o == "Старт" else "с"  # «с Стартом» → «со Стартом»
        fixed = f"Созвон {prep} {m[1]} по {m[2]}"
        return Enriched(
            ItemType.MEETING, fixed, t, [], [o], tp, None, _issue(t, fixed, "preposition"), 60
        )
    if m := re.fullmatch(rf"Демо проекта по ({_T})", t):
        return Enriched(
            ItemType.MEETING,
            t,
            t,
            [],
            [],
            _TOPIC_DAT[m[1]],
            "демо",
            None,
            DEFAULT_DURATION_MIN["demo"],
        )
    if m := re.fullmatch(rf"Провести планёрку по ({_T})", t):
        return Enriched(ItemType.MEETING, t, t, [], [], _TOPIC_DAT[m[1]], "планёрка", None, 60)

    # --- напоминания: «Напомнить Анной про CRM» → «Напомнить Анне про CRM» ---
    if m := re.fullmatch(rf"Напомнить ({_P}) про ({_T})", t):
        p, tp = _PERSON_INS[m[1]], _TOPIC_DAT[m[2]]
        fixed = f"Напомнить {PERSON_FORMS[p][DAT]} про {TOPIC_ACC[tp]}"
        return Enriched(ItemType.REMINDER, fixed, t, [p], [], tp, None, _issue(t, fixed, "case"))

    # --- задачи ---
    if m := re.fullmatch(rf"Позвонить ({_P})", t):
        p = _PERSON_INS[m[1]]
        fixed = f"Позвонить {PERSON_FORMS[p][DAT]}"
        return Enriched(ItemType.TASK, fixed, t, [p], [], None, "звонок", _issue(t, fixed, "case"))
    if m := re.fullmatch(rf"Отправить ({_OBJ}) ({_P})", t):
        p, obj = _PERSON_INS[m[2]], OBJECT_FORMS[m[1]]
        fixed = f"Отправить {m[1]} {PERSON_FORMS[p][DAT]}"
        return Enriched(
            ItemType.TASK, fixed, t, [p], [], _topic_of_object(obj), obj, _issue(t, fixed, "case")
        )
    if m := re.fullmatch(rf"Подготовить вопросы для ({_P})", t):
        p = _PERSON_INS[m[1]]
        fixed = f"Подготовить вопросы для {PERSON_FORMS[p][GEN]}"
        return Enriched(ItemType.TASK, fixed, t, [p], [], None, "вопросы", _issue(t, fixed, "case"))
    if m := re.fullmatch(rf"Подготовиться к встрече с ({_O})", t):
        o = _ORG_INS[m[1]]
        prep = "со" if o == "Старт" else "с"
        fixed = f"Подготовиться к встрече {prep} {m[1]}"
        return Enriched(
            ItemType.TASK, fixed, t, [], [o], None, "встреча", _issue(t, fixed, "preposition")
        )
    if m := re.fullmatch(rf"Обсудить бюджет по ({_T})", t):
        tp = _TOPIC_DAT[m[1]]
        fixed = "Обсудить бюджет" if tp == "бюджет" else t  # «бюджет по бюджету»
        return Enriched(
            ItemType.TASK, fixed, t, [], [], tp, "бюджет", _issue(t, fixed, "tautology")
        )
    if m := re.fullmatch(rf"Обсудить (CRM|{_T}) с ({_P})", t):
        tp = _TOPIC_DAT.get(m[1], m[1])
        p = _PERSON_INS[m[2]]
        fixed = f"Обсудить {TOPIC_ACC[tp]} с {m[2]}"
        return Enriched(ItemType.TASK, fixed, t, [p], [], tp, None, _issue(t, fixed, "case"))
    if m := re.fullmatch(rf"Согласовать ({_OBJ}) с ({_P})", t):
        obj = OBJECT_FORMS[m[1]]
        return Enriched(ItemType.TASK, t, t, [_PERSON_INS[m[2]]], [], _topic_of_object(obj), obj)
    if m := re.fullmatch(rf"Передать ({_OBJ}) на согласование", t):
        obj = OBJECT_FORMS[m[1]]
        return Enriched(ItemType.TASK, t, t, [], [], _topic_of_object(obj), obj)
    if m := re.fullmatch(rf"Подготовить ({_OBJ})", t):
        obj = OBJECT_FORMS[m[1]]
        return Enriched(ItemType.TASK, t, t, [], [], _topic_of_object(obj), obj)
    if m := re.fullmatch(rf"Проверить ({_OBJ}) по ({_T})", t):
        obj = OBJECT_FORMS[m[1]]
        return Enriched(ItemType.TASK, t, t, [], [], _TOPIC_DAT[m[2]], obj)
    if m := re.fullmatch(
        rf"(Проверить сроки|Проверить статус задачи|Обновить материалы|Собрать обратную связь)"
        rf" по ({_T})",
        t,
    ):
        obj = {
            "Проверить сроки": "сроки",
            "Проверить статус задачи": "статус задачи",
            "Обновить материалы": "материалы",
            "Собрать обратную связь": "обратная связь",
        }[m[1]]
        return Enriched(ItemType.TASK, t, t, [], [], _TOPIC_DAT[m[2]], obj)

    return _fallback(t)


def _topic_of_object(obj: str) -> str | None:
    """Объект, совпадающий с темой («договор», «презентация»), становится и темой."""
    return obj if obj in TOPIC_FORMS else None


def _fallback(t: str) -> Enriched:
    first = fold(t.split()[0]) if t else ""
    if first in {"встреча", "созвон", "демо", "планерка", "провести"}:
        typ = ItemType.MEETING
    elif first.startswith("напомн"):
        typ = ItemType.REMINDER
    else:
        typ = ItemType.TASK
    persons = [n for w in re.findall(r"\w+", t) if (n := _PERSON_ANY.get(fold(w)))]
    orgs = [n for n, f in ORG_FORMS.items() if any(form in t for form in f)]
    topic = next((k for k, v in TOPIC_FORMS.items() if v in t or k in t), None)
    return Enriched(
        typ,
        t,
        t,
        list(dict.fromkeys(persons)),
        orgs,
        topic,
        None,
        None,
        60 if typ is ItemType.MEETING else None,
    )


_NOM_CHECK = NOM, ACC  # экспорт для тестов


def csv_item(rec: dict[str, str], user_id: int, tz: tzinfo) -> tuple[Item, Enriched]:
    """Строка CSV организаторов → Item. Одна функция для PostgreSQL-импорта и для
    InMemoryRepository, чтобы данные (а значит, и поиск) в обоих хранилищах совпадали."""
    e = enrich(rec["text"])
    at = datetime.fromisoformat(f"{rec['date']}T{rec['time']}").replace(tzinfo=tz)
    meeting = e.type is ItemType.MEETING
    item = Item(
        id=int(rec["id"]),
        user_id=user_id,
        type=e.type,
        title=e.title,
        title_original=e.title_original,
        starts_at=at if meeting else None,
        ends_at=at + timedelta(minutes=e.duration_min or 60) if meeting else None,
        due_at=None if meeting else at,  # задача/напоминание — точка во времени
        has_time=True,
        status=ItemStatus.UNKNOWN,  # статуса в данных нет — не выдумываем
        person=e.person,
        organization=e.organization,
        topic=e.topic,
        object=e.object,
        source="import",
    )
    return item, e

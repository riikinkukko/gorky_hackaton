"""Тип реплики в разговоре: полная команда, продолжение, подтверждение, отказ (25.10, чат 0).

Правило (диалог, чат 2, применяет его в одном месте, NLU только размечает):

1. **command** — своё действие и свой объект или период: «Что у меня сегодня?», «Найди…»,
   «Поставь…». Выполняется как новая, сбрасывает контекст поиска или списка.
2. **continuation** — только время, повтор, участник или местоимение: «нет, давай в
   двенадцать», «давай её в понедельник», «пусть повторяется», «и с Марией», «а на следующей
   неделе?». Применяется к тому, что сейчас в фокусе.
3. **confirm** / **reject** — «да» / «нет» без продолжения. «Нет, <время>» — продолжение.
4. Из продолжения никогда не создаётся новая запись.

Поле в контракте (предложение чату 0): ``Command.turn``. Пока его нет, :func:`with_turn`
ничего не меняет, а :func:`classify_turn` доступна диалогу напрямую.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from app.contracts import Command, DialogState, Intent, ItemRef, Slots

from .dates import RuleDateResolver, remove_spans
from .entities import EntityNormalizer
from .text import simple_norm

TurnKind = Literal["command", "continuation", "confirm", "reject"]

# начало продолжения: «а…», «нет, …», «давай …», «лучше …», «да, …»
_LEAD = re.compile(
    r"^(?:(?:а|и|ну|ну а|нет|не|ой|ой нет|а нет|давай|давайте|лучше|а лучше|тогда|да|ладно|"
    r"пусть|можно|хотя|хотя нет|в смысле|то есть|ещё|еще|тоже|сделай|только)(?:\s+|$))+"
)
# что может остаться в продолжении, кроме дат и имён
_ALLOWED = re.compile(
    r"(?<![а-я])(?:ее|её|его|их|это|эту|этот|ту|туда|тоже|так же|также|же|то же|то же самое|"
    r"на то же время|в то же время|сделай|сделать|"
    r"повтор\w*|каждый|каждую|каждое|каждые|каждого|ежедневно|еженедельно|ежемесячно|"
    r"по\s+\w+ам|с|со|и|в|во|на|к|до|а|ну|раз|неделю|день|месяц|год|"
    r"следующ\w*|предыдущ\w*|ближайш\w*|прошл\w*|потом|дальше|раньше|когда|"
    r"встречи|задачи|дела|напоминания|созвоны|напоминалки|только|прошедшие|прошлые|будущие|"
    r"предстоящие|пожалуйста|плиз|пж|время|часов|часа|час|минут\w*)(?![а-я])"
)


def classify_turn(
    cmd: Command,
    state: DialogState | None,
    now: datetime,
    dates: RuleDateResolver,
    ents: EntityNormalizer,
    *,
    from_context: bool = False,
) -> TurnKind:
    """Тип реплики. ``from_context`` — команда собрана из ответа на ожидание диалога."""
    if cmd.intent == Intent.CONFIRM:
        return "confirm"
    if cmd.intent == Intent.REJECT:
        return "reject"
    if from_context:
        return "continuation"
    return "continuation" if is_continuation_text(cmd.raw_text, now, dates, ents) else "command"


def is_continuation_text(
    text: str, now: datetime, dates: RuleDateResolver, ents: EntityNormalizer
) -> bool:
    """Во фразе нет своего действия и объекта — только время, повтор, участник, местоимение."""
    t = re.sub(r"[?!,;«»\"]+|[.:](?!\d)", " ", text).strip()  # «15:00» — не трогаем
    low = simple_norm(t)
    m = _LEAD.match(low)
    if m is None and not _starts_with_value(low):
        return False
    dp = dates.extract(t, now, "point")
    rest = remove_spans(t, dp.spans) if dp.found else t
    for name in [*ents.people(rest), *ents.organizations_in(rest)]:
        stem = name[:4].lower()
        rest = re.sub(r"(?i)\b" + re.escape(stem) + r"\w*", " ", rest)
    rest = simple_norm(rest)
    rest = _LEAD.sub(" ", rest)
    rest = _ALLOWED.sub(" ", rest)
    rest = re.sub(r"\d+", " ", rest)
    left = rest.split()
    has_value = (
        dp.found
        or bool(dp.recurrence)
        or bool(ents.people(t))
        or _has_pronoun(low)
        or bool(re.search(r"(?<![а-я])(?:следующ|предыдущ|прошедш|прошл|будущ|предстоящ)", low))
    )
    return not left and (has_value or (m is not None and _has_type_follow(low)))


def _starts_with_value(low: str) -> bool:
    """«в три», «16:30», «завтра», «каждую неделю», «её в понедельник» — без вводного слова."""
    return bool(
        re.match(
            r"^(?:в|во|на|к|до|через|завтра|сегодня|послезавтра|каждый|каждую|каждое|ее|её|"
            r"его|пусть|\d)",
            low,
        )
    )


def _has_pronoun(low: str) -> bool:
    return bool(re.search(r"(?<![а-я])(?:ее|её|его|их|туда|то же|тоже)(?![а-я])", low))


def _has_type_follow(low: str) -> bool:
    return bool(re.search(r"(?<![а-я])(?:встречи|задачи|дела|напоминания|созвоны)(?![а-я])", low))


def with_turn(cmd: Command, kind: TurnKind) -> Command:
    """Проставить ``Command.turn``, когда поле появится в контракте (v1.3)."""
    if "turn" in type(cmd).model_fields:
        return cmd.model_copy(update={"turn": kind})
    return cmd


def continuation_never_creates(cmd: Command, state: DialogState | None) -> Command:
    """Правило 4: из продолжения не создаём запись — меняем то, что в фокусе.

    «нет, давай в двенадцать» → перенос последней записи на 12:00; «сделай её каждую неделю»
    → повтор у последней записи. Название «Её», «Лучше», «Пусть повторяется» не появится.
    """
    last = state.last_command if state else None
    if cmd.intent not in (Intent.CREATE, Intent.LIST, Intent.SEARCH, Intent.UNKNOWN):
        return cmd
    if cmd.intent != Intent.CREATE and (
        last is None or last.intent not in (Intent.CREATE, Intent.UPDATE)
    ):
        return cmd  # «а на следующей неделе?» после списка — продолжение списка
    s = cmd.slots
    when = s.when
    if not (when or s.recurrence or s.participants):
        return cmd
    if (
        when is not None
        and when.start is not None
        and not when.has_date
        and last is not None
        and last.slots.when is not None
        and last.slots.when.start is not None
    ):
        # «нет, в двенадцать» после «в пятницу в одиннадцать» — тот же день
        start = last.slots.when.start.replace(hour=when.start.hour, minute=when.start.minute)
        when = when.model_copy(update={"start": start, "end": None, "has_date": True})
    slots = Slots(
        target=ItemRef(pronoun=True),
        new_when=when,
        participants=s.participants,
        recurrence=s.recurrence,
    )
    return cmd.model_copy(update={"intent": Intent.UPDATE, "slots": slots})

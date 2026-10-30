"""Продолжение или новая команда — одно правило для всего диалога (dialog/v7).

Тип реплики:
- full          — своё действие и свой объект или период: «Что у меня сегодня?», «Найди…»,
                  «Поставь…». Выполняется как новая и сбрасывает контекст поиска/списка;
- continuation  — только время, повтор, участник или местоимение: «нет, давай в двенадцать»,
                  «давай её в понедельник», «пусть повторяется», «и с Марией», «а послезавтра?».
                  Применяется к тому, что в фокусе, и никогда не создаёт запись;
- confirm       — «да», «ага», «давай»;
- reject        — «нет», «не надо», «отмена» (без продолжения). «Нет, <время>» — это поправка,
                  то есть continuation.

Источник — поле Command.turn от NLU (предложено в контракт v1.3), а пока его нет — эвристика
ниже по тексту реплики. Таблицу «состояние × тип» применяет DialogManager.handle_turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.contracts import Command, DialogState, Intent
from app.dialog.normalize import _HALF, _words, only_time_words, strip_hesitations
from app.domain import ru

TurnKind = Literal["full", "continuation", "confirm", "reject"]
KINDS: tuple[TurnKind, ...] = ("full", "continuation", "confirm", "reject")

_YES = {"да", "ага", "угу", "конечно", "подтверждаю", "верно", "окей", "ок", "хорошо", "давай",
        "давайте", "ладно", "удаляй", "согласен", "согласна"}  # fmt: skip
_NO = {"нет", "неа", "отмена", "отбой", "стоп", "передумал", "передумала", "отменить", "забудь"}
_NO_PHRASES = ("не надо", "не нужно", "не стоит", "не хочу", "нет не надо", "нет не нужно")
_FILLERS = {"ну", "а", "и", "так", "вот", "тогда", "лучше", "пожалуй", "наверное", "пж",
            "пожалуйста", "слушай", "короче", "ой", "эм", "э", "ещё", "еще", "чё", "че", "чо",
            "только", "же"}  # fmt: skip
# «а прошлые?», «только будущие», «а в этом году?», «а ещё раньше?» — уточнение поиска/списка
_TENSE = {"прошлые", "прошлых", "прошедшие", "прошедших", "старые", "старых", "будущие",
          "будущих", "предстоящие", "предстоящих", "следующие", "последние", "раньше", "позже",
          "году", "годе", "этом", "прошлом", "прошлой", "прошлую", "позапрошлом", "этого",
          "былые", "следующая", "следующий", "следующее", "следующую", "предыдущая",
          "предыдущий", "предыдущее", "предыдущую"}  # fmt: skip
# служебные глаголы продолжения: «давай её в понедельник», «сделай её каждую неделю»
_LIGHT_VERBS = {"давай", "давайте", "сделай", "сделать", "пусть", "поставь", "поставим",
                "ставь", "перекинь", "можно"}  # fmt: skip
_PRONOUNS = {"её", "ее", "его", "их", "это", "эту", "этот", "эта", "неё", "нее", "него"}
_REPEAT = {"каждый", "каждую", "каждое", "каждые", "каждым", "по", "ежедневно", "еженедельно",
           "ежемесячно", "повторяется", "повторялась", "повторялся", "повторять", "повтор",
           "повторяющейся", "повторяющимся", "регулярно", "будням", "выходным", "понедельникам",
           "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям",
           "неделю", "день", "месяц", "год", "утрам", "вечерам"}  # fmt: skip
_SHIFT = {"позже", "раньше", "вперед", "вперёд", "назад", "минут", "минуты", "минуту", "час",
          "часа", "часов", "полчаса", "полтора", "дня", "дней", "день", "неделю", "на"}  # fmt: skip
_PARTICIPANT_PREP = {"с", "со", "и"}


@dataclass
class Continuation:
    """Что именно продолжает реплика."""

    time_text: str | None = None  # «в двенадцать», «до субботы», «на час позже»
    repeat: str | None = None  # RRULE
    participants: tuple[str, ...] = ()
    pronoun: bool = False
    tense: Literal["past", "future"] | None = None


def _content_words(text: str) -> list[str]:
    return _words(strip_hesitations(text))


def is_yes_only(text: str) -> bool:
    words = [w for w in _content_words(text) if w not in _FILLERS]
    return bool(words) and all(w in _YES for w in words)


def is_no_only(text: str) -> bool:
    low = " ".join(_content_words(text))
    if any(low == p or low.endswith(" " + p) or low.startswith(p) for p in _NO_PHRASES):
        rest = low
        for p in _NO_PHRASES:
            rest = rest.replace(p, " ")
        rest_words = [w for w in rest.split() if w not in _FILLERS and w not in _NO]
        return not rest_words
    words = [w for w in low.split() if w not in _FILLERS]
    return bool(words) and all(w in _NO for w in words)


def _after_no(text: str) -> str | None:
    """«нет, в 11» → «в 11»; «не в 10, а в 11» → «в 11»; «нет» → None."""
    low = " ".join(_content_words(text))
    m = re.match(r"^(?:а\s+|ну\s+)?(?:нет|неа|не)\s+(?:(?:в|на)\s+\S+\s+а\s+)?(.+)$", low)
    if not m:
        return None
    rest = m.group(1).strip()
    if rest in ("надо", "нужно", "стоит", "хочу") or not rest:
        return None
    return rest


def continuation_of(cmd: Command, text: str) -> Continuation | None:
    """Разбор продолжения или None, если в реплике есть своё содержание (это полная команда).

    Допустимы только: время/дата/сдвиг, повтор, участник («с Марией»), местоимение,
    плюс «нет», «давай», «сделай», «пусть» и слова-паразиты."""
    raw = strip_hesitations(text)
    words = _words(raw)
    if not words:
        return None
    org = [cmd.slots.organization] if cmd.slots.organization else []
    names = {ru.stem(p) for p in [*cmd.slots.participants, *org] if p}
    orig = re.findall(r"[A-Za-zА-ЯЁа-яё0-9]+", raw)
    capital = {ru.stem(w) for w in orig[1:] if w[:1].isupper()}  # «и с Марией»
    res = Continuation()
    rest: list[str] = []
    i = 0
    has_signal = False
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in _FILLERS or w in _NO or w in _LIGHT_VERBS or w in ("не", "да"):
            i += 1
            continue
        if w in _PRONOUNS:
            res.pronoun = True
            has_signal = True
            i += 1
            continue
        if w in _PARTICIPANT_PREP and nxt and (ru.stem(nxt) in names or ru.stem(nxt) in capital):
            res.participants = (*res.participants, nxt)
            has_signal = True
            i += 2
            continue
        rest.append(w)
        i += 1
    if not rest and not has_signal:
        return None  # «нет», «да», «давай» — это не продолжение
    timeish = [
        w for w in rest if only_time_words(w) or w.isdigit() or _HALF.fullmatch(w) or w in _TENSE
    ]
    repeat = [w for w in rest if w in _REPEAT]
    shift = [w for w in rest if w in _SHIFT]
    other = [w for w in rest if w not in timeish and w not in repeat and w not in shift]
    if other:
        return None  # своё содержание: «перенеси отчёт», «что у меня сегодня»
    if repeat or cmd.slots.recurrence:
        res.repeat = cmd.slots.recurrence or _rrule_from_words(repeat)
    if timeish or shift:
        res.time_text = " ".join(w for w in rest if w in timeish or w in shift) or None
    if not (res.time_text or res.repeat or res.participants or res.pronoun):
        return None
    concrete = repeat or shift or any(w.isdigit() for w in rest)
    res.tense = (
        None
        if concrete
        # «на следующую неделю» — это время, а не уточнение
        else (
            "past"
            if any(w in _PAST for w in rest)
            else "future"
            if any(w in _FUTURE for w in rest)
            else None
        )
    )
    return res


_PAST = {"прошлые", "прошлых", "прошедшие", "прошедших", "старые", "старых", "раньше", "былые",
         "предыдущая", "предыдущий", "предыдущее", "предыдущую"}  # fmt: skip
_FUTURE = {"будущие", "будущих", "предстоящие", "предстоящих", "следующие", "следующая",
           "следующий", "следующее", "следующую"}  # fmt: skip


def _rrule_from_words(words: list[str]) -> str | None:
    ws = set(words)
    if ws & {"ежедневно"} or ("каждый" in ws and "день" in ws):
        return "FREQ=DAILY"
    if ws & {"будням"}:
        return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if ws & {"ежемесячно"} or "месяц" in ws:
        return "FREQ=MONTHLY"
    if "год" in ws:
        return "FREQ=YEARLY"
    days = {"понедельникам": "MO", "вторникам": "TU", "средам": "WE", "четвергам": "TH",
            "пятницам": "FR", "субботам": "SA", "воскресеньям": "SU"}  # fmt: skip
    by = [code for word, code in days.items() if word in ws]
    if by:
        return "FREQ=WEEKLY;BYDAY=" + ",".join(by)
    return "FREQ=WEEKLY"  # «каждую неделю», «пусть повторяется», «регулярно»


def classify_turn(cmd: Command, state: DialogState) -> TurnKind:
    """Тип реплики: поле от NLU, если оно есть, иначе — по тексту."""
    given = getattr(cmd, "turn", None)
    if given in KINDS:
        return given
    text = cmd.raw_text or ""
    if cmd.intent == Intent.UNDO:
        return "full"
    if is_yes_only(text):
        return "confirm"
    if _after_no(text) is not None and continuation_of(cmd, _after_no(text) or "") is not None:
        return "continuation"  # «нет, в 11», «нет, давай в двенадцать», «нет, до субботы»
    if is_no_only(text) or (cmd.intent == Intent.REJECT and not _after_no(text)):
        return "reject"
    if cmd.intent == Intent.CONFIRM:
        return "confirm"
    if continuation_of(cmd, text) is not None:
        return "continuation"
    return "full"


def correction_text(text: str) -> str:
    """Часть реплики, которая несёт новое значение: «нет, давай в 12» → «давай в 12»."""
    return _after_no(text) or text


# ---------- полная команда сбрасывает унаследованные слоты ----------

_TYPE_ROOTS = {
    "meeting": ("встреч", "созвон", "демо", "планерк", "планёрк", "совещан"),
    "task": ("задач", "дел"),
    "reminder": ("напомин", "напомн"),
}


def drop_inherited(cmd: Command, last: Command | None) -> Command:
    """«Что у меня в субботу?» после «Найди всё про ТЗ»: NLU мог перенести query, людей,
    организацию и тип из прошлой команды. Полная команда их не наследует: оставляем только
    то, что названо в самой реплике."""
    if last is None or not cmd.raw_text:
        return cmd
    raw_stems = set(ru.stems(cmd.raw_text))
    low = ru.norm(cmd.raw_text)
    s, p = cmd.slots, last.slots
    upd: dict[str, object] = {}

    def grounded(value: str) -> bool:
        return any(st in raw_stems for st in ru.stems(value))

    if s.query and s.query == p.query and not grounded(s.query):
        upd["query"] = None
    if (
        s.participants
        and s.participants == p.participants
        and not any(grounded(x) for x in s.participants)
    ):
        upd["participants"] = []
    if s.organization and s.organization == p.organization and not grounded(s.organization):
        upd["organization"] = None
    if (
        s.type is not None
        and s.type == p.type
        and not any(r in low for r in _TYPE_ROOTS.get(s.type.value, ()))
    ):
        upd["type"] = None
    if s.tense != "any" and s.tense == p.tense and cmd.intent == Intent.LIST:
        upd["tense"] = s.tense  # время глагола в списке берём как есть
    if not upd:
        return cmd
    slots = s.model_copy(update=upd)
    intent = cmd.intent
    if (
        intent == Intent.SEARCH
        and not (slots.query or slots.participants or slots.organization)
        and slots.when is not None
        and slots.when.start is not None
    ):
        intent = Intent.LIST  # «Что у меня в субботу?» — список, а не поиск по старому запросу
    return cmd.model_copy(update={"slots": slots, "intent": intent})

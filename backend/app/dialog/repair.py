"""Обрезанное начало команды (QA-18): «Перенеси…» → «Неси…», «Удали…» → «Дали…».

Распознавание иногда теряет первый слог, и NLU честно видит создание задачи «Неси встречу».
Создавать такое нельзя. Если первое слово — хвост глагола-команды, а дальше идёт ссылка на
запись («встречу», «её», «эту задачу»), превращаем команду в перенос / удаление / отметку
с пониженной уверенностью. Дальше диалог сам переспросит («Перенести «…» на …?»): по низкой
уверенности (перенос, выполнено) или всегда (удаление). Ничего не меняется без «да».
"""

from __future__ import annotations

from app.contracts import Command, Intent, ItemRef, Slots
from app.domain import ru

REPAIRED_CONFIDENCE = 0.5  # ниже порога безопасности диалога → обязательное подтверждение

# глагол → интент; хвосты длиной от 3 букв, кроме самого слова целиком
_VERBS: dict[str, Intent] = {
    "перенеси": Intent.UPDATE,
    "передвинь": Intent.UPDATE,
    "сдвинь": Intent.UPDATE,
    "удали": Intent.DELETE,
    "отмени": Intent.DELETE,
    "отметь": Intent.COMPLETE,
}
_TAILS: dict[str, Intent] = {
    verb[i:]: intent
    for verb, intent in _VERBS.items()
    for i in range(1, len(verb) - 2)
    if len(verb[i:]) >= 3
}
# хвост должен стоять перед ссылкой на запись, иначе это может быть обычное слово
_REFERENCES = {
    "её", "ее", "его", "их", "это", "эту", "этот", "встречу", "задачу", "напоминание",
    "созвон", "запись", "последнюю", "все",
}  # fmt: skip
_PRONOUNS = {"её", "ее", "его", "их", "это", "эту", "этот"}


def repair_truncated(cmd: Command, text: str) -> Command:
    """Вернуть исправленную команду или исходную, если признаков обрезки нет."""
    if cmd.intent not in (Intent.CREATE, Intent.UNKNOWN):
        return cmd
    words = ru.norm(text).replace(",", " ").split()
    if len(words) < 2:
        return cmd
    head, rest = words[0], words[1:]
    intent = _TAILS.get(head)
    if intent is None or rest[0] not in _REFERENCES:
        return cmd
    s = cmd.slots
    pronoun = rest[0] in _PRONOUNS
    # «встречу», «эту задачу»: описание без смысловых слов — цель берётся из контекста
    description = " ".join(w for w in rest if not _is_time_word(w)) if not pronoun else None
    slots = Slots(
        type=s.type,
        participants=s.participants,
        organization=s.organization,
        target=ItemRef(pronoun=pronoun, description=description or None),
        new_when=s.when if intent == Intent.UPDATE else None,
    )
    return Command(
        intent=intent,
        slots=slots,
        confidence=min(cmd.confidence, REPAIRED_CONFIDENCE),
        source=cmd.source,
        raw_text=cmd.raw_text,
    )


_TIME_WORDS = {
    "на", "в", "во", "к", "завтра", "сегодня", "послезавтра", "понедельник", "вторник",
    "среду", "четверг", "пятницу", "субботу", "воскресенье", "утра", "вечера", "дня",
}  # fmt: skip


def _is_time_word(w: str) -> bool:
    return w in _TIME_WORDS or w[:1].isdigit()

"""Устойчивость к ошибкам распознавания (GigaAM): нормализация транскрипта до разбора.

Три уровня:
1. NORMALIZE — что делает слой asr.py с текстом (видно, какое правило сработало).
2. COMMANDS — итоговая команда на искажённой фразе: интент, дата, сущности.
3. KEEP — фразы, которые трогать нельзя (настоящие слова, числа не про время).

Реальные ошибки распознавания от чата 7 добавляйте в REAL_ASR внизу: транскрипт как есть
→ ожидаемая команда. Строка вида («текст», intent, {поле: значение}).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.contracts import DialogState, Intent, Item, ItemType
from app.nlu import AsrNormalizer, HybridNLU, KnownEntities, RepoEntitySource

from .conftest import NOW, dt, parse, state

N = AsrNormalizer()

# ---------- 1. нормализация текста ----------

NORMALIZE = [
    # время цифрами и словами
    ("созвон в 15 00", "созвон в 15:00"),
    ("напомни в 3 30 позвонить", "напомни в 3:30 позвонить"),
    ("встреча 15 00", "встреча 15:00"),
    ("завтра 15 00 созвон", "завтра 15:00 созвон"),
    ("поставь созвон на 29 октября 15 00", "поставь созвон на 29 октября 15:00"),
    ("в 15.00 планёрка", "в 15:00 планёрка"),
    ("в 15-30 демо", "в 15:30 демо"),
    ("пятнадцать ноль ноль", "15:00"),
    ("в девять ноль пять созвон", "в 9:05 созвон"),
    ("в девять тридцать встреча", "в 9:30 встреча"),
    ("в двадцать один сорок пять созвон", "в 21:45 созвон"),
    ("в двадцать ноль ноль", "в 20:00"),
    # заминки
    ("э-э ну короче напомни это самое купить хлеб", "напомни купить хлеб"),
    ("ммм покажи эм встречи", "покажи встречи"),
    # разорванные и искажённые сущности
    ("созвон с в лектором", "созвон с Вектором"),
    ("созвон с вектаром", "созвон с Вектором"),
    ("созвон с арбитой", "созвон с Орбитой"),
    ("найди договор с орбетой", "найди договор с Орбитой"),
    ("встреча с и ваном", "встреча с Иваном"),
    ("позвонить и вану", "позвонить Ивану"),
    ("найди презинтацию", "найди презентацию"),
    # пропущенный предлог и строчные буквы
    ("созвон маяком", "созвон с Маяком"),
    ("встреча иваном", "встреча с Иваном"),
    ("обсудить бюджет с натальей", "обсудить бюджет с Натальей"),
    ("встреча с партнером по сайту", "встреча с Партнёром по сайту"),
    ("позвонить сергею", "позвонить Сергею"),
]


@pytest.mark.parametrize("noisy,clean", NORMALIZE, ids=[n for n, _ in NORMALIZE])
def test_normalize_text(noisy: str, clean: str) -> None:
    res = N.normalize(noisy)
    assert res.text == clean, res.fixes
    assert res.fixes, "исправление должно быть объяснено в fixes"


KEEP = [
    "встреча с лектором в 12",  # «лектор» — настоящее слово, а не «Вектор»
    "купить 2 00 пакета",  # «2 00» без слова даты/встречи — не время
    "поставь на 5 марта",
    "через 15 минут",
    "летим на север",  # «север» — не организация вне контекста
    "старт продаж в понедельник",
    "в 2 раза больше",
    "в 15:00",
]


@pytest.mark.parametrize("text", KEEP)
def test_keep_untouched(text: str) -> None:
    res = N.normalize(text)
    assert res.text == text and not res.fixes


# ---------- 2. итоговая команда ----------

COMMANDS: list[tuple[str, Intent, dict[str, Any]]] = [
    # срез asr_noise и typo из golden_eval
    (
        "напомни завтра в десять утра позвонить ивану",
        Intent.CREATE,
        {"type": ItemType.REMINDER, "title": "Позвонить Ивану", "start": dt(2026, 10, 22, 10)},
    ),
    ("что у меня севодня", Intent.LIST, {"start": dt(2026, 10, 21)}),
    ("какие задачи на завтро", Intent.LIST, {"type": ItemType.TASK, "start": dt(2026, 10, 22)}),
    (
        "когда у меня была встреча с и ваном",
        Intent.SEARCH,
        {"participants": ["Иван"], "tense": "past"},
    ),
    ("найди всё про тэ зэ", Intent.SEARCH, {"query": "техническое задание"}),
    (
        "какие были напоминалки про си ар эм",
        Intent.SEARCH,
        {"type": ItemType.REMINDER, "query": "CRM"},
    ),
    (
        "найди презинтацию для максима",
        Intent.SEARCH,
        {"participants": ["Максим"], "query": "презентация"},
    ),
    (
        "э-э ну короче напомни это самое завтра в восемь отправить протокол елене",
        Intent.CREATE,
        {"title": "Отправить протокол Елене", "start": dt(2026, 10, 22, 8)},
    ),
    ("созвон с арбитой когда", Intent.SEARCH, {"organization": "Орбита"}),
    ("эээ", Intent.UNKNOWN, {}),
    # склеенные и искажённые организации
    (
        "созвон с в лектором завтра в 15 00",
        Intent.CREATE,
        {"type": ItemType.MEETING, "organization": "Вектор", "start": dt(2026, 10, 22, 15)},
    ),
    (
        "поставь созвон маяком послезавтра в 10",
        Intent.CREATE,
        {"organization": "Маяк", "title": "Созвон с Маяком", "start": dt(2026, 10, 23, 10)},
    ),
    ("перенеси созвон с арбитой на 16 30", Intent.UPDATE, {"organization": "Орбита"}),
    ("удали встречу с вектаром", Intent.DELETE, {"organization": "Вектор"}),
    ("найди договор с орбетой", Intent.SEARCH, {"organization": "Орбита", "query": "договор"}),
    # время, которое распознавание пишет по-своему
    (
        "напомни в 3 30 позвонить и вану",
        Intent.CREATE,
        {"participants": ["Иван"], "start": dt(2026, 10, 21, 15, 30)},
    ),
    (
        "встреча с марией завтра в пятнадцать ноль ноль",
        Intent.CREATE,
        {"participants": ["Мария"], "start": dt(2026, 10, 22, 15)},
    ),
    (
        "завтра в девять ноль пять созвон с альфой",
        Intent.CREATE,
        {"organization": "Альфа", "start": dt(2026, 10, 22, 9, 5)},
    ),
    ("поставь созвон на 29 октября 15 00", Intent.CREATE, {"start": dt(2026, 10, 29, 15)}),
    (
        "завтра в 15.00 планёрка по сайту",
        Intent.CREATE,
        {"type": ItemType.MEETING, "start": dt(2026, 10, 22, 15)},
    ),
    (
        "в двадцать один сорок пять созвон с севером",
        Intent.CREATE,
        {"organization": "Север", "start": dt(2026, 10, 21, 21, 45)},
    ),
    # строчные имена без предлога
    (
        "встреча иваном завтра в 11",
        Intent.CREATE,
        {"participants": ["Иван"], "title": "Встреча с Иваном"},
    ),
    (
        "когда последний раз звонил сергею",
        Intent.SEARCH,
        {"participants": ["Сергей"], "tense": "past"},
    ),
]


def _check(text: str, intent: Intent, want: dict[str, Any], nlu: HybridNLU) -> None:
    c = parse(nlu, text)
    assert c.intent == intent, (text, c.slots)
    s = c.slots
    when = s.when or s.new_when
    for k, v in want.items():
        got = when.start if k == "start" and when else getattr(s, k, None)
        assert got == v, (text, k, got, s.model_dump(exclude_defaults=True))


@pytest.mark.parametrize("text,intent,want", COMMANDS, ids=[t or "<empty>" for t, _, _ in COMMANDS])
def test_command_on_noisy_transcript(nlu, text, intent, want) -> None:
    _check(text, intent, want, nlu)


def test_time_answer_after_clarify(nlu) -> None:
    # «Поставь встречу с Иваном завтра» → «На какое время?» → распознано «в 15 00»
    first = parse(nlu, "Поставь встречу с Иваном завтра")
    c = parse(nlu, "в 15 00", state(pending=first, awaiting="time"))
    assert c.intent == Intent.CREATE and c.slots.when
    assert c.slots.when.start == dt(2026, 10, 22, 15)


def test_raw_text_is_normalized(nlu) -> None:
    # диалог разбирает ответы по raw_text — он должен быть уже нормализован
    c = parse(nlu, "созвон с арбитой когда")
    assert c.raw_text == "созвон с Орбитой когда"


def test_can_be_disabled() -> None:
    off = HybridNLU(None, asr_normalize=False)
    c = parse(off, "созвон с арбитой когда")
    assert c.raw_text == "созвон с арбитой когда"


def test_log_has_original_and_normalized(nlu, caplog) -> None:
    with caplog.at_level("INFO", logger="app.nlu"):
        parse(nlu, "созвон с в лектором завтра в 15 00")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("asr "))
    assert "созвон с в лектором завтра в 15 00" in line and "Вектором" in line
    assert "15:00" in line and "склейка" in line


# ---------- словарь сущностей пользователя ----------


class _Repo:
    """Минимальный репозиторий: сущности, которых нет в synonyms.yml."""

    def __init__(self) -> None:
        self.calls = 0

    async def list_range(self, user_id: int, start: Any, end: Any, type: Any = None) -> list[Item]:
        self.calls += 1
        return [
            Item(id=1, user_id=user_id, type=ItemType.MEETING, title="Созвон с Гаммой",
                 starts_at=NOW, organization=["Гамма"], person=["Тимофей"]),
        ]  # fmt: skip


def test_user_entities_from_repository() -> None:
    repo = _Repo()
    nlu = HybridNLU(None)
    nlu.attach_repository(repo)
    st = DialogState(session_id="u", user_id=7)
    c = asyncio.run(nlu.parse("созвон с гамой завтра в 10", st, NOW))
    assert c.slots.organization == "Гамма" or "Гамм" in c.raw_text
    assert "Гамм" in c.raw_text
    asyncio.run(nlu.parse("позвонить тимофею", st, NOW))
    assert repo.calls == 1  # кэш словаря на пользователя


def test_repo_with_known_entities_method() -> None:
    class Direct:
        async def known_entities(self, user_id: int) -> dict[str, list[str]]:
            return {"person": [], "organization": ["Гамма"], "topic": ["отчётность"]}

    ents = asyncio.run(RepoEntitySource(Direct()).known_entities(1))
    assert ents == KnownEntities(set(), {"Гамма"}, {"отчётность"})


def test_entity_source_failure_does_not_break_parse() -> None:
    class Broken:
        async def list_range(self, *a: Any, **k: Any) -> list[Item]:
            raise RuntimeError("db down")

    nlu = HybridNLU(None)
    nlu.attach_repository(Broken())
    c = asyncio.run(
        nlu.parse("созвон с арбитой когда", DialogState(session_id="x", user_id=1), NOW)
    )
    assert c.intent == Intent.SEARCH and c.slots.organization == "Орбита"


# ---------- реальные ошибки распознавания (чат 7) ----------
# Формат: (транскрипт как есть, ожидаемый интент, {поле: значение}). Источник — в комментарии.

REAL_ASR: list[tuple[str, Intent, dict[str, Any]]] = [
    # из задачи чата 0 (22.10): «вектором» → «в лектором», «с Орбитой» → «с арбитой»
    (
        "поставь созвон с в лектором на пятницу в 15 00",
        Intent.CREATE,
        {"organization": "Вектор", "start": dt(2026, 10, 23, 15)},
    ),
    ("найди созвон с арбитой", Intent.SEARCH, {"organization": "Орбита"}),
]


@pytest.mark.parametrize("text,intent,want", REAL_ASR, ids=[t for t, _, _ in REAL_ASR])
def test_real_asr_errors(nlu, text, intent, want) -> None:
    _check(text, intent, want, nlu)

"""Собирает qa/live/2026-10-23.jsonl из лога стенда gorky_logs/turns/2026-10-23.log (49 реплик).

    python qa/tools/build_live_2310.py ../gorky_logs/turns/2026-10-23.log

Каждая реплика лога — отдельный кейс, history — все предыдущие реплики её сессии (сессия
проигрывается целиком). «Сейчас» — время реплики (лог в UTC, +3 ч → МСК). Ожидания и владельцы —
разбор чата 0 от 23.10 (таблица EXPECT ниже); реплики вне разбора отмечены note «вне разбора».
8 фраз со скриншотов (M01–M08) переносятся из текущего файла без изменений.
known выставляет qa/regress.py --update-known по результату прогона.
"""

# ruff: noqa: E402, E501  (таблица ожиданий: длинные строки — данные)
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_turn_log import parse

OUT = ROOT / "qa/live/2026-10-23.jsonl"

C1, C2 = "чат 1", "чат 2"
OUT_OF_TRIAGE = "вне разбора чата 0 — владелец предложен чатом 7"
UPCOMING = 2  # к этому моменту у пользователя 3 минимум 2 будущие записи
MEMORY_ONLY: set[int] = set()  # S26/S37/S46 падали на заглушке поиска в памяти; v5 починил и там

# номер строки лога → (класс, владелец, находка, expect, note)
EXPECT: dict[int, tuple[str, str, str, dict, str]] = {
    # --- сессия 2a2570fc, пользователь 1 (данные CSV) ---
    1: ("OK", C1, "-", {"intent": "create_item", "action": ["clarify"]}, ""),
    2: (
        "OK",
        C2,
        "-",
        {"action": ["created"], "type": "meeting", "at": "2026-10-24T13:00", "title_has": ["иван"]},
        "",
    ),
    3: (
        "B",
        C1,
        "QA-26",
        {
            "intent": "update_item",
            "action": ["updated", "clarify"],
            "if_updated_at": "2026-10-24T12:00",
        },
        "«можешь перенести, пожалуйста, …» → поиск вместо переноса",
    ),
    4: ("OK", C2, "-", {"action": ["clarify"]}, ""),
    5: (
        "OK",
        C1,
        "-",
        {
            "action": ["created"],
            "type": "task",
            "at": "2026-10-24T16:00",
            "title_has": ["починить часы"],
        },
        "",
    ),
    6: (
        "B",
        C1,
        "QA-26",
        {
            "intent": "update_item",
            "action": ["updated", "clarify"],
            "if_updated_at": "2026-10-24T15:30",
        },
        "повтор глагола «перенеси задачу поставить … перенеси её» — цель «починить часы»; "
        + OUT_OF_TRIAGE,
    ),
    21: (
        "B",
        C1,
        "QA-27",
        {
            "action": ["created"],
            "at": "2026-10-24T13:00",
            "title_has": ["сходить на футбол"],
            "title_not": ["нужно", "будет"],
        },
        "мусор в title: «Нужно будет сходить на футбол»",
    ),
    23: (
        "B",
        C1,
        "QA-40",
        {"action": ["created"], "at": "2026-10-24T13:30", "title_has": ["тренировка по волейболу"]},
        "на стенде создал GigaChat; правила (LLM_PROVIDER=rules) → «Во сколько поставить: 13:30 или 13:30?» — "
        "интервал «с … до …» принят за два варианта времени; " + OUT_OF_TRIAGE,
    ),
    25: (
        "C",
        C2,
        "QA-28",
        {
            "reply_has": ["длительност", "продолжительност", "не указ", "минут"],
            "forbid_action": ["created", "updated", "deleted"],
        },
        "«сколько длится» → «Нашёл: … 13:30» без ответа про длительность",
    ),
    # --- сессия e738918f, пользователь 3 (пустой) ---
    7: (
        "OK",
        C1,
        "-",
        {"intent": "search_items", "action": ["found"]},
        "у пользователя 3 ТЗ нет — честное «не нашёл»",
    ),
    8: (
        "B",
        C1,
        "QA-29",
        {
            "action": ["created", "clarify"],
            "forbid_at": ["T15:00"],
            "if_created_date": "2026-10-24",
        },
        "«на 3 дня» понято как «в 3» → 15:00; длительность в днях, а не время",
    ),
    9: (
        "OK",
        C2,
        "-",
        {"action": ["created", "clarify"]},
        "24.10, чат 0: 10:00 по умолчанию для напоминания без времени — сознательное решение чата 2",
    ),
    10: (
        "C",
        C2,
        "QA-41",
        {
            "action": ["created", "updated"],
            "at": "2026-10-30T15:00",
            "title_has": ["сдать тестовое"],
            "title_not": ["напомни", "пж", "нам будет"],
        },
        "на стенде — мусор в title (QA-27, исправлено в v5). С 24.10 S09 сразу создаёт напоминание на "
        "10:00, и «15:00» — поправка времени только что созданной записи; сейчас → список дел на завтра",
    ),
    11: (
        "OK",
        C1,
        "-",
        {"intent": ["smalltalk", "unknown"], "forbid_action": ["found", "created"]},
        "",
    ),
    12: ("OK", C1, "-", {"intent": "list_items", "action": ["listed"]}, ""),
    13: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи все мои дела» → только сегодня («Сегодня задач нет»)",
    ),
    14: (
        "C",
        C2,
        "QA-30",
        {"intent": ["list_items", "search_items"], "reply_has": ["это всё", "это все", "тестов"]},
        "на стенде «а потом что» повторило тот же ответ. С v5 S13 показывает весь список, и верный ответ — "
        "«это всё» или следующие записи; повтор «Сегодня задач нет…» — ошибка",
    ),
    15: (
        "B",
        C1,
        "QA-31",
        {"intent": "list_items", "action": ["listed"], "forbid_action": ["created", "clarify"]},
        "«на неделе что будет» → создание задачи «Что» (fallback)",
    ),
    16: (
        "B",
        C1,
        "QA-31",
        {"title_not": ["что"], "forbid_action": ["created"]},
        "следствие строки 15: «Да, 21:15» создало задачу «Что»",
    ),
    17: (
        "B",
        C1,
        "QA-32",
        {"intent": "list_items", "action": ["listed"], "items_have": ["тестов"]},
        "«на следующий неделе» (ошибка согласования) → поиск вместо списка 26.10–01.11",
    ),
    18: (
        "B",
        C1,
        "QA-33",
        {"action": ["created"], "date": "2026-10-31", "title_has": ["зарегистрировать команду"]},
        "«в последний день месяца» → вопрос о времени, дата не понята",
    ),
    19: (
        "B",
        C1,
        "QA-33",
        {"forbid_action": ["found"], "if_created_date": "2026-10-31", "reply_not": ["встреч"]},
        "ответ «в последний день месяца» → «На какой день и время поставить встречу?»",
    ),
    20: (
        "OK",
        C1,
        "QA-33",
        {"forbid_intent": ["search_items"]},
        "на стенде (LLM) «31» → поиск; в main правила отвечают «не понял» — страж: не искать",
    ),
    22: (
        "B",
        C1,
        "QA-33",
        {
            "action": ["created"],
            "at": "2026-10-31T19:00",
            "title_has": ["зарегистрировать команду"],
            "title_not": ["на 31"],
        },
        "«на 31 вечером» → 24.10 19:00 и «На 31 …» в title (мусор в title ×3)",
    ),
    24: (
        "B",
        C1,
        "QA-34",
        {"intent": ["smalltalk", "unknown"], "forbid_action": ["found"]},
        "вопрос о возможностях → поиск «записывать продолжительность»",
    ),
    26: (
        "OK",
        C1,
        "QA-25",
        {"action": ["found", "listed"], "min_items": 1},
        "в памяти — заглушка поиска fakes/ (слово «скоро»/«все что будет» ищется буквально); на стенде и в PostgreSQL проходит",
    ),
    27: (
        "OK",
        C1,
        "-",
        {"forbid_action": ["created", "deleted"]},
        "на стенде цель — мусорная задача «Что»; без неё «не нашёл» допустимо",
    ),
    28: (
        "OK",
        C1,
        "-",
        {"forbid_action": ["created", "deleted"]},
        "зависит от строк 16 и 27 (где сейчас задача «Что»): проверяем только, что ничего не создано/удалено",
    ),
    29: (
        "B",
        C1,
        "QA-35",
        {
            "intent": "update_item",
            "forbid_action": ["created", "deleted"],
            "reply_not": ["не нашёл запись «эту же"],
        },
        "«эту же задачу» → «Не нашёл запись «эту же задачу еще»»",
    ),
    30: ("OK", C1, "-", {"forbid_action": ["created", "deleted"]}, ""),
    31: (
        "B",
        C1,
        "QA-36",
        {
            "intent": "delete_item",
            "action": ["clarify"],
            "target_has": ["собрат"],
            "max_items": 1,
            "unchanged": True,
        },
        "«удали все задачу собраться командо» (распознавание) → «Удалить все 3 задачи?»; "
        + OUT_OF_TRIAGE,
    ),
    32: ("OK", C2, "-", {"forbid_action": ["deleted", "created"], "unchanged": True}, ""),
    33: (
        "OK",
        C2,
        "-",
        {"intent": "delete_item", "action": ["clarify"], "target_has": ["собрат"]},
        "",
    ),
    34: ("OK", C2, "-", {"action": ["deleted"], "target_has": ["собрат"]}, ""),
    35: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи все мои задачи» → только сегодня",
    ),
    36: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи все» → только сегодня",
    ),
    37: (
        "OK",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "на стенде (LLM) — список будущих; в памяти — заглушка поиска fakes/ (слово «скоро»/«все что будет» ищется буквально); на стенде и в PostgreSQL проходит",
    ),
    38: (
        "B",
        C1,
        "QA-37",
        {
            "action": ["clarify", "created"],
            "reply_not": ["встреч"],
            "if_created_reply_has": ["прош", "вчера"],
        },
        "«задачу на вчера» → «На какое время поставить встречу?» — прошлое и смена типа",
    ),
    39: (
        "C",
        C2,
        "QA-38",
        {
            "forbid_action": ["listed", "found"],
            "if_created_type": "task",
            "if_created_reply_has": ["прош", "вчера", "уже"],
        },
        "ответ «10:00» → создана ВСТРЕЧА на вчера 10:00 без предупреждения",
    ),
    40: (
        "OK",
        C1,
        "-",
        {"action": ["created"], "at": "2026-10-23T21:32", "title_has": ["магазин"]},
        "",
    ),
    41: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи все» → только сегодня (1 запись)",
    ),
    42: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи все» повторно → то же",
    ),
    43: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«все задачи» → только сегодня; " + OUT_OF_TRIAGE + " (та же причина, что QA-25)",
    ),
    44: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«все дела» → только сегодня; " + OUT_OF_TRIAGE + " (та же причина, что QA-25)",
    ),
    45: (
        "B",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "«покажи вообще все» → только сегодня",
    ),
    46: (
        "OK",
        C1,
        "QA-25",
        {"intent": ["list_items", "search_items"], "min_items": UPCOMING},
        "на стенде (LLM) «Не понял дату»; правила + PostgreSQL дают список. в памяти — заглушка поиска fakes/ (слово «скоро»/«все что будет» ищется буквально); на стенде и в PostgreSQL проходит",
    ),
    47: (
        "B",
        C1,
        "QA-32",
        {
            "intent": "list_items",
            "action": ["listed"],
            "items_have": ["тестов"],
            "forbid_action": ["created"],
        },
        "на стенде список дал GigaChat; правила → создана задача «Вся» на 29.10 12:00",
    ),
    48: (
        "OK",
        C1,
        "QA-39",
        {
            "action": ["created"],
            "type": "meeting",
            "at": "2026-10-25T12:30",
            "duration_min": 120,
            "title_has": ["кино"],
        },
        "страж: «пол первого на два часа» → встреча 12:30–14:30",
    ),
    # --- сессия smoke, пользователь 1 ---
    49: (
        "OK",
        C1,
        "-",
        {"intent": "list_items", "action": ["listed"]},
        "на стенде в списке мусорная запись «Привет, мне встречу , , после дождичка , …» — "
        "создана 22.10 до исправлений (см. L01/L02)",
    ),
}


def main() -> int:
    log = Path(
        sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "gorky_logs/turns/2026-10-23.log"
    )
    lines = log.read_text(encoding="utf-8").splitlines()
    turns = parse(lines)
    assert len(turns) == len(EXPECT) == 49, (len(turns), len(EXPECT))
    history: dict[str, list[str]] = {}
    cases = []
    for n, t in enumerate(turns, 1):
        cls, owner, qa, expect, note = EXPECT[n]
        hist = history.setdefault(t["session"], [])
        now = datetime.fromisoformat(t["at"]) + timedelta(hours=3)  # лог в UTC
        case = {
            "id": f"S{n:02d}",
            "cls": cls,
            "owner": owner,
            "qa": qa,
            "session": t["session"],
            "user": t["user"],
            "now": now.strftime("%Y-%m-%dT%H:%M:%S+03:00"),
            "text": t["text"],
            "expect": expect,
            "was": f"{t['intent']}/{t['action']} ({t['source']}): {t['reply'][:110]}",
            "log": f"gorky_logs/turns/2026-10-23.log:{n}",
        }
        if hist:
            case["history"] = list(hist)
        if note:
            case["note"] = note
        if n in MEMORY_ONLY:
            case["known_on"] = ["memory"]
        cases.append(case)
        hist.append(t["text"])
    screenshots, known = [], set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"].startswith("M"):
                screenshots.append(row)
            elif row.get("known"):
                known.add(row["id"])
    for case in cases:  # пометки known переживают пересборку
        if case["id"] in known:
            case["known"] = True
    rows = cases + screenshots
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"{OUT.relative_to(ROOT)}: {len(cases)} реплик лога + {len(screenshots)} со скриншотов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

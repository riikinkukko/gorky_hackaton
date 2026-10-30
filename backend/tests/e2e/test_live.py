"""Живые реплики со стенда (qa/live/*.jsonl): транскрипт → /ws/voice → ожидаемое действие.

Каждая строка набора — отдельный тест в свежем приложении (настоящие NLU и диалог на правилах,
память с CSV, «сейчас» = вечер теста). ``known: true`` — находка ещё не исправлена: тест xfail
(нестрогий, чтобы починка в чужой ветке не роняла CI). Когда владелец исправит,
``pytest tests/e2e -rxX`` покажет XPASS — qa/regress.py это подсветит, а чат 7 снимет known.
``known_on: ["memory"]`` — реплика падает только на заглушке хранилища в памяти (поиск fakes/),
а на PostgreSQL (как на стенде) обязана проходить.

Добавить реплики с нового живого теста: ``python qa/tools/import_turn_log.py лог.txt``.
"""

from __future__ import annotations

from typing import Any

import pytest

from .live import backend_name, check, load_cases, run_case

CASES = load_cases()


def _param(case: dict[str, Any]) -> Any:
    marks = []
    if case.get("known") or backend_name() in case.get("known_on", []):
        marks.append(
            pytest.mark.xfail(
                strict=False, reason=f"{case.get('qa')} ({case.get('owner')}): {case.get('was')}"
            )
        )
    return pytest.param(case, id=f"{case['_set']}-{case['id']}-{case['cls']}", marks=marks)


@pytest.mark.parametrize("case", [_param(c) for c in CASES])
def test_live_phrase(case: dict[str, Any]) -> None:
    outcome = run_case(case)
    problems = check(case, outcome)
    assert not problems, f"{case['text']!r} → {outcome.summary()}\n  " + "\n  ".join(problems)


def test_live_set_is_well_formed() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "повторяются id"
    for c in CASES:
        assert c["text"].strip() and c.get("expect"), c["id"]
        assert c["cls"] in {"A", "B", "C", "D", "OK"}, c["id"]

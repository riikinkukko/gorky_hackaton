"""Критерий готовности NLU на golden_eval: интент ≥ 95%, даты ≥ 98% (режим без LLM)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.nlu.eval_nlu import run

GOLDEN = Path(__file__).parent / "fixtures" / "golden_eval.jsonl"


def test_golden_thresholds(capsys) -> None:
    totals = asyncio.run(run(GOLDEN, "rules", verbose=False))
    ok, n = totals["intent"]
    dok, dn = totals["date"]
    assert ok / n >= 0.95, capsys.readouterr().out
    assert dok / dn >= 0.98, capsys.readouterr().out

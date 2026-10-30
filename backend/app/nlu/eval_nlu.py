"""Оценка только NLU на golden_eval.jsonl: точность интента, дат, типа, заголовка, времени (tense).

Запуск:
    python -m app.nlu.eval_nlu path/to/golden_eval.jsonl \
        [--provider rules|gigachat|yandex] [-v]
Полный eval (с поиском и диалогом) — в backend/eval/ у чата 3; этот скрипт проверяет вход в него.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.contracts import Command, DialogState, Intent, ItemType

GOLD_INTENT = {
    "create": Intent.CREATE,
    "list": Intent.LIST,
    "search": Intent.SEARCH,
    "update": Intent.UPDATE,
    "complete": Intent.COMPLETE,
    "delete": Intent.DELETE,
    "confirm": Intent.CONFIRM,
    "reject": Intent.REJECT,
    "undo": Intent.UNDO,
    "smalltalk": Intent.SMALLTALK,
    "unknown": Intent.UNKNOWN,
}


def missing_slots(cmd: Command) -> list[str]:
    """Обязательные слоты по ТЗ.

    Встреча — название, дата и время; напоминание — текст и дата-время; задача — текст.
    """
    s = cmd.slots
    miss: list[str] = []
    if cmd.intent != Intent.CREATE:
        return miss
    if not s.title:
        miss.append("title")
    if s.type == ItemType.MEETING:
        if s.when is None or s.when.start is None or s.when.ambiguous:
            miss.append("date")
        elif not s.when.has_time:
            miss.append("time")
    elif s.type == ItemType.REMINDER:
        if s.when is None or s.when.start is None:
            miss.append("datetime")
        elif not s.when.has_time:
            miss.append("time")
    return miss


def simulate_state(history_cmds: list[Command], category: str) -> DialogState:
    """Мини-имитация DialogManager (чат 2): что ассистент ждёт после истории."""
    st = DialogState(session_id="eval", user_id=1)
    if not history_cmds:
        return st
    last = history_cmds[-1]
    st.last_command = last
    st.last_item_ids = [1]
    miss = missing_slots(last)
    if miss:
        st.pending, st.awaiting = (
            last,
            ("time" if miss == ["time"] else "date" if "title" not in miss else "title"),
        )
    elif last.intent == Intent.CREATE and category == "conflict":
        st.pending, st.awaiting = last, "confirm_conflict"
    elif last.intent == Intent.DELETE:
        st.pending, st.awaiting = last, "confirm_delete"
    return st


def _norm_title(s: str | None) -> str:
    return (s or "").lower().replace("ё", "е").strip(" .")


@dataclass
class Check:
    qid: str
    query: str
    results: dict[str, bool] = field(default_factory=dict)
    got: str = ""


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _date_check(exp: dict, cmd: Command, now: datetime) -> bool | None:
    from app.nlu.dates import has_explicit_date
    from app.nlu.router import parse_shift

    s = cmd.slots
    w = s.when
    if "new_datetime" in exp:
        nw = s.new_when
        if nw is None:
            return False
        if nw.start is None:
            # «на час позже» — сдвиг применяет диалог к времени записи
            return parse_shift(nw.text) is not None
        if not has_explicit_date(nw.text, now) and (s.when is None or s.when.start is None):
            # «на 16:30» — дату берёт диалог из записи; проверяем время
            return nw.start.strftime("%H:%M") == exp["new_datetime"][11:16]
        return _fmt(nw.start) == exp["new_datetime"]
    if exp.get("ambiguous") is True and cmd.intent == Intent.LIST:
        return bool(w and w.ambiguous)
    if "datetime" in exp:
        if exp["datetime"] is None:
            return w is None
        return bool(w and w.start and w.has_time and _fmt(w.start) == exp["datetime"])
    if "due" in exp:
        return bool(w and w.start and _fmt(w.start) == exp["due"])
    if "date" in exp:
        return bool(w and w.start and w.start.date().isoformat() == exp["date"] and not w.has_time)
    if exp.get("missing") == ["time"]:
        return bool(w and w.start and not w.has_time)
    if exp.get("missing") and "datetime" in exp["missing"]:
        return w is None or w.start is None
    if "period" in exp:
        if not (w and w.start and w.end):
            return False
        a, b = exp["period"]
        last = w.end - timedelta(microseconds=1)
        if "T" in a:
            return _fmt(w.start) == a and _fmt(last) == b
        ok = w.start.date().isoformat() == a and last.date().isoformat() == b
        if not ok and "note" in exp and w.start.date() == now.date():
            ok = last.date().isoformat() == b  # «оставшаяся часть недели» тоже допустима
        return ok
    return None


async def run(path: Path, provider: str, verbose: bool) -> dict[str, tuple[int, int]]:
    from app.nlu import build_nlu

    nlu = build_nlu(None, provider=provider)
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    rows = [json.loads(lm) for lm in text.splitlines() if lm.strip()]
    checks: list[Check] = []
    prev: Command | None = None
    for r in rows:
        now = datetime.fromisoformat(r["now"])
        exp = r["expected"]
        hist: list[Command] = []
        for h in r.get("history", []):
            hist.append(await nlu.parse(h, simulate_state(hist, r["category"]), now))
        if not hist and exp.get("context") and prev is not None:
            hist = [prev]  # q16, q32: продолжение предыдущей строки набора
        state = simulate_state(hist, r["category"])
        cmd = await nlu.parse(r["query"], state, now)
        prev = cmd
        c = Check(r["qid"], r["query"])
        c.results["intent"] = cmd.intent == GOLD_INTENT[exp["intent"]]
        dc = _date_check(exp, cmd, now)
        if dc is not None:
            c.results["date"] = dc
        if "type" in exp:
            c.results["type"] = cmd.slots.type is not None and cmd.slots.type.value == exp["type"]
        if "title" in exp:
            ok_titles = [exp["title"], *exp.get("title_accept", [])]
            c.results["title"] = _norm_title(cmd.slots.title) in {_norm_title(x) for x in ok_titles}
        if "tense" in exp:
            c.results["tense"] = cmd.slots.tense == exp["tense"]
        if "recurrence" in exp:
            c.results["slots"] = cmd.slots.recurrence == exp["recurrence"]
        if "ends" in exp:
            c.results["slots"] = cmd.slots.duration_min == exp["duration_min"]
        if exp.get("context") and cmd.intent == Intent.UPDATE:
            c.results["context"] = bool(cmd.slots.target and cmd.slots.target.pronoun)
        if exp.get("missing") is not None and cmd.intent == Intent.CREATE:
            c.results["clarify"] = sorted(missing_slots(cmd)) == sorted(
                "date" if m == "datetime" and cmd.slots.type == ItemType.MEETING else m
                for m in exp["missing"]
            )
        c.got = (
            f"{cmd.intent.value} src={cmd.source} conf={cmd.confidence:.2f} "
            f"slots={cmd.slots.model_dump(exclude_defaults=True, mode='json')}"
        )
        checks.append(c)

    totals: dict[str, tuple[int, int]] = {}
    for c in checks:
        for k, v in c.results.items():
            ok, n = totals.get(k, (0, 0))
            totals[k] = (ok + int(v), n + 1)
        bad = [k for k, v in c.results.items() if not v]
        if verbose or bad:
            mark = "FAIL " + ",".join(bad) if bad else "ok"
            print(f"{c.qid} [{mark}] {c.query}\n      {c.got}")
    print("\n| Метрика | Верно | Всего | Точность |\n|---|---|---|---|")
    for k, (ok, n) in totals.items():
        print(f"| {k} | {ok} | {n} | {ok / n:.1%} |")
    return totals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--provider", default="rules")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    totals = asyncio.run(run(a.path, a.provider, a.verbose))
    ok, n = totals.get("intent", (0, 1))
    dok, dn = totals.get("date", (0, 1))
    sys.exit(0 if ok / n >= 0.95 and dok / dn >= 0.98 else 1)


if __name__ == "__main__":
    main()

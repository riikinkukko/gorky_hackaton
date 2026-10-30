"""Строки «turn …» из лога стенда → черновик набора qa/live/drafts/<дата>.jsonl.

    # на сервере:
    docker compose -f docker-compose.yml -f docker-compose.shared.yml logs api --since 24h \\
        | grep "turn " > turns.txt
    # здесь:
    python qa/tools/import_turn_log.py turns.txt [--date 2026-10-23]

Формат строки (app/api/chat.py):
    turn session=ab12cd34 user=2 text='…' → intent=create_item source=rules action=created reply='…'
Реплики группируются по session и user: всё, что было раньше в той же сессии, становится history.
В черновике у каждой реплики ``expect`` пустой, а ``was`` — что ответил стенд. Дальше человек
(чат 7) отмечает ошибки: заполняет expect, cls, owner, qa и переносит строки в qa/live/<дата>.jsonl.
Повторяющиеся реплики (тот же текст и та же история) склеиваются.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_STR = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""  # repr() строки: '…' или "…"
LINE = re.compile(
    rf"turn session=(?P<session>\S+) user=(?P<user>\S+) text=(?P<text>{_STR})"
    r" → intent=(?P<intent>\S+) source=(?P<source>\S+) action=(?P<action>\S+)"
    rf"(?: reply=(?P<reply>{_STR}))?"
)
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def parse(lines: list[str]) -> list[dict]:
    turns = []
    for raw in lines:
        m = LINE.search(raw)
        if not m:
            continue
        ts = STAMP.search(raw)
        turns.append(
            {
                "session": m["session"],
                "user": int(m["user"]) if m["user"].isdigit() else m["user"],
                "text": ast.literal_eval(m["text"]),
                "intent": m["intent"],
                "source": m["source"],
                "action": m["action"],
                "reply": ast.literal_eval(m["reply"]) if m["reply"] else "",
                "at": f"{ts[1]}T{ts[2]}" if ts else None,
            }
        )
    return turns


def to_cases(turns: list[dict], day: str) -> list[dict]:
    history: dict[tuple, list[str]] = {}
    seen: set[tuple] = set()
    cases = []
    for t in turns:
        key = (t["session"], t["user"])
        hist = history.setdefault(key, [])
        sig = (t["text"].strip().lower(), tuple(h.lower() for h in hist[-3:]))
        if sig not in seen:
            seen.add(sig)
            case = {
                "id": f"D{len(cases) + 1:02d}",
                "cls": "?",
                "owner": "?",
                "qa": "?",
                "user": t["user"],
                "text": t["text"],
                "expect": {},
                "was": f"{t['intent']}/{t['action']} ({t['source']}): {t['reply'][:100]}",
                "note": f"стенд {t['at'] or day}, сессия {t['session']}",
            }
            if hist:
                case["history"] = hist[-3:]  # дальний контекст редко важен
            if t["at"]:
                case["now"] = f"{t['at']}+03:00"
            cases.append(case)
        hist.append(t["text"])
    return cases


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("log", type=Path)
    p.add_argument("--date", default=date.today().isoformat())
    a = p.parse_args()
    turns = parse(a.log.read_text(encoding="utf-8", errors="replace").splitlines())
    cases = to_cases(turns, a.date)
    out = ROOT / "qa/live/drafts" / f"{a.date}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8"
    )
    print(f"{len(turns)} строк turn → {len(cases)} реплик → {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

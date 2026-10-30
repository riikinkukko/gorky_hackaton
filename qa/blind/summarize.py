"""Сводка слепого теста: доля ✓ / ~ / ✗, медиана секунд и попыток — по задачам ТЗ и карточкам.

    python qa/blind/summarize.py qa/blind/results/*.csv [--md qa/blind/results/SUMMARY.md]

Вход — таблицы ведущего (results_template.csv): participant, day, device, env, task, task_type,
said, result (✓ / ~ / ✗), seconds, attempts, note. Строки с note «техн.» в метрики времени
не входят (сбой вне помощника), в долю успеха — входят по фактическому результату.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

TASK_TYPES = {
    "record_task": "записать задачу / напоминание",
    "record_meeting": "записать встречу",
    "upcoming": "узнать планы на период",
    "find_old": "найти старую запись",
    "correct": "исправить / отменить",
}
CARD_TYPE = {
    1: "record_task", 2: "upcoming", 3: "find_old", 4: "record_meeting", 5: "correct",
    6: "record_task", 7: "upcoming", 8: "find_old", 9: "record_meeting", 10: "correct",
}  # fmt: skip
OK, PART, FAIL = "✓", "~", "✗"


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if not r.get("task") or r.get("participant", "").startswith("#"):
                    continue
                r["task"] = int(r["task"])
                r["task_type"] = r.get("task_type") or CARD_TYPE[r["task"]]
                r["result"] = (r.get("result") or "").strip()[:1] or FAIL
                r["seconds"] = float(r["seconds"]) if r.get("seconds") else None
                r["attempts"] = int(r["attempts"]) if r.get("attempts") else None
                r["tech"] = "техн" in (r.get("note") or "")
                rows.append(r)
    return rows


def stats(rs: list[dict]) -> str:
    n = len(rs)
    ok = sum(r["result"] == OK for r in rs)
    part = sum(r["result"] == PART for r in rs)
    secs = [r["seconds"] for r in rs if r["seconds"] is not None and not r["tech"]]
    att = [r["attempts"] for r in rs if r["attempts"] is not None]
    med_s = f"{statistics.median(secs):.0f}" if secs else "—"
    med_a = f"{statistics.median(att):.0f}" if att else "—"
    first = sum(r["result"] == OK and r["attempts"] == 1 for r in rs)
    if not n:
        return "0 | — | — | — | — | —"
    return (
        f"{n} | {ok}/{n} = {ok / n:.0%} | {part} | {first}/{n} = {first / n:.0%} | "
        f"{med_s} | {med_a}"
    )


def render(rows: list[dict]) -> str:
    head = "| Срез | Задач | ✓ | ~ | ✓ с первой фразы | Медиана, с | Медиана попыток |"
    sep = "|---|---|---|---|---|---|---|"
    people = sorted({r["participant"] for r in rows})
    lines = [
        "# Слепой тест — сводка",
        "",
        f"Участников: {len(people)} ({', '.join(people)}), строк: {len(rows)}.",
        "",
        "## По задачам ТЗ",
        "",
        head,
        sep,
    ]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["task_type"]].append(r)
    for t, name in TASK_TYPES.items():
        lines.append(f"| {name} | {stats(by_type.get(t, []))} |")
    lines.append(f"| **всего** | {stats(rows)} |")
    lines += ["", "## По карточкам", "", head, sep]
    by_card: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_card[r["task"]].append(r)
    for k in sorted(by_card):
        lines.append(f"| {k} | {stats(by_card[k])} |")
    by_dev: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_dev[f"{r.get('device', '')} · {r.get('env', '')}"].append(r)
    if len(by_dev) > 1:
        lines += ["", "## По устройству и обстановке", "", head, sep]
        lines += [f"| {k} | {stats(v)} |" for k, v in sorted(by_dev.items())]
    fails = [r for r in rows if r["result"] != OK]
    if fails:
        lines += ["", "## Неудачи и «почти»", "", "| Участник | № | Сказал | Итог | Заметки |",
                  "|---|---|---|---|---|"]  # fmt: skip
        for r in sorted(fails, key=lambda r: (r["task"], r["participant"])):
            said = (r.get("said") or "").replace("|", "/")
            note = (r.get("note") or "").replace("|", "/")
            lines.append(f"| {r['participant']} | {r['task']} | {said} | {r['result']} | {note} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("csv", nargs="+", type=Path)
    p.add_argument("--md", type=Path)
    a = p.parse_args()
    text = render(load(a.csv))
    print(text)
    if a.md:
        a.md.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

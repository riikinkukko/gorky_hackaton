"""Проверка обрезки начала фразы на телефонах по логу стенда (чек-лист qa/DEVICE_CHECK.md).

    docker compose -f docker-compose.yml -f docker-compose.shared.yml logs api --since 2h \\
        | grep "turn " > turns.txt
    python qa/tools/onset_report.py turns.txt                 # iPhone = user 2, Android = user 3
    python qa/tools/onset_report.py turns.txt --map 2=iPhone,3=Android --phrase "удали эту задачу"

Для каждой реплики, похожей на контрольную фразу (совпадает хвост «… эту задачу»), смотрим:
* «первая буква на месте» — транскрипт начинается с контрольного слова (удали), а не с обрезка
  (дали, али, ли) и не с пустоты;
* «безопасно» — ассистент ничего не выполнил молча: action ∈ {clarify, none} или (при полной
  фразе) deleted только после подтверждения. Тестировщик на вопрос отвечает «нет».
Отчёт — таблица по устройствам и список всех реплик (вставляется в qa/REPORT.md).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_turn_log import parse

SAFE = {"clarify", "none"}


def norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", t.lower().replace("ё", "е")).split())


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("log", type=Path)
    p.add_argument("--map", default="2=iPhone,3=Android", help="user=устройство через запятую")
    p.add_argument("--phrase", default="удали эту задачу")
    p.add_argument("--cold-s", type=float, default=25, help="пауза перед «холодной» попыткой, с")
    a = p.parse_args()
    devices = dict(x.split("=", 1) for x in a.map.split(","))
    want = norm(a.phrase).split()
    head, tail = want[0], " ".join(want[1:])

    turns = parse(a.log.read_text(encoding="utf-8", errors="replace").splitlines())
    rows = []
    last_at: dict[str, datetime] = {}
    for t in turns:
        at = datetime.fromisoformat(t["at"]) if t["at"] else None
        prev = last_at.get(str(t["user"]))
        if at:
            last_at[str(t["user"])] = at
        # холодная попытка: до неё ≥ cold_s без реплик — iPhone закрыл микрофон, pre-roll пуст
        cold = bool(at and (prev is None or (at - prev).total_seconds() >= a.cold_s))
        dev = devices.get(str(t["user"]))
        text = norm(t["text"])
        if dev is None or not text.endswith(tail):
            continue
        first = text[: -len(tail)].strip()
        rows.append(
            {
                "dev": dev,
                "cold": cold,
                "at": t["at"] or "",
                "text": t["text"],
                "onset_ok": first == head,
                "safe": t["action"] in SAFE or (first == head and t["action"] == "deleted"),
                "action": t["action"],
                "intent": t["intent"],
            }
        )
    if not rows:
        print(f"нет реплик «… {tail}» от пользователей {sorted(devices)}")
        return 1
    print("| Устройство | Попытка | Реплик | Первая буква на месте | Безопасно (ничего молча) |")
    print("|---|---|---|---|---|")
    for dev in dict.fromkeys(devices.values()):
        for kind, flag in (("подряд", False), (f"после паузы ≥ {a.cold_s:.0f} с", True)):
            rs = [r for r in rows if r["dev"] == dev and r["cold"] == flag]
            if rs:
                print(
                    f"| {dev} | {kind} | {len(rs)} | {sum(r['onset_ok'] for r in rs)}/{len(rs)} | "
                    f"{sum(r['safe'] for r in rs)}/{len(rs)} |"
                )
    print("\n| Время | Устройство | Транскрипт | intent / action |\n|---|---|---|---|")
    for r in rows:
        mark = ("" if r["onset_ok"] else " ✂") + (" (холодная)" if r["cold"] else "")
        what = f"{r['intent']} / {r['action']}"
        print(f"| {r['at'][11:19]} | {r['dev']} | {r['text']}{mark} | {what} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

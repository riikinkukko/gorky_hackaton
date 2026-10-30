"""Сборка состязательных сценариев qa/adversarial/<задача>.jsonl.

Данные сценариев — в qa/adversarial/scenarios/*.py, здесь — только сборка и проверки:
  * конфликты с записями CSV (пользователь 1) вычисляются, а не угадываются:
    если новая запись пересекается с записью из CSV, к action добавляется "conflict";
  * цель правки (correct) ищется в будущих записях CSV по корням: цель — ближайшая (как q89);
    если в ближайшие 30 дней кандидатов несколько — допустим ещё "clarify" (как q91); новая дата/время при переносе «только даты» или сдвиге
    считаются от времени цели;
  * у поиска старого проверяется, что в CSV есть прошлая запись с корнем из title_contains.

Запуск: python qa/adversarial/build.py [--csv путь к CSV]
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FREEZE = "2026-10-23T15:00:00+03:00"
NOW = datetime(2026, 10, 23, 15, 0)
TASKS = ["record_task", "record_meeting", "upcoming", "find_old", "correct"]
PREFIX = {"record_task": "rt", "record_meeting": "rm", "upcoming": "up", "find_old": "fo", "correct": "co"}
AXES = {
    "word_order", "colloquial_time", "asr_cut", "numbers_words", "fillers_slang", "self_correction",
    "two_commands", "context", "tense", "polite_indirect", "nonsense", "stand_live",
}
MEETING_PREFIXES = ("встреча", "созвон", "демо", "провести планёрку")


def load_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(f"{r['date']}T{r['time']}")
            text = r["text"]
            low = text.lower()
            is_meeting = low.startswith(MEETING_PREFIXES)
            dur = 30 if low.startswith("демо") else 60
            rows.append({"id": int(r["id"]), "dt": dt, "text": text, "low": low,
                         "meeting": is_meeting, "dur": dur})
    return rows


def find_csv() -> Path:
    for p in [ROOT / "data", ROOT / "backend"]:
        for f in p.rglob("voice_assistant_events_1000*.csv"):
            return f
    raise SystemExit("CSV не найден, укажите --csv")


def conflicts(rows: list[dict], start: datetime, is_meeting: bool, dur: int) -> list[dict]:
    end = start + timedelta(minutes=dur)
    out = []
    for r in rows:
        if r["meeting"]:
            r_end = r["dt"] + timedelta(minutes=r["dur"])
            if is_meeting and r["dt"] < end and start < r_end:
                out.append(r)
            elif not is_meeting and r["dt"] <= start < r_end:
                out.append(r)
        elif is_meeting and start <= r["dt"] < end:
            out.append(r)
    return out


def as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def finalize(task: str, sc: dict, rows: list[dict], problems: list[str]) -> dict:
    exp = dict(sc["expect"])
    sid = sc["id"]
    # ---- создание: конфликт с CSV
    if task in ("record_task", "record_meeting") and exp.get("date") and exp.get("time"):
        acts = as_list(exp.get("action", []))
        if "created" in acts:
            start = datetime.fromisoformat(f"{exp['date']}T{exp['time']}")
            is_meeting = exp.get("type") == "meeting"
            dur = exp.get("duration_min") or 60
            if is_meeting or exp.get("type") in ("task", "reminder"):
                hit = conflicts(rows, start, is_meeting, dur)
                if hit and "conflict" not in acts:
                    exp["action"] = acts + ["conflict"]
                    sc.setdefault("note", "")
                    sc["note"] = (sc["note"] + "; " if sc["note"] else "") + \
                        "пересекается с CSV: " + ", ".join(f"{h['dt']:%d.%m %H:%M} {h['text']}" for h in hit)
    # ---- правка: цель из будущих записей CSV
    tgt = sc.pop("target", None)
    if tgt:
        stems = [s.lower() for s in tgt["stems"]]
        day = tgt.get("date")
        cand = [r for r in rows if r["dt"] >= NOW and all(s in r["low"] for s in stems)
                and (day is None or r["dt"].date().isoformat() == day)]
        if not cand:
            problems.append(f"{sid}: цель {stems} {day} не найдена в CSV")
        else:
            cand.sort(key=lambda r: r["dt"])
            t = cand[0]
            exp.setdefault("title_contains", [stems[-1]])
            near = [r for r in cand if r["dt"] < NOW + timedelta(days=30)]
            if len(near) > 1 and tgt.get("unique", True):
                acts = as_list(exp["action"])
                if "clarify" not in acts:
                    exp["action"] = acts + ["clarify"]
                sc["note"] = ((sc.get("note") or "") + "; " if sc.get("note") else "") + \
                    f"кандидатов в ближайшие 30 дней: {len(near)}, ближайший {t['dt']:%d.%m %H:%M} — можно уточнить"
            if "new_date" in tgt or "shift_min" in tgt or "new_time" in tgt:
                base = t["dt"]
                if "shift_min" in tgt:
                    nd = base + timedelta(minutes=tgt["shift_min"])
                else:
                    d = tgt.get("new_date", base.date().isoformat())
                    tm = tgt.get("new_time", base.strftime("%H:%M"))
                    nd = datetime.fromisoformat(f"{d}T{tm}")
                exp["date"], exp["time"] = nd.date().isoformat(), nd.strftime("%H:%M")
                others = [r for r in rows if r["id"] != t["id"]]
                hit = conflicts(others, nd, t["meeting"], t["dur"])
                acts = as_list(exp["action"])
                if hit and "conflict" not in acts:
                    exp["action"] = acts + ["conflict"]
                    sc["note"] = ((sc.get("note") or "") + "; " if sc.get("note") else "") + \
                        "новое время пересекается с CSV: " + \
                        ", ".join(f"{h['dt']:%d.%m %H:%M} {h['text']}" for h in hit)
    # ---- поиск старого: в CSV должна быть прошлая запись
    if task == "find_old" and exp.get("title_contains") and not sc.pop("skip_csv_check", False):
        for s in exp["title_contains"]:
            if not any(r["dt"] < NOW and s in r["low"] for r in rows):
                problems.append(f"{sid}: в прошлом CSV нет «{s}»")
    if task == "upcoming" and exp.get("title_contains"):
        for s in exp["title_contains"]:
            if not any(r["dt"] >= NOW and s in r["low"] for r in rows):
                problems.append(f"{sid}: в будущем CSV нет «{s}»")
    # ---- порядок ключей
    out = {"id": sid, "task": task, "axis": sc["axis"], "turns": sc["turns"],
           "freeze_now": sc.get("freeze_now", FREEZE)}
    if sc.get("user"):
        out["user"] = sc["user"]
    order = ["intent", "action", "type", "date", "time", "period", "duration_min", "title_contains",
             "title_not", "tense", "must_not", "forbid_at", "min_items", "max_items", "reply_has", "reply_not"]
    out["expect"] = {k: exp[k] for k in order if k in exp} | {k: v for k, v in exp.items() if k not in order}
    if sc.get("note"):
        out["note"] = sc["note"]
    return out


def validate(task: str, sc: dict, problems: list[str]) -> None:
    sid = sc["id"]
    if sc["axis"] not in AXES:
        problems.append(f"{sid}: неизвестная ось {sc['axis']}")
    if not sc["turns"] or not all(isinstance(t, str) and t.strip() for t in sc["turns"]):
        problems.append(f"{sid}: пустые turns")
    e = sc["expect"]
    if not ({"action", "intent", "must_not"} & e.keys()):
        problems.append(f"{sid}: нечего проверять")
    for k in ("date",):
        if k in e:
            datetime.fromisoformat(e[k])
    if "period" in e:
        a, b = e["period"]
        if datetime.fromisoformat(a) > datetime.fromisoformat(b):
            problems.append(f"{sid}: period наоборот")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()
    rows = load_csv(args.csv or find_csv())
    sys.path.insert(0, str(HERE))
    problems: list[str] = []
    seen_text: dict[str, str] = {}
    summary = {}
    for task in TASKS:
        mod = importlib.import_module(f"scenarios.{task}")
        out = []
        for i, sc in enumerate(mod.SCENARIOS, 1):
            sc = json.loads(json.dumps(sc, ensure_ascii=False))
            sc["id"] = f"{PREFIX[task]}-{i:03d}"
            if isinstance(sc["turns"], str):
                sc["turns"] = [sc["turns"]]
            key = " || ".join(t.lower() for t in sc["turns"])
            if key in seen_text:
                problems.append(f"{sc['id']}: дубль {seen_text[key]}")
            seen_text[key] = sc["id"]
            validate(task, sc, problems)
            out.append(finalize(task, sc, rows, problems))
        (HERE / f"{task}.jsonl").write_text(
            "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), encoding="utf-8")
        summary[task] = out
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)
    # сводка
    axes = sorted(AXES)
    lines = ["| ось | " + " | ".join(TASKS) + " | всего |", "|---|" + "---:|" * (len(TASKS) + 1)]
    for ax in axes:
        c = [sum(1 for o in summary[t] if o["axis"] == ax) for t in TASKS]
        lines.append(f"| {ax} | " + " | ".join(map(str, c)) + f" | {sum(c)} |")
    tot = [len(summary[t]) for t in TASKS]
    mt = [sum(1 for o in summary[t] if len(o["turns"]) > 1) for t in TASKS]
    lines.append("| **всего** | " + " | ".join(map(str, tot)) + f" | {sum(tot)} |")
    lines.append("| многоходовых | " + " | ".join(f"{m} ({m * 100 // n}%)" for m, n in zip(mt, tot))
                 + f" | {sum(mt)} |")
    (HERE / "SUMMARY.md").write_text("# Сводка сценариев\n\nСобрано `build.py`.\n\n" + "\n".join(lines) + "\n",
                                     encoding="utf-8")
    print("\n".join(lines))
    acts = Counter(a for t in TASKS for o in summary[t] for a in as_list(o["expect"].get("action", [])))
    print("actions:", dict(acts))


if __name__ == "__main__":
    main()

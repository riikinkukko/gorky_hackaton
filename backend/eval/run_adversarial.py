"""Прогон состязательных реплик чата 9 (qa/adversarial/*.jsonl) + оракулы.

    python -m eval.run_adversarial                      # все 5 файлов, отчёт в stdout
    python -m eval.run_adversarial --out adv_report.md --json adv_rows.json
    python -m eval.run_adversarial --task find_old --only fo-001,fo-007

Среда как у golden: свежий клон CSV на сценарий (PostgreSQL — Sandbox, память — новый
контейнер), пользователь — клон шаблона, «сейчас» = freeze_now. Оценивается ответ на последнюю
реплику из turns; остальные — история той же сессии.

Две группы проверок:
- expect.* — то, что автор сценария (чат 9) записал как однозначное
  (формат — qa/adversarial/README.md);
- оракулы — правила, которые должны выполняться для ЛЮБОЙ фразы: название без мусора,
  ответ называет сохранённое время, «прошлое» не возвращает будущее, нет исключений.

Не блокирует CI: код возврата 0, если не задан --min-pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.contracts import AssistantReply, DialogState, Item
from app.storage.config import DEFAULT_TZ
from eval.run_eval import ContainerRunner, MemorySandbox, Sandbox, find_csv

ROOT = Path(__file__).resolve().parents[2]
ADV = ROOT / "qa" / "adversarial"
TASKS = ["record_task", "record_meeting", "upcoming", "find_old", "correct"]
TASK_RU = {
    "record_task": "Записать дело",
    "record_meeting": "Записать встречу",
    "upcoming": "Узнать, что впереди",
    "find_old": "Найти старое",
    "correct": "Поправить",
}

MUST_NOT = {
    "create": lambda r, i: r.action == "created" or (i == "create_item" and r.action == "created"),
    "update": lambda r, i: r.action == "updated",
    "delete": lambda r, i: r.action == "deleted",
    "complete": lambda r, i: r.action == "completed",
    "undo": lambda r, i: r.action == "undone",
    "search": lambda r, i: r.action == "found",
    "list": lambda r, i: r.action == "listed",
}

# ---------- оракулы ----------

_W = r"(?<![а-яё])({})(?![а-яё])"
JUNK_WORDS = re.compile(
    _W.format(
        "часов|часа|час|утра|вечера|дня|ночи|завтра|сегодня|послезавтра|вчера|через|"
        "напомни|напомнить|поставь|поставить|добавь|перенеси|перенести|удали|запиши|"
        "задачу|встречу|пж|пожалуйста|плиз|плз|ну|короче|типа|блин|э+|ээ|ой|"
        "понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|"
        "её|его|это|этом|этот|эту|тот|ту|туда|пусть|лучше|можно|давай"
    ),
    re.I,
)
MAT = re.compile(
    r"(?<![а-яё])(бля\w*|хуй\w*|хуе\w*|пизд\w*|ебан\w*|ёбан\w*|сука|нахуй)(?![а-яё])", re.I
)
QUESTION_ONLY = re.compile(r"^(что|какие|какая|какой|когда|где|кто|сколько)\??$", re.I)
DATE_IN_TITLE = re.compile(r"(?<!\d)([0-2]?\d[:.][0-5]\d|\d{1,2} (?:октябр|ноябр|декабр)\w*)", re.I)

# при conflict запись ещё не сохранена, а items[0] — то, с чем пересеклось: слоты не проверяем
MUTATING = {"created", "updated"}


def _at(it: Item) -> datetime | None:
    return it.starts_at or it.due_at


def _hm(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).strftime("%H:%M")


def oracles(reply: AssistantReply, now: datetime, tense: str | None) -> list[str]:
    bad: list[str] = []
    if reply.action in MUTATING:
        for it in reply.items[:1]:
            t = (it.title or "").strip()
            low = t.lower()
            if len(t) < 2 or QUESTION_ONLY.match(low):
                bad.append(f"O1 пустое название «{t}»")
            elif m := JUNK_WORDS.search(low):
                bad.append(f"O1 мусор в названии «{t}» ({m.group(1)})")
            elif MAT.search(low):
                bad.append(f"O1 мат в названии «{t}»")
            elif re.search(r",\s*,|^[,.\s]|\s[,.]\s*$", t):
                bad.append(f"O1 пустые запятые в названии «{t}»")
            elif DATE_IN_TITLE.search(low):
                bad.append(f"O1 дата/время в названии «{t}»")
            at = _at(it)
            if at is not None and _hm(at) not in ("23:59", "00:00"):
                hm = _hm(at)
                alt = hm.lstrip("0")
                if hm not in reply.text and alt not in reply.text:
                    bad.append(f"O2 ответ не называет время {hm}")
    if reply.action == "found" and tense == "past":
        future = [i for i in reply.items if (a := _at(i)) and a > now]
        if future:
            bad.append(f"O3 «прошлое», а найдено будущее: {len(future)} шт.")
    return bad


# ---------- проверка expect ----------


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


def _roots_in(title: str, roots: list[str]) -> bool:
    low = title.lower().replace("ё", "е")
    return all(r.lower().replace("ё", "е") in low for r in roots)


def check(row: dict, reply: AssistantReply, now: datetime) -> tuple[dict[str, bool], list[str]]:
    exp = row.get("expect", {})
    dbg = reply.debug or {}
    intent = dbg.get("intent")
    slots = dbg.get("slots") or {}
    c: dict[str, bool] = {}
    first = reply.items[0] if reply.items else None

    if "intent" in exp:
        c["intent"] = intent in _as_list(exp["intent"])
    if "action" in exp:
        c["action"] = reply.action in _as_list(exp["action"])
    if "type" in exp and reply.action in MUTATING:
        c["type"] = bool(first) and first.type.value == exp["type"]  # type: ignore[union-attr]
    is_list = intent == "list_items"
    if ("date" in exp or "time" in exp) and not is_list and reply.action in MUTATING:
        at = _at(first) if first else None
        loc = at.astimezone(DEFAULT_TZ) if at else None
        if "date" in exp:
            c["date"] = bool(loc) and loc.strftime("%Y-%m-%d") == exp["date"]  # type: ignore[union-attr]
        if "time" in exp:
            c["time"] = bool(loc) and loc.strftime("%H:%M") == exp["time"]  # type: ignore[union-attr]
    if is_list and ("date" in exp or "period" in exp):
        when = slots.get("when") or {}
        s = (when.get("start") or "")[:10]
        e = (when.get("end") or when.get("start") or "")[:10]
        if "date" in exp:
            c["date"] = s == exp["date"]
        if "period" in exp:
            from datetime import date, timedelta

            last = date.fromisoformat(exp["period"][1])
            # конец периода бывает включительным (вс 23:59) или исключающим (пн 00:00)
            ok_end = e in (last.isoformat(), (last + timedelta(days=1)).isoformat())
            c["period"] = s == exp["period"][0] and ok_end
    if "duration_min" in exp and reply.action in MUTATING and first:
        if first.starts_at and first.ends_at:
            c["duration"] = (first.ends_at - first.starts_at).total_seconds() / 60 == exp[
                "duration_min"
            ]
        else:
            c["duration"] = False
    if "title_contains" in exp and reply.action in MUTATING | {"found", "listed"}:
        c["title"] = any(_roots_in(i.title or "", exp["title_contains"]) for i in reply.items)
    if "title_not" in exp and reply.action in MUTATING:
        c["title_not"] = not any(
            w.lower() in (i.title or "").lower() for i in reply.items for w in exp["title_not"]
        )
    if "tense" in exp:
        c["tense"] = slots.get("tense", "any") == exp["tense"]
    for op in exp.get("must_not", []):
        c[f"not_{op}"] = not MUST_NOT[op](reply, intent)
    low = reply.text.lower()
    if "reply_has" in exp:  # хотя бы одна из подстрок
        c["reply_has"] = any(str(x).lower() in low for x in _as_list(exp["reply_has"]))
    if "reply_not" in exp:  # ни одной из подстрок
        c["reply_not"] = not any(str(x).lower() in low for x in _as_list(exp["reply_not"]))
    if "min_items" in exp:
        c["min_items"] = len(reply.items) >= int(exp["min_items"])
    if "max_items" in exp:
        c["max_items"] = len(reply.items) <= int(exp["max_items"])
    return c, oracles(reply, now, exp.get("tense") or slots.get("tense"))


# ---------- прогон ----------


async def run(rows: list[dict], runner: ContainerRunner, sb: Any) -> list[dict]:
    out = []
    for row in rows:
        now = datetime.fromisoformat(row["freeze_now"])
        os.environ["FREEZE_NOW"] = row["freeze_now"]
        uid, _ = await sb.clone()
        state = DialogState(session_id=f"adv-{row['id']}-{uuid.uuid4().hex[:6]}", user_id=uid)
        err = None
        try:
            for t in row["turns"][:-1]:
                await runner.turn(t, state, now)
            reply = await runner.turn(row["turns"][-1], state, now)
        except Exception as e:  # сценарий не должен ронять прогон
            reply, err = AssistantReply(text="", action="none"), f"{type(e).__name__}: {e}"
        checks, orc = check(row, reply, now)
        if err:
            orc.append(f"O0 исключение {err}")
        ok_exp = all(checks.values())
        out.append(
            {
                "id": row["id"],
                "task": row["task"],
                "axis": row["axis"],
                "turns": row["turns"],
                "ok": ok_exp and not orc,
                "ok_expect": ok_exp,
                "failed": [k for k, v in checks.items() if not v],
                "oracles": orc,
                "intent": (reply.debug or {}).get("intent"),
                "source": (reply.debug or {}).get("source"),
                "action": reply.action,
                "reply": reply.text[:200],
                "titles": [i.title for i in reply.items[:3]],
            }
        )
    return out


def _pct(v: list[bool]) -> str:
    return f"{sum(v) / len(v) * 100:.0f}%" if v else "—"


def render(res: list[dict]) -> str:
    axes = sorted({r["axis"] for r in res})
    tasks = [t for t in TASKS if any(r["task"] == t for r in res)]
    total = [r["ok"] for r in res]
    out = [
        f"## Состязательные реплики: {sum(total)}/{len(total)} ({_pct(total)}) прошли целиком",
        "",
        "Целиком = ожидания чата 9 (`expect`) + оракулы (название без мусора, время в ответе, "
        "прошлое ≠ будущее, без исключений).",
        "",
        "| Задача | Целиком | Только expect | N |",
        "|---|---:|---:|---:|",
    ]
    for t in tasks:
        rr = [r for r in res if r["task"] == t]
        out.append(
            f"| {TASK_RU[t]} | {_pct([r['ok'] for r in rr])} | "
            f"{_pct([r['ok_expect'] for r in rr])} | {len(rr)} |"
        )
    out += ["", "| Ось | " + " | ".join(TASK_RU[t] for t in tasks) + " | Всего |"]
    out.append("|---|" + "---:|" * (len(tasks) + 1))
    for a in axes:
        cells = []
        for t in tasks:
            cells.append(_pct([r["ok"] for r in res if r["task"] == t and r["axis"] == a]))
        out.append(
            f"| `{a}` | "
            + " | ".join(cells)
            + f" | {_pct([r['ok'] for r in res if r['axis'] == a])} |"
        )

    orc = Counter(o.split(" ", 1)[0] for r in res for o in r["oracles"])
    exp = Counter(k for r in res for k in r["failed"])
    names = {
        "O0": "исключение",
        "O1": "мусор/пусто в названии",
        "O2": "ответ не называет время",
        "O3": "«прошлое» вернуло будущее",
    }
    out += [
        "",
        "**Чаще всего ломается:** "
        + ", ".join(
            [f"{names.get(k, k)} — {v}" for k, v in orc.most_common()]
            + [f"`{k}` — {v}" for k, v in exp.most_common(6)]
        ),
    ]
    fails = [r for r in res if not r["ok"]]
    if fails:
        out += ["", f"<details><summary>Не прошли: {len(fails)}</summary>", ""]
        by: dict[str, list[dict]] = defaultdict(list)
        for r in fails:
            by[r["task"]].append(r)
        for t in tasks:
            if not by[t]:
                continue
            out.append(f"\n**{TASK_RU[t]}**\n")
            for r in by[t]:
                why = ", ".join(r["failed"] + r["oracles"])
                out.append(
                    f"- `{r['id']}` [{r['axis']}] «{' → '.join(r['turns'])}» — {why} · "
                    f"{r['intent']}/{r['action']} ({r['source']}): {r['reply'][:90]!r}"
                )
        out += ["", "</details>"]
    return "\n".join(out)


def load(task: str | None, only: str | None) -> list[dict]:
    rows: list[dict] = []
    for t in [task] if task else TASKS:
        p = ADV / f"{t}.jsonl"
        if p.exists():
            rows += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if only:
        keep = set(only.split(","))
        rows = [r for r in rows if r["id"] in keep]
    return rows


async def amain(a: argparse.Namespace) -> list[dict]:
    rows = load(a.task, a.only)
    if not rows:
        print("нет сценариев в qa/adversarial", file=sys.stderr)
        return []
    os.environ.setdefault("FREEZE_NOW", rows[0]["freeze_now"])
    cr = ContainerRunner(find_csv(a.csv))
    sb: Any = Sandbox() if cr.postgres else MemorySandbox(cr)
    await sb.setup(find_csv(a.csv))
    try:
        res = await run(rows, cr, sb)
    finally:
        await sb.teardown()
    return res


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.run_adversarial")
    ap.add_argument("--task", choices=TASKS)
    ap.add_argument("--only")
    ap.add_argument("--csv")
    ap.add_argument("--out")
    ap.add_argument("--json")
    ap.add_argument("--min-pass", type=float, default=0.0)
    a = ap.parse_args()
    res = asyncio.run(amain(a))
    if not res:
        return 0
    md = render(res)
    if a.out:
        Path(a.out).write_text(md + "\n", encoding="utf-8")
    else:
        print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    rate = sum(r["ok"] for r in res) / len(res)
    print(f"adversarial: {rate:.1%} ({sum(r['ok'] for r in res)}/{len(res)})", file=sys.stderr)
    return 1 if a.min_pass and rate < a.min_pass else 0


if __name__ == "__main__":
    raise SystemExit(main())

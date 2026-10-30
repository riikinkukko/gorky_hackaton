"""Прогон разговоров целиком (qa/sessions/*.jsonl): каждая реплика + каждая кнопка-подсказка.

    python -m eval.run_sessions                         # все файлы qa/sessions/
    python -m eval.run_sessions qa/sessions/demo.jsonl --strict
    python -m eval.run_sessions --no-chips --only demo-main

Зачем: каверзные реплики (run_adversarial) почти все однофразовые, а люди разговаривают
сессиями — поиск → список → перенос → «нет, в 11» → «отмени». Здесь проверяется КАЖДАЯ реплика:

- `expect` реплики (те же ключи, что в qa/adversarial/README.md), если задан;
- оракулы run_adversarial (название без мусора, время в ответе, прошлое ≠ будущее, исключения);
- O4 «тупик»: «Не нашёл запись «да»», «Не понял» и т. п. там, где сценарий этого не допускает.

Кнопки (по умолчанию включено): для каждого ответа с suggestions сессия проигрывается заново до
этой реплики и нажимается каждая кнопка. Кнопка не должна вести в тупик или к исключению.

Формат строки — одна сессия:
    {"id": "demo-main", "title": "…", "freeze_now": "2026-10-21T14:00:00+03:00",
     "known": "QA-… (чат 1): …",          # необязательно: известный провал, не блокирует --strict
     "turns": [{"say": "Что у меня сегодня?", "expect": {"intent": "list_items"}},
               {"click": "16:30"},          # нажать кнопку из предыдущего ответа
               {"say": "да", "allow_dead_end": true}]}

--strict: код возврата 1, если реплики упали в сессии без `known` или `known`-сессия неожиданно
прошла (снимите пометку). Так сценарий демо блокирует слияние, а известные баги — нет.
Тупики на кнопках пока только в отчёте.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.contracts import AssistantReply, DialogState
from eval.run_adversarial import check
from eval.run_eval import ContainerRunner, MemorySandbox, Sandbox, find_csv

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "qa" / "sessions"

_DEAD_TARGET = re.compile(r"Не нашёл запись «([^»]*)»")
_DEAD_WORDS = {
    "да", "нет", "ой", "ага", "угу", "ок", "окей", "только", "сделать", "его", "её", "ее",
    "это", "этот", "эту", "то", "лучше", "давай", "пусть", "туда", "так", "ну",
}  # fmt: skip
_NOT_UNDERSTOOD = re.compile(r"^(Не понял|Не совсем понял)")


def dead_end(reply: AssistantReply) -> str | None:
    """Ответ-тупик: бот не связал реплику с контекстом или не понял её вовсе."""
    m = _DEAD_TARGET.search(reply.text)
    if m:
        words = re.findall(r"[а-яё]+", m.group(1).lower())
        if not words or all(w in _DEAD_WORDS for w in words) or len(m.group(1)) <= 3:
            return f"O4 тупик: «{reply.text[:70]}»"
    unknown = (reply.debug or {}).get("intent") == "unknown"
    if _NOT_UNDERSTOOD.match(reply.text) or (
        unknown and reply.action == "none" and not reply.items
    ):
        return f"O4 не понял: «{reply.text[:70]}»"
    return None


class Player:
    def __init__(self, runner: ContainerRunner, sb: Any) -> None:
        self.runner, self.sb = runner, sb

    async def start(self, sid: str) -> DialogState:
        uid, _ = await self.sb.clone()
        return DialogState(session_id=f"ses-{sid}-{uuid.uuid4().hex[:6]}", user_id=uid)

    async def say(self, text: str, state: DialogState, now: datetime) -> AssistantReply:
        return await self.runner.turn(text, state, now)

    async def replay(
        self, ses: dict, upto: int, now: datetime
    ) -> tuple[DialogState, AssistantReply | None]:
        """Проиграть реплики [0, upto) в новой среде; вернуть состояние и последний ответ."""
        state, last = await self.start(ses["id"]), None
        for t in ses["turns"][:upto]:
            text = _text_of(t, last)
            if text is None:
                break
            last = await self.say(text, state, now)
        return state, last


def _text_of(turn: dict, last: AssistantReply | None) -> str | None:
    if "say" in turn:
        return str(turn["say"])
    want = str(turn["click"]).lower()
    for s in last.suggestions if last else []:
        if s.lower() == want or want in s.lower():
            return s
    return None


async def run_session(p: Player, ses: dict, chips: bool) -> dict:
    now = datetime.fromisoformat(ses["freeze_now"])
    os.environ["FREEZE_NOW"] = ses["freeze_now"]
    state = await p.start(ses["id"])
    last: AssistantReply | None = None
    turns_out: list[dict] = []
    for i, t in enumerate(ses["turns"]):
        text = _text_of(t, last)
        row: dict[str, Any] = {"n": i + 1, "say": t.get("say") or f"[кнопка] {t['click']}"}
        if text is None:
            row.update(
                ok=False, problems=[f"нет кнопки «{t['click']}» в {last and last.suggestions}"]
            )
            turns_out.append(row)
            break
        try:
            last = await p.say(text, state, now)
        except Exception as e:  # сессия не должна ронять прогон
            row.update(ok=False, problems=[f"O0 исключение {type(e).__name__}: {e}"])
            turns_out.append(row)
            break
        checks, orc = check({"expect": t.get("expect", {})}, last, now)
        problems = [f"expect.{k}" for k, v in checks.items() if not v] + orc
        if not t.get("allow_dead_end") and (d := dead_end(last)):
            problems.append(d)
        dbg = last.debug or {}
        row.update(
            ok=not problems,
            problems=problems,
            intent=dbg.get("intent"),
            action=last.action,
            reply=last.text[:160],
            suggestions=last.suggestions,
        )
        turns_out.append(row)

    chip_fails: list[dict] = []
    if chips:
        for i, row in enumerate(turns_out):
            for s in row.get("suggestions") or []:
                st, prev = await p.replay(ses, i + 1, now)
                if prev is None or s not in prev.suggestions:
                    continue  # ответ при повторе другой (недетерминизм) — пропускаем
                try:
                    r = await p.say(s, st, now)
                except Exception as e:
                    chip_fails.append({"after": i + 1, "chip": s, "why": f"O0 {type(e).__name__}"})
                    continue
                _, orc = check({"expect": {}}, r, now)
                why = [x for x in [dead_end(r), *orc] if x]
                if why:
                    chip_fails.append({"after": i + 1, "chip": s, "why": "; ".join(why)})
    turns_ok = all(r["ok"] for r in turns_out)
    return {
        "id": ses["id"],
        "title": ses.get("title", ""),
        "known": ses.get("known"),
        "ok": turns_ok and not chip_fails,
        "turns_ok": turns_ok,
        "turns": turns_out,
        "chip_fails": chip_fails,
    }


def render(res: list[dict]) -> str:
    n_turns = sum(len(r["turns"]) for r in res)
    ok_turns = sum(t["ok"] for r in res for t in r["turns"])
    chips = sum(len(r["chip_fails"]) for r in res)
    out = [
        f"## Сессии: {sum(r['ok'] for r in res)}/{len(res)} прошли целиком · "
        f"реплик {ok_turns}/{n_turns} · кнопок-тупиков {chips}",
        "",
        "| Сессия | Итог | Реплик ок | Кнопки-тупики | Пометка |",
        "|---|---|---:|---:|---|",
    ]
    for r in res:
        k = sum(t["ok"] for t in r["turns"])
        mark = "✅" if r["turns_ok"] else ("🟡 известно" if r["known"] else "❌")
        if r["turns_ok"] and r["chip_fails"]:
            mark = "✅ реплики · ⚠️ кнопки"
        if r["turns_ok"] and r["known"]:
            mark = "⚠️ прошла — снимите known"
        out.append(
            f"| `{r['id']}` {r['title']} | {mark} | {k}/{len(r['turns'])} | "
            f"{len(r['chip_fails'])} | {r['known'] or ''} |"
        )
    bad = [r for r in res if not r["ok"]]
    if bad:
        out += ["", "<details><summary>Подробности</summary>", ""]
        for r in bad:
            out.append(f"\n**{r['id']}** {r['title']}\n")
            for t in r["turns"]:
                if not t["ok"]:
                    out.append(
                        f"- реплика {t['n']} «{t['say']}» → {t.get('intent')}/{t.get('action')}: "
                        f"{t.get('reply', '')[:100]!r} — {'; '.join(t['problems'])}"
                    )
            for c in r["chip_fails"]:
                out.append(f"- кнопка «{c['chip']}» после реплики {c['after']} — {c['why']}")
        out += ["", "</details>"]
    return "\n".join(out)


def load(paths: list[str], only: str | None) -> list[dict]:
    files = [Path(p) for p in paths] or sorted(SESSIONS.glob("*.jsonl"))
    rows = [
        json.loads(x)
        for f in files
        for x in f.read_text(encoding="utf-8").splitlines()
        if x.strip() and not x.lstrip().startswith("//")
    ]
    if only:
        keep = set(only.split(","))
        rows = [r for r in rows if r["id"] in keep]
    return rows


async def amain(a: argparse.Namespace) -> list[dict]:
    rows = load(a.files, a.only)
    if not rows:
        return []
    os.environ.setdefault("FREEZE_NOW", rows[0]["freeze_now"])
    cr = ContainerRunner(find_csv(a.csv))
    sb: Any = Sandbox() if cr.postgres else MemorySandbox(cr)
    await sb.setup(find_csv(a.csv))
    try:
        p = Player(cr, sb)
        return [await run_session(p, s, chips=not a.no_chips) for s in rows]
    finally:
        await sb.teardown()


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.run_sessions")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--only")
    ap.add_argument("--csv")
    ap.add_argument("--out")
    ap.add_argument("--json")
    ap.add_argument("--no-chips", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    res = asyncio.run(amain(a))
    if not res:
        print("нет сессий", file=sys.stderr)
        return 0
    md = render(res)
    if a.out:
        Path(a.out).write_text(md + "\n", encoding="utf-8")
    else:
        print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    # блокируют только реплики; тупики на кнопках — в отчёте (кнопки переделывает чат 5)
    broken = [r["id"] for r in res if not r["turns_ok"] and not r["known"]]
    fixed = [r["id"] for r in res if r["turns_ok"] and r["known"]]
    print(f"sessions: {sum(r['ok'] for r in res)}/{len(res)}; broken={broken} fixed={fixed}",
          file=sys.stderr)  # fmt: skip
    return 1 if a.strict and (broken or fixed) else 0


if __name__ == "__main__":
    raise SystemExit(main())

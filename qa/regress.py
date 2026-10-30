"""Перепроверка регрессии после новой версии nlu / dialog / web.

    cd backend && .venv/bin/python ../qa/regress.py                      # текущее дерево
    .venv/bin/python ../qa/regress.py --merge nlu/v3 dialog/v3           # main + ветки (worktree)
    .venv/bin/python ../qa/regress.py --merge nlu/v3 --update-known      # снять/поставить known

Что делает:
1. С ``--merge``: временный git worktree от origin/main, в него сливаются указанные ветки
   (``git merge --no-edit``; конфликт — стоп с перечнем файлов). Набор qa/live, тесты e2e и
   eval/asr берутся из текущей ветки (qa/v1) — проверяем чужой код нашими ожиданиями.
2. Каждая реплика из qa/live/*.jsonl проходит через /ws/voice в свежем приложении.
3. Таблица: прошла / не прошла, и что изменилось относительно пометки known:
   «починено» (known, а прошла) и «регрессия» (не known, а не прошла).
   Плюс pytest backend/tests/e2e целиком.
4. ``--update-known`` переписывает known в наборе по результату (только для main-прогона
   или после мержа веток в main — иначе known отражает чужую ветку).
5. ``--web``: статическая проверка фронта на pre-roll / post-roll (класс D): см. web_check().

Отчёт — qa/runs/regress-<метка>.md. Код возврата 1, если есть регрессии.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARRY = ["qa/live", "backend/tests/e2e", "backend/eval/asr"]  # наша зона — в любой worktree


def sh(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check)


def prepare_tree(branches: list[str], keep: bool) -> Path:
    for b in ["main", *branches]:
        sh(["git", "fetch", "-q", "origin", f"+refs/heads/{b}:refs/remotes/origin/{b}"], ROOT)
    tmp = Path(tempfile.mkdtemp(prefix="qa-regress-"))
    tree = tmp / "tree"
    sh(["git", "worktree", "add", "-q", "--detach", str(tree), "origin/main"], ROOT)
    env = {"GIT_AUTHOR_NAME": "qa", "GIT_AUTHOR_EMAIL": "qa@local",
           "GIT_COMMITTER_NAME": "qa", "GIT_COMMITTER_EMAIL": "qa@local"}  # fmt: skip
    for b in branches:
        r = subprocess.run(
            ["git", "merge", "--no-edit", "-q", f"origin/{b}"],
            cwd=tree, text=True, capture_output=True, env={**os.environ, **env},
        )  # fmt: skip
        if r.returncode != 0:
            conflicts = sh(["git", "diff", "--name-only", "--diff-filter=U"], tree, False).stdout
            if not keep:
                cleanup(tree)
            sys.exit(f"конфликт при слиянии {b}:\n{conflicts or r.stderr}")
    for rel in CARRY:
        src, dst = ROOT / rel, tree / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return tree


def cleanup(tree: Path) -> None:
    sh(["git", "worktree", "remove", "--force", str(tree)], ROOT, check=False)
    shutil.rmtree(tree.parent, ignore_errors=True)


RUNNER = r"""
import json, sys
sys.path.insert(0, ".")
from tests.e2e.live import load_cases, run_case, check, backend_name
out = []
for c in load_cases():
    try:
        o = run_case(c)
        bad = check(c, o)
        summary = o.summary()
    except Exception as exc:  # падение — тоже результат
        bad, summary = [f"исключение: {type(exc).__name__}: {exc}"], ""
    out.append({"id": c["id"], "set": c["_set"], "cls": c["cls"], "owner": c.get("owner"),
                "qa": c.get("qa"), "text": c["text"],
                "known": bool(c.get("known")) or backend_name() in c.get("known_on", []),
                "known_on": c.get("known_on", []),
                "ok": not bad, "problems": bad, "summary": summary})
print("@@JSON@@" + json.dumps(out, ensure_ascii=False))
"""


def run_live(tree: Path, python: str) -> list[dict]:
    r = subprocess.run(
        [python, "-c", RUNNER], cwd=tree / "backend", text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(tree / "backend")},
    )  # fmt: skip
    m = re.search(r"@@JSON@@(.*)$", r.stdout, re.S)
    if not m:
        sys.exit(f"прогон набора упал:\n{r.stderr[-3000:]}")
    return json.loads(m.group(1))


def run_pytest(tree: Path, python: str) -> tuple[str, list[str], list[str]]:
    """→ (итоговая строка, XPASS — находки, которые починены, FAILED — что сломалось)."""
    r = subprocess.run(
        [python, "-m", "pytest", "tests/e2e", "-q", "-rxXf", "-p", "no:cacheprovider"],
        cwd=tree / "backend", text=True, capture_output=True,
    )  # fmt: skip
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    tail = lines[-1] if lines else "?"

    def names(prefix: str) -> list[str]:
        out = []
        for ln in lines:
            if ln.startswith(prefix):
                name = ln[len(prefix) :].split(" - ")[0].strip()
                out.append(re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m[1], 16)), name))
        return out

    return tail, names("XPASS "), names("FAILED ")


def web_check(tree: Path) -> list[tuple[str, bool, str]]:
    """Класс D по коду фронта: есть ли pre-roll и post-roll ≥ 300 мс.

    Ищем константы PREROLL_MS / POSTROLL_MS во всём frontend/src (сейчас — audio/micEngine.ts).
    Если их нет — старая эвристика: getUserMedia на каждое нажатие в useRecorder.start() и
    rec.stop() сразу при отпускании. Статическая проверка грубая; точный ответ даёт
    qa/DEVICE_CHECK.md на телефонах."""
    src = tree / "frontend/src"
    if not src.exists():
        return [("frontend/src", False, "папки нет")]
    code = {p: p.read_text(encoding="utf-8") for p in src.rglob("*.ts*")}
    rel = {p: p.relative_to(tree).as_posix() for p in code}

    def const(name: str) -> tuple[int, str] | None:
        for p, text in code.items():
            m = re.search(rf"\b{name}\s*=\s*(\d+)", text)
            if m:
                return int(m[1]), rel[p]
        return None

    out = []
    for label, name in (("pre-roll", "PREROLL_MS"), ("post-roll", "POSTROLL_MS")):
        found = const(name)
        if found:
            ms, where = found
            out.append((f"{label} ≥ 300 мс", ms >= 300, f"{name} = {ms} ({where})"))
        else:
            out.append((f"{label} ≥ 300 мс", False, f"{name} не найден во frontend/src"))
    return out


def render(
    label: str, res: list[dict], pt: tuple[str, list[str], list[str]], web: list | None
) -> str:
    pytest_line, xpass, failed = pt
    fixed = [r for r in res if r["known"] and r["ok"]]
    regress = [r for r in res if not r["known"] and not r["ok"]]
    still = [r for r in res if r["known"] and not r["ok"]]
    n_ok = sum(r["ok"] for r in res)
    lines = [
        f"# Регрессия: {label}",
        "",
        f"{datetime.now():%Y-%m-%d %H:%M} · реплик {len(res)} · прошли {n_ok} · "
        f"**починено {len(fixed)}** · **регрессий {len(regress)}** · ещё открыто {len(still)}",
        "",
        f"pytest tests/e2e: `{pytest_line}`",
        "",
    ]
    if xpass:
        lines += ["Починено среди находок QA-01…07 (XPASS — снять xfail):", ""]
        lines += [f"- `{x.split('::')[-1]}`" for x in xpass if "test_live_phrase" not in x]
        lines.append("")
    if failed:
        lines += ["Упали тесты e2e:", ""] + [f"- `{x.split('::')[-1]}`" for x in failed] + [""]
    by_cls: dict[str, list[int]] = {}
    for r in res:
        by_cls.setdefault(r["cls"], [0, 0])
        by_cls[r["cls"]][0] += r["ok"]
        by_cls[r["cls"]][1] += 1
    lines += ["| Класс | Прошли |", "|---|---|"]
    lines += [f"| {k} | {v[0]}/{v[1]} |" for k, v in sorted(by_cls.items())]
    for title, rows in (("Регрессии", regress), ("Починено", fixed), ("Ещё открыто", still)):
        if not rows:
            continue
        lines += ["", f"## {title}", "", "| id | Кл. | Находка | Реплика | Что вышло | Почему |",
                  "|---|---|---|---|---|---|"]  # fmt: skip
        for r in rows:
            why = "; ".join(r["problems"]) or "—"
            lines.append(
                f"| {r['id']} | {r['cls']} | {r['qa']} · {r['owner']} | {r['text']} | "
                f"{r['summary'].replace('|', '/')[:110]} | {why.replace('|', '/')[:160]} |"
            )
    if web is not None:
        lines += [
            "",
            "## Фронт (класс D, по коду)",
            "",
            "| Проверка | Итог | Детали |",
            "|---|---|---|",
        ]
        lines += [f"| {n} | {'✓' if ok else '✗'} | {d} |" for n, ok, d in web]
    return "\n".join(lines) + "\n"


def update_known(res: list[dict]) -> int:
    state = {(r["set"], r["id"]): not r["ok"] for r in res}
    changed = 0
    for path in sorted((ROOT / "qa/live").glob("*.jsonl")):
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        for row in rows:
            k = (path.stem, row["id"])
            if row.get("known_on"):
                continue  # пометка зависит от хранилища — правится руками
            if k in state and bool(row.get("known")) != state[k]:
                changed += 1
                if state[k]:
                    row["known"] = True
                else:
                    row.pop("known", None)
        path.write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8"
        )
    return changed


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--merge", nargs="*", default=None, help="ветки поверх origin/main")
    p.add_argument("--update-known", action="store_true")
    p.add_argument("--web", action="store_true", help="добавить проверку фронта (pre/post-roll)")
    p.add_argument("--keep", action="store_true", help="не удалять worktree")
    p.add_argument("--python", default=sys.executable)
    p.add_argument(
        "--pg",
        metavar="URL",
        help="живой набор на PostgreSQL: отдельная база с «qa» в имени "
        "(postgresql+psycopg://…/qa_live), записи пользователей 2/3 там стираются",
    )
    a = p.parse_args()
    if a.pg:
        os.environ["QA_LIVE_DATABASE_URL"] = a.pg  # уходит в подпроцессы run_live и pytest

    if a.merge is not None:
        tree = prepare_tree(a.merge, a.keep)
        label = "main + " + " + ".join(a.merge) if a.merge else "origin/main"
    else:
        tree = ROOT
        head = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], ROOT).stdout.strip()
        label = f"дерево {head}"
    if a.pg:
        label += " · postgres"
    try:
        res = run_live(tree, a.python)
        pt = run_pytest(tree, a.python)
        web = web_check(tree) if a.web or (a.merge and any("web" in b for b in a.merge)) else None
    finally:
        if tree != ROOT and not a.keep:
            cleanup(tree)
    text = render(label, res, pt, web)
    out = ROOT / "qa/runs" / f"regress-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"→ {out.relative_to(ROOT)}", file=sys.stderr)
    if a.update_known:
        print(f"known обновлено у {update_known(res)} реплик", file=sys.stderr)
    return 1 if pt[2] or any(not r["known"] and not r["ok"] for r in res) else 0


if __name__ == "__main__":
    raise SystemExit(main())

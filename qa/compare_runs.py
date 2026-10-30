"""Сравнение двух прогонов run_voice.py на одних и тех же файлах.

    python qa/compare_runs.py qa/runs/fp32 qa/runs/int8          # насколько int8 хуже fp32
    python qa/compare_runs.py qa/runs/int8-wav qa/runs/int8-post04  # что даёт post-roll 0,4 с

Печатает (и пишет в <B>/compare_<A>.md): WER/CER обоих, сколько фраз стали лучше/хуже по
WER, сколько ошибок исчезло и появилось (по классам), список изменившихся транскриптов.
Для post-roll сравнивайте с прогоном --as-wav, а не с исходным webm: так разница — только тишина.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from eval.asr.wer import corpus  # noqa: E402


def load(d: Path) -> dict[str, dict]:
    return {
        (r := json.loads(line))["file"]: r
        for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    da, db = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = load(da), load(db)
    common = sorted(set(a) & set(b))
    if not common:
        print("нет общих файлов")
        return 1
    wa, ca = corpus([(a[f]["ref"], a[f]["hyp"]) for f in common])
    wb, cb = corpus([(b[f]["ref"], b[f]["hyp"]) for f in common])
    better = [f for f in common if b[f]["wer"] < a[f]["wer"]]
    worse = [f for f in common if b[f]["wer"] > a[f]["wer"]]
    fixed = [f for f in common if a[f]["cls"] != "OK" and b[f]["cls"] == "OK"]
    broke = [f for f in common if a[f]["cls"] == "OK" and b[f]["cls"] != "OK"]
    ent = {
        k: sum(r[f]["ent_hit"] for f in common) / max(sum(r[f]["ent_total"] for f in common), 1)
        for k, r in (("a", a), ("b", b))
    }
    ok = {k: sum(r[f]["cls"] == "OK" for f in common) for k, r in (("a", a), ("b", b))}
    n = len(common)
    lines = [
        f"# {db.name} против {da.name}",
        "",
        f"Общих файлов: {n}",
        "",
        f"| | {da.name} | {db.name} | Δ |",
        "|---|---|---|---|",
        f"| WER | {pct(wa)} | {pct(wb)} | {(wb - wa) * 100:+.1f} п. п. |",
        f"| CER | {pct(ca)} | {pct(cb)} | {(cb - ca) * 100:+.1f} п. п. |",
        f"| Сущности | {pct(ent['a'])} | {pct(ent['b'])} | {(ent['b'] - ent['a']) * 100:+.1f} п. п. |",  # noqa: E501
        f"| Успех | {ok['a']}/{n} | {ok['b']}/{n} | {ok['b'] - ok['a']:+d} |",
        "",
        f"Фраз с WER лучше: {len(better)}, хуже: {len(worse)}. "
        f"Ошибок исчезло: {len(fixed)}, появилось: {len(broke)}.",
        "",
    ]
    by_cls: dict[str, int] = {}
    for f in fixed:
        by_cls[a[f]["cls"]] = by_cls.get(a[f]["cls"], 0) + 1
    if by_cls:
        lines.append(
            "Исчезли по классам: " + ", ".join(f"{k} — {v}" for k, v in sorted(by_cls.items()))
        )
        lines.append("")
    changed = [f for f in common if a[f]["hyp"] != b[f]["hyp"]]
    if changed:
        lines += ["| Файл | " + da.name + " | " + db.name + " |", "|---|---|---|"]
        for f in changed:
            lines.append(f"| {f} | {a[f]['hyp']} ({a[f]['cls']}) | {b[f]['hyp']} ({b[f]['cls']}) |")
    text = "\n".join(lines) + "\n"
    (db / f"compare_{da.name}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

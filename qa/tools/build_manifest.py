"""Собирает qa/audio/manifest.csv из файлов в qa/audio/.

    python qa/tools/build_manifest.py            # после распаковки архивов из рекордера

Имя файла задаёт всё: ``<speaker>__<device>__<env>__<mode>__<qid>.<ext>``, например
``artem__phone__noise__app__q10.m4a``. Так же называйте записи из диктофона телефона
(mode = ``memo``). Подпапки допускаются. Эталонный текст берётся из qa/phrases.jsonl.
Колонка ``skip`` (любое значение) исключает файл из прогона; существующие пометки
skip и note при пересборке сохраняются.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIO = ROOT / "qa/audio"
OUT = AUDIO / "manifest.csv"
EXTS = {".webm", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".mp3", ".aac", ".3gp", ".amr"}
COLS = ["file", "qid", "ref", "speaker", "device", "env", "mode", "skip", "note"]


def main() -> int:
    phrases = {
        (p := json.loads(line))["qid"]: p
        for line in (ROOT / "qa/phrases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    old: dict[str, dict] = {}
    if OUT.exists():
        with open(OUT, encoding="utf-8-sig", newline="") as f:
            old = {d["file"]: d for d in csv.DictReader(f)}
    rows, bad = [], []
    for path in sorted(AUDIO.rglob("*")):
        if path.suffix.lower() not in EXTS:
            continue
        rel = path.relative_to(AUDIO).as_posix()
        parts = path.stem.split("__")
        if len(parts) != 5 or parts[4] not in phrases:
            bad.append(rel)
            continue
        speaker, device, env, mode, qid = parts
        prev = old.get(rel, {})
        rows.append(
            {
                "file": rel,
                "qid": qid,
                "ref": phrases[qid]["say"],
                "speaker": speaker,
                "device": device,
                "env": env,
                "mode": mode,
                "skip": prev.get("skip", ""),
                "note": prev.get("note", ""),
            }
        )
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    speakers = sorted({r["speaker"] for r in rows})
    print(f"{OUT.relative_to(ROOT)}: {len(rows)} файлов, дикторы: {', '.join(speakers) or '—'}")
    if bad:
        print(
            f"не по шаблону <speaker>__<device>__<env>__<mode>__<qid>: {bad[:10]}", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

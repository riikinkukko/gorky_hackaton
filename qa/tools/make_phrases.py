"""Собирает qa/phrases.jsonl и список фраз в qa/recorder.html из backend/eval/golden_eval.jsonl.

    python qa/tools/make_phrases.py

Берём строки, которые человек естественно произносит: без history (контекст отдельной
сессии) и без тегов, где ошибка заложена в само написание (asr_noise, typo). Первая партия
(batch=1) — 20 фраз с именами, организациями и временем: на них ошибается распознавание.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "backend/eval/golden_eval.jsonl"
OUT = ROOT / "qa/phrases.jsonl"

# Имена и организации из CSV организаторов (10 + 8) и из эталона
PEOPLE = "Иван Мария Елена Максим Анна Алексей Наталья Дмитрий Ольга Сергей Пётр".split()
ORGS = "Альфа Вектор Север Орбита Маяк Старт Пульс Сигма".split()

BATCH1 = [
    "q01", "q04", "q07", "q10", "q19", "q22", "q24", "q25", "q31", "q34",
    "q35", "q40", "q42", "q44", "q47", "q48", "q63", "q78", "q80", "q89",
]  # fmt: skip
SKIP_TAGS = {"asr_noise", "typo"}


def entities(text: str) -> list[str]:
    """Имена и организации в любом падеже: совпадение по первым 4 буквам основы."""
    found = []
    words = re.findall(r"[А-ЯЁа-яё]+", text)
    for name in PEOPLE + ORGS:
        stem = name.lower().replace("ё", "е")[:4]
        if any(w.lower().replace("ё", "е").startswith(stem) and w[0].isupper() for w in words):
            found.append(name)
    for abbr in ("CRM", "ТЗ", "КП"):
        if re.search(rf"\b{abbr}\b", text):
            found.append(abbr)
    return found


def main() -> None:
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]
    out = []
    for r in rows:
        if r.get("history") and r["qid"] not in BATCH1:
            continue
        if SKIP_TAGS & set(r.get("tags", [])) and r["qid"] not in BATCH1:
            continue
        say = r["query"].strip()
        if say.endswith("?") or say.endswith("!"):
            say = say[:-1]
        out.append(
            {
                "pid": r["qid"].replace("q", "p"),
                "qid": r["qid"],
                "say": say,
                "entities": entities(r["query"]),
                "batch": 1 if r["qid"] in BATCH1 else 2,
            }
        )
    # партия 1 первой — её запись занимает ~3 минуты
    out.sort(key=lambda p: (p["batch"], int(p["qid"][1:])))
    OUT.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in out), encoding="utf-8")
    # те же фразы — в рекордер (между маркерами PHRASES:BEGIN / PHRASES:END)
    rec = ROOT / "qa/recorder.html"
    html = rec.read_text(encoding="utf-8")
    head, rest = html.split("// PHRASES:BEGIN", 1)
    _, tail = rest.split("// PHRASES:END", 1)
    js = json.dumps(out, ensure_ascii=False)
    rec.write_text(
        f"{head}// PHRASES:BEGIN — генерирует qa/tools/make_phrases.py\nconst ALL = {js};\n"
        f"// PHRASES:END{tail}",
        encoding="utf-8",
    )
    b1 = sum(p["batch"] == 1 for p in out)
    print(f"{OUT.relative_to(ROOT)}: {len(out)} фраз, партия 1 — {b1}")


if __name__ == "__main__":
    main()

"""Симулированные транскрипты для первой картины, пока нет живых записей → qa/sim/hyp_v1.csv.

    python qa/tools/make_sim_hyps.py

Варианты (clean и asr — партия 1, words — все фразы):
  clean  — эталон как есть: проверка, что путь через /ws/voice даёт то же, что /api/chat;
  words  — без цифр и пунктуации, строчными («пятнадцать тридцать», «срм»): так пишут
           v3_rnnt / v3_ctc без ITN и SpeechKit без нормализации;
  asr    — по одной типовой ошибке GigaAM на фразу: склейка/разрыв имени, «15 00» вместо
           «15:00», лишние «э-э», потерянный предлог. Ошибки взяты из наблюдений чата 4
           и из документа 08 («вектором» → «в лектором», «с Орбитой» → «с арбитой»).
Это НЕ замер распознавания: WER симуляции в отчёт как WER модели не ставится.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.voice.text_norm import normalize_for_wer  # noqa: E402

ASR = {
    "q01": "Напомни мне завтра в 10 утра позвонить и вану.",
    "q04": "Добавь встречу с командой завтра в 15 00.",
    "q07": "Послезавтра в девять созвон с альфой по СРМ.",
    "q10": "Поставь созвон с маяком 29 октября в 15 30.",
    "q19": "Когда у меня была встреча Иваном?",
    "q22": "Когда я последний раз звонил Сергей?",
    "q24": "Что у нас было в лектором?",
    "q25": "Что я должен был отправить Марине?",
    "q31": "Перенеси встречу с Марией в субботу на 19.",
    "q34": "Удали напоминания позвонить Ивану на завтра.",
    "q35": "Слушай, закинь на завтра в полдень созвон с вектором.",
    "q40": "Завтра без 15 10 созвон с пульсом.",
    "q42": "В среду в 18 созвон с сервером.",
    "q44": "В следующую среду в 11 демо для альфы.",
    "q47": "Встреча с Дмитрием 5 ноября в 14 30 на полтора часа.",
    "q48": "Добавь задачу подготовить т з для Вектора.",
    "q63": "Ну короче напомни это самое завтра в 8 отправить протокол Елене.",
    "q78": "Когда ближайший созвон с Альфа?",
    "q80": "Что я обсуждал с Алёной?",
    "q89": "Перенеси созвон с арбитой на 16:30.",
}


def words(text: str) -> str:
    t = normalize_for_wer(text)
    return t.replace("crm", "срм").replace(" тз ", " тэзэ ")


def main() -> None:
    phrases = [
        json.loads(line)
        for line in (ROOT / "qa/phrases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = {
        (r := json.loads(line))["qid"]: r
        for line in (ROOT / "backend/eval/golden_eval.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    out = ROOT / "qa/sim/hyp_v1.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["variant", "qid", "ref", "hyp"])
        w.writeheader()
        for p in phrases:
            q, ref = p["qid"], golden[p["qid"]]["query"]
            if p["batch"] == 1:
                w.writerow({"variant": "clean", "qid": q, "ref": ref, "hyp": ref})
            # «словами» — на всех фразах: дешёвый широкий срез класса B
            w.writerow({"variant": "words", "qid": q, "ref": ref, "hyp": words(ref)})
            if q in ASR:
                w.writerow({"variant": "asr", "qid": q, "ref": ref, "hyp": ASR[q]})
    print(out.relative_to(ROOT))


if __name__ == "__main__":
    main()

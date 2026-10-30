"""Бенчмарк STT: WER, CER, распознавание ключевых слов, задержка, память.

Записи — ``<папка>/<спикер>__<pid>.<webm|ogg|wav|mp4>`` (pid из ``phrases.jsonl``).
Удобнее всего записывать страницей ``recorder.html``: она даёт ровно такие имена и тот же кодек,
что и прод (MediaRecorder, webm/opus).

    python -m app.voice.bench.run_bench --audio-dir recordings \
        --providers gigaam,gigaam:v3_ctc,whisper:small,speechkit --out bench_report.md

Каждый провайдер гоняется в отдельном процессе: так честно меряется пиковая память
и модели не мешают друг другу. Результат — markdown-таблица (для README) + CSV по каждой фразе.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHRASES = HERE / "phrases.jsonl"
AUDIO_EXT = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
}


@dataclass
class Utt:
    file: str
    speaker: str
    pid: str
    ref: str
    hyp: str = ""
    audio_s: float = 0.0
    decode_ms: float = 0.0
    stt_ms: float = 0.0
    cpu_ms: float = 0.0
    error: str = ""


# ---------------------------------------------------------------- метрики


def _edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def wer_cer(pairs: list[tuple[str, str]]) -> tuple[float, float]:
    """Корпусные WER и CER (сумма ошибок / сумма слов эталона) после normalize_for_wer."""
    from app.voice.text_norm import normalize_for_wer

    we = wn = ce = cn = 0
    for ref, hyp in pairs:
        r, h = normalize_for_wer(ref), normalize_for_wer(hyp)
        we += _edit_distance(r.split(), h.split())
        wn += len(r.split())
        ce += _edit_distance(list(r.replace(" ", "")), list(h.replace(" ", "")))
        cn += len(r.replace(" ", ""))
    return we / max(wn, 1), ce / max(cn, 1)


def keyword_recall(utts: list[Utt], keywords: dict[str, list[str]]) -> float:
    """Доля ключевых слов (имена, компании, дни), распознанных хотя бы с тем же корнем.

    Корень — первые 4 буквы: «Иваном» и «Ивану» засчитываются за «Иван»."""
    from app.voice.text_norm import normalize_for_wer

    hit = total = 0
    for u in utts:
        words = normalize_for_wer(u.hyp).split()
        for kw in keywords.get(u.pid, []):
            stem = normalize_for_wer(kw)[:4]
            total += 1
            hit += any(w.startswith(stem) for w in words)
    return hit / max(total, 1)


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ------------------------------------------------- воркер: один провайдер в отдельном процессе


def _apply_spec(spec: str) -> str:
    """``gigaam:v3_ctc`` → STT_PROVIDER=gigaam, GIGAAM_MODEL=v3_ctc. Возвращает имя провайдера."""
    name, _, variant = spec.partition(":")
    os.environ["STT_PROVIDER"] = name
    if variant:
        os.environ[{"gigaam": "GIGAAM_MODEL", "whisper": "WHISPER_MODEL"}.get(name, "_UNUSED")] = (
            variant
        )
    return name


async def _worker(spec: str, files: list[Utt], out: Path) -> None:
    from app.voice.audio import decode_to_pcm16k
    from app.voice.config import reload_settings
    from app.voice.factory import build_stt

    _apply_spec(spec)
    settings = reload_settings()
    stt = build_stt(settings)
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    await stt.load()
    load_s = time.perf_counter() - t0
    for u in files:
        try:
            data = await asyncio.to_thread(Path(u.file).read_bytes)
            ta = time.perf_counter()
            pcm = await decode_to_pcm16k(data, AUDIO_EXT[Path(u.file).suffix.lower()])
            tb = time.perf_counter()
            cpu0 = time.process_time()
            u.hyp = await stt.transcribe_pcm(pcm)
            u.cpu_ms = (time.process_time() - cpu0) * 1000
            u.stt_ms = (time.perf_counter() - tb) * 1000
            u.decode_ms = (tb - ta) * 1000
            u.audio_s = pcm.duration_s
        except Exception as exc:  # одна битая запись не должна валить весь прогон
            u.error = f"{type(exc).__name__}: {exc}"[:200]
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: КБ
    await asyncio.to_thread(
        out.write_text,
        json.dumps(
            {
                "spec": spec,
                "local": stt.local,
                "load_s": load_s,
                "rss_before_mb": rss0 / 1024,
                "peak_rss_mb": peak_mb,
                "threads": settings.torch_threads,
                "utts": [asdict(u) for u in files],
            },
            ensure_ascii=False,
        ),
    )


# ---------------------------------------------------------------- сбор


def load_phrases() -> dict[str, dict]:
    return {
        r["pid"]: r for r in map(json.loads, PHRASES.read_text(encoding="utf-8").splitlines()) if r
    }


def discover(audio_dir: Path, phrases: dict[str, dict]) -> list[Utt]:
    utts = []
    for path in sorted(audio_dir.rglob("*")):
        if path.suffix.lower() not in AUDIO_EXT or "__" not in path.stem:
            continue
        speaker, pid = path.stem.rsplit("__", 1)
        if pid in phrases:
            utts.append(Utt(file=str(path), speaker=speaker, pid=pid, ref=phrases[pid]["say"]))
    return utts


def summarize(result: dict, keywords: dict[str, list[str]]) -> dict:
    utts = [Utt(**u) for u in result["utts"]]
    ok = [u for u in utts if not u.error]
    w, c = wer_cer([(u.ref, u.hyp) for u in ok])
    lat = [u.decode_ms + u.stt_ms for u in ok]
    rtf = [u.stt_ms / 1000 / u.audio_s for u in ok if u.audio_s > 0]
    cores = [u.cpu_ms / u.stt_ms for u in ok if u.stt_ms > 0]
    long_ = [u.decode_ms + u.stt_ms for u in ok if u.audio_s >= 4.0]
    decode_p50 = statistics.median([u.decode_ms for u in ok]) if ok else 0
    return {
        "spec": result["spec"],
        "n": len(utts),
        "errors": len(utts) - len(ok),
        "wer": w,
        "cer": c,
        "kw": keyword_recall(ok, keywords),
        "p50": pct(lat, 0.5),
        "p95": pct(lat, 0.95),
        "p95_long": pct(long_, 0.95) if long_ else float("nan"),
        "est_6s": (statistics.median(rtf) * 6 * 1000 + decode_p50) if rtf else float("nan"),
        "rtf": statistics.median(rtf) if rtf else float("nan"),
        "cores": statistics.median(cores) if cores else 0,
        "load_s": result["load_s"],
        "rss": result["peak_rss_mb"],
        "local": result["local"],
        "audio_mean": statistics.mean([u.audio_s for u in ok]) if ok else 0,
    }


def render(rows: list[dict], n_speakers: int, n_files: int) -> str:
    def ms(v: float) -> str:
        return "—" if v != v else f"{v / 1000:.2f} с"

    lines = [
        f"Записей: {n_files}, спикеров: {n_speakers}, средняя длина "
        f"{statistics.mean(r['audio_mean'] for r in rows):.1f} с. Время — decode + STT на CPU, "
        "без сети браузер→сервер. WER/CER после нормализации "
        "(регистр, пунктуация, ё, цифры → слова).",
        "",
        "| Модель | WER | CER | Ключевые слова | p50 | p95 | p95 (фразы ≥ 4 с) | оценка для 6 с "
        "| RTF | ядер CPU | RAM пик | загрузка | ошибки |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cpu = f"{r['cores']:.1f}" if r["local"] else "облако"
        ram = f"{r['rss']:.0f} МБ" if r["local"] else f"{r['rss']:.0f} МБ (клиент)"
        lines.append(
            f"| `{r['spec']}` | **{r['wer'] * 100:.1f}%** | {r['cer'] * 100:.1f}% "
            f"| {r['kw'] * 100:.0f}% | {ms(r['p50'])} | {ms(r['p95'])} | {ms(r['p95_long'])} "
            f"| {ms(r['est_6s'])} | {r['rtf']:.3f} | {cpu} | "
            f"{ram} | {r['load_s']:.1f} с | {r['errors']}/{r['n']} |"
        )
    lines += [
        "",
        "Цели: WER ≤ 10%, STT ≤ 1 с на фразу 5–6 с "
        "(столбец «оценка для 6 с» = медианный RTF × 6 + decode).",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--audio-dir", type=Path, required=True)
    ap.add_argument("--providers", default="gigaam,whisper:small,speechkit")
    ap.add_argument("--out", type=Path, default=Path("bench_report.md"))
    ap.add_argument("--limit", type=int, default=0, help="взять только первые N записей")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--worker-out", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()

    phrases = load_phrases()
    utts = discover(args.audio_dir, phrases)
    if args.limit:
        utts = utts[: args.limit]

    if args.worker:
        asyncio.run(_worker(args.worker, utts, args.worker_out))
        return

    if not utts:
        sys.exit(
            f"В {args.audio_dir} нет файлов вида <спикер>__pNN.webm — "
            "запишите их через recorder.html"
        )

    keywords = {pid: p.get("keywords", []) for pid, p in phrases.items()}
    rows, details = [], []
    tmp_dir = args.out.parent / ".bench_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for spec in [s.strip() for s in args.providers.split(",") if s.strip()]:
        out = tmp_dir / f"{spec.replace(':', '_')}.json"
        print(f"→ {spec}: {len(utts)} записей…", flush=True)
        cmd = [
            sys.executable,
            "-m",
            "app.voice.bench.run_bench",
            "--audio-dir",
            str(args.audio_dir),
            "--worker",
            spec,
            "--worker-out",
            str(out),
        ] + (["--limit", str(args.limit)] if args.limit else [])
        proc = subprocess.run(cmd, env=os.environ.copy())
        if proc.returncode != 0 or not out.exists():
            print(f"   {spec}: воркер упал (код {proc.returncode}), пропускаю", file=sys.stderr)
            continue
        result = json.loads(out.read_text())
        rows.append(summarize(result, keywords))
        details += [{"provider": spec, **u} for u in result["utts"]]
        r = rows[-1]
        print(
            f"   WER {r['wer'] * 100:.1f}%  p50 {r['p50']:.0f} мс  "
            f"p95 {r['p95']:.0f} мс  RAM {r['rss']:.0f} МБ"
        )

    if not rows:
        sys.exit("Ни один провайдер не отработал")
    speakers = {u.speaker for u in utts}
    report = render(rows, len(speakers), len(utts))
    args.out.write_text(report + "\n", encoding="utf-8")
    csv_path = args.out.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(details[0].keys()))
        w.writeheader()
        w.writerows(details)
    print("\n" + report + f"\n\nПодробно по фразам: {csv_path}")


if __name__ == "__main__":
    main()

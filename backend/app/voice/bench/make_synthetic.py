"""Синтетические записи фраз бенчмарка через TTS — только для смоук-теста пайплайна.

WER на синтезированной речи заметно оптимистичнее живой: в README такие цифры не ставим.

    python -m app.voice.bench.make_synthetic --tts silero --out recordings_synth
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.voice.bench.run_bench import load_phrases
from app.voice.config import get_settings
from app.voice.factory import build_tts
from app.voice.tts import CachedTTS


async def run(tts_name: str, out: Path) -> None:
    tts = build_tts(get_settings(), tts_name)
    if not isinstance(tts, CachedTTS):
        raise SystemExit("нужен реальный TTS: silero или speechkit")
    await tts.load()
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    for pid, p in load_phrases().items():
        audio, _ = await tts.synthesize(p["say"])
        await asyncio.to_thread((out / f"synth-{tts_name}__{pid}.ogg").write_bytes, audio)
    print(f"готово: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", default="silero")
    ap.add_argument("--out", type=Path, default=Path("recordings_synth"))
    a = ap.parse_args()
    asyncio.run(run(a.tts, a.out))

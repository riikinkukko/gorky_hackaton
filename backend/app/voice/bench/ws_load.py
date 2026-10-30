"""Нагрузка на работающий /ws/voice: память процесса API и время STT.

Внутри контейнера API (там есть ffmpeg и websockets из uvicorn[standard]):

    docker compose exec api python -m app.voice.bench.ws_load --clients 1 --turns 10
    docker compose exec api python -m app.voice.bench.ws_load --clients 3 --turns 5

Шлёт фразу (по умолчанию 5 с розового шума в webm/opus, как MediaRecorder), ждёт reply, берёт
``debug.voice.stt_ms``. Память: RSS процесса uvicorn (``--pid``, по умолчанию 1 — PID API в
контейнере) и, если доступно, ``memory.current`` / ``memory.peak`` cgroup контейнера — именно
с ними сравнивается лимит ``API_MEM_LIMIT``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path

import websockets

CGROUP = Path("/sys/fs/cgroup")


def _proc_mb(pid: int) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key = line.split(":")[0]
            if key in {"VmRSS", "VmHWM", "RssAnon", "RssFile"}:
                out[key] = int(line.split()[1]) // 1024
    except OSError:
        pass
    for name in ("memory.current", "memory.peak"):
        try:
            out[f"cgroup_{name.split('.')[1]}"] = int((CGROUP / name).read_text()) // 2**20
        except (OSError, ValueError):
            pass
    return out


def _audio(seconds: float, path: str | None) -> tuple[bytes, str]:
    if path:
        mime = "audio/ogg" if path.endswith(".ogg") else "audio/webm;codecs=opus"
        return Path(path).read_bytes(), mime
    src = f"anoisesrc=d={seconds}:c=pink:a=0.1:r=48000"
    data = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            src,
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return data, "audio/webm;codecs=opus"


def _p(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


async def run(args: argparse.Namespace) -> None:
    audio, mime = _audio(args.seconds, args.file)
    stt: list[float] = []
    total: list[float] = []

    async def client(i: int) -> None:
        url = f"{args.url}?session_id=load{i}&user_id={args.user_id}"
        async with websockets.connect(url, max_size=None) as ws:
            for _ in range(args.turns):
                t0 = time.perf_counter()
                await ws.send(json.dumps({"type": "start", "mime": mime}))
                for k in range(0, len(audio), 4000):
                    await ws.send(audio[k : k + 4000])
                await ws.send(json.dumps({"type": "end"}))
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        continue  # озвучка предыдущего ответа
                    data = json.loads(msg)
                    if data.get("type") == "reply":
                        stt.append(float(data["debug"]["voice"].get("stt_ms", 0)))
                        total.append((time.perf_counter() - t0) * 1000)
                        break

    before = _proc_mb(args.pid)
    peak = dict(before)

    async def sampler() -> None:
        while True:
            for k, v in _proc_mb(args.pid).items():
                peak[k] = max(peak.get(k, 0), v)
            await asyncio.sleep(0.05)

    task = asyncio.create_task(sampler())
    await asyncio.gather(*(client(i) for i in range(args.clients)))
    task.cancel()
    after = _proc_mb(args.pid)
    print(f"фраза {args.seconds:.0f} с, клиентов {args.clients} × {args.turns} ходов, n={len(stt)}")
    print(f"память до:    {before}")
    print(f"память пик:   {peak}")
    print(f"память после: {after}")
    print(
        f"STT, мс: p50 {statistics.median(stt):.0f}, p95 {_p(stt, 0.95):.0f}  "
        f"(при нескольких клиентах включает ожидание очереди: модель одна)\n"
        f"end → reply, мс: p50 {statistics.median(total):.0f}, p95 {_p(total, 0.95):.0f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="ws://localhost:8000/ws/voice")
    ap.add_argument("--pid", type=int, default=1)
    ap.add_argument("--clients", type=int, default=1)
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--file", help="своя запись вместо шума (.webm/.ogg)")
    ap.add_argument("--user-id", type=int, default=2, help="2 — пустой демо-пользователь")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()

"""int8 против fp32 и эффект post-roll — локально через docker compose (НЕ на стенде).

    python qa/ab_quant.py                     # всё: fp32, int8, int8 WAV, int8 WAV+0,4 с
    python qa/ab_quant.py --only int8         # один вариант
    python qa/ab_quant.py --skip-build        # образы уже собраны

Для каждого варианта модели: сборка api с GIGAAM_QUANTIZE=<none|int8> → up → ждём /health →
qa/run_voice.py по qa/audio/manifest.csv. Для int8 дополнительно два прогона тех же файлов:
--as-wav (контроль) и --postroll 0.4. В конце — qa/compare_runs.py:
  fp32 → int8            насколько квантование хуже;
  int8-wav → int8-post04 сколько ошибок убирает 0,4 с тишины в конце.
Запускать из корня репозитория; Python — venv бэкенда (нужны httpx, websockets).
Сборка int8 квантует веса и пикует ≈ 2 ГБ RAM — на машине разработчика, не на сервере.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker", "compose",
    "-f", "docker-compose.yml", "-f", "docker-compose.override.yml", "-f", "qa/compose.qa.yml",
]  # fmt: skip
BASE = "http://localhost:8080"


def sh(args: list[str], env: dict[str, str] | None = None) -> None:
    print("$", " ".join(args), file=sys.stderr)
    subprocess.run(args, cwd=ROOT, env={**os.environ, **(env or {})}, check=True)


def wait_health(timeout: float = 300) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=5) as r:
                if r.status == 200 and b'"stt":"ok"' in r.read().replace(b" ", b""):
                    return
        except OSError:
            pass
        time.sleep(3)
    sys.exit("api не поднялся за 5 минут: docker compose logs api")


def run_voice(name: str, *extra: str) -> None:
    sh([sys.executable, "qa/run_voice.py", "--base", BASE, "--name", name, *extra])


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", choices=["fp32", "int8"])
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--postroll", type=float, default=0.4)
    a = p.parse_args()
    variants = [("fp32", "none"), ("int8", "int8")]
    if a.only:
        variants = [v for v in variants if v[0] == a.only]
    for name, quant in variants:
        env = {"GIGAAM_QUANTIZE": quant, "TTS_MODELS": "0"}
        if not a.skip_build:
            sh([*COMPOSE, "build", "api"], env)
        sh([*COMPOSE, "up", "-d", "--force-recreate", "api", "db", "caddy", "web"], env)
        wait_health()
        run_voice(name)
        if quant == "int8":
            run_voice(f"{name}-wav", "--as-wav")
            tag = f"{name}-post{str(a.postroll).replace('.', '')}"
            run_voice(tag, "--postroll", str(a.postroll))
    runs = ROOT / "qa/runs"
    if not a.only:
        sh([sys.executable, "qa/compare_runs.py", str(runs / "fp32"), str(runs / "int8")])
    if not a.only or a.only == "int8":
        tag = f"int8-post{str(a.postroll).replace('.', '')}"
        sh([sys.executable, "qa/compare_runs.py", str(runs / "int8-wav"), str(runs / tag)])


if __name__ == "__main__":
    main()

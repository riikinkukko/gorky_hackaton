from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator

import pytest

from app.voice import limits
from app.voice.config import reload_settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

_VOICE_ENV = (
    "VOICE_MAX_CONCURRENCY",
    "VOICE_MAX_TTS_CONCURRENCY",
    "VOICE_QUEUE_TIMEOUT_S",
    "VOICE_MAX_SOCKETS",
    "VOICE_MAX_SOCKETS_PER_SESSION",
    "VOICE_IDLE_TIMEOUT_S",
    "VOICE_MAX_TURNS_PER_MIN",
    "VOICE_PARTIAL_TAIL_S",
    "VOICE_MAX_UTTERANCE_S",
    "STT_PROVIDER",
    "TTS_PROVIDER",
    "VOICE_PARTIALS",
    "VOICE_PARTIAL_INTERVAL_S",
    "TTS_PREWARM",
    "YANDEX_API_KEY",
    "YANDEX_IAM_TOKEN",
    "YANDEX_FOLDER_ID",
    "SPEECHKIT_VOICE",
    "TTS_CACHE_DIR",
)


def make_audio(fmt: str = "webm", seconds: float = 2.0, freq: int = 440) -> bytes:
    """Синус через ffmpeg в нужном контейнере (как MediaRecorder: opus 48 кГц)."""
    codec = {
        "webm": ["-c:a", "libopus"],
        "ogg": ["-c:a", "libopus"],
        "wav": ["-ar", "16000", "-ac", "1"],
        "mp4": ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov"],
    }[fmt]
    src = f"sine=frequency={freq}:duration={seconds}:sample_rate=48000"
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            src,
            *codec,
            "-f",
            fmt,
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return out.stdout


@pytest.fixture(autouse=True)
def _clean_voice_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    for var in _VOICE_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TTS_PREWARM", "0")  # прогрев меряет отдельный тест
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))  # чтобы не подхватить чужой .env
    reload_settings()
    limits.reset()
    yield
    reload_settings()
    limits.reset()

"""Аудио-утилиты на ffmpeg.

decode: контейнер браузера из белого списка → PCM 16 кГц моно; encode: PCM → ogg/opus.

SEC-02 («аудио-бомба»): формат задаётся по mime (``-f``), а не угадывается по содержимому;
длительность режется на входе (``-t``), а не после полного декодирования; ffmpeg читает
только локальный файл (``-protocol_whitelist file,pipe``). 1,8 МБ FLAC-тишины на 2,7 часа
раньше давали 313 МБ PCM и 8 с CPU — теперь ≤ VOICE_MAX_UTTERANCE_S звука или отказ.

Почему через временный файл, а не stdin: MediaRecorder в Safari пишет audio/mp4,
а mp4 без seek ffmpeg разбирает ненадёжно. Файл в tmpfs стоит единицы миллисекунд.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
import wave
from dataclasses import dataclass
from typing import Any

TARGET_SR = 16_000
# mime (без параметров) → (демультиплексор ffmpeg, расширение временного файла)
CONTAINERS: dict[str, tuple[str, str]] = {
    "audio/webm": ("webm", ".webm"),
    "audio/ogg": ("ogg", ".ogg"),
    "audio/mp4": ("mp4", ".mp4"),
    "audio/mpeg": ("mp3", ".mp3"),
    "audio/wav": ("wav", ".wav"),
    "audio/x-wav": ("wav", ".wav"),
    "audio/wave": ("wav", ".wav"),
}
_TMP_DIR = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None


class AudioError(RuntimeError):
    """Не удалось декодировать или закодировать аудио."""


@dataclass(frozen=True)
class Pcm:
    """16-бит little-endian, моно."""

    data: bytes
    sample_rate: int = TARGET_SR

    @property
    def duration_s(self) -> float:
        return len(self.data) / 2 / self.sample_rate

    def to_wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(self.data)
        return buf.getvalue()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def container_for(mime: str) -> tuple[str, str]:
    """(демультиплексор, суффикс) для mime из белого списка, иначе AudioError."""
    base = (mime or "").split(";")[0].strip().lower()
    if base not in CONTAINERS:
        raise AudioError(f"неподдерживаемый формат записи: {base or 'пусто'}")
    return CONTAINERS[base]


def is_supported_mime(mime: str) -> bool:
    return (mime or "").split(";")[0].strip().lower() in CONTAINERS


async def _run_ffmpeg(
    args: list[str],
    stdin: bytes | None = None,
    deadline_s: float = 15.0,
    max_stdout: int | None = None,
) -> bytes:
    """Запустить ffmpeg и вернуть stdout.

    ``max_stdout`` — сколько байт читать максимум (SEC-02). Как только набрали — процесс
    убиваем: даже если ``-t`` почему-то не сработал (битый контейнер, кривые таймстемпы),
    в память попадёт не больше лимита, а не весь файл целиком.
    """
    head = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + (
        ["-nostdin"] if stdin is None else []
    )
    proc = await asyncio.create_subprocess_exec(
        *head,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    stdout, stderr = proc.stdout, proc.stderr
    cut = False

    async def feed() -> None:
        if stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

    async def read_out() -> bytes:
        nonlocal cut
        buf = bytearray()
        while True:
            chunk = await stdout.read(1 << 16)
            if not chunk:
                return bytes(buf)
            buf += chunk
            if max_stdout is not None and len(buf) >= max_stdout:
                cut = True
                proc.kill()
                return bytes(buf[:max_stdout])

    async def read_err() -> bytes:
        tail = b""
        while chunk := await stderr.read(1 << 12):
            tail = (tail + chunk)[-2000:]
        return tail

    try:
        _, out, err = await asyncio.wait_for(
            asyncio.gather(feed(), read_out(), read_err()), timeout=deadline_s
        )
        await proc.wait()
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AudioError("ffmpeg timeout") from exc
    if proc.returncode != 0 and not cut:
        raise AudioError(f"ffmpeg exit {proc.returncode}: {err.decode(errors='replace')[-300:]}")
    return out


async def decode_to_pcm16k(
    audio: bytes, mime: str, *, partial: bool = False, max_seconds: float | None = None
) -> Pcm:
    """Запись браузера → PCM s16le 16 кГц моно, не длиннее ``max_seconds``.

    ``mime`` обязан быть из ``CONTAINERS``, и содержимое обязано ему соответствовать —
    иначе ``AudioError``. ``max_seconds`` по умолчанию — ``VOICE_MAX_UTTERANCE_S``: ``-t`` на входе
    ffmpeg и не больше ``max_seconds × 16000 × 2`` байт из его stdout.
    ``partial=True`` — буфер обрезан посреди записи (промежуточный транскрипт):
    ffmpeg ругается на незаконченный кластер, но отдаёт всё, что успел декодировать.
    """
    if not audio:
        raise AudioError("empty audio")
    demuxer, suffix = container_for(mime)
    limit_s = max_seconds if max_seconds is not None else _default_max_decode_s()
    max_bytes = int(limit_s * TARGET_SR) * 2
    if demuxer == "wav" and not partial:
        fast = _wav_fast_path(audio)
        if fast is not None:
            return Pcm(fast.data[:max_bytes])
    fd, path = tempfile.mkstemp(suffix=suffix, dir=_TMP_DIR)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        args = [
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            demuxer,
            "-t",
            f"{limit_s:.2f}",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(TARGET_SR),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ]
        if partial:
            args = ["-err_detect", "ignore_err", "-fflags", "+discardcorrupt", *args]
            try:
                out = await _run_ffmpeg(args, max_stdout=max_bytes)
            except AudioError:
                return Pcm(b"")
        else:
            out = await _run_ffmpeg(args, max_stdout=max_bytes)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    out = out[:max_bytes]
    if len(out) % 2:
        out = out[:-1]
    if not out and not partial:
        raise AudioError(f"в записи нет звука или она не {demuxer}")
    return Pcm(out)


def _default_max_decode_s() -> float:
    from .config import get_settings

    return get_settings().max_utterance_s


def _wav_fast_path(audio: bytes) -> Pcm | None:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            if w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == TARGET_SR:
                return Pcm(w.readframes(w.getnframes()))
    except (wave.Error, EOFError):
        return None
    return None


async def encode_ogg_opus(pcm: bytes, sample_rate: int, bitrate: str = "32k") -> bytes:
    """PCM s16le моно → ogg/opus (то, что отдаём браузеру и кладём в кэш)."""
    args = [
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-application",
        "voip",
        "-f",
        "ogg",
        "pipe:1",
    ]
    return await _run_ffmpeg(args, stdin=pcm)


def float_to_pcm16(samples: Any) -> bytes:  # numpy.ndarray | torch.Tensor
    import numpy as np

    arr = samples.detach().cpu().numpy() if hasattr(samples, "detach") else np.asarray(samples)
    arr = np.clip(arr.astype("float32"), -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def pcm16_to_float32(pcm: bytes) -> Any:  # numpy.ndarray float32
    import numpy as np

    return np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0

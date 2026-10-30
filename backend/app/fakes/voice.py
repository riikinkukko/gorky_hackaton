"""FakeSTT / FakeTTS. Настоящие GigaAM / Silero / SpeechKit делает чат 4 (backend/app/voice/)."""

from __future__ import annotations

import io
import math
import struct
import wave


class FakeSTT:
    """Возвращает фиксированную фразу.

    Для тестов: если «аудио» — это UTF-8 текст с префиксом `text:`, вернёт этот текст.
    """

    def __init__(self, default_text: str) -> None:
        self.default_text = default_text

    async def transcribe(self, audio: bytes, mime: str) -> str:
        if audio.startswith(b"text:"):
            return audio[5:].decode("utf-8", errors="ignore").strip()
        return self.default_text


class FakeTTS:
    """Короткий двухтональный сигнал WAV вместо речи (браузер умеет его играть)."""

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        rate, dur = 16000, 0.25
        frames = bytearray()
        for i in range(int(rate * dur * 2)):
            freq = 660 if i < rate * dur else 880
            sample = int(6000 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", sample)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return buf.getvalue(), "audio/wav"

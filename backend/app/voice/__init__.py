"""Голос: STTProvider, TTSProvider — зона чата 4.

Фабрики для app/container.py (вызываются, когда STT_PROVIDER / TTS_PROVIDER ≠ fake):
    build_stt(settings) -> STTProvider   # gigaam | speechkit | whisper
    build_tts(settings) -> TTSProvider   # silero | speechkit | browser

Жизненный цикл: startup(stt, tts) — из lifespan; provider_status(p) — для /health.
WebSocket-протокол — app/api/ws_voice.py; подробности — README.md рядом.
"""

from app.voice.factory import build_stt, build_tts, provider_status, startup

__all__ = ["build_stt", "build_tts", "provider_status", "startup"]

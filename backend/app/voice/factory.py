"""Фабрики для ``app/container.py`` и хуки жизненного цикла.

Контейнер вызывает ``build_stt`` / ``build_tts`` один раз, когда ``STT_PROVIDER`` / ``TTS_PROVIDER``
не равны ``fake`` (заглушки ``fake`` живут в ``app.fakes.voice``). ``startup`` вызывается из
lifespan в ``app/main.py``, ``health`` — из ``/health``.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import VoiceSettings, from_app_settings, get_settings
from .stt import BaseSTT, ConstSTT, GigaAMSTT, SpeechKitSTT, WhisperSTT
from .tts import BaseTTS, CachedTTS, NullTTS, SilenceTTS, SileroTTS, SpeechKitTTS

log = logging.getLogger(__name__)

STT_IMPLS: dict[str, type[BaseSTT]] = {
    "gigaam": GigaAMSTT,
    "speechkit": SpeechKitSTT,
    "whisper": WhisperSTT,
    "const": ConstSTT,
}
TTS_IMPLS: dict[str, type[BaseTTS]] = {
    "silero": SileroTTS,
    "speechkit": SpeechKitTTS,
    "silence": SilenceTTS,
}
NO_SERVER_TTS = {"browser", "none", "off"}


def _voice_settings(settings: Any) -> VoiceSettings:
    return settings if isinstance(settings, VoiceSettings) else from_app_settings(settings)


def build_stt(settings: Any, name: str | None = None) -> BaseSTT:
    vs = _voice_settings(settings)
    key = (name or vs.stt_provider).lower()
    if key not in STT_IMPLS:
        raise ValueError(f"STT_PROVIDER={key!r}: ожидается fake или одно из {sorted(STT_IMPLS)}")
    return STT_IMPLS[key](vs)


def build_tts(settings: Any, name: str | None = None) -> CachedTTS | NullTTS:
    vs = _voice_settings(settings)
    key = (name or vs.tts_provider).lower()
    if key in NO_SERVER_TTS:
        return NullTTS()
    if key not in TTS_IMPLS:
        raise ValueError(
            f"TTS_PROVIDER={key!r}: ожидается fake, browser или одно из {sorted(TTS_IMPLS)}"
        )
    return CachedTTS(TTS_IMPLS[key](vs), vs.tts_cache_size, vs.tts_cache_dir)


async def startup(stt: object, tts: object, *, prewarm: bool = True) -> None:
    """Загрузить модели и прогреть кэш TTS. Ошибка не роняет API: текстовый режим работает."""
    for kind, provider in (("STT", stt), ("TTS", tts)):
        load = getattr(provider, "load", None)
        if load is None:
            continue
        try:
            await load()
        except Exception:
            log.exception("%s %s failed to load", kind, getattr(provider, "name", provider))
    if prewarm and get_settings().tts_prewarm and isinstance(tts, CachedTTS):
        await tts.prewarm()


async def provider_status(provider: object) -> str:
    """ok | down. Заглушки без метода health считаются рабочими."""
    health = getattr(provider, "health", None)
    if health is None:
        return "ok"
    try:
        return "ok" if await health() else "down"
    except Exception:
        return "down"

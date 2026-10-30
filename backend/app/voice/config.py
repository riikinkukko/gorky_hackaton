"""Настройки голосового контура из переменных окружения (и файла ``.env``, как у ``app.config``).

Выбор провайдера (``STT_PROVIDER``, ``TTS_PROVIDER``) берётся из ``app.config.Settings`` —
см. ``from_app_settings``; остальные переменные голоса — отсюда, чтобы не раздувать общий конфиг.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache


def _read_dotenv(path: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return values


_DOTENV: dict[str, str] = {}


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        value = _DOTENV.get(name, default)
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class VoiceSettings:
    # --- выбор провайдеров ---
    stt_provider: str = "gigaam"  # gigaam | speechkit | whisper | const (бенчмарк)
    tts_provider: str = "silero"  # silero | speechkit | silence | browser/none
    # --- GigaAM ---
    gigaam_model: str = (
        "v3_e2e_rnnt"  # v3_e2e_rnnt (пунктуация + цифры) | v3_e2e_ctc | v3_ctc | v3_rnnt
    )
    gigaam_cache_dir: str = "/models/gigaam"
    gigaam_quantize: str = (
        "none"  # none (fp32) | int8 — лёгкий профиль, готовится при сборке образа
    )
    # --- faster-whisper (для бенчмарка и как запасной вариант) ---
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 1
    whisper_cache_dir: str = "/models/whisper"
    # --- Silero ---
    silero_model: str = "v5_6_ru"
    silero_speaker: str = "xenia"  # aidar | baya | kseniya | xenia | eugene
    silero_sample_rate: int = 48000
    silero_cache_dir: str = "/models/silero"
    # --- Yandex SpeechKit ---
    yandex_api_key: str = ""
    yandex_iam_token: str = ""
    yandex_folder_id: str = ""
    speechkit_voice: str = "alena"
    speechkit_speed: float = 1.1
    speechkit_timeout_s: float = 10.0
    # --- общие ---
    torch_threads: int = 4
    opus_bitrate: str = "32k"
    max_utterance_s: float = 20.0  # GigaAM.transcribe держит до 25 с, SpeechKit sync — до 30 с
    max_utterance_bytes: int = 2_000_000
    partials: str = "auto"  # auto | on | off  (auto: включены только для локального STT)
    partial_interval_s: float = 2.0  # SEC-02: не чаще раза в 2 с
    partial_tail_s: float = 6.0  # промежуточный транскрипт — только по хвосту записи
    # --- SEC-03: параллелизм и сокеты ---
    max_stt_concurrency: int = 2  # одновременных decode+STT на процесс
    max_tts_concurrency: int = 2  # одновременных синтезов (кэш не считается)
    queue_timeout_s: float = 5.0  # дольше ждать слот — «Сервер занят»
    max_sockets: int = 20  # открытых /ws/voice на процесс
    max_sockets_per_session: int = 2
    idle_timeout_s: float = 60.0  # закрыть сокет без сообщений
    max_turns_per_min: int = 10  # реплик на (пользователь, сессия)
    min_audio_s: float = 0.3  # короче — считаем пустым нажатием
    tts_cache_size: int = 256
    tts_cache_dir: str = ""  # пусто — только память
    tts_prewarm: bool = True

    @property
    def partials_enabled(self) -> bool:
        if self.partials in {"on", "1", "true", "yes"}:
            return True
        if self.partials in {"off", "0", "false", "no"}:
            return False
        # SpeechKit тарифицируется за каждый запрос — промежуточные транскрипты там не делаем.
        return self.stt_provider in {"gigaam", "whisper", "const"}


@lru_cache(maxsize=1)
def get_settings() -> VoiceSettings:
    _DOTENV.clear()
    _DOTENV.update(_read_dotenv())
    return VoiceSettings(
        stt_provider=_env("STT_PROVIDER", "gigaam").lower(),
        tts_provider=_env("TTS_PROVIDER", "silero").lower(),
        gigaam_model=_env("GIGAAM_MODEL", "v3_e2e_rnnt"),
        gigaam_cache_dir=_env("GIGAAM_CACHE_DIR", "/models/gigaam"),
        gigaam_quantize=_env("GIGAAM_QUANTIZE", "none").lower() or "none",
        whisper_model=_env("WHISPER_MODEL", "small"),
        whisper_compute_type=_env("WHISPER_COMPUTE_TYPE", "int8"),
        whisper_beam_size=_env_int("WHISPER_BEAM_SIZE", 1),
        whisper_cache_dir=_env("WHISPER_CACHE_DIR", "/models/whisper"),
        silero_model=_env("SILERO_MODEL", "v5_6_ru"),
        silero_speaker=_env("SILERO_SPEAKER", "xenia"),
        silero_sample_rate=_env_int("SILERO_SAMPLE_RATE", 48000),
        silero_cache_dir=_env("SILERO_CACHE_DIR", "/models/silero"),
        yandex_api_key=_env("YANDEX_API_KEY"),
        yandex_iam_token=_env("YANDEX_IAM_TOKEN"),
        yandex_folder_id=_env("YANDEX_FOLDER_ID"),
        speechkit_voice=_env("SPEECHKIT_VOICE", "alena"),
        speechkit_speed=_env_float("SPEECHKIT_SPEED", 1.1),
        speechkit_timeout_s=_env_float("SPEECHKIT_TIMEOUT_S", 10.0),
        torch_threads=_env_int("VOICE_TORCH_THREADS", 4),
        opus_bitrate=_env("VOICE_OPUS_BITRATE", "32k"),
        max_utterance_s=_env_float("VOICE_MAX_UTTERANCE_S", 20.0),
        max_utterance_bytes=_env_int("VOICE_MAX_UTTERANCE_BYTES", 2_000_000),
        partials=_env("VOICE_PARTIALS", "auto").lower(),
        partial_interval_s=_env_float("VOICE_PARTIAL_INTERVAL_S", 2.0),
        partial_tail_s=_env_float("VOICE_PARTIAL_TAIL_S", 6.0),
        max_stt_concurrency=_env_int("VOICE_MAX_CONCURRENCY", 2),
        max_tts_concurrency=_env_int("VOICE_MAX_TTS_CONCURRENCY", 2),
        queue_timeout_s=_env_float("VOICE_QUEUE_TIMEOUT_S", 5.0),
        max_sockets=_env_int("VOICE_MAX_SOCKETS", 20),
        max_sockets_per_session=_env_int("VOICE_MAX_SOCKETS_PER_SESSION", 2),
        idle_timeout_s=_env_float("VOICE_IDLE_TIMEOUT_S", 60.0),
        max_turns_per_min=_env_int("VOICE_MAX_TURNS_PER_MIN", 10),
        min_audio_s=_env_float("VOICE_MIN_AUDIO_S", 0.3),
        tts_cache_size=_env_int("TTS_CACHE_SIZE", 256),
        tts_cache_dir=_env("TTS_CACHE_DIR"),
        tts_prewarm=_env_bool("TTS_PREWARM", True),
    )


def from_app_settings(app_settings: object) -> VoiceSettings:
    """Голосовые настройки с провайдерами из общего ``app.config.Settings``."""
    base = get_settings()
    return replace(
        base,
        stt_provider=str(getattr(app_settings, "stt_provider", base.stt_provider)).lower(),
        tts_provider=str(getattr(app_settings, "tts_provider", base.tts_provider)).lower(),
    )


def reload_settings() -> VoiceSettings:
    get_settings.cache_clear()
    return get_settings()

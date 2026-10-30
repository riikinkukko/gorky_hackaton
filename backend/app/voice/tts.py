"""TTS-провайдеры (``contracts.TTSProvider``: ``synthesize(text) -> (audio, mime)``).

Всегда отдаём ``audio/ogg`` (opus внутри): играется в Chrome, Firefox и Safari 17+,
весит ~4 КБ на секунду речи.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any

import httpx

from .audio import encode_ogg_opus, float_to_pcm16
from .config import VoiceSettings
from .limits import tts_slot
from .text_norm import normalize_for_tts, split_for_tts

log = logging.getLogger(__name__)

OGG_MIME = "audio/ogg"

# Фразы, которые звучат постоянно: синтезируем на старте, дальше отдаём из кэша мгновенно.
PREWARM_PHRASES = [
    "На какое время поставить встречу?",
    "На какое время поставить напоминание?",
    "На какой день?",
    "Готово.",
    "Отменил.",
    "Хорошо, не буду.",
    "Удалить?",
    "Не расслышал, повторите, пожалуйста.",
    "Не понял запрос. Попробуйте сказать иначе.",
    "Сервис временно недоступен, попробуйте ещё раз.",
]


class TTSError(RuntimeError):
    pass


class BaseTTS(ABC):
    name = "base"
    voice = ""
    mime = OGG_MIME  # известен заранее: reply с audio_mime уходит, не дожидаясь синтеза

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        text = text.strip()
        if not text:
            raise TTSError("empty text")
        return await self._synthesize(text), OGG_MIME

    @abstractmethod
    async def _synthesize(self, text: str) -> bytes: ...

    async def load(self) -> None:  # noqa: B027 — у облачных провайдеров загружать нечего
        pass

    async def health(self) -> bool:
        return True

    @property
    def cache_key_prefix(self) -> str:
        return f"{self.name}:{self.voice}"


# ---------------------------------------------------------------- Silero (локально)


class SileroTTS(BaseTTS):
    """Silero TTS ``v5_6_ru`` (CC BY-NC-SA 4.0 — только некоммерческое использование!).

    Для коммерции: TTS_PROVIDER=speechkit или MIT-модель ``v5_cis_base``
    (у неё нет автоударений — нужен отдельный расстановщик)."""

    name = "silero"
    URL_TMPL = "https://models.silero.ai/models/tts/ru/{model}.pt"
    PAUSE_S = 0.15

    def __init__(self, settings: VoiceSettings) -> None:
        super().__init__(settings)
        self.voice = f"{settings.silero_model}/{settings.silero_speaker}"
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_lock = asyncio.Lock()

    def model_path(self) -> str:
        return os.path.join(self.settings.silero_cache_dir, f"{self.settings.silero_model}.pt")

    def _load_sync(self) -> Any:
        import torch

        torch.set_num_threads(self.settings.torch_threads)
        path = self.model_path()
        if not os.path.isfile(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            torch.hub.download_url_to_file(
                self.URL_TMPL.format(model=self.settings.silero_model), path
            )
        t0 = time.perf_counter()
        model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        model.to(torch.device("cpu"))
        log.info("Silero %s loaded in %.1fs", self.settings.silero_model, time.perf_counter() - t0)
        return model

    async def load(self) -> None:
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)

    def _synth_sync(self, text: str) -> bytes:
        import torch

        sr = self.settings.silero_sample_rate
        pause = torch.zeros(int(sr * self.PAUSE_S))
        pieces = []
        with self._lock:
            for chunk in split_for_tts(normalize_for_tts(text)):
                audio = self._model.apply_tts(
                    text=chunk, speaker=self.settings.silero_speaker, sample_rate=sr
                )
                pieces.extend([audio, pause])
        return float_to_pcm16(torch.cat(pieces[:-1]))

    async def _synthesize(self, text: str) -> bytes:
        if self._model is None:
            await self.load()
        pcm = await asyncio.to_thread(self._synth_sync, text)
        return await encode_ogg_opus(
            pcm, self.settings.silero_sample_rate, self.settings.opus_bitrate
        )

    async def health(self) -> bool:
        return self._model is not None


# ---------------------------------------------------------------- Yandex SpeechKit (облако)


class SpeechKitTTS(BaseTTS):
    """SpeechKit API v1 ``tts:synthesize``, сразу ``oggopus``. Числа и даты SpeechKit читает сам."""

    name = "speechkit"
    URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"

    def __init__(self, settings: VoiceSettings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        self.voice = settings.speechkit_voice
        self._client = client or httpx.AsyncClient(timeout=settings.speechkit_timeout_s)

    def _headers(self) -> dict[str, str]:
        if self.settings.yandex_api_key:
            return {"Authorization": f"Api-Key {self.settings.yandex_api_key}"}
        if self.settings.yandex_iam_token:
            return {"Authorization": f"Bearer {self.settings.yandex_iam_token}"}
        raise TTSError("SpeechKit: не задан YANDEX_API_KEY или YANDEX_IAM_TOKEN")

    async def _synthesize(self, text: str) -> bytes:
        form = {
            "text": text[:4900],
            "lang": "ru-RU",
            "voice": self.settings.speechkit_voice,
            "format": "oggopus",
            "speed": str(self.settings.speechkit_speed),
        }
        if self.settings.yandex_folder_id:
            form["folderId"] = self.settings.yandex_folder_id
        for attempt in (1, 2):
            try:
                resp = await self._client.post(self.URL, data=form, headers=self._headers())
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise TTSError(f"SpeechKit network: {exc}") from exc
                continue
            if resp.status_code >= 500 and attempt == 1:
                continue
            if resp.status_code != 200:
                raise TTSError(f"SpeechKit {resp.status_code}: {resp.text[:200]}")
            return resp.content
        raise TTSError("SpeechKit: unreachable")

    async def health(self) -> bool:
        return bool(self.settings.yandex_api_key or self.settings.yandex_iam_token)


# ---------------------------------------------------------------- тишина и «озвучивает браузер»


class SilenceTTS(BaseTTS):
    """Тишина длиной ~60 мс на символ в ogg/opus. Нужен ffmpeg; для тестов голосового контура."""

    name = "silence"
    voice = "silence"

    async def _synthesize(self, text: str) -> bytes:
        sr = 16_000
        n = min(int(sr * 0.06 * len(text)), sr * 10)
        return await encode_ogg_opus(b"\x00\x00" * n, sr, self.settings.opus_bitrate)


def _read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _write_file_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class NullTTS:
    """TTS_PROVIDER=browser|none: сервер не синтезирует, озвучивает браузер (speechSynthesis).
    Возвращает пустое аудио — /ws/voice тогда не шлёт бинарный кадр, а audio_mime = null."""

    name = "browser"
    mime = ""

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        return b"", ""

    async def load(self) -> None:
        return None

    async def health(self) -> bool:
        return True


# ---------------------------------------------------------------- кэш


class CachedTTS:
    """LRU в памяти + (опционально) диск. Одинаковые параллельные запросы синтезируются один раз."""

    def __init__(self, inner: BaseTTS, max_items: int = 256, disk_dir: str = "") -> None:
        self.inner = inner
        self.max_items = max_items
        self.disk_dir = disk_dir
        self._mem: OrderedDict[str, bytes] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[bytes]] = {}
        self.hits = 0
        self.misses = 0
        if disk_dir:
            os.makedirs(disk_dir, exist_ok=True)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def mime(self) -> str:
        return self.inner.mime

    def _key(self, text: str) -> str:
        norm = " ".join(text.split()).lower()
        return hashlib.sha256(f"{self.inner.cache_key_prefix}|{norm}".encode()).hexdigest()[:32]

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Кэш → иначе синтез под общим семафором TTS (SEC-03; VoiceBusy, если очередь)."""
        key = self._key(text)
        if key in self._mem:
            self._mem.move_to_end(key)
            self.hits += 1
            return self._mem[key], OGG_MIME
        disk_path = os.path.join(self.disk_dir, f"{key}.ogg") if self.disk_dir else ""
        cached = await asyncio.to_thread(_read_file, disk_path) if disk_path else None
        if cached is not None:
            data = cached
            self._remember(key, data)
            self.hits += 1
            return data, OGG_MIME
        if key in self._inflight:
            self.hits += 1
            return await asyncio.shield(self._inflight[key]), OGG_MIME
        self.misses += 1
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            async with tts_slot():
                data, _ = await self.inner.synthesize(text)
            fut.set_result(data)
        except Exception as exc:
            fut.set_exception(exc)
            fut.exception()  # помечаем как прочитанное, чтобы не было warning'а
            raise
        except BaseException:
            fut.cancel()
            raise
        finally:
            self._inflight.pop(key, None)
        self._remember(key, data)
        if disk_path:
            await asyncio.to_thread(_write_file_atomic, disk_path, data)
        return data, OGG_MIME

    def _remember(self, key: str, data: bytes) -> None:
        self._mem[key] = data
        self._mem.move_to_end(key)
        while len(self._mem) > self.max_items:
            self._mem.popitem(last=False)

    async def prewarm(self, phrases: list[str] | None = None) -> None:
        for phrase in phrases or PREWARM_PHRASES:
            try:
                await self.synthesize(phrase)
            except Exception as exc:  # прогрев не должен ронять старт
                log.warning("TTS prewarm failed for %r: %s", phrase, exc)
                return

    async def load(self) -> None:
        await self.inner.load()

    async def health(self) -> bool:
        return await self.inner.health()

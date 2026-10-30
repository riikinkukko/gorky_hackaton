"""STT-провайдеры. Все реализуют ``contracts.STTProvider`` (``transcribe(audio, mime) -> str``)
и дополнительно ``transcribe_pcm`` — его использует стриминг, чтобы не декодировать дважды.

Выбор — переменной ``STT_PROVIDER`` (см. ``factory.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .audio import Pcm, decode_to_pcm16k, pcm16_to_float32
from .config import VoiceSettings

log = logging.getLogger(__name__)


class STTError(RuntimeError):
    """Провайдер не смог распознать (сеть, квота, битое аудио)."""


class BaseSTT(ABC):
    name: str = "base"
    local: bool = True

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self.last_timing: dict[str, float] = {}

    async def transcribe(self, audio: bytes, mime: str) -> str:
        t0 = time.perf_counter()
        pcm = await decode_to_pcm16k(audio, mime)
        t1 = time.perf_counter()
        text = await self.transcribe_pcm(pcm)
        self.last_timing = {
            "decode_ms": (t1 - t0) * 1000,
            "stt_ms": (time.perf_counter() - t1) * 1000,
            "audio_s": pcm.duration_s,
        }
        return text

    async def transcribe_pcm(self, pcm: Pcm) -> str:
        if pcm.duration_s < self.settings.min_audio_s:
            return ""
        if pcm.duration_s > self.settings.max_utterance_s:
            pcm = Pcm(
                pcm.data[: int(self.settings.max_utterance_s * pcm.sample_rate) * 2],
                pcm.sample_rate,
            )
        return (await self._recognize(pcm)).strip()

    @abstractmethod
    async def _recognize(self, pcm: Pcm) -> str: ...

    async def load(self) -> None:  # noqa: B027 — у облачных провайдеров загружать нечего
        """Загрузить модель заранее (на старте приложения), чтобы первый запрос не ждал."""

    async def health(self) -> bool:
        return True


# ---------------------------------------------------------------- GigaAM (локально)


class GigaAMSTT(BaseSTT):
    """GigaAM-v3 (Сбер, MIT). По умолчанию ``v3_e2e_rnnt``: сразу пунктуация и цифры («в 15:00»),
    что удобно для NLU и DateResolver. ``v3_ctc`` — чуть быстрее, без пунктуации.
    ``GIGAAM_QUANTIZE=int8`` — лёгкий профиль (см. ``gigaam_loader``)."""

    name = "gigaam"
    local = True

    def __init__(self, settings: VoiceSettings) -> None:
        super().__init__(settings)
        self._model: Any = None
        self._lock = threading.Lock()  # torch-модель не делим между потоками
        self._load_lock = asyncio.Lock()

    def _load_sync(self) -> Any:
        from . import gigaam_loader

        return gigaam_loader.load(
            self.settings.gigaam_model,
            self.settings.gigaam_cache_dir,
            self.settings.gigaam_quantize,
            self.settings.torch_threads,
        )

    async def load(self) -> None:
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
                await self._recognize(
                    Pcm(b"\x00\x00" * 16000)
                )  # прогрев: первый проход заметно медленнее

    def _recognize_sync(self, pcm: Pcm) -> str:
        import torch

        model = self._model
        with self._lock:
            wav = torch.from_numpy(pcm16_to_float32(pcm.data))
            # на CPU всё во float32 (int8-квантование внутри Linear-слоёв, вход у них float)
            prepared = (wav.unsqueeze(0), torch.full([1], wav.shape[-1]))
            # transcribe() принимает путь к файлу и сам зовёт ffmpeg. PCM у нас уже есть,
            # поэтому подменяем prepare_wav на время вызова и экономим лишний процесс.
            model.prepare_wav = lambda _path: (
                prepared
            )  # атрибут экземпляра перекрывает метод класса
            try:
                result = model.transcribe("<pcm>")
            finally:
                del model.prepare_wav
        return result.text if hasattr(result, "text") else str(result)

    async def _recognize(self, pcm: Pcm) -> str:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._recognize_sync, pcm)

    async def health(self) -> bool:
        return self._model is not None


# ---------------------------------------------------------------- faster-whisper (для сравнения)


class WhisperSTT(BaseSTT):
    name = "whisper"
    local = True

    def __init__(self, settings: VoiceSettings) -> None:
        super().__init__(settings)
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_lock = asyncio.Lock()

    def _load_sync(self) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            self.settings.whisper_model,
            device="cpu",
            compute_type=self.settings.whisper_compute_type,
            cpu_threads=self.settings.torch_threads,
            download_root=self.settings.whisper_cache_dir,
        )

    async def load(self) -> None:
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
                await self._recognize(Pcm(b"\x00\x00" * 16000))

    def _recognize_sync(self, pcm: Pcm) -> str:
        with self._lock:
            segments, _info = self._model.transcribe(
                pcm16_to_float32(pcm.data),
                language="ru",
                beam_size=self.settings.whisper_beam_size,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            return " ".join(s.text.strip() for s in segments)

    async def _recognize(self, pcm: Pcm) -> str:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._recognize_sync, pcm)

    async def health(self) -> bool:
        return self._model is not None


# ---------------------------------------------------------------- Yandex SpeechKit (облако)


class SpeechKitSTT(BaseSTT):
    """SpeechKit API v1, синхронное распознавание (до 30 с и 1 МБ). Шлём LPCM 16 кГц:
    webm SpeechKit не принимает, а перекодировать в PCM у нас всё равно нужно для лимита длины."""

    name = "speechkit"
    local = False
    URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"

    def __init__(self, settings: VoiceSettings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        self._client = client or httpx.AsyncClient(timeout=settings.speechkit_timeout_s)

    def _headers(self) -> dict[str, str]:
        if self.settings.yandex_api_key:
            return {"Authorization": f"Api-Key {self.settings.yandex_api_key}"}
        if self.settings.yandex_iam_token:
            return {"Authorization": f"Bearer {self.settings.yandex_iam_token}"}
        raise STTError("SpeechKit: не задан YANDEX_API_KEY или YANDEX_IAM_TOKEN")

    async def _recognize(self, pcm: Pcm) -> str:
        params = {
            "lang": "ru-RU",
            "topic": "general",
            "format": "lpcm",
            "sampleRateHertz": str(pcm.sample_rate),
            "profanityFilter": "false",
        }
        if self.settings.yandex_folder_id:
            params["folderId"] = self.settings.yandex_folder_id
        data = pcm.data[:1_000_000]  # жёсткий лимит API v1
        for attempt in (1, 2):
            try:
                resp = await self._client.post(
                    self.URL, params=params, headers=self._headers(), content=data
                )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise STTError(f"SpeechKit network: {exc}") from exc
                continue
            if resp.status_code >= 500 and attempt == 1:
                continue
            if resp.status_code != 200:
                raise STTError(f"SpeechKit {resp.status_code}: {resp.text[:200]}")
            return str(resp.json().get("result", ""))
        raise STTError("SpeechKit: unreachable")

    async def health(self) -> bool:
        return bool(self.settings.yandex_api_key or self.settings.yandex_iam_token)


# ---------------------------------------------------------------- Const (тесты и смоук бенчмарка)


class ConstSTT(BaseSTT):
    """Возвращает заданный текст, но проходит весь путь BaseSTT (декодирование, лимиты, partials).
    Заглушка для API — ``app.fakes.voice.FakeSTT`` (STT_PROVIDER=fake); эта — для тестов голоса."""

    name = "const"
    local = True

    def __init__(self, settings: VoiceSettings, text: str | None = None) -> None:
        super().__init__(settings)
        self.text = (
            text if text is not None else os.environ.get("FAKE_STT_TEXT", "Что у меня сегодня?")
        )

    async def _recognize(self, pcm: Pcm) -> str:
        return self.text

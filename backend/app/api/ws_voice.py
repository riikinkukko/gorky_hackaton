"""WS /ws/voice?session_id=…&user_id=…  — голосовой ход (зона чата 4).

Клиент → сервер
    необязательно ``{"type":"start","mime":"audio/mp4"}`` — контейнер записи (любой, что понимает
    ffmpeg; по умолчанию ``audio/webm;codecs=opus``), сбрасывает буфер;
    бинарные чанки записи (MediaRecorder c timeslice или одним куском);
    ``{"type":"end"}`` — распознать и ответить; ``{"type":"cancel"}`` — выбросить запись.

Сервер → клиент
    ``{"type":"transcript","text":…,"final":false}`` — промежуточные, пока идёт запись
        (только локальный STT и только если клиент шлёт чанки по ходу записи);
    ``{"type":"transcript","text":…,"final":true}`` — после ``end``;
    ``{"type":"error","message":…}`` — не расслышали, сбой STT или диалога;
        следом всё равно приходит ``reply`` с тем же текстом и озвучкой, чтобы голосовой
        пользователь услышал, что произошло;
    ``{"type":"reply", …AssistantReply, "audio_mime":…}`` — ``debug.voice`` содержит метрики STT;
    бинарный кадр с озвучкой ответа (``audio_mime``); при ``TTS_PROVIDER=browser`` кадра нет,
        ``audio_mime`` = null.

Соединение живёт дальше: следующую фразу можно говорить в том же сокете.

Безопасность (docs/security/REPORT.md)
    SEC-10  ``Origin`` (если прислан) — хост запроса или один из ``CORS_ORIGINS``,
            иначе close(1008).
    SEC-01  Пользователь — из подписанной cookie ``pomnyu_session``. Без неё в
            ``APP_ENV=prod`` close(4401); исключение — переходный ``ALLOW_HEADER_AUTH=true``.
            Вне prod (и в переходном режиме) — ``X-User-Id`` или ``?user_id=``
            (браузерный WS не ставит заголовки).
    SEC-11  ``session_id`` — ``^[A-Za-z0-9_-]{1,100}$``, иначе close(1008).
    SEC-03  ≤ ``VOICE_MAX_SOCKETS`` сокетов на процесс и ≤ ``VOICE_MAX_SOCKETS_PER_SESSION``
            на сессию (close 1013); 60 с без сообщений — close(4408);
            ≤ ``VOICE_MAX_TURNS_PER_MIN`` реплик в минуту на сессию;
            decode+STT — под общим семафором (``app.voice.limits``).
    SEC-02  Контейнер — по белому списку mime, длительность режется при декодировании.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.chat import run_turn
from app.api.session import COOKIE, verify_cookie
from app.container import Container
from app.contracts import AssistantReply
from app.voice.audio import TARGET_SR, AudioError, Pcm, decode_to_pcm16k
from app.voice.config import VoiceSettings, from_app_settings
from app.voice.limits import MSG_BUSY, SocketRegistry, VoiceBusy, stt_slot
from app.voice.stt import BaseSTT

router = APIRouter()
log = logging.getLogger(__name__)

DEFAULT_MIME = "audio/webm;codecs=opus"
MSG_NOT_HEARD = "Не расслышал, повторите, пожалуйста."
MSG_STT_FAILED = "Не получилось распознать речь. Повторите или напишите текстом."
MSG_INTERNAL = "Что-то пошло не так. Попробуйте ещё раз."
MSG_TOO_OFTEN = "Слишком часто, подождите немного."
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
CLOSE_POLICY = 1008  # Origin, session_id
CLOSE_TRY_LATER = 1013  # лимит сокетов
CLOSE_UNAUTHORIZED = 4401  # нет сессии
CLOSE_IDLE = 4408  # нет сообщений дольше VOICE_IDLE_TIMEOUT_S


class _Utterance:
    """Буфер одной реплики и состояние промежуточных транскриптов."""

    def __init__(self, mime: str = DEFAULT_MIME) -> None:
        self.mime = mime
        self.chunks: list[bytes] = []
        self.size = 0
        self.last_partial_at = 0.0
        self.last_partial_size = 0
        self.last_partial_text = ""
        self.partial_task: asyncio.Task[None] | None = None

    def add(self, data: bytes) -> None:
        self.chunks.append(data)
        self.size += len(data)

    def audio(self) -> bytes:
        return b"".join(self.chunks)

    async def cancel_partial(self) -> None:
        task, self.partial_task = self.partial_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class VoiceSession:
    def __init__(
        self,
        ws: WebSocket,
        c: Container,
        session_id: str,
        user_id: int,
        limit_sid: str | None = None,
    ) -> None:
        self.ws = ws
        self.c = c
        self.session_id = session_id
        # SEC-18: ключ лимитов — sid из cookie; session_id из query — только состояние диалога
        self.limit_sid = limit_sid or session_id
        self.user_id = user_id
        self.vs: VoiceSettings = from_app_settings(c.settings)
        # Наш STT умеет распознавать уже декодированный PCM — нужно для промежуточных
        # транскриптов. Заглушка app.fakes.voice.FakeSTT получает сырые байты как есть.
        self.local_stt: BaseSTT | None = c.stt if isinstance(c.stt, BaseSTT) else None
        self._send_lock = asyncio.Lock()
        self.utt = _Utterance()

    # ---------- отправка: промежуточные транскрипты идут из фоновой задачи, поэтому под замком
    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))

    async def send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(data)

    # ---------- цикл
    async def run(self) -> None:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(self.ws.receive(), self.vs.idle_timeout_s)
                except TimeoutError:
                    await self.ws.close(CLOSE_IDLE, "idle")
                    break
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self._on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    await self._on_control(msg["text"])
        except WebSocketDisconnect:
            pass
        finally:
            await self.utt.cancel_partial()

    async def _on_audio(self, data: bytes) -> None:
        if not data:
            return
        self.utt.add(data)
        if self.utt.size >= self.vs.max_utterance_bytes:
            log.info("utterance exceeded %d bytes, finalizing", self.vs.max_utterance_bytes)
            await self._finalize()
            return
        self._maybe_schedule_partial()

    async def _on_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "end":
            await self._finalize()
        elif kind in {"start", "cancel"}:
            await self.utt.cancel_partial()
            mime = str(msg.get("mime") or DEFAULT_MIME) if kind == "start" else self.utt.mime
            self.utt = _Utterance(mime)

    # ---------- промежуточные транскрипты
    def _maybe_schedule_partial(self) -> None:
        if self.local_stt is None or not self.vs.partials_enabled:
            return
        utt = self.utt
        if utt.partial_task and not utt.partial_task.done():
            return
        now = time.perf_counter()
        if (
            now - utt.last_partial_at < self.vs.partial_interval_s
            or utt.size == utt.last_partial_size
        ):
            return
        utt.last_partial_at, utt.last_partial_size = now, utt.size
        utt.partial_task = asyncio.create_task(self._partial(self.local_stt, utt, utt.audio()))

    async def _partial(self, stt: BaseSTT, utt: _Utterance, audio: bytes) -> None:
        try:
            async with stt_slot(wait=False):  # занято финальным распознаванием — пропускаем
                pcm = await decode_to_pcm16k(audio, utt.mime, partial=True)
                if pcm.duration_s < self.vs.min_audio_s:
                    return
                tail = int(self.vs.partial_tail_s * TARGET_SR) * 2
                text = await stt.transcribe_pcm(Pcm(pcm.data[-tail:]))
        except Exception as exc:  # промежуточный результат не критичен
            log.debug("partial failed: %s", exc)
            return
        if text and text != utt.last_partial_text and utt is self.utt:
            utt.last_partial_text = text
            await self.send_json({"type": "transcript", "text": text, "final": False})

    # ---------- финал
    async def _transcribe(self, audio: bytes, mime: str, timing: dict[str, Any]) -> str:
        async with stt_slot():
            return await self._transcribe_locked(audio, mime, timing)

    async def _transcribe_locked(self, audio: bytes, mime: str, timing: dict[str, Any]) -> str:
        t0 = time.perf_counter()
        timing["queue_ms"] = round((t0 - timing.pop("_t_end")) * 1000)
        if self.local_stt is None:
            text = await self.c.stt.transcribe(audio, mime)
            timing["stt_ms"] = round((time.perf_counter() - t0) * 1000)
            return text.strip()
        pcm = await decode_to_pcm16k(audio, mime, max_seconds=self.vs.max_utterance_s)
        t1 = time.perf_counter()
        text = await self.local_stt.transcribe_pcm(pcm)
        timing.update(
            audio_s=round(pcm.duration_s, 2),
            decode_ms=round((t1 - t0) * 1000),
            stt_ms=round((time.perf_counter() - t1) * 1000),
        )
        return text

    async def _finalize(self) -> None:
        utt, self.utt = self.utt, _Utterance(self.utt.mime)
        await utt.cancel_partial()
        timing: dict[str, Any] = {
            "stt_provider": self.c.impl.get("stt", type(self.c.stt).__name__),
            "_t_end": time.perf_counter(),
        }
        audio = utt.audio()

        if self._too_often():
            await self._fail(MSG_TOO_OFTEN, {k: v for k, v in timing.items() if k[0] != "_"})
            return
        text, problem = "", None
        try:
            text = await self._transcribe(audio, utt.mime, timing) if audio else ""
        except VoiceBusy:
            problem = MSG_BUSY
        except AudioError as exc:
            log.warning("decode failed (%d bytes, %s): %s", len(audio), utt.mime, exc)
            problem = MSG_STT_FAILED
        except Exception:
            log.exception("STT failed")
            problem = MSG_STT_FAILED
        if problem is None:
            await self.send_json({"type": "transcript", "text": text, "final": True})
            if not text:
                problem = MSG_NOT_HEARD

        timing.pop("_t_end", None)
        if problem is not None:
            await self._fail(problem, timing)
            return
        reply = await self._run_turn(text)
        reply.debug = {**(reply.debug or {}), "voice": timing}
        await self._send_reply(reply)

    async def _fail(self, message: str, timing: dict[str, Any]) -> None:
        """{"type":"error"} для фронта и reply с тем же текстом — чтобы его услышали."""
        await self.send_json({"type": "error", "message": message})
        reply = AssistantReply(text=message, action="none", debug={"voice": timing})
        await self._send_reply(reply)

    def _too_often(self) -> bool:
        """≤ VOICE_MAX_TURNS_PER_MIN реплик на (пользователь, сессия) и общий бюджет /api/chat."""
        lim, s = self.c.limiter, self.c.settings
        key = f"voice:{self.user_id}:{self.limit_sid}"
        if lim.hit(key, self.vs.max_turns_per_min, 60) is not None:
            return True
        return lim.hit("chat:service", s.rate_service_per_hour, 3600) is not None

    async def _run_turn(self, text: str) -> AssistantReply:
        try:
            return await run_turn(self.c, self.user_id, self.session_id, text)
        except Exception:
            log.exception("turn failed on %r", text)
            await self.send_json({"type": "error", "message": MSG_INTERNAL})
            return AssistantReply(text=MSG_INTERNAL, action="none")

    async def _send_reply(self, reply: AssistantReply) -> None:
        payload = {"type": "reply", **reply.model_dump(mode="json")}
        declared = getattr(self.c.tts, "mime", None)
        if declared is not None:
            # mime известен заранее (наш TTS) — текст уходит сразу, звук догоняет
            await self.send_json({**payload, "audio_mime": declared or None})
            audio = await self._synthesize(reply.text)
        else:
            audio = await self._synthesize(reply.text)
            await self.send_json({**payload, "audio_mime": audio[1] if audio else None})
        if audio and audio[0]:
            await self.send_bytes(audio[0])

    async def _synthesize(self, text: str) -> tuple[bytes, str] | None:
        if not text:
            return None
        try:
            return await self.c.tts.synthesize(text)
        except Exception:
            log.exception("TTS failed")  # текст ответа у клиента уже есть — обходимся без звука
            return None


def _origin_allowed(ws: WebSocket, settings: Any) -> bool:
    """SEC-10 (CSWSH). Браузер всегда шлёт Origin; без него — не браузер, проверять нечего.

    Сравниваем хост, а не схему: за nginx → Caddy → uvicorn схема запроса приходит как http,
    хотя пользователь открыл https (Caddy не доверяет X-Forwarded-Proto от nginx).
    """
    origin = ws.headers.get("origin")
    if not origin:
        return True
    allowed = {o.strip().rstrip("/").lower() for o in settings.cors_origins.split(",") if o.strip()}
    if origin.rstrip("/").lower() in allowed:
        return True
    host = (ws.headers.get("host") or "").lower()
    return bool(host) and urlsplit(origin).netloc.lower() == host


def _resolve_user(
    ws: WebSocket, settings: Any, query_user_id: str | None
) -> tuple[int, str | None] | None:
    """SEC-01: cookie → (вне prod или ALLOW_HEADER_AUTH) X-User-Id / ?user_id= / 1.

    Возвращает (user_id, sid из cookie | None).
    """
    found = verify_cookie(settings, ws.cookies.get(COOKIE))
    if found is not None:
        return found[0], found[1]
    prod = settings.app_env == "prod"
    if prod and not settings.allow_header_auth:
        return None
    raw = ws.headers.get("x-user-id") or query_user_id
    try:
        uid = int(raw) if raw else 1
    except ValueError:
        return None
    if not 1 <= uid <= 2_147_483_647 or (prod and uid not in settings.demo_users()):
        return None
    return uid, None


def _registry(ws: WebSocket) -> SocketRegistry:
    state = ws.app.state
    if not hasattr(state, "voice_sockets"):
        state.voice_sockets = SocketRegistry()
    registry: SocketRegistry = state.voice_sockets
    return registry


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    c: Container = ws.app.state.container
    settings = c.settings
    # проверки — до accept(): отказ рукопожатия, браузер получит 403
    if not _origin_allowed(ws, settings):
        log.warning("ws/voice: чужой Origin %r", ws.headers.get("origin"))
        await ws.close(CLOSE_POLICY)
        return
    raw_sid = ws.query_params.get("session_id") or ""
    if raw_sid and not SESSION_ID_RE.fullmatch(raw_sid):
        await ws.close(CLOSE_POLICY)
        return
    resolved = _resolve_user(ws, settings, ws.query_params.get("user_id"))
    if resolved is None:
        await ws.close(CLOSE_UNAUTHORIZED)
        return
    user_id, cookie_sid = resolved
    session_id = raw_sid or uuid.uuid4().hex

    vs = from_app_settings(settings)
    registry, key = _registry(ws), (user_id, cookie_sid or session_id)
    refused = registry.try_open(key, vs.max_sockets, vs.max_sockets_per_session)
    await ws.accept()
    if refused is not None:
        await ws.send_text(json.dumps({"type": "error", "message": refused}, ensure_ascii=False))
        await ws.close(CLOSE_TRY_LATER)
        return
    try:
        await VoiceSession(ws, c, session_id, user_id, cookie_sid).run()
    finally:
        registry.close(key)

import { useCallback, useEffect, useRef, useState } from "react";
import { BackendError, MUTATING, UNDOABLE, type AssistantReply, type Backend, type VoiceSession } from "../api";
import { AUDIO_WAIT_MS, SLOW_AFTER_MS, TIMEOUT_MS } from "../config";
import { dbg as micDebug } from "../audio/micEngine";
import { useRecorder } from "./useRecorder";
import { useSpeaker } from "./useSpeaker";

export type Msg =
  | { id: string; role: "user"; text: string; via: "voice" | "text" }
  | {
      id: string;
      role: "assistant";
      status: "pending" | "done" | "error";
      reply?: AssistantReply;
      slow?: boolean;
      error?: string;
      retryText?: string;
      /** 429: повтор доступен не раньше этого момента (Date.now()), без автоповтора. */
      retryAt?: number;
      undoable?: boolean;
      undone?: boolean;
      /** id записей, которых больше нет после отмены (undo создания → items=[]): карточки скрываем. */
      removedIds?: number[];
    };

type AsstMsg = Extract<Msg, { role: "assistant" }>;

const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));

/** sessionId — из /api/session (API v1.3): по нему сервер хранит состояние диалога. */
export function useAssistant(backend: Backend, userId: number, serverSessionId: string) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [interim, setInterim] = useState<string | null>(null); // null — не слушаем
  const [serverDown, setServerDown] = useState(false);
  const [agendaVersion, setAgendaVersion] = useState(0);
  const [highlight, setHighlight] = useState<{ ids: number[]; action: AssistantReply["action"] } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const sessionId = useRef(serverSessionId);
  const speaker = useSpeaker();
  type Holder = { asstMsgId?: string; userMsgId?: string; waitAudio?: number; replyText?: string };
  const voice = useRef<{ session: VoiceSession; startedAt: number; holder: Holder; live: boolean } | null>(null);
  const interimRef = useRef<string | null>(null);
  const setInterimBoth = useCallback((v: string | null) => {
    interimRef.current = v;
    setInterim(v);
  }, []);
  const timers = useRef(new Map<string, number[]>());

  useEffect(() => {
    sessionId.current = serverSessionId;
    setMessages([]);
    setHighlight(null);
  }, [userId, serverSessionId]);

  const patch = useCallback((id: string, p: Partial<AsstMsg>) => {
    setMessages((ms) => ms.map((m) => (m.id === id && m.role === "assistant" ? { ...m, ...p } : m)));
  }, []);

  const clearTimers = (id: string) => {
    timers.current.get(id)?.forEach(clearTimeout);
    timers.current.delete(id);
  };

  /** Таймеры «долгого ответа» на pending-сообщение. */
  const track = useCallback(
    (id: string, retryText: string | undefined, onTimeout?: () => void) => {
      const slow = window.setTimeout(() => patch(id, { slow: true }), SLOW_AFTER_MS);
      const dead = window.setTimeout(() => {
        onTimeout?.();
        patch(id, { status: "error", error: "Сервер слишком долго не отвечает.", retryText });
      }, TIMEOUT_MS);
      timers.current.set(id, [slow, dead]);
    },
    [patch],
  );

  const fail = useCallback(
    (id: string, e: unknown, retryText?: string) => {
      clearTimers(id);
      const err = e instanceof BackendError ? e : new BackendError("network", String(e));
      if (err.kind === "aborted") return;
      if (err.kind === "unauthorized") {
        // App уже переключается на экран входа; пузырь просто закрываем
        patch(id, { status: "error", error: "Нужно войти заново.", retryText: undefined });
        return;
      }
      if (err.kind === "network") setServerDown(true);
      patch(id, {
        status: "error",
        error: err.kind === "network" ? "Не удаётся связаться с сервером." : err.message,
        retryText,
        retryAt: err.kind === "rate" ? Date.now() + (err.retryAfterS ?? 2) * 1000 : undefined,
      });
    },
    [patch],
  );

  const speakReply = useCallback(
    async (text: string) => {
      if (!speaker.enabled) return;
      const blob = await backend.tts(text);
      if (blob) speaker.playBlob(blob);
      else speaker.speakText(text);
    },
    [backend, speaker],
  );

  const finish = useCallback(
    (id: string, reply: AssistantReply) => {
      clearTimers(id);
      setServerDown(false);
      const undoable = UNDOABLE.has(reply.action);
      const undone = reply.action === "undone";
      setMessages((ms) => {
        // Отмена (кнопкой, текстом «отмени» или голосом) относится к последнему действию
        const target = undone
          ? [...ms].reverse().find((m) => m.role === "assistant" && m.id !== id && m.reply && UNDOABLE.has(m.reply.action) && !m.undone)
          : undefined;
        // Отмена создания: записи больше нет. Контракт — items=[], но бэкенд может прислать и саму запись
        // (как она была до отмены) — тогда тоже убираем её из карточек.
        const t = target?.role === "assistant" ? target.reply! : undefined;
        const gone = t && (t.action === "created" || reply.items.length === 0) ? t.items.map((i) => i.id) : [];
        const shown = gone.length ? { ...reply, items: reply.items.filter((i) => !gone.includes(i.id)) } : reply;
        return ms.map((m) => {
          if (m.role !== "assistant") return m;
          if (m.id === id) return { ...m, status: "done", reply: shown, slow: false, undoable };
          if (target && m.id === target.id) return { ...m, undoable: false, undone: true, removedIds: gone };
          return undoable && m.undoable ? { ...m, undoable: false } : m;
        });
      });
      if (undone) setHighlight(null);
      if (MUTATING.has(reply.action)) setAgendaVersion((v) => v + 1);
      // undone не подсвечиваем в панели: запись могла исчезнуть или вернуться к прежнему виду
      if (["created", "updated", "completed"].includes(reply.action))
        setHighlight({ ids: reply.items.map((i) => i.id), action: reply.action });
    },
    [],
  );

  // ---------- текст ----------

  const sendText = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      speaker.stop();
      const asstId = uid();
      setMessages((ms) => [
        ...ms,
        { id: uid(), role: "user", text, via: "text" },
        { id: asstId, role: "assistant", status: "pending" },
      ]);
      const ctl = new AbortController();
      track(asstId, text, () => ctl.abort());
      try {
        const reply = await backend.chat(text, sessionId.current, ctl.signal);
        finish(asstId, reply);
        speakReply(reply.text);
      } catch (e) {
        fail(asstId, e, text);
      }
    },
    [backend, finish, fail, track, speakReply, speaker],
  );

  const retry = useCallback(
    (msgId: string, text: string) => {
      setMessages((ms) => {
        // убираем ошибочный ответ и вопрос перед ним, чтобы не дублировать
        const i = ms.findIndex((m) => m.id === msgId);
        return i > 0 && ms[i - 1].role === "user" ? [...ms.slice(0, i - 1), ...ms.slice(i + 1)] : ms.filter((m) => m.id !== msgId);
      });
      sendText(text);
    },
    [sendText],
  );

  const undo = useCallback(
    async (msgId: string) => {
      patch(msgId, { undoable: false });
      const asstId = uid();
      setMessages((ms) => [...ms, { id: asstId, role: "assistant", status: "pending" }]);
      track(asstId, undefined);
      try {
        const reply = await backend.undo(sessionId.current);
        finish(asstId, reply);
        speakReply(reply.text);
      } catch (e) {
        patch(msgId, { undoable: true });
        fail(asstId, e);
      }
    },
    [backend, finish, fail, patch, track, speakReply],
  );

  // ---------- голос ----------

  // Куда идут куски записи. Отдельно от voice.current: post-roll дописывается уже после отпускания.
  const chunkTarget = useRef<VoiceSession | null>(null);
  const recorder = useRecorder({ onChunk: (c) => chunkTarget.current?.send(c) });

  const startVoice = useCallback(async () => {
    if (voice.current) return;
    micDebug("press");
    speaker.stop(); // перебиваем озвучку
    setHint(null);
    const holder: Holder = {};
    const session = backend.openVoice(sessionId.current, {
      onTranscript(text, final) {
        if (!holder.userMsgId) setInterimBoth(text);
        else if (final || text) setMessages((ms) => ms.map((m) => (m.id === holder.userMsgId && m.role === "user" ? { ...m, text } : m)));
      },
      onReply(reply, audioMime) {
        if (!holder.asstMsgId) return;
        finish(holder.asstMsgId, reply);
        if (!audioMime) {
          // TTS сервера выключен: звука не будет, говорим браузером
          speaker.speakText(reply.text);
          session.abort();
          return;
        }
        // ждём бинарный кадр; если синтез сломался и звук не пришёл — озвучиваем сами
        holder.waitAudio = window.setTimeout(() => {
          speaker.speakText(reply.text);
          session.abort();
        }, AUDIO_WAIT_MS);
        holder.replyText = reply.text;
      },
      onAudio(blob) {
        clearTimeout(holder.waitAudio);
        if (blob.size) speaker.playBlob(blob);
        else if (holder.replyText) speaker.speakText(holder.replyText);
        session.abort();
      },
      onError(err) {
        if (holder.asstMsgId) fail(holder.asstMsgId, err);
        else {
          setInterimBoth(null);
          setHint(err.message);
          recorder.cancel();
          chunkTarget.current = null;
          voice.current = null;
          if (err.kind === "network") setServerDown(true);
        }
      },
    });
    const v = { session, startedAt: Date.now(), holder, live: false };
    voice.current = v;
    // start уходит до заголовка WAV и pre-roll: recorder.start() отдаёт их сразу
    session.start(recorder.mime());
    chunkTarget.current = session;
    const ok = await recorder.start();
    if (!ok || voice.current !== v) {
      if (chunkTarget.current === session) chunkTarget.current = null;
      if (voice.current === v) {
        session.abort();
        voice.current = null;
      }
      setInterimBoth(null);
      return;
    }
    // «Говорите…» — только когда пошли реальные кадры с микрофона
    v.live = true;
    v.startedAt = Date.now();
    setInterimBoth("");
  }, [backend, recorder, speaker, finish, fail, setInterimBoth]);

  const stopVoice = useCallback(async () => {
    const v = voice.current;
    if (!v) return;
    if (!v.live) {
      // отпустили раньше, чем микрофон реально заработал (спрашивали разрешение, холодный старт)
      recorder.cancel();
      chunkTarget.current = null;
      v.session.abort();
      voice.current = null;
      setInterimBoth(null);
      setHint("Микрофон готов. Теперь зажмите кнопку и говорите.");
      return;
    }
    if (Date.now() - v.startedAt < 400) {
      // случайный тап
      recorder.cancel();
      chunkTarget.current = null;
      v.session.abort();
      voice.current = null;
      setInterimBoth(null);
      setHint("Удерживайте кнопку, пока говорите, — или переключитесь в режим «Нажать».");
      return;
    }
    // Пузырь показываем сразу, а запись ещё POSTROLL_MS дописывает хвост фразы
    const { holder } = v;
    holder.userMsgId = uid();
    holder.asstMsgId = uid();
    const text = interimRef.current?.trim() || "…";
    setInterimBoth(null);
    setMessages((ms) => [
      ...ms,
      { id: holder.userMsgId!, role: "user", text, via: "voice" },
      { id: holder.asstMsgId!, role: "assistant", status: "pending" },
    ]);
    track(holder.asstMsgId, undefined, () => v.session.abort());
    voice.current = null;
    await recorder.stop();
    if (chunkTarget.current === v.session) chunkTarget.current = null;
    v.session.end();
  }, [recorder, track, setInterimBoth]);

  const cancelVoice = useCallback(() => {
    const v = voice.current;
    if (!v) return;
    recorder.cancel();
    chunkTarget.current = null;
    v.session.abort();
    voice.current = null;
    setInterimBoth(null);
  }, [recorder, setInterimBoth]);

  const pending = messages.some((m) => m.role === "assistant" && m.status === "pending");

  return {
    messages,
    sendText,
    retry,
    undo,
    pending,
    serverDown,
    setServerDown,
    agendaVersion,
    highlight,
    hint,
    setHint,
    speaker,
    voice: {
      interim,
      listening: interim !== null,
      mic: recorder.state,
      warm: recorder.warm,
      level: recorder.level,
      retryMic: recorder.retry,
      start: startVoice,
      stop: stopVoice,
      cancel: cancelVoice,
    },
  };
}

export type AssistantApi = ReturnType<typeof useAssistant>;

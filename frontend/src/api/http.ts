import { BackendError, type Backend, type BackendOptions, type SessionInfo, type VoiceHandlers, type VoiceSession } from "./backend";
import type { AssistantReply, Health, Item, ItemType, VoiceClientMessage, VoiceServerMessage } from "./types";
import { iso } from "../lib/time";

export const RATE_LIMIT_MESSAGE = "Слишком часто — подождите пару секунд";

/**
 * Реальный бэкенд по контракту (API v1.3). Пользователь определяется подписанной httpOnly-cookie
 * `pomnyu_session`, которую ставит POST /api/session: запросы same-origin, браузер прикладывает её сам.
 * Заголовок X-User-Id и ?user_id= больше не отправляются.
 */
export function createHttpBackend(opts: BackendOptions, base = ""): Backend {
  let serverTtsOff = false;

  async function send(path: string, init: RequestInit = {}): Promise<Response> {
    try {
      return await fetch(base + path, {
        ...init,
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw new BackendError("aborted", "Запрос отменён");
      throw new BackendError("network", "Сервер недоступен");
    }
  }

  function fail(res: Response): never {
    if (res.status === 401) {
      opts.onUnauthorized();
      throw new BackendError("unauthorized", "Нужно войти заново", 401);
    }
    if (res.status === 429) {
      const ra = Number(res.headers.get("Retry-After"));
      throw new BackendError("rate", RATE_LIMIT_MESSAGE, 429, Number.isFinite(ra) && ra > 0 ? ra : 2);
    }
    if (res.status === 403) throw new BackendError("forbidden", "Доступ запрещён", 403);
    throw new BackendError("http", `Ошибка сервера ${res.status}`, res.status);
  }

  async function request<T>(path: string, init: RequestInit = {}, parse: "json" | "blob" = "json"): Promise<T> {
    const res = await send(path, init);
    if (!res.ok) fail(res);
    return (parse === "json" ? res.json() : res.blob()) as Promise<T>;
  }

  return {
    kind: "http",

    async session() {
      const res = await send("/api/session");
      if (res.status === 401) return null; // нет входа — это не ошибка, просто экран входа
      if (!res.ok) fail(res);
      return (await res.json()) as SessionInfo;
    },

    async login(userId) {
      const res = await send("/api/session", { method: "POST", body: JSON.stringify({ user_id: userId }) });
      if (res.status === 403) throw new BackendError("forbidden", "Такого демо-пользователя нет", 403);
      if (!res.ok) fail(res);
      return (await res.json()) as SessionInfo;
    },

    async logout() {
      const res = await send("/api/session", { method: "DELETE" });
      if (!res.ok && res.status !== 401) fail(res);
    },

    chat: (text, session_id, signal) =>
      request<AssistantReply>("/api/chat", { method: "POST", body: JSON.stringify({ text, session_id }), signal }),

    undo: (session_id) =>
      request<AssistantReply>("/api/undo", { method: "POST", body: JSON.stringify({ session_id }) }),

    items(from: Date, to: Date, type?: ItemType) {
      const q = new URLSearchParams({ from: iso(from), to: iso(to) });
      if (type) q.set("type", type);
      return request<Item[]>(`/api/items?${q}`);
    },

    async tts(text) {
      if (serverTtsOff) return null;
      try {
        const blob = await request<Blob>("/api/tts", { method: "POST", body: JSON.stringify({ text }) }, "blob");
        // TTS_PROVIDER=browser|none: 200 с пустым телом — дальше не спрашиваем, озвучивает браузер
        if (blob.size === 0) {
          serverTtsOff = true;
          return null;
        }
        return blob;
      } catch {
        return null; // 429, сеть — озвучим браузером, без повторов
      }
    },

    health: () => request<Health>("/health"),

    openVoice(sessionId: string, h: VoiceHandlers): VoiceSession {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      // Пользователь — из cookie сессии (браузер прикладывает её к same-origin WebSocket сам)
      const url = `${proto}://${location.host}${base}/ws/voice?session_id=${encodeURIComponent(sessionId)}`;
      const ws = new WebSocket(url);
      ws.binaryType = "blob";
      const queue: (Blob | string)[] = [];
      let closedByUs = false;
      let gotReply = false;
      let audioMime = "audio/ogg";

      const flush = () => {
        while (queue.length && ws.readyState === WebSocket.OPEN) ws.send(queue.shift()!);
      };
      const push = (data: Blob | string) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data);
        else if (ws.readyState === WebSocket.CONNECTING) queue.push(data);
      };
      const sendJson = (m: VoiceClientMessage) => push(JSON.stringify(m));

      ws.onopen = flush;
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          // Бинарный кадр приходит без типа — проставляем audio_mime из reply, иначе <audio> может не сыграть
          h.onAudio(new Blob([ev.data as Blob], { type: audioMime }));
          return;
        }
        let msg: VoiceServerMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          h.onError(new BackendError("protocol", "Непонятный ответ сервера"));
          return;
        }
        if (msg.type === "transcript") h.onTranscript(msg.text, msg.final);
        else if (msg.type === "reply") {
          gotReply = true;
          const { type: _t, audio_mime, ...reply } = msg;
          if (audio_mime) audioMime = audio_mime;
          h.onReply(reply, audio_mime || null);
        } else if (msg.type === "error") h.onError(new BackendError("http", msg.message));
      };
      ws.onerror = () => {
        if (!closedByUs && !gotReply) h.onError(new BackendError("network", "Голосовой канал недоступен"));
      };
      // Сервер закрыл сокет без ответа с кодом «нет входа» (4401 / 1008) — на экран входа
      ws.onclose = (ev) => {
        if (closedByUs || gotReply) return;
        if (ev.code === 4401 || ev.code === 1008) {
          opts.onUnauthorized();
          h.onError(new BackendError("unauthorized", "Нужно войти заново", 401));
        }
      };

      return {
        start: (mime) => sendJson({ type: "start", mime }),
        send: (chunk) => push(chunk),
        end: () => sendJson({ type: "end" }),
        abort: () => {
          closedByUs = true;
          queue.length = 0;
          ws.close();
        },
      };
    },
  };
}

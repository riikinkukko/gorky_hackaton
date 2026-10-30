import type { AssistantReply, Health, Item, ItemType } from "./types";

export type BackendErrorKind =
  | "network"
  | "http"
  | "timeout"
  | "aborted"
  | "protocol"
  | "unauthorized" // 401: сессии нет или она истекла → экран входа
  | "forbidden" // 403: например, неизвестный демо-пользователь
  | "rate"; // 429: слишком часто, см. retryAfterS

export class BackendError extends Error {
  constructor(
    public kind: BackendErrorKind,
    message: string,
    public status?: number,
    /** Для 429: через сколько секунд можно повторить (заголовок Retry-After). */
    public retryAfterS?: number,
  ) {
    super(message);
  }
}

/** Ответ /api/session (API v1.3). Сам токен — в httpOnly-cookie, фронту он не виден. */
export interface SessionInfo {
  user_id: number;
  session_id: string;
}

export interface BackendOptions {
  /** Любой запрос получил 401 — сессия истекла или cookie нет. */
  onUnauthorized(): void;
}

export interface VoiceHandlers {
  onTranscript(text: string, final: boolean): void;
  /** audioMime = null — сервер звук не пришлёт (TTS_PROVIDER=browser), озвучиваем в браузере. */
  onReply(reply: AssistantReply, audioMime: string | null): void;
  onAudio(audio: Blob): void;
  onError(err: BackendError): void;
}

export interface VoiceSession {
  /** Шлёт {"type":"start","mime"} — до первого чанка (протокол v1.1). */
  start(mime: string): void;
  /** Чанк от MediaRecorder в контейнере, объявленном в start(). */
  send(chunk: Blob): void;
  /** Пользователь закончил говорить: отправляет {"type":"end"}. */
  end(): void;
  /** Отмена без ответа (Esc, слишком короткое нажатие). */
  abort(): void;
}

/** Всё, что фронтенд знает о бэкенде. Реализации: http.ts (по контракту) и mock/. */
export interface Backend {
  readonly kind: "http" | "mock";
  /** GET /api/session: текущая сессия по cookie; null — нужен вход (401). */
  session(): Promise<SessionInfo | null>;
  /** POST /api/session: вход демо-пользователем, сервер ставит cookie. */
  login(userId: number): Promise<SessionInfo>;
  /** DELETE /api/session: выход, сервер стирает cookie. */
  logout(): Promise<void>;
  chat(text: string, sessionId: string, signal?: AbortSignal): Promise<AssistantReply>;
  undo(sessionId: string): Promise<AssistantReply>;
  items(from: Date, to: Date, type?: ItemType): Promise<Item[]>;
  /** null — синтез на стороне сервера выключен или недоступен, используем speechSynthesis браузера. */
  tts(text: string): Promise<Blob | null>;
  health(): Promise<Health>;
  openVoice(sessionId: string, handlers: VoiceHandlers): VoiceSession;
}

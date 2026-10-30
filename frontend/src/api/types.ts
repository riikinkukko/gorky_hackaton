// Зеркало backend/app/contracts.py (документ 03). Менять только вместе с чатом 0.
// Даты приходят из Pydantic строками ISO 8601 с таймзоной.

export type ItemType = "task" | "meeting" | "reminder";
export type ItemStatus = "open" | "done" | "cancelled" | "unknown";

export interface Item {
  id: number;
  user_id: number;
  type: ItemType;
  title: string;
  title_original?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  due_at?: string | null;
  has_time: boolean;
  status: ItemStatus;
  rrule?: string | null;
  person: string[];
  organization: string[];
  topic?: string | null;
  object?: string | null;
  source: "voice" | "text" | "import";
  created_at: string;
  updated_at: string;
}

export type ReplyAction =
  | "created"
  | "listed"
  | "found"
  | "updated"
  | "completed"
  | "deleted"
  | "undone"
  | "clarify"
  | "conflict"
  | "none";

export interface AssistantReply {
  text: string;
  action: ReplyAction;
  items: Item[];
  suggestions: string[];
  debug?: Record<string, unknown> | null;
}

export interface Health {
  db: string;
  llm: string;
  stt: string;
  tts: string;
  /** Не в contracts.ts: system.py отдаёт текущее время сервера (с учётом FREEZE_NOW) и реализации модулей. */
  now?: string;
  impl?: Record<string, string>;
}

/** Сообщения сервера в /ws/voice (текстовые кадры). Бинарные кадры — аудио ответа. */
export type VoiceServerMessage =
  | { type: "transcript"; text: string; final: boolean }
  | ({ type: "reply"; audio_mime?: string | null } & AssistantReply) // audio/ogg (TTS) или audio/wav (заглушка)
  | { type: "error"; message: string };

/** Сообщения клиента в /ws/voice (текстовые кадры). Между start и end — бинарные чанки аудио. */
export type VoiceClientMessage = { type: "start"; mime: string } | { type: "end" };

/** Действия, после которых данные изменились: обновляем панель и показываем «Отменить». */
export const MUTATING: ReadonlySet<ReplyAction> = new Set([
  "created",
  "updated",
  "completed",
  "deleted",
  "undone",
]);
export const UNDOABLE: ReadonlySet<ReplyAction> = new Set(["created", "updated", "completed", "deleted"]);

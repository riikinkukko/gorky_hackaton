// Зеркало backend/app/contracts.py для фронтенда. Владелец — чат 0: меняется вместе с контрактом.

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
  | "created" | "listed" | "found" | "updated" | "completed"
  | "deleted" | "undone" | "clarify" | "conflict" | "none";

export interface AssistantReply {
  text: string;
  action: ReplyAction;
  items: Item[];
  suggestions: string[];
  debug?: Record<string, unknown> | null;
}

// WS /ws/voice — сообщения сервера
export type VoiceServerMessage =
  | { type: "transcript"; text: string; final: boolean }
  | ({ type: "reply"; audio_mime?: string } & AssistantReply)
  | { type: "error"; message: string };

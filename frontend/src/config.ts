// Демо-логин (API v1.3): выбор пользователя → POST /api/session, дальше пользователь — в httpOnly-cookie.
// Список пользователей в контракте не описан, поэтому он фиксирован здесь и совпадает с seed бэкенда.
export const DEMO_USERS = [
  { id: 1, name: "Демо", hint: "1000 записей организаторов, 2024–2027" },
  { id: 2, name: "Анна", hint: "чистый календарь" },
  { id: 3, name: "Пётр", hint: "чистый календарь" },
] as const;

export type DemoUser = (typeof DEMO_USERS)[number];

/** Через сколько показывать «Думаю дольше обычного…» и когда сдаваться. */
export const SLOW_AFTER_MS = 3000;
export const TIMEOUT_MS = 20000;
/** Сколько ждать аудио ответа после reply с audio_mime, прежде чем озвучить браузером. */
export const AUDIO_WAIT_MS = 6000;
/** Максимальная длина голосовой команды. */
export const MAX_RECORD_MS = 30000;

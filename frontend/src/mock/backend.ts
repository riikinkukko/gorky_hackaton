import { BackendError, type Backend, type BackendOptions, type SessionInfo, type VoiceHandlers, type VoiceSession } from "../api/backend";
import { DEMO_USERS } from "../config";
import { RATE_LIMIT_MESSAGE } from "../api/http";
import { MockServer } from "./server";
import { mockSettings } from "./settings";

const server = new MockServer();
export const resetMock = () => server.reset();

/** Фразы демо-сценария: подставляются, если в браузере нет распознавания речи (Firefox, Safari без Siri). */
const DEMO_PHRASES = [
  "Что у меня сегодня?",
  "Напомни мне завтра в 10 утра позвонить Ивану",
  "Поставь встречу с Иваном завтра",
  "в три",
  "Поставь созвон с Маяком 29 октября в 15:30",
  "16:30",
  "Когда у меня была встреча с Иваном?",
  "Найди всё про ТЗ",
];
let demoIdx = 0;

async function latency<T>(fn: () => T, base = 350): Promise<T> {
  const { offline, slow } = mockSettings.get();
  await new Promise((r) => setTimeout(r, slow ? 7000 : base + Math.random() * 300));
  if (offline) throw new BackendError("network", "Сервер недоступен");
  return fn();
}

// Минимальные типы Web Speech API (нет в lib.dom)
interface SR extends EventTarget {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
}
const SRCtor = (window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR })
  .SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => SR }).webkitSpeechRecognition;

// Мок «cookie»: сессия живёт в localStorage, как httpOnly-cookie у настоящего сервера живёт между перезагрузками
const SESSION_KEY = "pomnyu.mock.session";
function readSession(): SessionInfo | null {
  try {
    const s = JSON.parse(localStorage.getItem(SESSION_KEY) ?? "null") as SessionInfo | null;
    return s && DEMO_USERS.some((u) => u.id === s.user_id) ? s : null;
  } catch {
    return null;
  }
}

export function createMockBackend(opts: BackendOptions): Backend {
  let current = readSession();
  const getUserId = () => {
    if (!current) {
      opts.onUnauthorized();
      throw new BackendError("unauthorized", "Нужно войти заново", 401);
    }
    return current.user_id;
  };
  return {
    kind: "mock",
    session: () => latency(() => (current = readSession()), 50),
    login: (userId) =>
      latency(() => {
        if (!DEMO_USERS.some((u) => u.id === userId)) throw new BackendError("forbidden", "Такого демо-пользователя нет", 403);
        current = { user_id: userId, session_id: crypto.randomUUID?.() ?? String(Math.random()).slice(2) };
        try {
          localStorage.setItem(SESSION_KEY, JSON.stringify(current));
        } catch {
          /* приватный режим — сессия до перезагрузки */
        }
        return current;
      }, 100),
    logout: () =>
      latency(() => {
        current = null;
        try {
          localStorage.removeItem(SESSION_KEY);
        } catch {
          /* ignore */
        }
      }, 50),
    chat: (text, sessionId) =>
      latency(() => {
        if (mockSettings.get().rate) throw new BackendError("rate", RATE_LIMIT_MESSAGE, 429, 2);
        return server.chat(getUserId(), sessionId, text);
      }),
    undo: (sessionId) => latency(() => server.undo(getUserId(), sessionId), 150),
    items: (from, to, type) => latency(() => server.listRange(getUserId(), from, to, type), 150),
    tts: async () => null, // озвучит speechSynthesis браузера
    health: () => latency(() => ({ db: "ok", llm: "mock", stt: SRCtor ? "browser" : "demo", tts: "browser" }), 50),

    openVoice(sessionId: string, h: VoiceHandlers): VoiceSession {
      // Аудио-чанки мок не разбирает: транскрипт берём из Web Speech API (если есть) или из демо-сценария.
      let finalText = "";
      let interim = "";
      let aborted = false;
      let ended = false;
      let srDone = !SRCtor;
      let rec: SR | null = null;

      const finish = () => {
        if (aborted || !ended || !srDone) return;
        const text = (finalText || interim).trim() || (SRCtor ? "" : DEMO_PHRASES[demoIdx++ % DEMO_PHRASES.length]);
        if (!text) {
          h.onError(new BackendError("protocol", "Не удалось распознать речь. Попробуйте ещё раз или напишите текстом."));
          return;
        }
        h.onTranscript(text, true);
        latency(() => server.chat(getUserId(), sessionId, text)).then(
          (r) => !aborted && h.onReply(r, null),
          (e) => !aborted && h.onError(e),
        );
      };

      if (SRCtor) {
        rec = new SRCtor();
        rec.lang = "ru-RU";
        rec.interimResults = true;
        rec.continuous = true;
        rec.onresult = (e) => {
          interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) finalText += r[0].transcript + " ";
            else interim += r[0].transcript;
          }
          if (!aborted) h.onTranscript((finalText + interim).trim(), false);
        };
        rec.onerror = () => {
          srDone = true;
          finish();
        };
        rec.onend = () => {
          srDone = true;
          finish();
        };
        try {
          rec.start();
        } catch {
          srDone = true;
        }
      } else {
        // Имитируем промежуточный транскрипт, чтобы было видно, как это выглядит
        const phrase = DEMO_PHRASES[demoIdx % DEMO_PHRASES.length];
        const words = phrase.split(" ");
        let k = 0;
        const timer = setInterval(() => {
          if (aborted || ended || k >= words.length) return clearInterval(timer);
          h.onTranscript(words.slice(0, ++k).join(" "), false);
        }, 280);
      }

      return {
        start: () => {},
        send: () => {},
        end: () => {
          ended = true;
          if (rec && !srDone) {
            // даём распознаванию дослушать хвост фразы
            setTimeout(() => rec?.stop(), 350);
          }
          finish();
        },
        abort: () => {
          aborted = true;
          rec?.abort();
        },
      };
    },
  };
}

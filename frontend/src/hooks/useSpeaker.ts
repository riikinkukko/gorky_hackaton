import { useCallback, useEffect, useRef, useState } from "react";

const KEY = "pomnyu.voice";

/** Голоса в Chrome подгружаются асинхронно: прогреваем список заранее. */
if (typeof window !== "undefined" && "speechSynthesis" in window) {
  speechSynthesis.getVoices();
  speechSynthesis.addEventListener?.("voiceschanged", () => speechSynthesis.getVoices());
}

function pickRussianVoice(): SpeechSynthesisVoice | undefined {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("ru"));
  // локальный голос надёжнее сетевого (не зависит от интернета)
  return voices.find((v) => v.localService) ?? voices[0];
}

function readPref(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

/**
 * Озвучка ответов: аудио с сервера (WS или /api/tts), иначе speechSynthesis браузера.
 * Выключатель запоминается между сессиями.
 */
export function useSpeaker() {
  const [enabled, setEnabled] = useState(readPref);
  const [speaking, setSpeaking] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const url = useRef<string | null>(null);

  const stop = useCallback(() => {
    audio.current?.pause();
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = null;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, enabled ? "on" : "off");
    } catch {
      /* приватный режим */
    }
    if (!enabled) stop();
  }, [enabled, stop]);

  const playBlob = useCallback(
    async (blob: Blob) => {
      if (!enabled) return;
      stop();
      const a = (audio.current ??= new Audio());
      url.current = URL.createObjectURL(blob);
      a.src = url.current;
      a.onended = a.onerror = () => setSpeaking(false);
      setSpeaking(true);
      try {
        await a.play();
      } catch {
        setSpeaking(false); // автоплей запрещён — молча, текст и так на экране
      }
    },
    [enabled, stop],
  );

  const speakText = useCallback(
    (text: string) => {
      if (!enabled || !text || !("speechSynthesis" in window)) return;
      const busy = speechSynthesis.speaking || speechSynthesis.pending;
      stop();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "ru-RU";
      const ru = pickRussianVoice();
      if (ru) u.voice = ru;
      u.rate = 1.05;
      u.onend = u.onerror = () => setSpeaking(false);
      setSpeaking(true);
      // Chrome теряет фразу, если speak() идёт сразу за cancel()
      if (busy) setTimeout(() => speechSynthesis.speak(u), 60);
      else speechSynthesis.speak(u);
    },
    [enabled, stop],
  );

  return { enabled, setEnabled, speaking, playBlob, speakText, stop };
}

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { MIME, micEngine, type EngineError } from "../audio/micEngine";

export type MicState =
  | "idle"
  | "starting" // нажали, ждём реальных кадров с микрофона
  | "recording"
  | "denied" // пользователь запретил доступ
  | "insecure" // страница не по HTTPS — браузер не даст микрофон
  | "unsupported" // нет getUserMedia / Web Audio
  | "error"; // нет устройства и прочее

export function initialMicState(): MicState {
  if (!window.isSecureContext) return "insecure";
  const hasAudio = "AudioContext" in window || "webkitAudioContext" in window;
  if (!navigator.mediaDevices?.getUserMedia || !hasAudio) return "unsupported";
  return "idle";
}

interface Options {
  onChunk(chunk: Blob): void;
}

/** Уровень громкости читается прямо из движка, без ререндеров React. */
const levelRef = {
  get current() {
    return micEngine.level;
  },
} as RefObject<number>;

/**
 * Запись через micEngine: микрофон наготове + pre-roll 400 мс + post-roll 400 мс.
 * Интерфейс прежний: start() → запись пошла, stop() → дописали хвост, cancel() → выбросить.
 */
export function useRecorder({ onChunk }: Options) {
  const [state, setState] = useState<MicState>(initialMicState);
  const [warm, setWarm] = useState(micEngine.warm);
  const [granted, setGranted] = useState(false);
  const chunkRef = useRef(onChunk);
  chunkRef.current = onChunk;
  const lastPreroll = useRef(0);

  useEffect(() => micEngine.onChange(() => setWarm(micEngine.warm)), []);

  // Разрешение уже есть → открываем микрофон заранее (кроме iOS). Следим за сменой разрешения.
  useEffect(() => {
    if (!navigator.permissions?.query) return;
    let status: PermissionStatus | undefined;
    const apply = (s: PermissionState) => {
      setGranted(s === "granted");
      if (s === "denied") {
        micEngine.release();
        setState((st) => (st === "idle" ? "denied" : st));
      } else setState((st) => (st === "denied" ? "idle" : st));
    };
    navigator.permissions
      .query({ name: "microphone" as PermissionName })
      .then((s) => {
        status = s;
        apply(s.state);
        s.onchange = () => apply(s.state);
      })
      .catch(() => {}); // Firefox: нет такого разрешения — откроем при первом нажатии
    return () => {
      if (status) status.onchange = null;
    };
  }, []);

  useEffect(() => {
    if (granted && micEngine.keepWarm && initialMicState() === "idle") micEngine.open().catch(() => {});
  }, [granted]);

  // Вкладку свернули — гасим микрофон; вернулись — снова наготове
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden") {
        if (!micEngine.recording) micEngine.release();
      } else if (micEngine.keepWarm && (granted || micEngine.warm)) micEngine.open().catch(() => {});
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [granted]);

  /** true — пошли реальные кадры, можно говорить. */
  const start = useCallback(async (): Promise<boolean> => {
    if (micEngine.recording) return true;
    setState("starting");
    try {
      lastPreroll.current = await micEngine.begin((c) => chunkRef.current(c));
    } catch (e) {
      const err = (typeof e === "string" ? e : "error") as EngineError;
      setState(err);
      return false;
    }
    if (!micEngine.recording) {
      // отменили, пока ждали микрофон
      setState("idle");
      return false;
    }
    setGranted(true);
    setState("recording");
    return true;
  }, []);

  /** Дописывает post-roll и резолвится, когда последний кусок ушёл в onChunk. */
  const stop = useCallback(async () => {
    await micEngine.end();
    setState("idle");
  }, []);

  const cancel = useCallback(() => {
    micEngine.cancel();
    setState((st) => (st === "recording" || st === "starting" ? "idle" : st));
  }, []);

  const retry = useCallback(() => setState(initialMicState()), []);
  const isRecording = useCallback(() => micEngine.recording, []);
  const mime = useCallback(() => MIME, []);
  /** Сколько мс pre-roll ушло в последней записи (0 — был холодный старт). */
  const preroll = useCallback(() => lastPreroll.current, []);

  return { state, level: levelRef, warm, start, stop, cancel, retry, isRecording, mime, preroll };
}

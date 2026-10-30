import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { MAX_RECORD_MS } from "../config";
import type { MicState } from "../hooks/useRecorder";
import { Icon } from "./Icon";

type Mode = "hold" | "tap";
const MODE_KEY = "pomnyu.micmode";

interface Props {
  mic: MicState;
  listening: boolean;
  /** Микрофон открыт заранее (pre-roll): начало фразы не теряется. */
  warm: boolean;
  level: RefObject<number>;
  start(): void;
  stop(): void;
  cancel(): void;
  sendText(text: string): void;
  hint: string | null;
  clearHint(): void;
}

export function Composer({ mic, listening, warm, level, start, stop, cancel, sendText, hint, clearHint }: Props) {
  const [mode, setMode] = useState<Mode>(() => {
    try {
      return (localStorage.getItem(MODE_KEY) as Mode) || "hold";
    } catch {
      return "hold";
    }
  });
  const [text, setText] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const holding = useRef(false);
  const micDisabled = mic === "insecure" || mic === "unsupported";

  useEffect(() => {
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* ignore */
    }
  }, [mode]);

  // таймер записи и автостоп. stop меняется на каждом рендере (промежуточный транскрипт),
  // поэтому держим его в ref — иначе таймер перезапускался бы и автостоп не срабатывал
  const stopRef = useRef(stop);
  stopRef.current = stop;
  useEffect(() => {
    if (!listening) return setElapsed(0);
    const t0 = Date.now();
    const id = setInterval(() => {
      const ms = Date.now() - t0;
      setElapsed(ms);
      if (ms >= MAX_RECORD_MS) stopRef.current();
    }, 200);
    return () => clearInterval(id);
  }, [listening]);

  // Пробел — «зажми и говори» с клавиатуры; Esc — отмена
  useEffect(() => {
    const typing = (e: KeyboardEvent) => (e.target as HTMLElement)?.closest("input, textarea, button, select");
    const down = (e: KeyboardEvent) => {
      if (e.key === "Escape" && listening) return cancel();
      if (e.code !== "Space" || e.repeat || typing(e) || micDisabled) return;
      e.preventDefault();
      if (mode === "tap") return listening ? stop() : start();
      holding.current = true;
      start();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || !holding.current) return;
      holding.current = false;
      stop();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [mode, listening, start, stop, cancel, micDisabled]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    sendText(text);
    setText("");
  };

  const label =
    mic === "starting"
      ? "Включаю микрофон…"
      : listening
        ? mode === "hold"
          ? "Говорите · отпустите, чтобы отправить"
          : "Говорите · нажмите, чтобы отправить"
        : mode === "hold"
          ? "Зажмите и говорите"
          : "Нажмите и говорите";

  return (
    <div className="border-t border-line bg-paper/90 px-4 pb-[max(env(safe-area-inset-bottom),12px)] pt-3 backdrop-blur sm:px-6">
      <div className="mx-auto flex max-w-2xl flex-col items-center gap-3">
        {hint && (
          <button onClick={clearHint} className="rounded-full bg-warn-soft px-3 py-1 text-sm text-warn">
            {hint}
          </button>
        )}

        <div className="flex w-full items-center justify-center gap-4">
          <ModeToggle mode={mode} setMode={setMode} disabled={listening || micDisabled} />
          <MicButton
            disabled={micDisabled}
            listening={listening}
            starting={mic === "starting"}
            level={level}
            onPress={() => {
              clearHint();
              if (mode === "tap") return listening ? stop() : start();
              holding.current = true;
              start();
            }}
            onRelease={() => {
              if (mode === "hold" && holding.current) {
                holding.current = false;
                stop();
              }
            }}
          />
          <div className="w-[104px] text-sm tabular-nums text-muted">
            {listening ? (
              <span className="inline-flex items-center gap-2">
                <span className="size-2 rounded-full bg-accent" /> {formatSec(elapsed)}
                <button onClick={cancel} className="ml-1 rounded-full p-1 hover:bg-sunken" aria-label="Отменить запись (Esc)">
                  <Icon name="x" className="size-4" />
                </button>
              </span>
            ) : warm ? (
              <span
                className="inline-flex items-center gap-1.5 text-xs text-faint"
                title="Микрофон открыт заранее, чтобы не терять начало фразы. На сервер уходит только то, что вы говорите с нажатой кнопкой (плюс 0,4 с до и после)."
              >
                <span className="size-1.5 rounded-full bg-ok" /> наготове
              </span>
            ) : (
              <span className="hidden text-faint sm:inline">или пробел</span>
            )}
          </div>
        </div>
        <p className="-mt-1 text-sm text-muted" aria-live="polite">
          {micDisabled ? "Голосовой ввод недоступен — пишите текстом" : label}
        </p>

        <form onSubmit={submit} className="flex w-full items-center gap-2 rounded-2xl border border-line bg-surface p-1.5 pl-4 focus-within:border-ink">
          <Icon name="keyboard" className="size-5 shrink-0 text-faint" />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Или напишите: «Что у меня на неделе?»"
            className="min-w-0 flex-1 bg-transparent py-2 outline-none placeholder:text-faint"
            enterKeyHint="send"
            aria-label="Текстовый запрос"
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className="grid size-10 place-items-center rounded-xl bg-ink text-surface disabled:opacity-25"
            aria-label="Отправить"
          >
            <Icon name="send" />
          </button>
        </form>
      </div>
    </div>
  );
}

function formatSec(ms: number) {
  const s = Math.floor(ms / 1000);
  return `0:${String(s).padStart(2, "0")}`;
}

function ModeToggle({ mode, setMode, disabled }: { mode: Mode; setMode(m: Mode): void; disabled: boolean }) {
  return (
    <div className="flex w-[104px] flex-col gap-1 text-xs" role="radiogroup" aria-label="Режим кнопки">
      {(["hold", "tap"] as const).map((m) => (
        <button
          key={m}
          role="radio"
          aria-checked={mode === m}
          disabled={disabled}
          onClick={() => setMode(m)}
          className={`rounded-full px-2.5 py-1 text-left transition disabled:opacity-40 ${
            mode === m ? "bg-ink text-surface" : "text-muted hover:bg-sunken"
          }`}
        >
          {m === "hold" ? "Удерживать" : "Нажать"}
        </button>
      ))}
    </div>
  );
}

function MicButton({
  disabled,
  listening,
  starting,
  level,
  onPress,
  onRelease,
}: {
  disabled: boolean;
  listening: boolean;
  starting: boolean;
  level: RefObject<number>;
  onPress(): void;
  onRelease(): void;
}) {
  const ring = useRef<HTMLSpanElement>(null);

  // Индикатор уровня: читаем ref в rAF и двигаем кольцо напрямую, без ререндеров
  useEffect(() => {
    if (!listening) return;
    let id = 0;
    const tick = () => {
      const l = level.current ?? 0;
      if (ring.current) ring.current.style.transform = `scale(${1 + l * 0.55})`;
      id = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(id);
  }, [listening, level]);

  return (
    <div className="relative grid size-24 place-items-center">
      {listening && (
        <>
          <span ref={ring} className="absolute inset-0 rounded-full bg-accent/25 transition-transform duration-75" />
          <span className="absolute inset-0 animate-pulse-ring rounded-full border-2 border-accent" />
        </>
      )}
      <button
        type="button"
        disabled={disabled}
        aria-label={listening ? "Остановить запись" : "Начать запись"}
        aria-pressed={listening}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          onPress();
        }}
        onPointerUp={onRelease}
        onPointerCancel={onRelease}
        onContextMenu={(e) => e.preventDefault()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
            e.preventDefault();
            onPress();
          }
        }}
        onKeyUp={(e) => {
          if (e.key === "Enter" || e.key === " ") onRelease();
        }}
        className={`relative grid size-20 touch-none select-none place-items-center rounded-full text-white shadow-lg transition active:scale-95 disabled:bg-faint disabled:shadow-none ${
          listening ? "scale-105 bg-accent-strong" : "bg-accent hover:bg-accent-strong"
        }`}
        style={{ WebkitUserSelect: "none", WebkitTouchCallout: "none" } as React.CSSProperties}
      >
        {starting ? (
          <span className="size-6 animate-spin rounded-full border-2 border-white/40 border-t-white" />
        ) : listening ? (
          <span className="flex h-6 items-center gap-1">
            {[0, 1, 2, 3].map((i) => (
              <span key={i} className="w-1 animate-pulse rounded-full bg-white" style={{ height: `${10 + ((i * 7) % 14)}px`, animationDelay: `${i * 90}ms` }} />
            ))}
          </span>
        ) : (
          <Icon name="mic" className="size-8" />
        )}
      </button>
    </div>
  );
}

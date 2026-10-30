import { useEffect, useRef, useState } from "react";
import { UNDOABLE, type ReplyAction } from "../api";
import type { Msg } from "../hooks/useAssistant";
import { looksClipped } from "../lib/transcript";
import { Icon } from "./Icon";
import { ItemCard, type Mark } from "./ItemCard";

const MARK: Partial<Record<ReplyAction, Mark>> = {
  created: "new",
  updated: "changed",
  completed: "done",
  deleted: "deleted",
  undone: "undone",
};

/** Бэкенд кладёт «Отменить» в suggestions после действия — у нас для этого своя кнопка, дубль скрываем. */
const UNDO_WORDS = /^(отменить|отмени|отмена)$/i;

const EXAMPLES = [
  "Что у меня сегодня?",
  "Напомни мне завтра в 10 утра позвонить Ивану",
  "Поставь встречу с Иваном завтра",
  "Когда у меня была встреча с Иваном?",
  "Найди всё про ТЗ",
];

interface Props {
  messages: Msg[];
  interim: string | null;
  onSend(text: string): void;
  onUndo(id: string): void;
  onRetry(id: string, text: string): void;
  busy: boolean;
}

export function ChatHistory({ messages, interim, onSend, onUndo, onRetry, busy }: Props) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, interim]);

  const lastAsst = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div className="scroll-thin flex-1 overflow-y-auto px-4 pb-4 sm:px-6" role="log" aria-live="polite" aria-label="Диалог">
      <div className="mx-auto flex max-w-2xl flex-col gap-3 pt-4">
        {messages.length === 0 && interim === null && <Empty onSend={onSend} />}

        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="animate-rise flex flex-col items-end gap-1">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-surface">
                {m.via === "voice" && <Icon name="mic" className="mr-1.5 -mt-0.5 inline size-4 opacity-60" />}
                {m.text}
              </p>
              {m.via === "voice" && looksClipped(m.text) && (
                <p className="max-w-[85%] text-right text-xs text-warn">
                  Похоже, начало фразы не расслышано. Если ассистент понял не так — скажите ещё раз или нажмите «Отменить».
                </p>
              )}
            </div>
          ) : (
            <AssistantBubble
              key={m.id}
              m={m}
              isLast={m.id === lastAsst?.id}
              busy={busy}
              onSend={onSend}
              onUndo={onUndo}
              onRetry={onRetry}
            />
          ),
        )}

        {interim !== null && (
          <div className="flex justify-end">
            <p className="max-w-[85%] rounded-2xl rounded-br-md border border-dashed border-accent bg-accent-soft px-4 py-2.5 text-ink">
              {interim ? <>{interim}<span className="ml-0.5 inline-block animate-pulse">▍</span></> : <span className="text-muted">Слушаю…</span>}
            </p>
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

function AssistantBubble({
  m,
  isLast,
  busy,
  onSend,
  onUndo,
  onRetry,
}: {
  m: Extract<Msg, { role: "assistant" }>;
  isLast: boolean;
  busy: boolean;
  onSend(t: string): void;
  onUndo(id: string): void;
  onRetry(id: string, t: string): void;
}) {
  const [showAll, setShowAll] = useState(false);

  if (m.status === "pending") {
    return (
      <div className="animate-rise flex items-center gap-3 text-muted">
        <span className="flex gap-1 rounded-2xl rounded-bl-md bg-surface px-4 py-3.5" aria-label="Ассистент думает">
          {[0, 1, 2].map((i) => (
            <span key={i} className="size-1.5 animate-bounce rounded-full bg-faint" style={{ animationDelay: `${i * 120}ms` }} />
          ))}
        </span>
        {m.slow && <span className="text-sm">Думаю дольше обычного…</span>}
      </div>
    );
  }

  if (m.status === "error") {
    return (
      <div className="animate-rise flex max-w-[85%] items-start gap-3 rounded-2xl rounded-bl-md border border-danger/30 bg-danger-soft px-4 py-3">
        <Icon name="alert" className="mt-0.5 size-5 shrink-0 text-danger" />
        <div className="flex-1">
          <p className="text-ink">{m.error}</p>
          <p className="mt-0.5 text-sm text-muted">Команда не выполнена. Можно повторить или ввести текстом.</p>
          {m.retryText && <RetryButton at={m.retryAt} onClick={() => onRetry(m.id, m.retryText!)} />}
        </div>
      </div>
    );
  }

  const r = m.reply!;
  const mark = MARK[r.action] ?? null;
  const removed = new Set(m.removedIds ?? []);
  const visible = r.items.filter((it) => !removed.has(it.id));
  const items = showAll ? visible : visible.slice(0, 5);
  const suggestions = UNDOABLE.has(r.action) ? r.suggestions.filter((s) => !UNDO_WORDS.test(s.trim())) : r.suggestions;

  return (
    <div className="animate-rise flex flex-col items-start gap-2">
      <p className="max-w-[85%] whitespace-pre-line rounded-2xl rounded-bl-md bg-surface px-4 py-2.5 shadow-[0_1px_0_var(--color-line)]">
        {r.text}
      </p>

      {items.length > 0 && (
        <div className="grid w-full max-w-md gap-2">
          {r.action === "conflict" && <p className="text-xs font-medium uppercase tracking-wide text-warn">Пересекается с</p>}
          {items.map((it) => (
            <ItemCard key={it.id} item={it} mark={m.undone ? null : mark} />
          ))}
          {visible.length > 5 && !showAll && (
            <button onClick={() => setShowAll(true)} className="justify-self-start text-sm text-muted underline underline-offset-4 hover:text-ink">
              Показать ещё {visible.length - 5}
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {m.undoable && (
          <button
            onClick={() => onUndo(m.id)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:border-ink disabled:opacity-50"
          >
            <Icon name="undo" className="size-4" /> Отменить
          </button>
        )}
        {m.undone && <span className="text-sm text-faint">Действие отменено</span>}
        {isLast &&
          suggestions.map((s) => (
            <button
              key={s}
              onClick={() => onSend(s)}
              disabled={busy}
              className="rounded-full bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent-strong hover:bg-accent hover:text-white disabled:opacity-50"
            >
              {s}
            </button>
          ))}
      </div>

      {import.meta.env.DEV && r.debug && (
        <details className="text-xs text-faint">
          <summary className="cursor-pointer select-none">debug</summary>
          <pre className="mt-1 overflow-x-auto rounded-lg bg-sunken p-2">{JSON.stringify(r.debug, null, 1)}</pre>
        </details>
      )}
    </div>
  );
}

/** «Повторить» — только по нажатию; после 429 кнопка оживает, когда истёк Retry-After. */
function RetryButton({ at, onClick }: { at?: number; onClick(): void }) {
  const [wait, setWait] = useState(() => (at ? Math.max(0, Math.ceil((at - Date.now()) / 1000)) : 0));
  useEffect(() => {
    if (!wait) return;
    const id = setTimeout(() => setWait((w) => Math.max(0, w - 1)), 1000);
    return () => clearTimeout(id);
  }, [wait]);
  return (
    <button
      onClick={onClick}
      disabled={wait > 0}
      className="mt-2 rounded-full bg-ink px-3 py-1 text-sm text-surface hover:opacity-90 disabled:opacity-40"
    >
      {wait > 0 ? `Повторить через ${wait} с` : "Повторить"}
    </button>
  );
}

function Empty({ onSend }: { onSend(t: string): void }) {
  return (
    <div className="flex flex-col items-center px-2 pt-[8vh] text-center">
      <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Скажите, что запомнить</h2>
      <p className="mt-2 max-w-sm text-muted">Задачи, напоминания и встречи — обычными словами. Можно спросить, что было раньше.</p>
      <div className="mt-6 flex max-w-lg flex-wrap justify-center gap-2">
        {EXAMPLES.map((e) => (
          <button
            key={e}
            onClick={() => onSend(e)}
            className="rounded-full border border-line bg-surface px-3.5 py-2 text-sm hover:border-ink"
          >
            «{e}»
          </button>
        ))}
      </div>
    </div>
  );
}

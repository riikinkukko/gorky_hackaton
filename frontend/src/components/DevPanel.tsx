import { useState, useSyncExternalStore } from "react";
import { mockSettings } from "../mock/settings";

/** Только в мок-режиме: включить «сервер недоступен» и «долгий ответ» без бэкенда. */
export function DevPanel({ onReset }: { onReset(): void }) {
  const [open, setOpen] = useState(false);
  const s = useSyncExternalStore(mockSettings.subscribe, mockSettings.get);
  return (
    <div className="fixed left-3 top-16 z-50 text-sm lg:bottom-3 lg:top-auto">
      {open ? (
        <div className="w-60 rounded-2xl border border-line bg-surface p-3 shadow-xl">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-semibold">Мок-API</span>
            <button onClick={() => setOpen(false)} className="text-muted hover:text-ink">свернуть</button>
          </div>
          <Toggle label="Сервер недоступен" on={s.offline} set={(v) => mockSettings.set({ offline: v })} />
          <Toggle label="Долгий ответ (7 с)" on={s.slow} set={(v) => mockSettings.set({ slow: v })} />
          <Toggle label="Слишком часто (429)" on={s.rate} set={(v) => mockSettings.set({ rate: v })} />
          <button onClick={onReset} className="mt-2 w-full rounded-lg bg-sunken px-2 py-1.5 hover:bg-line">
            Сбросить данные и диалог
          </button>
          <p className="mt-2 text-xs text-faint">«Сейчас» = {import.meta.env.VITE_FREEZE_NOW ?? "реальное время"}</p>
        </div>
      ) : (
        <button onClick={() => setOpen(true)} className="rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted shadow hover:text-ink">
          мок
        </button>
      )}
    </div>
  );
}

function Toggle({ label, on, set }: { label: string; on: boolean; set(v: boolean): void }) {
  return (
    <label className="flex cursor-pointer items-center justify-between py-1">
      {label}
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
    </label>
  );
}

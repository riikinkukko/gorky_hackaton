import { useEffect, useMemo, useState } from "react";
import type { Backend, Item } from "../api";
import { dayHeading, itemDate, plural } from "../lib/format";
import { addDays, dayKey, now, startOfDay, startOfWeek } from "../lib/time";
import { Icon } from "./Icon";
import { ItemCard } from "./ItemCard";

type Tab = "today" | "week";

interface Props {
  backend: Backend;
  version: number; // меняется после каждого изменения данных
  clock: number; // меняется после синхронизации с часами сервера
  userId: number;
  highlightIds: number[];
  onCount?(n: number): void;
}

export function AgendaPanel({ backend, version, clock, userId, highlightIds, onCount }: Props) {
  const [tab, setTab] = useState<Tab>("today");
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  const range = useMemo(() => {
    const n = now();
    return tab === "today"
      ? { from: startOfDay(n), to: addDays(startOfDay(n), 1) }
      : { from: startOfWeek(n), to: addDays(startOfWeek(n), 7) };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- clock: пересчитать «сегодня» по часам сервера
  }, [tab, clock]);

  useEffect(() => {
    let alive = true;
    setError(false);
    backend
      .items(range.from, range.to)
      .then((r) => alive && setItems(r))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [backend, range, version, userId, reload]);

  useEffect(() => {
    if (tab === "today" && items) onCount?.(items.length);
  }, [tab, items, onCount]);

  const groups = useMemo(() => {
    const g = new Map<string, Item[]>();
    for (const it of items ?? []) {
      const d = itemDate(it);
      if (!d) continue;
      const k = dayKey(d);
      g.set(k, [...(g.get(k) ?? []), it]);
    }
    return [...g.entries()];
  }, [items]);

  const hl = new Set(highlightIds);

  return (
    <section className="flex h-full flex-col" aria-label="Расписание">
      <div className="flex items-center gap-1 px-4 pb-2 pt-4">
        {(["today", "week"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium ${tab === t ? "bg-ink text-surface" : "text-muted hover:bg-sunken"}`}
          >
            {t === "today" ? "Сегодня" : "Неделя"}
          </button>
        ))}
        {items && (
          <span className="ml-auto text-sm text-faint">
            {items.length} {plural(items.length, ["запись", "записи", "записей"])}
          </span>
        )}
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-4">
        {error ? (
          <div className="m-2 rounded-xl bg-danger-soft p-3 text-sm">
            <p>Не удалось загрузить расписание.</p>
            <button onClick={() => setReload((r) => r + 1)} className="mt-1 font-medium text-danger underline underline-offset-4">
              Повторить
            </button>
          </div>
        ) : items === null ? (
          <ul className="space-y-2 p-2" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-9 animate-pulse rounded-xl bg-sunken" />
            ))}
          </ul>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-10 text-center text-sm text-muted">
            <Icon name="calendar" className="size-8 text-faint" />
            <p className="mt-2">{tab === "today" ? "На сегодня ничего не запланировано." : "На этой неделе пусто."}</p>
            <p className="mt-1 text-faint">Скажите: «Напомни завтра в 10 позвонить Ивану»</p>
          </div>
        ) : (
          groups.map(([k, list]) => (
            <div key={k} className="mb-2">
              {tab === "week" && (
                <h3 className="sticky top-0 bg-surface/95 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted backdrop-blur lg:bg-paper/95">
                  {dayHeading(itemDate(list[0])!)}
                </h3>
              )}
              <ul>
                {list.map((it) => (
                  <ItemCard key={it.id} item={it} compact mark={hl.has(it.id) ? "new" : null} />
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

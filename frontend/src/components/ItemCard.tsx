import type { Item, ItemType } from "../api";
import { formatTime, formatWhen, itemDate, STATUS_LABEL, TYPE_LABEL } from "../lib/format";
import { now } from "../lib/time";
import { Icon } from "./Icon";

const DOT: Record<ItemType, string> = {
  meeting: "bg-meeting",
  task: "bg-task",
  reminder: "bg-reminder",
};
const TEXT: Record<ItemType, string> = {
  meeting: "text-meeting",
  task: "text-task",
  reminder: "text-reminder",
};

export type Mark = "new" | "changed" | "done" | "deleted" | "undone" | null;

const MARK_LABEL: Record<Exclude<Mark, null>, string> = {
  new: "Новая",
  changed: "Изменена",
  done: "Отмечена",
  deleted: "Удалена",
  undone: "Как было",
};

interface Props {
  item: Item;
  mark?: Mark;
  /** compact — для боковой панели: время слева, без даты */
  compact?: boolean;
}

export function ItemCard({ item, mark = null, compact = false }: Props) {
  const d = itemDate(item);
  const past = d ? d < now() : false;
  const done = item.status === "done" || item.status === "cancelled";
  const status = STATUS_LABEL[item.status];

  if (compact) {
    return (
      <li
        className={`flex items-start gap-3 rounded-xl px-3 py-2.5 ${mark ? "bg-accent-soft animate-flash" : "hover:bg-sunken"} ${
          past && !mark ? "opacity-55" : ""
        }`}
      >
        <span className="w-11 shrink-0 pt-0.5 text-sm tabular-nums text-muted">{d && item.has_time ? formatTime(d) : "—"}</span>
        <span className={`mt-2 size-2 shrink-0 rounded-full ${DOT[item.type]}`} title={TYPE_LABEL[item.type]} />
        <span className={`min-w-0 flex-1 text-sm leading-snug ${done ? "text-muted line-through" : ""}`}>{item.title}</span>
      </li>
    );
  }

  return (
    <article
      className={`rounded-2xl border bg-surface px-4 py-3 ${
        mark === "deleted" ? "border-line opacity-60" : mark === "undone" ? "border-line" : mark ? "border-accent animate-flash" : "border-line"
      }`}
    >
      <div className="flex items-center gap-2 text-xs font-medium">
        <span className={`size-2 rounded-full ${DOT[item.type]}`} />
        <span className={TEXT[item.type]}>{TYPE_LABEL[item.type]}</span>
        {item.rrule && (
          <span className="inline-flex items-center gap-1 text-muted">
            <Icon name="repeat" className="size-3.5" /> повторяется
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {status && (
            <span className="inline-flex items-center gap-1 rounded-full bg-sunken px-2 py-0.5 text-ok">
              {item.status === "done" && <Icon name="check" className="size-3.5" />}
              {status}
            </span>
          )}
          {mark && (
            <span className={`rounded-full px-2 py-0.5 ${mark === "deleted" || mark === "undone" ? "bg-sunken text-muted" : "bg-accent text-white"}`}>
              {MARK_LABEL[mark]}
            </span>
          )}
        </span>
      </div>
      <p className={`mt-1.5 font-medium leading-snug ${done || mark === "deleted" ? "text-muted line-through" : ""}`}>{item.title}</p>
      <p className={`mt-0.5 text-sm ${past ? "text-faint" : "text-muted"}`}>
        {formatWhen(item)}
        {past && d && " · прошло"}
      </p>
    </article>
  );
}

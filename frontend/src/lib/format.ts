import type { Item, ItemStatus, ItemType } from "../api/types";
import { TZ, dayKey, now, addDays, toWall } from "./time";

const dayFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: TZ, weekday: "short", day: "numeric", month: "long" });
const dayYearFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: TZ,
  weekday: "short",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const timeFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const longDayFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });

export const itemDate = (it: Item): Date | null => {
  const s = it.starts_at ?? it.due_at;
  return s ? new Date(s) : null;
};

export function formatDay(d: Date): string {
  const sameYear = toWall(d).y === toWall(now()).y;
  return (sameYear ? dayFmt : dayYearFmt).format(d).replace(/\s?г\.$/, "");
}

export const formatTime = (d: Date) => timeFmt.format(d);

/** «чт, 22 октября, 10:00» — абсолютная дата, как в подтверждениях ассистента. */
export function formatWhen(it: Item): string {
  const d = itemDate(it);
  if (!d) return "без срока";
  const day = formatDay(d);
  if (!it.has_time) return day;
  if (it.type === "meeting" && it.ends_at) return `${day}, ${formatTime(d)}–${formatTime(new Date(it.ends_at))}`;
  return `${day}, ${formatTime(d)}`;
}

/** Заголовок группы в списке недели: «Сегодня», «Завтра», «пятница, 23 октября». */
export function dayHeading(d: Date): string {
  const k = dayKey(d);
  if (k === dayKey(now())) return "Сегодня";
  if (k === dayKey(addDays(now(), 1))) return "Завтра";
  const s = longDayFmt.format(d);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const TYPE_LABEL: Record<ItemType, string> = {
  task: "Задача",
  meeting: "Встреча",
  reminder: "Напоминание",
};

export const STATUS_LABEL: Partial<Record<ItemStatus, string>> = {
  done: "Выполнено",
  cancelled: "Отменено",
};

/** plural(5, ["запись", "записи", "записей"]) → «записей» */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

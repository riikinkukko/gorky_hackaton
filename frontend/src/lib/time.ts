// Вся работа со временем — в Europe/Moscow, независимо от таймзоны браузера.
// В МСК нет перехода на летнее время, поэтому достаточно фиксированного смещения +03:00.

export const TZ = "Europe/Moscow";
const OFFSET_MS = 3 * 3600_000;

export interface Wall {
  y: number;
  m: number; // 1–12
  d: number;
  h: number;
  min: number;
  wd: number; // 0 = понедельник … 6 = воскресенье
}

export function toWall(date: Date): Wall {
  const t = new Date(date.getTime() + OFFSET_MS);
  return {
    y: t.getUTCFullYear(),
    m: t.getUTCMonth() + 1,
    d: t.getUTCDate(),
    h: t.getUTCHours(),
    min: t.getUTCMinutes(),
    wd: (t.getUTCDay() + 6) % 7,
  };
}

export function fromWall(y: number, m: number, d: number, h = 0, min = 0): Date {
  return new Date(Date.UTC(y, m - 1, d, h, min) - OFFSET_MS);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** ISO 8601 с явным +03:00 — так же, как отдаёт Pydantic на бэкенде. */
export function iso(date: Date): string {
  const w = toWall(date);
  return `${w.y}-${pad(w.m)}-${pad(w.d)}T${pad(w.h)}:${pad(w.min)}:00+03:00`;
}

export function dayKey(date: Date): string {
  const w = toWall(date);
  return `${w.y}-${pad(w.m)}-${pad(w.d)}`;
}

export function startOfDay(date: Date): Date {
  const w = toWall(date);
  return fromWall(w.y, w.m, w.d);
}

export function addDays(date: Date, n: number): Date {
  const w = toWall(date);
  return fromWall(w.y, w.m, w.d + n, w.h, w.min);
}

export function addMinutes(date: Date, n: number): Date {
  return new Date(date.getTime() + n * 60_000);
}

export function startOfWeek(date: Date): Date {
  const w = toWall(date);
  return fromWall(w.y, w.m, w.d - w.wd);
}

export function withTime(date: Date, h: number, min: number): Date {
  const w = toWall(date);
  return fromWall(w.y, w.m, w.d, h, min);
}

let serverOffsetMs = 0;

/** Часы сервера из /health (`now`): там может стоять FREEZE_NOW, и «сегодня» должно совпадать с бэкендом. */
export function syncServerNow(serverNow: string | undefined): void {
  const t = serverNow ? Date.parse(serverNow) : NaN;
  if (!Number.isNaN(t)) serverOffsetMs = t - Date.now();
}

/** «Сейчас» для фронта: VITE_FREEZE_NOW (мок) → часы сервера → время браузера. */
export function now(): Date {
  const frozen = import.meta.env.VITE_FREEZE_NOW as string | undefined;
  return frozen ? new Date(frozen) : new Date(Date.now() + serverOffsetMs);
}

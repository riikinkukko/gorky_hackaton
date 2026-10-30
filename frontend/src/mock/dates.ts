// Мини-резолвер дат ТОЛЬКО для мока. Настоящий — DateResolver чата 1 на бэкенде.
// Политики совпадают с разделом 6 документа 01: «в 3» → 15:00, «вечером» → 19:00 и т. д.
import { addDays, addMinutes, fromWall, startOfDay, startOfWeek, toWall } from "../lib/time";

export interface Span {
  start: number;
  end: number;
}

export interface When {
  date?: Date; // полночь дня
  time?: { h: number; min: number };
  absolute?: Date; // «через два часа»
  deadline?: Date; // «до конца недели»
  period?: { from: Date; to: Date; label: string };
  spans: Span[]; // какие куски текста заняты датой (вырезаем из названия)
  _wd?: number;
}

const NUM: Record<string, number> = {
  один: 1, одну: 1, одна: 1, два: 2, две: 2, три: 3, четыре: 4, пять: 5, шесть: 6, семь: 7,
  восемь: 8, девять: 9, десять: 10, одиннадцать: 11, двенадцать: 12,
};
const NUM_RE = Object.keys(NUM).join("|");
const WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресень"];
const WD_LABEL = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"];
const MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"];
const MONTH_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];
const MONTH_GEN = "(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)";

const num = (s: string) => (/^\d+$/.test(s) ? Number(s) : NUM[s] ?? NaN);

export const normalize = (s: string) => s.replace(/ё/g, "е").replace(/Ё/g, "Е");

export function parseWhen(original: string, now: Date): When {
  const text = normalize(original).toLowerCase();
  const res: When = { spans: [] };
  const take = (m: RegExpExecArray) => res.spans.push({ start: m.index, end: m.index + m[0].length });
  let m: RegExpExecArray | null;
  const today = startOfDay(now);

  // через N часов / минут
  if ((m = new RegExp(`через\\s+(полчаса|час|(\\d+|${NUM_RE})\\s+(часов|часа|час|минуты|минуту|минут))`).exec(text))) {
    take(m);
    if (m[1] === "полчаса") res.absolute = addMinutes(now, 30);
    else if (m[1] === "час") res.absolute = addMinutes(now, 60);
    else res.absolute = addMinutes(now, num(m[2]) * (m[3].startsWith("час") ? 60 : 1));
    return res;
  }

  // до конца недели → вс 23:59
  if ((m = /до конца (этой )?недели/.exec(text))) {
    take(m);
    const w = toWall(startOfWeek(now));
    res.deadline = fromWall(w.y, w.m, w.d + 6, 23, 59);
  }

  // периоды (для списков)
  if ((m = /(на |в )?(следующей неделе|следующую неделю)/.exec(text))) {
    take(m);
    const from = addDays(startOfWeek(now), 7);
    res.period = { from, to: addDays(from, 7), label: "На следующей неделе" };
  } else if ((m = /(на |в )?(эту неделю|этой неделе|неделю|неделе)/.exec(text))) {
    take(m);
    const from = startOfWeek(now);
    res.period = { from, to: addDays(from, 7), label: "На этой неделе" };
  } else if ((m = /в (январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)/.exec(text))) {
    take(m);
    const mi = MONTH_IN.indexOf(m[1]) + 1;
    const nw = toWall(now);
    const y = mi < nw.m ? nw.y + 1 : nw.y;
    res.period = { from: fromWall(y, mi, 1), to: fromWall(y, mi + 1, 1), label: `В ${m[1]}` };
  }

  // конкретный день
  if ((m = /(на )?послезавтра/.exec(text))) {
    take(m);
    res.date = addDays(today, 2);
  } else if ((m = /(на )?завтра/.exec(text))) {
    take(m);
    res.date = addDays(today, 1);
  } else if ((m = /(на )?сегодня/.exec(text))) {
    take(m);
    res.date = today;
  } else if ((m = new RegExp(`(\\d{1,2})\\s+${MONTH_GEN}`).exec(text))) {
    take(m);
    const mi = MONTHS.findIndex((p) => m![2].startsWith(p)) + 1;
    const nw = toWall(now);
    let d = fromWall(nw.y, mi, Number(m[1]));
    if (d < today) d = fromWall(nw.y + 1, mi, Number(m[1]));
    res.date = d;
  } else if ((m = /(?:в|во|на)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)/.exec(text))) {
    take(m);
    const wd = WEEKDAYS.findIndex((p) => m![1].startsWith(p));
    res.date = addDays(today, (wd - toWall(now).wd + 7) % 7);
    res._wd = wd;
  }

  // время
  if ((m = /(?:в|на|к)\s+(\d{1,2})[:.](\d{2})/.exec(text))) {
    take(m);
    res.time = { h: Number(m[1]), min: Number(m[2]) };
  } else if (
    (m = new RegExp(`(?:в|на|к)\\s+(\\d{1,2}|${NUM_RE})(?:\\s+(?:часов|часа|час))?(?:\\s+(утра|вечера|дня|ночи))?(?![\\wа-я])`).exec(text))
  ) {
    let h = num(m[1]);
    if (!Number.isNaN(h) && h <= 23) {
      take(m);
      if ((m[2] === "вечера" || m[2] === "дня") && h < 12) h += 12;
      else if (!m[2] && h < 8) h += 12; // «в 3» → 15:00
      res.time = { h, min: 0 };
    }
  } else if ((m = /(вечером|утром|днем)/.exec(text))) {
    take(m);
    res.time = { h: m[1] === "вечером" ? 19 : m[1] === "утром" ? 9 : 13, min: 0 };
  }

  // «в пятницу», когда сегодня пятница и время уже прошло → через неделю
  if (res._wd !== undefined && res.date && res.time) {
    const at = fromWall(toWall(res.date).y, toWall(res.date).m, toWall(res.date).d, res.time.h, res.time.min);
    if (at < now) res.date = addDays(res.date, 7);
  }
  delete res._wd;
  return res;
}

export function resolvePoint(w: When): Date | undefined {
  if (w.absolute) return w.absolute;
  if (w.date && w.time) {
    const d = toWall(w.date);
    return fromWall(d.y, d.m, d.d, w.time.h, w.time.min);
  }
  return undefined;
}

export const weekdayAccusative = (d: Date) => WD_LABEL[toWall(d).wd];

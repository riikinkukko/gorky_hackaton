// Мок-сервер: хранилище + простейший NLU на регулярках + диалог.
// Цель — отдавать AssistantReply ровно по контракту и проходить демо-сценарий из раздела 9 документа 01.
// Это НЕ замена чатам 1–3: качество понимания здесь намеренно минимальное.
import type { AssistantReply, Item, ItemType } from "../api/types";
import { formatDay, formatTime, formatWhen, itemDate, plural } from "../lib/format";
import { addDays, addMinutes, fromWall, iso, now as getNow, startOfDay, toWall } from "../lib/time";
import { normalize, parseWhen, resolvePoint, weekdayAccusative, type When } from "./dates";
import seed from "./seed.json";

interface SeedRow {
  id: number;
  type: ItemType;
  title: string;
  starts_at: string | null;
  ends_at: string | null;
  due_at: string | null;
}

interface Draft {
  type: ItemType;
  title: string;
  date?: Date;
  time?: { h: number; min: number };
  at?: Date;
  deadline?: Date;
}

type Awaiting = "time" | "datetime" | "confirm_conflict" | "confirm_delete" | null;

interface Session {
  awaiting: Awaiting;
  draft?: Draft;
  deleteId?: number;
  lastIds: number[];
  lastListType?: ItemType;
  undo: { kind: "create" | "update" | "complete" | "delete"; before?: Item; id: number }[];
}

interface Stored extends Item {
  deleted?: boolean;
}

const importedAt = "2026-10-20T09:00:00+03:00";

export class MockServer {
  private items = new Map<number, Stored[]>();
  private sessions = new Map<string, Session>();
  private nextId = 10_000;

  reset() {
    this.items.clear();
    this.sessions.clear();
  }

  private store(userId: number): Stored[] {
    let s = this.items.get(userId);
    if (!s) {
      s =
        userId === 1
          ? (seed as SeedRow[]).map((r) => ({
              ...r,
              user_id: 1,
              title_original: r.title,
              has_time: true,
              status: "unknown" as const,
              person: [],
              organization: [],
              source: "import" as const,
              created_at: importedAt,
              updated_at: importedAt,
            }))
          : [];
      this.items.set(userId, s);
    }
    return s;
  }

  private session(id: string): Session {
    let s = this.sessions.get(id);
    if (!s) this.sessions.set(id, (s = { awaiting: null, lastIds: [], undo: [] }));
    return s;
  }

  private live(userId: number) {
    return this.store(userId).filter((i) => !i.deleted);
  }

  // ---------- REST ----------

  listRange(userId: number, from: Date, to: Date, type?: ItemType): Item[] {
    return sortAsc(
      this.live(userId).filter((i) => {
        const d = itemDate(i);
        return d && d >= from && d < to && (!type || i.type === type);
      }),
    ).map(strip);
  }

  undo(userId: number, sessionId: string): AssistantReply {
    const s = this.session(sessionId);
    const last = s.undo.pop();
    if (!last) return reply("Отменять пока нечего.", "none");
    const all = this.store(userId);
    const idx = all.findIndex((i) => i.id === last.id);
    if (idx < 0) return reply("Не нашёл, что отменить.", "none");
    const cur = all[idx];
    s.awaiting = null;
    if (last.kind === "create") {
      cur.deleted = true;
      return reply(`Отменил: «${cur.title}» не создана.`, "undone", [], [], { undid: "create" });
    }
    all[idx] = { ...(last.before as Stored), deleted: false };
    s.lastIds = [cur.id];
    const what = { update: "перенос", complete: "отметку о выполнении", delete: "удаление" }[last.kind];
    return reply(`Отменил ${what}: «${all[idx].title}», ${formatWhen(all[idx])}.`, "undone", [strip(all[idx])]);
  }

  // ---------- Диалог ----------

  chat(userId: number, sessionId: string, raw: string): AssistantReply {
    const s = this.session(sessionId);
    const now = getNow();
    const text = normalize(raw.trim()).toLowerCase().replace(/[?!.,«»"]/g, " ").replace(/\s+/g, " ").trim();
    if (!text) return reply("Я ничего не услышал. Попробуйте ещё раз.", "none");

    // 1. Ответ на заданный вопрос имеет приоритет над новой командой.
    if (s.awaiting) {
      const r = this.continueDialog(userId, s, raw, text, now);
      if (r) return r;
      s.awaiting = null;
      s.draft = undefined;
    }

    if (/^(отмени|отменить|верни|верни как было)( это| последнее| действие)?$/.test(text)) return this.undo(userId, sessionId);
    if (/^(удали|убери|удалить)/.test(text)) return this.startDelete(userId, s, raw, text, now);
    if (/(отметь|выполнил|сделал|готово|закрой задачу)/.test(text)) return this.complete(userId, s, text, now);
    if (/(перенеси|передвинь|сдвинь|перенести)/.test(text)) return this.update(userId, s, text, now);
    if (/^(найди|поищи|когда|что у нас было|какие были|что я должен|я когда-то)|когда-то|последний раз/.test(text) || /^(встречи|созвоны|задачи|напоминания) (с|со|по|про) /.test(text) || /^(созвоны|встречи) (с|со)/.test(text))
      return this.search(userId, s, raw, text, now);
    if (/(что у меня|планы|покажи|какие (задачи|встречи|напоминания|дела)|есть ли|запланировано|расписание|что на |^а (на|в|завтра|сегодня|послезавтра))/.test(text))
      return this.list(userId, s, raw, text, now);
    if (/^(напомни|поставь|добавь|создай|запиши|запланируй)|напомни|нужно|надо|встреча|созвон/.test(text))
      return this.create(userId, s, raw, text, now);
    if (/^(привет|здравствуй|спасибо|добрый)/.test(text))
      return reply("Здравствуйте! Скажите, что запланировать или найти.", "none", [], ["Что у меня сегодня?", "Найди всё про ТЗ"]);

    return reply(
      "Не понял запрос. Можно попросить создать задачу, показать планы или найти старую запись.",
      "none",
      [],
      ["Что у меня сегодня?", "Напомни завтра в 10 позвонить Ивану", "Когда у меня была встреча с Иваном?"],
    );
  }

  private continueDialog(userId: number, s: Session, raw: string, text: string, now: Date): AssistantReply | null {
    const cancel = /^(нет|отмена|отмени|не надо|не нужно|стоп)(?![а-я])/.test(text);
    const d = s.draft;

    if (s.awaiting === "confirm_delete") {
      if (/^(да|удаляй|удали|подтверждаю|ага)/.test(text)) {
        const all = this.store(userId);
        const it = all.find((i) => i.id === s.deleteId);
        s.awaiting = null;
        if (!it) return reply("Запись уже удалена.", "none");
        s.undo.push({ kind: "delete", before: { ...it }, id: it.id });
        it.deleted = true;
        return reply(`Удалил: «${it.title}», ${formatWhen(it)}.`, "deleted", [strip(it)]);
      }
      if (cancel) {
        s.awaiting = null;
        return reply("Хорошо, ничего не удаляю.", "none");
      }
      return null;
    }

    if (!d) return null;
    if (cancel) {
      s.awaiting = null;
      s.draft = undefined;
      return reply("Хорошо, не создаю.", "none");
    }

    if (s.awaiting === "confirm_conflict") {
      if (/(все равно|оставь|да|давай так)/.test(text)) return this.save(userId, s, d, now, true);
      const w = parseWhen(/^\d/.test(text) ? `в ${raw}` : raw, now);
      if (w.time) {
        d.at = fromWall(toWall(d.at!).y, toWall(d.at!).m, toWall(d.at!).d, w.time.h, w.time.min);
        if (w.date) d.at = resolvePoint({ ...w, spans: [] });
        return this.save(userId, s, d, now);
      }
      return null;
    }

    // awaiting time / datetime — «в три», «15:00», «завтра в 10»
    const w = parseWhen(/^\d/.test(text) || /^(три|два|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать)/.test(text) ? `в ${raw}` : raw, now);
    if (w.absolute) d.at = w.absolute;
    else {
      if (w.date) d.date = w.date;
      if (w.time) d.time = w.time;
    }
    if (!d.at && d.date && d.time) d.at = resolvePoint({ date: d.date, time: d.time, spans: [] });
    if (!d.at && d.time && !d.date) {
      d.at = resolvePoint({ date: startOfDay(now), time: d.time, spans: [] })!;
      if (d.at < now) d.at = addDays(d.at, 1);
    }
    if (d.at) return this.save(userId, s, d, now);
    if (w.date || w.time) return this.askMissing(s, d);
    return null;
  }

  private create(userId: number, s: Session, raw: string, text: string, now: Date): AssistantReply {
    const type: ItemType = /напомн/.test(text)
      ? "reminder"
      : /(встреч|созвон|демо|планерк|совещани)/.test(text)
        ? "meeting"
        : "task";
    const w = parseWhen(raw, now);
    const d: Draft = { type, title: extractTitle(raw, w), date: w.date, time: w.time, at: w.absolute, deadline: w.deadline };
    if (!d.at && d.date && d.time) d.at = resolvePoint(w);
    if (!d.at && d.time && !d.date && type !== "task") {
      d.at = resolvePoint({ ...w, date: startOfDay(now) })!;
      if (d.at < now) d.at = addDays(d.at, 1);
    }
    if (!d.title) {
      return reply("Что именно записать?", "clarify", [], ["Позвонить Ивану", "Купить билеты"]);
    }
    s.draft = d;
    if (type !== "task" && !d.at) return this.askMissing(s, d);
    return this.save(userId, s, d, now);
  }

  private askMissing(s: Session, d: Draft): AssistantReply {
    if (d.type === "meeting") {
      s.awaiting = "time";
      if (!d.date) return reply("На какой день и время поставить встречу?", "clarify", [], ["Завтра в 15:00", "В пятницу в 11"], { missing: ["date", "time"] });
      return reply("На какое время поставить встречу?", "clarify", [], ["10:00", "15:00", "17:00"], { missing: ["time"] });
    }
    s.awaiting = "datetime";
    if (d.date) return reply("Во сколько напомнить?", "clarify", [], ["В 9 утра", "В 13:00", "Вечером"], { missing: ["time"] });
    return reply("Когда напомнить?", "clarify", [], ["Через час", "Завтра в 10", "Вечером"], { missing: ["datetime"] });
  }

  private save(userId: number, s: Session, d: Draft, now: Date, force = false): AssistantReply {
    const all = this.store(userId);
    const dur = /^демо/i.test(d.title) ? 30 : 60;
    let starts: Date | null = null;
    let due: Date | null = null;
    let hasTime = true;
    if (d.type === "meeting") starts = d.at!;
    else if (d.at) due = d.at;
    else if (d.deadline) (due = d.deadline), (hasTime = false);
    else if (d.date) (due = fromWall(toWall(d.date).y, toWall(d.date).m, toWall(d.date).d, 23, 59)), (hasTime = false);
    else hasTime = false;

    if (d.type === "meeting" && starts && !force) {
      const conflicts = this.overlaps(userId, starts, addMinutes(starts, dur));
      if (conflicts.length) {
        const free = this.freeSlot(userId, starts, dur);
        s.awaiting = "confirm_conflict";
        s.draft = { ...d, at: starts };
        const names = conflicts.map((c) => `«${c.title}»`).join(" и ");
        const t = formatTime(starts);
        const sugg = [...(free ? [formatTime(free)] : []), `Всё равно ${t}`, "Отмена"];
        return reply(
          `${capital(formatDay(starts))}, ${t} уже занято: ${names}. ${free ? `Поставить на ${formatTime(free)} или всё равно на ${t}?` : `Всё равно поставить на ${t}?`}`,
          "conflict",
          conflicts.map(strip),
          sugg,
        );
      }
    }

    const ts = iso(now);
    const item: Stored = {
      id: this.nextId++,
      user_id: userId,
      type: d.type,
      title: d.title,
      starts_at: starts ? iso(starts) : null,
      ends_at: starts ? iso(addMinutes(starts, dur)) : null,
      due_at: due ? iso(due) : null,
      has_time: hasTime,
      status: "open",
      person: [],
      organization: [],
      source: "text",
      created_at: ts,
      updated_at: ts,
    };
    all.push(item);
    s.awaiting = null;
    s.draft = undefined;
    s.lastIds = [item.id];
    s.undo.push({ kind: "create", id: item.id });
    const verb = d.type === "meeting" ? "Поставил" : d.type === "reminder" ? "Напомню" : "Записал задачу";
    const when = itemDate(item) ? formatWhen(item) : "без срока";
    return reply(`${verb}: ${lowerFirst(item.title)} — ${when}.`, "created", [strip(item)]);
  }

  private overlaps(userId: number, start: Date, end: Date, exceptId?: number): Stored[] {
    return this.live(userId).filter((i) => {
      if (i.id === exceptId || !i.has_time) return false;
      const s = itemDate(i)!;
      if (!s) return false;
      const e = i.ends_at ? new Date(i.ends_at) : addMinutes(s, 1);
      return s < end && e > start;
    });
  }

  private freeSlot(userId: number, from: Date, dur: number): Date | null {
    for (let t = addMinutes(from, 15); toWall(t).h < 21 || (toWall(t).h === 21 && toWall(t).min === 0); t = addMinutes(t, 15)) {
      const end = addMinutes(t, dur);
      if (toWall(end).h > 21 || (toWall(end).h === 21 && toWall(end).min > 0)) break;
      if (!this.overlaps(userId, t, end).length) return t;
    }
    return null;
  }

  private list(userId: number, s: Session, raw: string, text: string, now: Date): AssistantReply {
    const w = parseWhen(raw, now);
    let type: ItemType | undefined = /встреч|созвон/.test(text)
      ? "meeting"
      : /задач|дела/.test(text)
        ? "task"
        : /напомин/.test(text)
          ? "reminder"
          : undefined;
    if (!type && /^а /.test(text)) type = s.lastListType;
    s.lastListType = type;

    let from: Date, to: Date, label: string;
    if (w.period) ({ from, to, label } = w.period);
    else {
      from = w.date ?? startOfDay(now);
      to = addDays(from, 1);
      const delta = Math.round((from.getTime() - startOfDay(now).getTime()) / 86_400_000);
      const wd = weekdayAccusative(from);
      label =
        delta === 0 ? "Сегодня" : delta === 1 ? "Завтра" : delta === 2 ? "Послезавтра" : delta < 7 && delta > 0 ? `${wd === "вторник" ? "Во" : "В"} ${wd}` : capital(formatDay(from));
    }
    const found = this.listRange(userId, from, to, type);
    s.lastIds = found.map((i) => i.id);
    const noun: [string, string, string] =
      type === "meeting" ? ["встреча", "встречи", "встреч"] : type === "task" ? ["задача", "задачи", "задач"] : type === "reminder" ? ["напоминание", "напоминания", "напоминаний"] : ["запись", "записи", "записей"];

    if (!found.length) {
      const next = sortAsc(this.live(userId).filter((i) => (itemDate(i) ?? new Date(0)) >= to && (!type || i.type === type)))[0];
      const tail = next ? ` Ближайшее — «${next.title}», ${formatWhen(next)}.` : "";
      return reply(`${label} ${noun[2]} нет.${tail}`, "listed", next ? [strip(next)] : []);
    }
    const head = `${label} ${found.length} ${plural(found.length, noun)}`;
    const line = (i: Item) => `${formatTime(itemDate(i)!)} — ${lowerFirst(i.title)}`;
    const multiDay = to.getTime() - from.getTime() > 86_400_000;
    if (found.length <= 5) {
      const parts = found.map((i) => (multiDay ? `${formatDay(itemDate(i)!)}, ${line(i)}` : line(i)));
      return reply(`${head}: ${parts.join("; ")}.`, "listed", found);
    }
    const upcoming = found.filter((i) => itemDate(i)! >= now);
    const top = (upcoming.length ? upcoming : found).slice(0, 3);
    return reply(
      `${head}. Ближайшие: ${top.map((i) => `${formatDay(itemDate(i)!)}, ${line(i)}`).join("; ")}.`,
      "listed",
      found,
      ["Только встречи", "Только задачи"],
    );
  }

  // ---------- Поиск ----------

  private find(userId: number, text: string, now: Date): { items: Stored[]; tense: "past" | "future" | "any" } {
    const tense = /(был|звонил|ставил|последний раз|когда-то|должен был|отправлял)/.test(text)
      ? "past"
      : /(будет|предстоит|ближайш)/.test(text)
        ? "future"
        : "any";
    const type: ItemType | undefined = /напоминалк|напоминани/.test(text)
      ? "reminder"
      : /(встреч|созвон|демо|планерк)/.test(text) && !/задач/.test(text)
        ? "meeting"
        : undefined;
    const prepared = text.replace(/звонил/g, "позвонить").replace(/отправить|отправлял/g, "отправить");
    const groups = tokenize(prepared)
      .filter((t) => !STOP.has(t))
      .map((t) => SYNONYMS[stem(t)] ?? [stem(t)])
      .filter((g) => g.every((x) => x.length >= 2));

    let items = this.live(userId).filter((i) => {
      if (type && i.type !== type) return false;
      const d = itemDate(i);
      if (tense === "past" && d && d >= now) return false;
      if (tense === "future" && d && d < now) return false;
      const stems = tokenize(normalize(i.title).toLowerCase()).map(stem);
      return groups.every((alts) => alts.some((a) => stems.some((st) => st.startsWith(a) || (a.startsWith(st) && st.length >= 3))));
    });
    items = tense === "past" ? sortDesc(items) : sortByProximity(items, now);
    return { items, tense };
  }

  private search(userId: number, s: Session, raw: string, text: string, now: Date): AssistantReply {
    const { items, tense } = this.find(userId, text, now);
    const label = raw.replace(/^\s*(найди|поищи)\s+/i, "").replace(/[?!.]+$/, "").trim();
    s.lastIds = items.slice(0, 1).map((i) => i.id);
    if (!items.length) return reply(`Не нашёл записей по запросу «${label}».`, "found", [], ["Найди всё про ТЗ", "Что у меня на неделе?"]);
    const top = items.slice(0, 10).map(strip);
    if (items.length === 1) return reply(`Нашёл одну: «${items[0].title}», ${formatWhen(items[0])}.`, "found", top);
    if (tense === "past" && items.length <= 5) {
      const [first, ...rest] = items;
      return reply(
        `Последняя — ${formatWhen(first)}: «${first.title}». Ещё были: ${rest.map((i) => formatDay(itemDate(i)!)).join(", ")}.`,
        "found",
        top,
      );
    }
    if (items.length <= 5)
      return reply(`Нашёл ${items.length} ${plural(items.length, ["запись", "записи", "записей"])}: ${items.map((i) => `«${i.title}», ${formatDay(itemDate(i)!)}`).join("; ")}.`, "found", top);
    return reply(
      `Нашёл ${items.length} ${plural(items.length, ["запись", "записи", "записей"])}. ${tense === "past" ? "Последние" : "Ближайшие"}: ${items
        .slice(0, 3)
        .map((i) => `«${i.title}», ${formatDay(itemDate(i)!)}`)
        .join("; ")}. Уточнить по человеку или дате?`,
      "found",
      top,
      ["Только в прошлом", "Только встречи"],
    );
  }

  /** Ссылка на запись: «её/эту» → последняя упомянутая; иначе поиск по описанию и дате. */
  private resolveTarget(userId: number, s: Session, raw: string, desc: string, now: Date): Stored | undefined {
    const live = this.live(userId);
    const words = tokenize(desc);
    if (words.some((t) => PRONOUNS.has(t)) || !words.filter((t) => !STOP.has(t) && !CMD.has(t)).length) {
      return live.find((i) => i.id === s.lastIds[0]);
    }
    const w = parseWhen(raw, now);
    const cleaned = stripSpans(desc, parseWhen(desc, now).spans);
    let { items } = this.find(userId, tokenize(cleaned).filter((t) => !CMD.has(t)).join(" "), now);
    if (w.date) {
      const sameDay = items.filter((i) => itemDate(i) && startOfDay(itemDate(i)!).getTime() === w.date!.getTime());
      if (sameDay.length) items = sameDay;
    }
    return sortByProximity(items.filter((i) => i.status !== "done"), now)[0] ?? items[0];
  }

  private update(userId: number, s: Session, text: string, now: Date): AssistantReply {
    // «перенеси <что> на <когда>»: «на» перед датой/временем отделяет новое время
    const m = / на ((?:\d|завтра|послезавтра|сегодня|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|следующ|вечер|утро|\d{1,2} ).*)$/.exec(text);
    const desc = m ? text.slice(0, m.index) : text;
    const newWhen = parseWhen(m ? `на ${m[1]}` : "", now);
    const target = this.resolveTarget(userId, s, desc, desc, now);
    if (!target) return reply("Не понял, какую запись перенести. Назовите её, например: «перенеси встречу с Марией на завтра».", "clarify");
    if (!newWhen.date && !newWhen.time && !newWhen.absolute) {
      s.lastIds = [target.id];
      return reply(`На когда перенести «${target.title}»?`, "clarify", [strip(target)], ["На завтра", "На пятницу", "На час позже"]);
    }
    const old = itemDate(target) ?? now;
    const ow = toWall(old);
    let at: Date;
    if (newWhen.absolute) at = newWhen.absolute;
    else {
      const dw = newWhen.date ? toWall(newWhen.date) : ow;
      const t = newWhen.time ?? { h: ow.h, min: ow.min };
      at = fromWall(dw.y, dw.m, dw.d, t.h, t.min);
    }
    const before = { ...target };
    const shift = at.getTime() - old.getTime();
    if (target.starts_at) {
      target.starts_at = iso(at);
      target.ends_at = target.ends_at ? iso(new Date(new Date(target.ends_at).getTime() + shift)) : null;
    } else target.due_at = iso(at);
    if (newWhen.time) target.has_time = true;
    target.updated_at = iso(now);
    s.lastIds = [target.id];
    s.undo.push({ kind: "update", before, id: target.id });
    return reply(`Перенёс «${target.title}» на ${formatWhen(target)}.`, "updated", [strip(target)]);
  }

  private complete(userId: number, s: Session, text: string, now: Date): AssistantReply {
    const desc = tokenize(text).filter((t) => !DONE_WORDS.has(t) && !t.startsWith("выполненн")).join(" ");
    const target = this.resolveTarget(userId, s, desc, desc, now);
    if (!target) return reply("Какую задачу отметить выполненной?", "clarify");
    const before = { ...target };
    target.status = "done";
    target.updated_at = iso(now);
    s.lastIds = [target.id];
    s.undo.push({ kind: "complete", before, id: target.id });
    return reply(`Отметил выполненной: «${target.title}», ${formatWhen(target)}.`, "completed", [strip(target)]);
  }

  private startDelete(userId: number, s: Session, raw: string, text: string, now: Date): AssistantReply {
    const desc = tokenize(text).filter((t) => !CMD.has(t) && t !== "удалить").join(" ");
    let target = this.resolveTarget(userId, s, raw, desc, now);
    // «удали напоминание позвонить Ивану на завтра» — сначала ищем среди созданных в этой сессии
    const recent = this.live(userId).filter((i) => s.undo.some((u) => u.id === i.id && u.kind === "create"));
    if (/напомин/.test(text)) {
      const r = recent.filter((i) => i.type === "reminder");
      if (r.length) target = r[r.length - 1];
    }
    if (!target) return reply("Не нашёл, что удалить. Уточните название или дату.", "clarify");
    s.awaiting = "confirm_delete";
    s.deleteId = target.id;
    s.lastIds = [target.id];
    return reply(`Удалить «${target.title}», ${formatWhen(target)}?`, "clarify", [strip(target)], ["Да, удалить", "Нет"]);
  }
}

// ---------- утилиты ----------

function reply(
  text: string,
  action: AssistantReply["action"],
  items: Item[] = [],
  suggestions: string[] = [],
  extra: Record<string, unknown> = {},
): AssistantReply {
  return { text, action, items, suggestions, debug: { source: "mock", ...extra } };
}

const strip = (i: Stored): Item => {
  const { deleted: _d, ...rest } = i;
  return rest;
};
const t0 = (i: Item) => itemDate(i)?.getTime() ?? Infinity;
const sortAsc = <T extends Item>(a: T[]) => [...a].sort((x, y) => t0(x) - t0(y));
const sortDesc = <T extends Item>(a: T[]) => [...a].sort((x, y) => t0(y) - t0(x));
/** Будущее — ближайшие первыми, потом прошлое — свежие первыми. */
function sortByProximity<T extends Item>(a: T[], now: Date): T[] {
  const n = now.getTime();
  const fut = sortAsc(a.filter((i) => t0(i) >= n));
  const past = sortDesc(a.filter((i) => t0(i) < n));
  return [...fut, ...past];
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const lowerFirst = (s: string) => (/^[А-ЯЁ][а-яё]/.test(s) && !/^(ТЗ|CRM)/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);

const tokenize = (s: string) => normalize(s).toLowerCase().split(/[^a-zа-я0-9-]+/i).filter(Boolean);
const ENDINGS = /(ями|ами|ого|его|ему|ому|ыми|ими|ией|ией|ой|ей|ом|ем|ам|ям|ах|ях|ую|юю|ая|яя|ое|ее|ые|ие|ый|ий|ов|ев|ию|ия|ье|ью|у|ю|а|я|о|е|ы|и|ь)$/;
function stem(t: string): string {
  if (t.length <= 4) return t;
  const s = t.replace(ENDINGS, "");
  return s.length >= 3 ? s : t;
}

const STOP = new Set(
  "найди поищи когда у меня мне была был были было что нас с со по про я все всё всю какие какая какой задачу задача задачи задач ее её его ставил когда-то последний раз должен встречу встреча встречи встреч напоминалки напоминание напоминания на в и а это эту the запись записи".split(
    " ",
  ),
);
const CMD = new Set("перенеси передвинь сдвинь удали убери отметь".split(" "));
const PRONOUNS = new Set("ее её его эту это этот ней нее".split(" "));
const DONE_WORDS = new Set("отметь что я выполнил выполнила сделал сделала проверил проверила готово закрой задачу как".split(" "));
const SYNONYMS: Record<string, string[]> = {
  тз: ["техническ"],
  мобилк: ["мобильн"],
  мобилка: ["мобильн"],
  деньг: ["бюджет", "смет"],
  деньгам: ["бюджет", "смет"],
  созвон: ["созвон", "встреч"],
  созвоны: ["созвон"],
  планерк: ["планерк"],
  планерки: ["планерк"],
  презентаци: ["презентац"],
};

function extractTitle(raw: string, w: When): string {
  let t = stripSpans(normalize(raw), w.spans);
  t = t
    .replace(/^\s*(напомни(те)?( мне)?|поставь( мне)?|добавь( мне)?|создай|запиши|запланируй)\s*/i, "")
    .replace(/(?<![а-я])(задачу|задача|напоминание)(?![а-я])\s*/i, "")
    .replace(/(?<![а-я])(у меня|мне нужно|нужно|надо|мне)(?![а-я])/gi, "")
    .replace(/^\s*(о том,? что|что|про|чтобы)\s+/i, "")
    .replace(/[?!.,]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  t = t.replace(/^встречу(?![а-я])/i, "Встреча").replace(/^демку(?![а-я])/i, "Демо");
  // вернуть «ё» из исходной фразы не пытаемся — это зона нормализатора на бэкенде
  return capital(t);
}

function stripSpans(s: string, spans: { start: number; end: number }[]): string {
  return [...spans]
    .sort((a, b) => b.start - a.start)
    .reduce((acc, sp) => acc.slice(0, sp.start) + " " + acc.slice(sp.end), s)
    .replace(/\s+/g, " ")
    .trim();
}

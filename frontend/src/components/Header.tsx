import type { DemoUser } from "../config";
import { Icon } from "./Icon";

interface Props {
  user: DemoUser;
  online: boolean | null; // null — ещё не проверяли
  voiceOn: boolean;
  speaking: boolean;
  toggleVoice(): void;
  todayCount: number | null;
  openAgenda(): void;
  logout(): void;
}

export function Header({ user, online, voiceOn, speaking, toggleVoice, todayCount, openAgenda, logout }: Props) {
  return (
    <header className="flex items-center gap-2 border-b border-line px-4 py-3 sm:px-6">
      <div className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-xl bg-accent text-white">
          <Icon name="mic" className="size-4.5" />
        </span>
        <h1 className="text-lg font-semibold tracking-tight">Помню</h1>
        <span
          className={`ml-1 size-2 rounded-full ${online === null ? "bg-faint" : online ? "bg-ok" : "bg-danger"}`}
          title={online === null ? "Проверяем связь" : online ? "Сервер на связи" : "Сервер недоступен"}
        />
      </div>

      <div className="ml-auto flex items-center gap-1">
        <button
          onClick={openAgenda}
          className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm hover:bg-sunken lg:hidden"
          aria-label="Открыть расписание"
        >
          <Icon name="calendar" className="size-4.5" />
          Сегодня{todayCount !== null && <span className="rounded-full bg-sunken px-1.5 text-xs tabular-nums">{todayCount}</span>}
        </button>
        <button
          onClick={toggleVoice}
          aria-pressed={voiceOn}
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm hover:bg-sunken ${voiceOn ? "" : "text-muted"}`}
          title={voiceOn ? "Озвучка включена" : "Озвучка выключена"}
        >
          <Icon name={voiceOn ? "volume" : "mute"} className={`size-4.5 ${speaking ? "animate-pulse text-accent" : ""}`} />
          <span className="hidden sm:inline">{voiceOn ? "Озвучка" : "Без звука"}</span>
        </button>
        <button onClick={logout} className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm hover:bg-sunken" title="Сменить пользователя">
          <span className="grid size-6 place-items-center rounded-full bg-ink text-xs font-semibold text-surface">{user.name[0]}</span>
          <span className="hidden sm:inline">{user.name}</span>
        </button>
      </div>
    </header>
  );
}

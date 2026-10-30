import { DEMO_USERS, type DemoUser } from "../config";
import { Icon } from "./Icon";

interface Props {
  onPick(u: DemoUser): void;
  busy: boolean;
  /** Почему мы на экране входа: сессия истекла, сервер недоступен, 429. */
  notice: string | null;
}

export function Login({ onPick, busy, notice }: Props) {
  return (
    <main className="grid min-h-full place-items-center px-4 py-10">
      <div className="w-full max-w-sm">
        <span className="grid size-12 place-items-center rounded-2xl bg-accent text-white">
          <Icon name="mic" className="size-6" />
        </span>
        <h1 className="mt-5 text-3xl font-semibold tracking-tight">Помню</h1>
        <p className="mt-1 text-muted">Голосовой помощник для задач, напоминаний и встреч.</p>

        {notice && (
          <p role="alert" className="mt-6 rounded-xl bg-warn-soft px-3 py-2 text-sm text-ink">
            {notice}
          </p>
        )}

        <p className="mt-8 text-sm font-medium text-muted">Демо-вход — выберите пользователя</p>
        <ul className="mt-2 grid gap-2" aria-busy={busy}>
          {DEMO_USERS.map((u) => (
            <li key={u.id}>
              <button
                onClick={() => onPick(u)}
                disabled={busy}
                className="flex w-full items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3 text-left hover:border-ink disabled:opacity-60"
              >
                <span className="grid size-9 place-items-center rounded-full bg-ink font-semibold text-surface">{u.name[0]}</span>
                <span>
                  <span className="block font-medium">{u.name}</span>
                  <span className="block text-sm text-muted">{u.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <p className="mt-6 text-xs text-faint">
          У каждого пользователя свои записи. Пароль в прототипе не нужен: сервер выдаёт подписанную сессию на сутки.
        </p>
      </div>
    </main>
  );
}

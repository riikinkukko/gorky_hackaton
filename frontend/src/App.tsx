import { useCallback, useEffect, useRef, useState } from "react";
import { BackendError, loadBackend, USE_MOCK, type Backend, type SessionInfo } from "./api";
import { micEngine } from "./audio/micEngine";
import { AgendaPanel } from "./components/AgendaPanel";
import { Banners } from "./components/Banners";
import { ChatHistory } from "./components/ChatHistory";
import { Composer } from "./components/Composer";
import { DevPanel } from "./components/DevPanel";
import { Header } from "./components/Header";
import { Icon } from "./components/Icon";
import { Login } from "./components/Login";
import { DEMO_USERS, type DemoUser } from "./config";
import { useAssistant } from "./hooks/useAssistant";
import { useHealth } from "./hooks/useHealth";

interface Auth {
  user: DemoUser;
  sessionId: string;
}

const toAuth = (s: SessionInfo | null): Auth | null => {
  const user = s && DEMO_USERS.find((u) => u.id === s.user_id);
  return user && s ? { user, sessionId: s.session_id } : null;
};

export default function App() {
  const [backend, setBackend] = useState<Backend | null>(null);
  const [auth, setAuth] = useState<Auth | null | undefined>(undefined); // undefined — проверяем cookie
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const authRef = useRef(auth);
  authRef.current = auth;

  useEffect(() => {
    try {
      localStorage.removeItem("pomnyu.user"); // до v4 пользователь хранился здесь; теперь — cookie сессии
    } catch {
      /* ignore */
    }
    let alive = true;
    loadBackend({
      onUnauthorized() {
        if (authRef.current) setNotice("Сессия закончилась — войдите снова.");
        micEngine.release();
        setAuth(null);
      },
    }).then(async (b) => {
      if (!alive) return;
      setBackend(b);
      try {
        setAuth(toAuth(await b.session()));
      } catch (e) {
        setAuth(null);
        setNotice(e instanceof BackendError && e.kind === "network" ? "Сервер недоступен. Попробуйте войти чуть позже." : null);
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  const login = async (u: DemoUser) => {
    if (!backend || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const a = toAuth(await backend.login(u.id));
      if (!a) throw new BackendError("forbidden", "Такого демо-пользователя нет");
      setAuth(a);
    } catch (e) {
      const err = e instanceof BackendError ? e : null;
      setNotice(
        err?.kind === "network"
          ? "Сервер недоступен. Попробуйте ещё раз."
          : err?.kind === "rate" || err?.kind === "forbidden"
            ? err.message
            : "Не получилось войти. Попробуйте ещё раз.",
      );
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    micEngine.release();
    setAuth(null);
    try {
      await backend?.logout();
    } catch {
      /* cookie всё равно истечёт; на экране входа можно выбрать другого */
    }
  };

  if (auth === undefined || !backend) return null;
  if (!auth) return <Login onPick={login} busy={busy} notice={notice} />;
  return <Main key={`${auth.user.id}:${auth.sessionId}`} backend={backend} auth={auth} logout={logout} />;
}

function Main({ backend, auth, logout }: { backend: Backend; auth: Auth; logout(): void }) {
  const { user } = auth;
  const a = useAssistant(backend, user.id, auth.sessionId);
  const [clock, setClock] = useState(0); // меняется, когда узнали часы сервера
  const onSynced = useCallback(() => setClock((c) => c + 1), []);
  const health = useHealth(backend, a.serverDown, a.setServerDown, onSynced);
  const [agendaOpen, setAgendaOpen] = useState(false);
  const [todayCount, setTodayCount] = useState<number | null>(null);
  const onCount = useCallback((n: number) => setTodayCount(n), []);

  const resetMock = async () => {
    const m = await import("./mock/backend");
    m.resetMock();
    location.reload();
  };

  const agenda = (
    <AgendaPanel backend={backend} version={a.agendaVersion} clock={clock} userId={user.id} highlightIds={a.highlight?.ids ?? []} onCount={onCount} />
  );

  return (
    <div className="flex h-full flex-col">
      <Header
        user={user}
        online={health.online}
        voiceOn={a.speaker.enabled}
        speaking={a.speaker.speaking}
        toggleVoice={() => a.speaker.setEnabled(!a.speaker.enabled)}
        todayCount={todayCount}
        openAgenda={() => setAgendaOpen(true)}
        logout={logout}
      />
      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <Banners mic={a.voice.mic} serverDown={a.serverDown} onRetryServer={health.check} onRetryMic={a.voice.retryMic} />
          <ChatHistory
            messages={a.messages}
            interim={a.voice.interim}
            onSend={a.sendText}
            onUndo={a.undo}
            onRetry={a.retry}
            busy={a.pending}
          />
          <Composer
            mic={a.voice.mic}
            listening={a.voice.listening}
            warm={a.voice.warm}
            level={a.voice.level}
            start={a.voice.start}
            stop={a.voice.stop}
            cancel={a.voice.cancel}
            sendText={a.sendText}
            hint={a.hint}
            clearHint={() => a.setHint(null)}
          />
        </main>
        <aside className="hidden w-[340px] shrink-0 border-l border-line lg:block">{agenda}</aside>
      </div>

      {agendaOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Расписание">
          <button className="absolute inset-0 bg-ink/30" onClick={() => setAgendaOpen(false)} aria-label="Закрыть" />
          <div className="animate-rise absolute inset-x-0 bottom-0 flex h-[75vh] flex-col rounded-t-3xl bg-surface shadow-2xl">
            <div className="flex items-center justify-between px-4 pt-3">
              <span className="mx-auto h-1 w-10 rounded-full bg-line" />
            </div>
            <button onClick={() => setAgendaOpen(false)} className="absolute right-3 top-3 rounded-full p-2 hover:bg-sunken" aria-label="Закрыть">
              <Icon name="x" />
            </button>
            <div className="min-h-0 flex-1">{agenda}</div>
          </div>
        </div>
      )}

      {USE_MOCK && <DevPanel onReset={resetMock} />}
    </div>
  );
}

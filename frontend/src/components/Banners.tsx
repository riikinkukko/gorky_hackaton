import type { MicState } from "../hooks/useRecorder";
import { Icon, type IconName } from "./Icon";

interface Props {
  mic: MicState;
  serverDown: boolean;
  onRetryServer(): void;
  onRetryMic(): void;
}

/** Состояния, при которых часть возможностей недоступна. Текстовый ввод работает всегда. */
export function Banners({ mic, serverDown, onRetryServer, onRetryMic }: Props) {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 px-4 pt-3 empty:hidden sm:px-6">
      {serverDown && (
        <Banner tone="danger" icon="wifi" title="Сервер недоступен">
          Команды не выполняются. Проверьте интернет — мы переподключимся сами.
          <Action onClick={onRetryServer}>Проверить сейчас</Action>
        </Banner>
      )}
      {mic === "insecure" && (
        <Banner tone="warn" icon="lock" title="Микрофон работает только по HTTPS">
          Откройте прототип по адресу, начинающемуся с https://, или на localhost. Пока можно писать текстом.
        </Banner>
      )}
      {mic === "denied" && (
        <Banner tone="warn" icon="mic" title="Нет доступа к микрофону">
          Разрешите микрофон для этого сайта: значок замка в адресной строке → «Микрофон» → «Разрешить».
          <Action onClick={onRetryMic}>Я разрешил</Action>
        </Banner>
      )}
      {mic === "unsupported" && (
        <Banner tone="warn" icon="mic" title="Браузер не умеет записывать звук">
          Откройте прототип в свежем Chrome, Edge, Safari или Firefox. Текстовый ввод работает.
        </Banner>
      )}
      {mic === "error" && (
        <Banner tone="warn" icon="mic" title="Микрофон не найден">
          Подключите гарнитуру или проверьте, не занят ли микрофон другим приложением.
          <Action onClick={onRetryMic}>Попробовать снова</Action>
        </Banner>
      )}
    </div>
  );
}

function Banner({ tone, icon, title, children }: { tone: "warn" | "danger"; icon: IconName; title: string; children: React.ReactNode }) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={`flex items-start gap-3 rounded-2xl px-4 py-3 text-sm ${tone === "danger" ? "bg-danger-soft" : "bg-warn-soft"}`}
    >
      <Icon name={icon} className={`mt-0.5 size-5 shrink-0 ${tone === "danger" ? "text-danger" : "text-warn"}`} />
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5 text-muted">{children}</p>
      </div>
    </div>
  );
}

function Action({ onClick, children }: { onClick(): void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="ml-1 font-medium text-ink underline underline-offset-4">
      {children}
    </button>
  );
}

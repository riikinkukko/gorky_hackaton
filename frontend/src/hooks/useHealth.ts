import { useCallback, useEffect, useState } from "react";
import type { Backend } from "../api";
import { syncServerNow } from "../lib/time";

/** Опрос /health: точка в шапке и автоматическое снятие баннера «Сервер недоступен». */
let synced = false;

export function useHealth(
  backend: Backend,
  serverDown: boolean,
  setServerDown: (v: boolean) => void,
  onSynced?: () => void,
) {
  const [online, setOnline] = useState<boolean | null>(null);

  const check = useCallback(async () => {
    try {
      const h = await backend.health();
      if (!synced) {
        syncServerNow(h.now);
        synced = true;
        onSynced?.();
      }
      setOnline(true);
      setServerDown(false);
    } catch {
      setOnline(false);
      setServerDown(true);
    }
  }, [backend, setServerDown, onSynced]);

  useEffect(() => {
    check();
    const id = setInterval(check, serverDown ? 5000 : 30000);
    const onl = () => check();
    window.addEventListener("online", onl);
    return () => {
      clearInterval(id);
      window.removeEventListener("online", onl);
    };
  }, [check, serverDown]);

  useEffect(() => {
    if (serverDown) setOnline(false);
  }, [serverDown]);

  return { online, check };
}

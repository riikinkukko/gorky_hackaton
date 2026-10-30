import type { Backend, BackendOptions } from "./backend";
import { createHttpBackend } from "./http";

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

/** Мок подгружается отдельным чанком и не попадает в боевую сборку. */
export async function loadBackend(opts: BackendOptions): Promise<Backend> {
  if (USE_MOCK) {
    const m = await import("../mock/backend");
    return m.createMockBackend(opts);
  }
  return createHttpBackend(opts);
}

export * from "./backend";
export * from "./types";
export { RATE_LIMIT_MESSAGE } from "./http";

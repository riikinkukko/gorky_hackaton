// Переключатели мок-режима (панель «Мок» в углу): позволяют показать состояния ошибок без бэкенда.
export interface MockSettings {
  offline: boolean; // сервер недоступен
  slow: boolean; // ответ ~7 с — проверка состояния «долгий ответ»
  rate: boolean; // каждый запрос /api/chat отвечает 429
}

let state: MockSettings = { offline: false, slow: false, rate: false };
const subs = new Set<() => void>();

export const mockSettings = {
  get: () => state,
  set(patch: Partial<MockSettings>) {
    state = { ...state, ...patch };
    subs.forEach((f) => f());
  },
  subscribe(f: () => void) {
    subs.add(f);
    return () => void subs.delete(f);
  },
};

// Сверка с зеркалом контракта чата 0 (frontend/src/api/contracts.ts).
// Запуск: npm run check:contract — падает, если типы фронта и контракт разошлись.
// В обычную сборку не входит, чтобы фронт собирался и без contracts.ts.
import type * as C from "./contracts";
import type * as F from "./types";

type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;

export type _checks = [
  Assert<Same<F.ItemType, C.ItemType>>,
  Assert<Same<F.ItemStatus, C.ItemStatus>>,
  Assert<Same<F.AssistantReply["action"], C.AssistantReply["action"]>>,
  // ответы сервера должны подходить под типы, которыми пользуется фронт
  Assert<[C.Item] extends [F.Item] ? true : false>,
  Assert<[C.AssistantReply] extends [F.AssistantReply] ? true : false>,
];

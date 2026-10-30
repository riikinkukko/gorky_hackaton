"""Ограничение частоты запросов (SEC-03). Один воркер uvicorn → счётчики в памяти, без Redis."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_s: float) -> float | None:
        """Засчитать запрос. None — можно; иначе через сколько секунд повторить."""
        if limit <= 0:
            return None
        now = time.monotonic()
        q = self._hits[key]
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return max(1.0, q[0] + window_s - now)
        q.append(now)
        if len(self._hits) > 50_000:  # не копим ключи бесконечно
            for k in [k for k, v in self._hits.items() if not v][:10_000]:
                del self._hits[k]
        return None

    def check(self, key: str, limit: int, window_s: float) -> None:
        retry = self.hit(key, limit, window_s)
        if retry is not None:
            raise HTTPException(
                status_code=429,
                detail="Слишком часто — подождите пару секунд",
                headers={"Retry-After": str(int(retry) + 1)},
            )

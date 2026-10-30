"""FakeSearchEngine — поиск для STORAGE_IMPL=memory.

Раньше здесь было совпадение по 4-буквенным префиксам, и выдача расходилась с PostgreSQL
(«старую» совпадало со «Стартом»). Теперь это тот же алгоритм, что у HybridSearchEngine:
app.storage.memory.InMemorySearchEngine поверх app.storage.ranking (чат 3).
"""

from __future__ import annotations

from app.fakes.repository import InMemoryRepository
from app.storage.memory import InMemorySearchEngine


class FakeSearchEngine(InMemorySearchEngine):
    def __init__(self, repo: InMemoryRepository) -> None:
        super().__init__(repo)

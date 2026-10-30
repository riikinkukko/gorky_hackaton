"""Сборка зависимостей: какая реализация каждого интерфейса подключена.

Соглашение для соседних чатов: каждый пакет отдаёт фабрики в своём `__init__.py`
(импортируются лениво, только когда включены переменной окружения):

    app.nlu      → build_date_resolver(settings) -> DateResolver          (NLU_IMPL=real)
                   build_nlu(settings, dates) -> NLU
    app.storage  → build_repository(settings) -> ItemRepository            (STORAGE_IMPL=postgres)
                   build_search(settings, repo) -> SearchEngine
    app.voice    → build_stt(settings) -> STTProvider                      (STT_PROVIDER≠fake)
                   build_tts(settings) -> TTSProvider                      (TTS_PROVIDER≠fake)
    app.dialog   → build_orchestrator(settings, nlu, dates, repo, search) -> Orchestrator
                                                                           (DIALOG_IMPL=real)
"""

from __future__ import annotations

import importlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.api.ratelimit import SlidingWindow
from app.config import Settings
from app.contracts import (
    NLU,
    DateResolver,
    DialogState,
    ItemRepository,
    Orchestrator,
    SearchEngine,
    STTProvider,
    TTSProvider,
)
from app.fakes.dates import FakeDateResolver
from app.fakes.nlu import FakeNLU
from app.fakes.orchestrator import FakeOrchestrator
from app.fakes.repository import InMemoryRepository
from app.fakes.search import FakeSearchEngine
from app.fakes.voice import FakeSTT, FakeTTS


class SessionStore:
    """Состояние диалога по (user_id, session_id) в памяти: LRU на max_items, TTL ttl_s.

    Ключ включает пользователя: чужой session_id не продолжает чужой диалог (SEC-01).
    """

    def __init__(self, max_items: int = 10_000, ttl_s: float = 86_400) -> None:
        self._states: OrderedDict[tuple[int, str], tuple[float, DialogState]] = OrderedDict()
        self.max_items, self.ttl_s = max_items, ttl_s

    def get(self, session_id: str, user_id: int) -> DialogState:
        key = (user_id, session_id)
        hit = self._states.get(key)
        if hit is None or time.monotonic() - hit[0] > self.ttl_s:
            return DialogState(session_id=session_id, user_id=user_id)
        self._states.move_to_end(key)
        return hit[1]

    def save(self, state: DialogState) -> None:
        key = (state.user_id, state.session_id)
        self._states[key] = (time.monotonic(), state)
        self._states.move_to_end(key)
        while len(self._states) > self.max_items:
            self._states.popitem(last=False)


@dataclass
class Container:
    settings: Settings
    dates: DateResolver
    nlu: NLU
    repo: ItemRepository
    search: SearchEngine
    stt: STTProvider
    tts: TTSProvider
    orchestrator: Orchestrator
    sessions: SessionStore = field(default_factory=SessionStore)
    limiter: SlidingWindow = field(default_factory=SlidingWindow)
    impl: dict[str, str] = field(default_factory=dict)


def _factory(module: str, name: str) -> Any:
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as e:
        raise RuntimeError(
            f"Реальная реализация не найдена: {module}.{name}. "
            f"Верните заглушку в .env или допишите фабрику. ({e})"
        ) from e


def build_container(settings: Settings) -> Container:
    impl: dict[str, str] = {}

    # --- даты и NLU (чат 1) ---
    dates: DateResolver
    nlu: NLU
    if settings.nlu_impl == "real":
        dates = _factory("app.nlu", "build_date_resolver")(settings)
        nlu = _factory("app.nlu", "build_nlu")(settings, dates)
        impl["nlu"] = f"real:{settings.llm_provider}"
    else:
        dates = FakeDateResolver()
        nlu = FakeNLU(dates)
        impl["nlu"] = "fake"

    # --- хранение и поиск (чат 3) ---
    repo: ItemRepository
    search: SearchEngine
    if settings.storage_impl == "postgres":
        repo = _factory("app.storage", "build_repository")(settings)
        search = _factory("app.storage", "build_search")(settings, repo)
        impl["storage"] = "postgres"
    else:
        mem = InMemoryRepository()
        if settings.seed_csv:
            mem.load_csv(settings.seed_csv, settings.seed_user_id, settings.tz)
        repo, search = mem, FakeSearchEngine(mem)
        impl["storage"] = "memory"

    # --- голос (чат 4) ---
    stt: STTProvider = (
        FakeSTT(settings.fake_stt_text)
        if settings.stt_provider == "fake"
        else _factory("app.voice", "build_stt")(settings)
    )
    tts: TTSProvider = (
        FakeTTS()
        if settings.tts_provider == "fake"
        else _factory("app.voice", "build_tts")(settings)
    )
    impl["stt"], impl["tts"] = settings.stt_provider, settings.tts_provider

    # --- диалог (чат 2) ---
    orchestrator: Orchestrator
    if settings.dialog_impl == "real":
        orchestrator = _factory("app.dialog", "build_orchestrator")(
            settings=settings, nlu=nlu, dates=dates, repo=repo, search=search
        )
        impl["dialog"] = "real"
    else:
        orchestrator = FakeOrchestrator(nlu, repo, search)
        impl["dialog"] = "fake"

    return Container(
        settings=settings,
        dates=dates,
        nlu=nlu,
        repo=repo,
        search=search,
        stt=stt,
        tts=tts,
        orchestrator=orchestrator,
        impl=impl,
    )

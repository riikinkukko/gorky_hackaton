"""Общий контракт между направлениями. Меняет только чат 0. История — docs/CONTRACT.md."""
from __future__ import annotations
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol
from pydantic import BaseModel, Field

CONTRACT_VERSION = "1.3"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ItemType(StrEnum):
    TASK = "task"; MEETING = "meeting"; REMINDER = "reminder"

class ItemStatus(StrEnum):
    OPEN = "open"; DONE = "done"; CANCELLED = "cancelled"; UNKNOWN = "unknown"

class Intent(StrEnum):
    CREATE = "create_item"; LIST = "list_items"; SEARCH = "search_items"
    UPDATE = "update_item"; COMPLETE = "complete_item"; DELETE = "delete_item"
    UNDO = "undo"; CONFIRM = "confirm"; REJECT = "reject"
    SMALLTALK = "smalltalk"; UNKNOWN = "unknown"


class TimeSpec(BaseModel):
    """Результат DateResolver. Все datetime — с таймзоной."""
    text: str                         # исходная фраза: «в пятницу в 11»
    start: datetime | None = None
    end: datetime | None = None       # конец периода или встречи
    has_time: bool = False            # False — только дата («в пятницу»)
    has_date: bool = False            # v1.1: дата названа явно; «в три» → has_date=False, has_time=True
    is_period: bool = False           # «на следующей неделе»
    ambiguous: bool = False           # нужен уточняющий вопрос


class ItemRef(BaseModel):
    """На что ссылается пользователь: «её», «встречу с Марией в субботу»."""
    pronoun: bool = False
    description: str | None = None
    item_id: int | None = None


class Slots(BaseModel):
    type: ItemType | None = None
    title: str | None = None
    when: TimeSpec | None = None
    duration_min: int | None = None
    participants: list[str] = Field(default_factory=list)   # леммы: «Иван»
    organization: str | None = None
    recurrence: str | None = None       # RRULE, напр. "FREQ=WEEKLY;BYDAY=MO"
    query: str | None = None            # для поиска, синонимы уже раскрыты
    tense: Literal["past", "future", "any"] = "any"
    status: ItemStatus | None = None
    target: ItemRef | None = None       # для update / complete / delete
    new_when: TimeSpec | None = None
    new_title: str | None = None


class Command(BaseModel):
    """Выход NLU."""
    intent: Intent
    slots: Slots = Field(default_factory=Slots)
    confidence: float = 1.0
    source: Literal["rules", "llm", "fallback"]
    raw_text: str
    # v1.3: роль реплики в разговоре (NLU → диалог, dialog/turns.py). None — NLU не решил,
    # диалог определяет сам по тексту. full — своё действие и объект/период; continuation —
    # только время / повтор / участник / местоимение к тому, что в фокусе; confirm / reject.
    turn: Literal["full", "continuation", "confirm", "reject"] | None = None


class Item(BaseModel):
    id: int = 0                           # v1.1: при create не передаётся, id выдаёт репозиторий
    user_id: int
    type: ItemType
    title: str
    title_original: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    due_at: datetime | None = None
    has_time: bool = True
    status: ItemStatus = ItemStatus.OPEN
    rrule: str | None = None
    person: list[str] = Field(default_factory=list)
    organization: list[str] = Field(default_factory=list)
    topic: str | None = None
    object: str | None = None
    source: Literal["voice", "text", "import"] = "text"
    created_at: datetime = Field(default_factory=_utcnow)   # v1.1: репозиторий ставит сам
    updated_at: datetime = Field(default_factory=_utcnow)


class ActionRecord(BaseModel):
    """v1.1. Журнал изменений для undo. Хранит чат 3, пишет и читает чат 2."""
    id: int = 0                           # выдаёт репозиторий
    session_id: str
    user_id: int
    kind: Literal["create", "update", "complete", "delete"]
    item_id: int
    before: Item | None = None            # снимок до (None для create)
    after: Item | None = None             # снимок после (None для delete)
    undone: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class DialogState(BaseModel):
    session_id: str
    user_id: int
    pending: Command | None = None        # незавершённая команда
    awaiting: str | None = None           # "time" | "date" | "title" | "target" | "confirm_conflict" | "confirm_delete"
    last_item_ids: list[int] = Field(default_factory=list)
    last_action_id: int | None = None     # для undo
    last_command: Command | None = None   # v1.2: последняя выполненная команда — для «А на следующей неделе?», «нет, в 11»


class AssistantReply(BaseModel):
    text: str                             # показ + озвучка
    action: Literal["created", "listed", "found", "updated", "completed",
                    "deleted", "undone", "clarify", "conflict", "none"]
    items: list[Item] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)   # кнопки: «16:30», «Всё равно 15:30»
    debug: dict | None = None             # intent, slots, source, latency — для отладки


# ---------- Интерфейсы между направлениями ----------

class NLU(Protocol):                       # чат 1
    async def parse(self, text: str, state: DialogState, now: datetime) -> Command: ...

class DateResolver(Protocol):              # чат 1
    def resolve(self, text: str, now: datetime,
                mode: Literal["point", "period"] = "point") -> TimeSpec: ...

class ItemRepository(Protocol):            # чат 3
    """list_range / find_overlaps: полуоткрытый интервал [start, end).
    Запись попадает в период по coalesce(starts_at, due_at); удалённые (soft) не возвращаются.
    Повторяющаяся серия (rrule) возвращается одной записью, если её начало < end;
    развёртку по вхождениям делает домен (чат 2)."""
    async def create(self, item: Item) -> Item: ...
    async def get(self, user_id: int, item_id: int) -> Item | None: ...
    async def update(self, item: Item) -> Item: ...
    async def soft_delete(self, user_id: int, item_id: int) -> None: ...
    async def restore(self, user_id: int, item_id: int) -> None: ...
    async def list_range(self, user_id: int, start: datetime, end: datetime,
                         type: ItemType | None = None) -> list[Item]: ...
    async def find_overlaps(self, user_id: int, start: datetime, end: datetime) -> list[Item]: ...
    # v1.1: журнал для undo
    async def log_action(self, action: ActionRecord) -> int: ...          # → action_id
    async def get_action(self, user_id: int, action_id: int) -> ActionRecord | None: ...
    async def mark_undone(self, user_id: int, action_id: int) -> None: ...

class SearchEngine(Protocol):              # чат 3
    async def search(self, user_id: int, slots: Slots, now: datetime,
                     limit: int = 20) -> list[tuple[Item, float]]: ...

class STTProvider(Protocol):               # чат 4
    async def transcribe(self, audio: bytes, mime: str) -> str: ...

class TTSProvider(Protocol):               # чат 4
    async def synthesize(self, text: str) -> tuple[bytes, str]: ...   # (аудио, mime)

class Orchestrator(Protocol):              # чат 2
    """handle() меняет переданный DialogState на месте; API сохраняет его после вызова."""
    async def handle(self, text: str, state: DialogState, now: datetime) -> AssistantReply: ...

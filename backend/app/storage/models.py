"""SQLAlchemy-модели (схема из раздела 5.2 документа 01)."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    tz: Mapped[str] = mapped_column(Text, server_default="Europe/Moscow")
    ics_token: Mapped[str] = mapped_column(
        Text, unique=True, server_default=text("md5(random()::text || clock_timestamp()::text)")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemRow(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_original: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_time: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, server_default="open")
    rrule: Mapped[str | None] = mapped_column(Text)
    person: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    organization: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    topic: Mapped[str | None] = mapped_column(Text)
    object: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="text")
    source_ref: Mapped[str | None] = mapped_column(Text)  # "csv:688" — id в исходном файле
    # --- поисковые поля (заполняет репозиторий) ---
    person_norm: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    organization_norm: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    search_text: Mapped[str] = mapped_column(Text, server_default="")
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('simple', coalesce(search_text, '')), 'B')",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # «момент» записи: начало встречи или срок задачи — для диапазонов и сортировки
    sort_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), Computed("coalesce(starts_at, due_at)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("type IN ('task','meeting','reminder')", name="items_type_chk"),
        CheckConstraint("status IN ('open','done','cancelled','unknown')", name="items_status_chk"),
        CheckConstraint("source IN ('voice','text','import')", name="items_source_chk"),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="items_interval_chk",
        ),
        Index("ix_items_user_starts", "user_id", "starts_at"),
        Index(
            "ux_items_user_source_ref",
            "user_id",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
        Index("ix_items_user_due", "user_id", "due_at"),
        Index(
            "ix_items_user_sort_alive",
            "user_id",
            "sort_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_items_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_items_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_items_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        Index("ix_items_person_norm", "person_norm", postgresql_using="gin"),
        Index("ix_items_org_norm", "organization_norm", postgresql_using="gin"),
        Index(
            "ix_items_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DialogSessionRow(Base):
    __tablename__ = "dialog_sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    last_item_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionRow(Base):
    """Журнал действий: основа undo (DialogState.last_action_id)."""

    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # create | update | complete | delete
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_actions_user_created", "user_id", "created_at"),
        CheckConstraint("kind IN ('create','update','complete','delete')", name="actions_kind_chk"),
    )


class TurnRow(Base):
    """Ход диалога: для метрик и отладки."""

    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, index=True)
    input_type: Mapped[str] = mapped_column(Text)  # voice | text
    transcript: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    slots: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

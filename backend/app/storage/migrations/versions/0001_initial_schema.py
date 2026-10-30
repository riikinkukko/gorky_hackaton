"""Начальная схема: users, items (FTS + trigram + pgvector), dialog_sessions, actions, turns.

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "turns",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("input_type", sa.Text(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("latency_ms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_turns_session_id"), "turns", ["session_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), server_default="Europe/Moscow", nullable=False),
        sa.Column(
            "ics_token",
            sa.Text(),
            server_default=sa.text("md5(random()::text || clock_timestamp()::text)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ics_token"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "dialog_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "state", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "last_item_ids", postgresql.ARRAY(sa.BigInteger()), server_default="{}", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dialog_sessions_user_id"), "dialog_sessions", ["user_id"], unique=False
    )
    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_original", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_time", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("rrule", sa.Text(), nullable=True),
        sa.Column("person", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("organization", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("object", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="text", nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("person_norm", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "organization_norm", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("search_text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
                "setweight(to_tsvector('simple', coalesce(search_text, '')), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column(
            "sort_at",
            sa.DateTime(timezone=True),
            sa.Computed("coalesce(starts_at, due_at)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('voice','text','import')", name="items_source_chk"),
        sa.CheckConstraint(
            "status IN ('open','done','cancelled','unknown')", name="items_status_chk"
        ),
        sa.CheckConstraint("type IN ('task','meeting','reminder')", name="items_type_chk"),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="items_interval_chk",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_items_embedding",
        "items",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_items_org_norm", "items", ["organization_norm"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_items_person_norm", "items", ["person_norm"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_items_search_trgm",
        "items",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_items_title_trgm",
        "items",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index("ix_items_tsv", "items", ["search_tsv"], unique=False, postgresql_using="gin")
    op.create_index("ix_items_user_due", "items", ["user_id", "due_at"], unique=False)
    op.create_index(op.f("ix_items_user_id"), "items", ["user_id"], unique=False)
    op.create_index(
        "ix_items_user_sort_alive",
        "items",
        ["user_id", "sort_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_items_user_starts", "items", ["user_id", "starts_at"], unique=False)
    op.create_index(
        "ux_items_user_source_ref",
        "items",
        ["user_id", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('create','update','complete','delete')", name="actions_kind_chk"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_actions_user_created", "actions", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_actions_user_created", table_name="actions")
    op.drop_table("actions")
    op.drop_index("ux_items_user_source_ref", table_name="items")
    op.drop_index("ix_items_user_starts", table_name="items")
    op.drop_index(
        "ix_items_user_sort_alive",
        table_name="items",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_items_user_id"), table_name="items")
    op.drop_index("ix_items_user_due", table_name="items")
    op.drop_index("ix_items_tsv", table_name="items", postgresql_using="gin")
    op.drop_index(
        "ix_items_title_trgm",
        table_name="items",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_items_search_trgm",
        table_name="items",
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.drop_index("ix_items_person_norm", table_name="items", postgresql_using="gin")
    op.drop_index("ix_items_org_norm", table_name="items", postgresql_using="gin")
    op.drop_index(
        "ix_items_embedding",
        table_name="items",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("items")
    op.drop_index(op.f("ix_dialog_sessions_user_id"), table_name="dialog_sessions")
    op.drop_table("dialog_sessions")
    op.drop_table("users")
    op.drop_index(op.f("ix_turns_session_id"), table_name="turns")
    op.drop_table("turns")

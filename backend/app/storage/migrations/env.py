from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.storage.config import DATABASE_URL
from app.storage.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # psycopg3 работает и синхронно, поэтому тот же DATABASE_URL подходит Alembic.
    engine = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

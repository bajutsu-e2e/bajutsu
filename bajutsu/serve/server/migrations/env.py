"""Alembic environment for the hosted backend's system of record (BE-0015 7a-2).

The database URL comes from ``BAJUTSU_DATABASE_URL`` (the same env var ``repository_from_env`` reads),
and ``target_metadata`` is the ORM's metadata so ``alembic revision --autogenerate`` and the drift
test compare migrations against models.py. Imported only when Alembic runs, never on the default
serve/CLI path."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from bajutsu.serve.server.models import Base

config = context.config

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("BAJUTSU_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("BAJUTSU_DATABASE_URL is required to run the migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

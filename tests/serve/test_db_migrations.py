"""The initial Alembic migration must build the same schema as the ORM metadata (BE-0015 7a-2) —
a guard against the migration drifting from models.py. It compares a per-table schema signature
(columns + types + nullability, foreign keys, unique constraints), not just the set of table names,
so a column or constraint that drifts is caught too. It runs Alembic against a SQLite file, so the
gate needs no live Postgres."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect

import bajutsu.serve.server as server_pkg
from bajutsu.serve.server.models import Base


def _alembic_config():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(server_pkg.__file__).parent / "migrations"))
    return cfg


def _schema_signature(engine) -> dict[str, Any]:
    """Per-table (columns+types+nullability, foreign keys, unique constraints) for every table but
    Alembic's own bookkeeping one — enough to catch column/constraint drift, not just a missing
    table."""
    insp = inspect(engine)
    signature: dict[str, Any] = {}
    for table in insp.get_table_names():
        if table == "alembic_version":
            continue
        columns = {(c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns(table)}
        foreign_keys = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in insp.get_foreign_keys(table)
        }
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints(table)}
        signature[table] = (columns, foreign_keys, uniques)
    return signature


def test_initial_migration_matches_the_orm_schema(tmp_path, monkeypatch) -> None:
    from alembic import command

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    command.upgrade(_alembic_config(), "head")
    migrated = _schema_signature(create_engine(url))

    fresh = create_engine("sqlite://")
    Base.metadata.create_all(fresh)
    assert migrated == _schema_signature(fresh)


def test_downgrade_base_removes_the_tables(tmp_path, monkeypatch) -> None:
    from alembic import command

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    remaining = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert remaining == set()

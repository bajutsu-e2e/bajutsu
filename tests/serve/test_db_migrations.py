"""The initial Alembic migration must build the same schema as the ORM metadata (BE-0015 7a-2) —
a guard against the migration drifting from models.py. It compares a per-table schema signature
(columns + types + nullability, foreign keys, unique constraints), not just the set of table names,
so a column or constraint that drifts is caught too. It runs Alembic against a SQLite file, so the
gate needs no live Postgres."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine, inspect

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


def _load_migration(name: str):
    """Load a migration module by filename stem (e.g. '0010_run_project_fk_set_null')."""
    path = Path(server_pkg.__file__).parent / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_project_id_fk_name_reflects_the_correct_constraint() -> None:
    # Exercises the reflection helper from migration 0010 against a SQLite schema where the FK
    # is created with an explicit name — simulating the Postgres auto-name the migration assumes.
    # Guards that the constrained_columns filter and name extraction work correctly.
    meta = MetaData()
    Table("projects", meta, Column("id", String, primary_key=True))
    Table(
        "runs",
        meta,
        Column("id", String, primary_key=True),
        Column("project_id", String, ForeignKey("projects.id", name="runs_project_id_fkey")),
    )
    engine = create_engine("sqlite://")
    meta.create_all(engine)

    mod = _load_migration("0010_run_project_fk_set_null")
    with engine.connect() as conn:
        name = mod._project_id_fk_name(conn)
    assert name == "runs_project_id_fkey"


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

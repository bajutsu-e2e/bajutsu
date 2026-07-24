"""The initial Alembic migration must build the same schema as the ORM metadata (BE-0015 7a-2) —
a guard against the migration drifting from models.py. It compares a per-table schema signature
(columns + types + nullability, foreign keys, unique constraints), not just the set of table names,
so a column or constraint that drifts is caught too.

The upgrade/downgrade tests are parametrized over both dialects (BE-0309): the fast gate runs them
against a throwaway SQLite file, and the serve-db.yml lane reruns the same assertions against a real
Postgres service — the only place the dialect-specific migration code (0010's `postgresql` FK branch
and the JSONB column variants) actually executes."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)

import bajutsu.serve.server as server_pkg
from bajutsu.serve.server.models import Base

_POSTGRES_URL_ENV = "BAJUTSU_TEST_POSTGRES_URL"

# The serve-db.yml lane sets this to fail (not skip) when the Postgres URL is missing, so a future
# edit that drops the URL from the workflow can't make the one lane whose whole purpose is Postgres
# report a false green. The fast gate leaves it unset, so it keeps skipping the Postgres parameter.
_REQUIRE_POSTGRES_ENV = "BAJUTSU_REQUIRE_POSTGRES"

# Both dialects run the same migration assertions. The Postgres parameter carries the `postgres`
# marker so the default gate (which deselects `-m 'not postgres'`) stays SQLite-only, while the
# serve-db.yml lane runs it with `-m postgres` against a real Postgres service.
_DIALECTS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param("postgresql", id="postgresql", marks=pytest.mark.postgres),
]


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


def _reset_schema(url: str) -> None:
    """Drop every table so an upgrade or `create_all` starts from an empty database. The SQLite
    branch uses a throwaway file per test (fresh), but the shared Postgres service persists across
    tests, so each test must clear it explicitly to avoid leftover tables from a prior parameter."""
    engine = create_engine(url)
    try:
        if engine.dialect.name == "postgresql":
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
        else:
            meta = MetaData()
            meta.reflect(bind=engine)
            meta.drop_all(engine)
    finally:
        engine.dispose()


@pytest.fixture
def migration_db_url(request, tmp_path, monkeypatch) -> str:
    """A clean, empty database URL for the requested dialect, wired into `BAJUTSU_DATABASE_URL` so
    the Alembic config resolves it. SQLite uses a throwaway file; Postgres uses the service from
    `BAJUTSU_TEST_POSTGRES_URL`, skipping when it is unset so the fast gate never needs one — unless
    `BAJUTSU_REQUIRE_POSTGRES` is set (the dedicated lane does), in which case a missing URL fails
    loudly instead of skipping, so the lane can't report a false green."""
    if request.param == "postgresql":
        url = os.environ.get(_POSTGRES_URL_ENV)
        if not url:
            if os.environ.get(_REQUIRE_POSTGRES_ENV):
                pytest.fail(
                    f"{_REQUIRE_POSTGRES_ENV} is set but {_POSTGRES_URL_ENV} is missing — the "
                    "Postgres lane is misconfigured (it must run against a real Postgres service)."
                )
            pytest.skip(f"{_POSTGRES_URL_ENV} not set")
    else:
        url = f"sqlite:///{tmp_path / 'm.db'}"
    _reset_schema(url)
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    return url


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


@pytest.mark.parametrize("migration_db_url", _DIALECTS, indirect=True)
def test_initial_migration_matches_the_orm_schema(migration_db_url) -> None:
    from alembic import command

    command.upgrade(_alembic_config(), "head")
    migrated_engine = create_engine(migration_db_url)
    try:
        migrated = _schema_signature(migrated_engine)
    finally:
        # Dispose before `_reset_schema` drops the Postgres `public` schema, so this test invocation
        # fully owns its connections and no pooled connection lingers during the DDL.
        migrated_engine.dispose()

    _reset_schema(migration_db_url)
    fresh = create_engine(migration_db_url)
    try:
        Base.metadata.create_all(fresh)
        assert migrated == _schema_signature(fresh)
    finally:
        fresh.dispose()


@pytest.mark.parametrize("migration_db_url", _DIALECTS, indirect=True)
def test_downgrade_base_removes_the_tables(migration_db_url) -> None:
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(migration_db_url)
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()
    assert remaining == set()

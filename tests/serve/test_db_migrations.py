"""The initial Alembic migration must build the same schema as the ORM metadata (BE-0015 7a-2) —
a guard against the migration drifting from models.py. It runs Alembic against a SQLite file, so the
gate needs no live Postgres."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

import bajutsu.serve.server as server_pkg
from bajutsu.serve.server.models import Base


def _alembic_config():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(server_pkg.__file__).parent / "migrations"))
    return cfg


def test_initial_migration_builds_the_orm_table_set(tmp_path, monkeypatch) -> None:
    from alembic import command

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    command.upgrade(_alembic_config(), "head")

    migrated = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}

    fresh = create_engine("sqlite://")
    Base.metadata.create_all(fresh)
    assert migrated == set(inspect(fresh).get_table_names())


def test_downgrade_base_removes_the_tables(tmp_path, monkeypatch) -> None:
    from alembic import command

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    remaining = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert remaining == set()

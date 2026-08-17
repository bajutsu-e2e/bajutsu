"""The system-of-record schema (BE-0015 7a): the five tables, the org_id foreign keys that 7c will
scope on, and the per-org project uniqueness. Exercised on in-memory SQLite in the gate and, behind
the `postgres` marker, against a real Postgres service in the serve-db.yml lane (BE-0309) — the same
models the gate tests and production Postgres both build from."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, inspect

from bajutsu.serve.server.models import Base


def test_metadata_creates_the_five_tables(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"orgs", "users", "projects", "runs", "audit_log"} <= tables


def test_runs_has_its_columns_and_foreign_keys(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("runs")}
    assert {
        "id",
        "org_id",
        "project_id",
        "created_by",
        "status",
        "ok",
        "created_at",
        "summary",
        # Run provenance mirrored from manifest.json so flakiness can group by scenario (BE-0220).
        "scenario_hash",
        "tool_version",
        "git_revision",
        # The other half of that grouping key: the OS version the run happened on (BE-0358).
        "device_runtime",
    } <= cols
    referred = {fk["referred_table"] for fk in insp.get_foreign_keys("runs")}
    assert {"orgs", "projects", "users"} <= referred


def test_projects_are_unique_per_org(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    uniques = {tuple(u["column_names"]) for u in inspect(engine).get_unique_constraints("projects")}
    assert ("org_id", "name") in uniques


def test_audit_log_references_org_and_actor(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    referred = {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("audit_log")}
    assert {"orgs", "users"} <= referred

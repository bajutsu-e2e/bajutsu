"""The system-of-record schema (BE-0015 7a): the five tables, the org_id foreign keys that 7c will
scope on, and the per-org project uniqueness. Exercised on in-memory SQLite in the gate and, behind
the `postgres` marker, against a real Postgres service in the serve-db.yml lane (BE-0309) — the same
models the gate tests and production Postgres both build from."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, inspect

from bajutsu.serve.server.models import Base


def test_metadata_creates_the_core_tables(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"orgs", "users", "runs", "audit_log"} <= tables
    # The project layer is gone (BE-0404): the org holds its config source directly, and a run
    # carries its partition as a plain `label` column instead of a foreign key into a registry.
    assert "projects" not in tables


def test_runs_has_its_columns_and_foreign_keys(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("runs")}
    assert {
        "id",
        "org_id",
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
        # The run-history partition and the target axis (BE-0404 units 2 and 3).
        "label",
        "target",
    } <= cols
    referred = {fk["referred_table"] for fk in insp.get_foreign_keys("runs")}
    assert {"orgs", "users"} <= referred


def test_orgs_hold_one_config_source(serve_engine: Callable[..., Engine]) -> None:
    # The durable memory a hosted replica reads to recover an uploaded bundle it never received
    # (BE-0404 unit 1) — one record per org, on the org row, not a registry of named bindings.
    engine = serve_engine()
    Base.metadata.create_all(engine)
    assert "config_source" in {c["name"] for c in inspect(engine).get_columns("orgs")}


def test_audit_log_references_org_and_actor(serve_engine: Callable[..., Engine]) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    referred = {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("audit_log")}
    assert {"orgs", "users"} <= referred

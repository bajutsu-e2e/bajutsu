"""The DB-backed `SqlProjectRegistry` (BE-0225 unit 2): the same `ProjectRegistry` seam as the local
JSON store, but delegating to the SQL `Repository` (unit 1) and partitioning runs by the
`runs.project_id` column. Built against an in-memory SQLite database in the gate and, behind the
`postgres` marker, against a real Postgres service in the serve-db.yml lane (BE-0309), matching
`test_db_repository.py`."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine

from bajutsu.serve.project_registry import SqlProjectRegistry
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base


def _repo_fk(serve_engine: Callable[..., Engine]) -> SqlRepository:
    """A repository with FK enforcement (needed for the runs→projects FK).

    `serve_engine(foreign_keys=True)` sets the SQLite `PRAGMA foreign_keys=ON` per connection;
    Postgres enforces the FK natively, so the flag is a no-op there.
    """
    engine = serve_engine(foreign_keys=True)
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _registry(serve_engine: Callable[..., Engine]) -> SqlProjectRegistry:
    return _repo_and_registry(serve_engine)[1]


def _repo_and_registry(
    serve_engine: Callable[..., Engine],
) -> tuple[SqlRepository, SqlProjectRegistry]:
    """A registry plus the repository it wraps, for tests that also record runs directly (runs go
    through the repository's own contract, not the registry)."""
    repo = _repo_fk(serve_engine)
    repo.ensure_org("default", slug="default", name="Default")
    return repo, SqlProjectRegistry(repo)


def test_add_then_get_and_list_a_project(serve_engine: Callable[..., Engine]) -> None:
    reg = _registry(serve_engine)
    source = {"kind": "file", "locator": {"path": "checkout.yaml"}}

    added = reg.add(org_id="default", name="checkout", source=source)

    assert added.name == "checkout" and added.source == source and added.id
    got = reg.get(org_id="default", name="checkout")
    assert got is not None and got.id == added.id
    assert [p.name for p in reg.list_projects(org_id="default")] == ["checkout"]


def test_add_is_idempotent_by_name_and_rebinds_the_source(
    serve_engine: Callable[..., Engine],
) -> None:
    reg = _registry(serve_engine)
    first = reg.add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "a.yaml"}}
    )

    rebound = reg.add(
        org_id="default", name="checkout", source={"kind": "file", "locator": {"path": "b.yaml"}}
    )

    # Re-adding an existing name reuses its id (no (org_id, name) unique-constraint collision) and
    # rebinds the source — the seam hides the resolve-existing-id-first contract unit 1 documents.
    assert rebound.id == first.id
    assert rebound.source == {"kind": "file", "locator": {"path": "b.yaml"}}
    assert [p.name for p in reg.list_projects(org_id="default")] == ["checkout"]


def test_remove_deregisters_the_project(serve_engine: Callable[..., Engine]) -> None:
    reg = _registry(serve_engine)
    reg.add(org_id="default", name="checkout", source=None)

    reg.remove(org_id="default", name="checkout")

    assert reg.get(org_id="default", name="checkout") is None


def test_set_and_resolve_the_active_project(serve_engine: Callable[..., Engine]) -> None:
    reg = _registry(serve_engine)
    reg.add(org_id="default", name="checkout", source=None)
    reg.add(org_id="default", name="settings", source=None)

    reg.set_active(org_id="default", name="settings")

    active = reg.resolve_active(org_id="default")
    assert active is not None and active.name == "settings"


def test_no_active_project_until_one_is_set(serve_engine: Callable[..., Engine]) -> None:
    reg = _registry(serve_engine)
    reg.add(org_id="default", name="checkout", source=None)
    assert reg.resolve_active(org_id="default") is None


def test_removing_the_active_project_clears_active(serve_engine: Callable[..., Engine]) -> None:
    reg = _registry(serve_engine)
    reg.add(org_id="default", name="checkout", source=None)
    reg.set_active(org_id="default", name="checkout")

    reg.remove(org_id="default", name="checkout")

    assert reg.resolve_active(org_id="default") is None


def test_run_ids_partition_by_the_project_id_column(serve_engine: Callable[..., Engine]) -> None:
    repo, reg = _repo_and_registry(serve_engine)
    p = reg.add(org_id="default", name="checkout", source=None)
    other = reg.add(org_id="default", name="settings", source=None)
    repo.record_run(RunRecord(id="run-1", org_id="default", status="done", project_id=p.id))
    repo.record_run(RunRecord(id="run-2", org_id="default", status="done", project_id=p.id))
    repo.record_run(RunRecord(id="run-3", org_id="default", status="done", project_id=other.id))

    ids = reg.run_ids(org_id="default", project_id=p.id)

    assert set(ids) == {"run-1", "run-2"}
    assert "run-3" not in ids


def test_run_ids_is_not_capped_at_the_list_runs_default(
    serve_engine: Callable[..., Engine],
) -> None:
    """The seam's `run_ids` docstring promises *all* of a project's runs, and `LocalProjectRegistry`
    returns an unbounded list — so the DB backend must not silently cap at `list_runs`'s default
    page of 50, or the two backends (and the cross-project dashboard reading them) disagree once a
    project passes 50 runs."""
    repo, reg = _repo_and_registry(serve_engine)
    p = reg.add(org_id="default", name="checkout", source=None)
    for i in range(60):
        repo.record_run(
            RunRecord(id=f"run-{i:03d}", org_id="default", status="done", project_id=p.id)
        )

    ids = reg.run_ids(org_id="default", project_id=p.id)

    assert len(ids) == 60


def test_run_ids_honours_a_limit_at_the_db_query(serve_engine: Callable[..., Engine]) -> None:
    """A caller wanting a bounded window (the cross-project dashboard) can cap the read so the DB
    fetches only the newest N rows, rather than every run and a client-side truncation."""
    repo, reg = _repo_and_registry(serve_engine)
    p = reg.add(org_id="default", name="checkout", source=None)
    for i in range(10):
        repo.record_run(
            RunRecord(id=f"run-{i:03d}", org_id="default", status="done", project_id=p.id)
        )

    ids = reg.run_ids(org_id="default", project_id=p.id, limit=3)

    assert len(ids) == 3


def test_deregister_retains_the_runs_unlabeled(serve_engine: Callable[..., Engine]) -> None:
    repo, reg = _repo_and_registry(serve_engine)
    p = reg.add(org_id="default", name="checkout", source=None)
    repo.record_run(RunRecord(id="run-1", org_id="default", status="done", project_id=p.id))

    reg.remove(org_id="default", name="checkout")

    # The run survives deregistration (ON DELETE SET NULL, unit 1) — it just loses its project label,
    # so a per-project listing no longer surfaces it.
    assert repo.get_run("run-1") is not None
    assert reg.run_ids(org_id="default", project_id=p.id) == []

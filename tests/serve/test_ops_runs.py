"""The run-lifecycle serve operations: delete / restore / bulk-delete / retention sweep (BE-0239).

Covers both deployment shapes: local (no repository — the artifact store's trash is the whole
story) and hosted (a repository whose `deleted_at` column drives the DB-backed listing, updated
alongside the store). Also the purge admin gate the path-based RBAC can't apply, org scoping, the
audit-log entry, and the lazy retention sweep with an injected clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select

from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import AuditLog, Base
from bajutsu.serve.state import ServeState


def _local_state(tmp_path: Path) -> ServeState:
    return ServeState(runs_dir=tmp_path / "runs")


def _hosted_state(tmp_path: Path) -> tuple[ServeState, SqlRepository]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user("admin", org_id="default", github_login="admin", email="a@x", role="admin")
    repo.upsert_user("editor", org_id="default", github_login="editor", email="e@x", role="editor")
    state = ServeState(runs_dir=tmp_path / "runs", repository=repo)
    return state, repo


def _run_dir(state: ServeState, run_id: str) -> None:
    d = state.runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"ok": true, "scenarios": []}')


def _audit_actions(repo: SqlRepository) -> list[tuple[str, str]]:
    with repo._engine.connect() as conn:
        rows = conn.execute(select(AuditLog.action, AuditLog.target)).all()
    return [(str(r[0]), str(r[1])) for r in rows]


# --- local (no repository) ---


def test_local_soft_delete_then_restore(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    assert ops.delete_run(state, "r1")[1] == 200
    assert ops.runs_payload(state)[0] == []  # delisted
    assert ops.restore_run(state, "r1")[1] == 200
    assert [r["id"] for r in ops.runs_payload(state)[0]] == ["r1"]


def test_local_delete_missing_run_is_404(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    assert ops.delete_run(state, "ghost")[1] == 404
    assert ops.restore_run(state, "ghost")[1] == 404


def test_local_purge_is_allowed_without_a_repository(tmp_path: Path) -> None:
    # Local serve has no RBAC (no repository) — purge is full-access, like every other action.
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    payload, status = ops.delete_run(state, "r1", purge=True)
    assert status == 200 and payload["purged"] is True
    assert not (state.runs_dir / "r1").exists()


def test_bulk_delete_reports_deleted_and_not_found(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    _run_dir(state, "r1")
    _run_dir(state, "r2")
    payload, status = ops.bulk_delete_runs(state, {"ids": ["r1", "r2", "ghost"]})
    assert status == 200
    assert set(payload["deleted"]) == {"r1", "r2"}
    assert payload["notFound"] == ["ghost"]
    assert ops.runs_payload(state)[0] == []


def test_bulk_delete_rejects_a_non_list_ids(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    assert ops.bulk_delete_runs(state, {"ids": "r1"})[1] == 400


# --- hosted (repository) ---


def test_hosted_soft_delete_updates_store_and_db(tmp_path: Path) -> None:
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    _run_dir(state, "r1")  # a regular run has both a DB row and store bytes
    assert ops.delete_run(state, "r1", actor="editor")[1] == 200
    assert repo.list_runs(org_id="default") == []  # DB listing hides it
    assert ("run.soft_delete", "r1") in _audit_actions(repo)


def test_hosted_purge_requires_admin(tmp_path: Path) -> None:
    state, repo = _hosted_state(tmp_path)
    repo.record_run(RunRecord(id="r1", org_id="default", status="done", ok=True))
    _run_dir(state, "r1")
    # An editor may soft-delete but not purge.
    assert ops.delete_run(state, "r1", purge=True, actor="editor")[1] == 403
    assert ops.delete_run(state, "r1", purge=True, actor="admin")[1] == 200
    assert repo.get_run("r1") is None


def test_hosted_bulk_purge_requires_admin(tmp_path: Path) -> None:
    state, _repo = _hosted_state(tmp_path)
    assert ops.bulk_delete_runs(state, {"ids": [], "purge": True}, actor="editor")[1] == 403
    assert ops.bulk_delete_runs(state, {"ids": [], "purge": True}, actor="admin")[1] == 200


# --- retention sweep ---


def test_sweep_purges_only_runs_past_the_window(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    state.run_retention_days = 30
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")  # trashed ~now
    # Nothing is old enough yet, so the sweep keeps it.
    assert ops.sweep_expired_trash(state, now=datetime.now(UTC)) == 0
    assert state.artifacts.list_trashed_runs() != []
    # 40 days later it is past the 30-day window and gets purged.
    purged = ops.sweep_expired_trash(state, now=datetime.now(UTC) + timedelta(days=40))
    assert purged == 1
    assert state.artifacts.list_trashed_runs() == []


def test_sweep_is_a_no_op_when_retention_is_disabled(tmp_path: Path) -> None:
    state = _local_state(tmp_path)
    state.run_retention_days = 0  # disabled — trash kept until a manual purge
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")
    assert ops.sweep_expired_trash(state, now=datetime.now(UTC) + timedelta(days=999)) == 0
    assert state.artifacts.list_trashed_runs() != []


def test_runs_payload_sweeps_before_listing(tmp_path: Path) -> None:
    # The lazy trigger (BE-0239): a history read purges expired trash first. Backdate the trashed
    # run's directory mtime (the deletion clock's start) past the window so the next read purges it.
    import os

    state = _local_state(tmp_path)
    state.run_retention_days = 30
    _run_dir(state, "r1")
    ops.delete_run(state, "r1")
    old = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(state.runs_dir / ".trash" / "r1", (old, old))
    assert ops.runs_payload(state)[0] == []  # listed empty, and the sweep purged the expired trash
    assert state.artifacts.list_trashed_runs() == []

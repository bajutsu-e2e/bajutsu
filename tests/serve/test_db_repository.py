"""The `Repository` seam (BE-0015 7a / BE-0225): the run round-trip, org-scoped listing, the
env-driven factory, and the project CRUD methods, all against an in-memory SQLite database built
inside each test (no live Postgres). `ProjectRecord` and the project methods arrived with BE-0225;
orgs/users/audit_log are tested elsewhere in this file (implemented in 7b/7c)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve.server.db import (
    ProjectRecord,
    RunRecord,
    SqlRepository,
    engine_from_url,
    repository_from_env,
)
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import AuditLog, Base, Org, User
from bajutsu.serve.server.post_completion_logbus import PostCompletionLogBus


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _repo_fk() -> SqlRepository:
    """Like `_repo()` but with SQLite FK enforcement enabled.

    Needed for tests that verify ON DELETE behaviour (e.g. SET NULL on project deletion) that
    SQLite only enacts when ``PRAGMA foreign_keys=ON`` is set. Callers must satisfy *all* FKs,
    so an org row must be created via ``ensure_org`` before inserting projects or runs.
    """
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _engine_repo() -> tuple[object, SqlRepository]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, SqlRepository(engine)


def test_ensure_org_is_idempotent() -> None:
    engine, repo = _engine_repo()
    repo.ensure_org("default", slug="default", name="Default")
    repo.ensure_org("default", slug="default", name="Default")  # again — no duplicate, no error
    with Session(engine) as s:
        orgs = list(s.scalars(select(Org)))
    assert len(orgs) == 1
    assert orgs[0].slug == "default"


def test_upsert_user_inserts_then_updates_in_place() -> None:
    engine, repo = _engine_repo()
    repo.ensure_org("default", slug="default", name="Default")
    email = "alice@users.noreply.github.com"
    repo.upsert_user("alice", org_id="default", github_login="alice", email=email)
    repo.upsert_user("alice", org_id="default", github_login="alice", email=email)
    with Session(engine) as s:
        users = list(s.scalars(select(User)))
    assert len(users) == 1
    assert users[0].github_login == "alice"
    assert users[0].org_id == "default"


def test_upsert_user_defaults_to_editor() -> None:
    # The default role matches the policy default (an allowlisted user can run), so model /
    # migration / upsert agree and no caller accidentally persists an over-restrictive viewer.
    _engine, repo = _engine_repo()
    repo.ensure_org("default", slug="default", name="Default")
    repo.upsert_user("a", org_id="default", github_login="a", email="a@x")
    assert repo.user_role("a") == "editor"


def test_upsert_user_stores_and_updates_the_role() -> None:
    _engine, repo = _engine_repo()
    repo.ensure_org("default", slug="default", name="Default")
    repo.upsert_user("a", org_id="default", github_login="a", email="a@x", role="admin")
    assert repo.user_role("a") == "admin"
    repo.upsert_user("a", org_id="default", github_login="a", email="a@x", role="viewer")
    assert repo.user_role("a") == "viewer"  # a re-login recomputes the role
    assert repo.user_role("nobody") is None


def test_user_org_returns_the_users_org() -> None:
    _engine, repo = _engine_repo()
    repo.ensure_org("acme", slug="acme", name="Acme")
    repo.upsert_user("a", org_id="acme", github_login="a", email="a@x")
    assert repo.user_org("a") == "acme"
    assert repo.user_org("nobody") is None


def test_record_audit_appends_a_row_with_actor_and_detail() -> None:
    engine, repo = _engine_repo()
    repo.ensure_org("default", slug="default", name="Default")
    repo.upsert_user(
        "alice", org_id="default", github_login="alice", email="a@users.noreply.github.com"
    )
    repo.record_audit(
        org_id="default",
        actor_id="alice",
        action="run",
        target="demo/smoke.yaml",
        detail={"workers": 2},
    )
    with Session(engine) as s:
        rows = list(s.scalars(select(AuditLog)))
    assert len(rows) == 1
    assert rows[0].action == "run"
    assert rows[0].target == "demo/smoke.yaml"
    assert rows[0].actor_id == "alice"
    assert rows[0].detail == {"workers": 2}


def test_record_then_get_round_trips() -> None:
    repo = _repo()
    repo.record_run(
        RunRecord(id="r1", org_id="o1", status="done", ok=True, summary={"passed": 3, "failed": 0})
    )
    got = repo.get_run("r1")
    assert got is not None
    assert got.id == "r1"
    assert got.org_id == "o1"
    assert got.status == "done"
    assert got.ok is True
    assert got.summary == {"passed": 3, "failed": 0}


def test_get_missing_returns_none() -> None:
    assert _repo().get_run("nope") is None


def test_list_runs_filters_by_org_and_orders_newest_first() -> None:
    repo = _repo()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_run(RunRecord(id="a", org_id="o1", status="done", created_at=base.replace(hour=1)))
    repo.record_run(RunRecord(id="b", org_id="o1", status="done", created_at=base.replace(hour=3)))
    repo.record_run(RunRecord(id="c", org_id="o2", status="done", created_at=base.replace(hour=2)))
    assert [r.id for r in repo.list_runs(org_id="o1")] == ["b", "a"]


def test_list_runs_respects_limit() -> None:
    repo = _repo()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        repo.record_run(
            RunRecord(id=f"r{i}", org_id="o1", status="done", created_at=base.replace(minute=i))
        )
    assert len(repo.list_runs(org_id="o1", limit=2)) == 2


def test_summary_json_roundtrips_a_nested_value() -> None:
    repo = _repo()
    summary = {"counts": {"passed": 2, "failed": 1}, "steps": [{"name": "tap", "ok": True}]}
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", summary=summary))
    got = repo.get_run("r1")
    assert got is not None
    assert got.summary == summary


def test_record_run_is_idempotent_by_id() -> None:
    repo = _repo()
    repo.record_run(RunRecord(id="r1", org_id="o1", status="running"))
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", ok=True))
    got = repo.get_run("r1")
    assert got is not None
    assert got.status == "done"
    assert got.ok is True
    assert len(repo.list_runs(org_id="o1")) == 1


def test_create_then_list_and_get_project() -> None:
    repo = _repo()
    repo.create_project(
        ProjectRecord(
            id="p1",
            org_id="o1",
            name="checkout",
            source={"kind": "file", "locator": {"path": "a.yaml"}},
        )
    )
    repo.create_project(ProjectRecord(id="p2", org_id="o1", name="search"))
    repo.create_project(ProjectRecord(id="p3", org_id="o2", name="other"))

    assert [p.name for p in repo.list_projects(org_id="o1")] == ["checkout", "search"]
    got = repo.get_project(org_id="o1", name="checkout")
    assert got is not None
    assert got.id == "p1"
    assert got.source == {"kind": "file", "locator": {"path": "a.yaml"}}
    # A missing name, and another org's project, both read as not found (org-scoped).
    assert repo.get_project(org_id="o1", name="nope") is None
    assert repo.get_project(org_id="o1", name="other") is None


def test_create_project_is_idempotent_by_id_and_rebinds_source() -> None:
    repo = _repo()
    repo.create_project(
        ProjectRecord(id="p1", org_id="o1", name="checkout", source={"kind": "file"})
    )
    # Re-registering the same id rebinds the source in place rather than colliding (BE-0225).
    repo.create_project(
        ProjectRecord(id="p1", org_id="o1", name="checkout", source={"kind": "git"})
    )
    got = repo.get_project(org_id="o1", name="checkout")
    assert got is not None
    assert got.source == {"kind": "git"}
    assert len(repo.list_projects(org_id="o1")) == 1


def test_delete_project_on_delete_set_null_keeps_run_history_fk_enforced() -> None:
    # Uses FK-enforcing helper to catch the Postgres-vs-SQLite gap: without ondelete="SET NULL"
    # on Run.project_id, deleting a project raises IntegrityError on Postgres but silently
    # succeeds on the gate's SQLite. With the fix, the run is retained and project_id is NULL.
    repo = _repo_fk()
    repo.ensure_org("o1", slug="o1", name="Org1")
    repo.create_project(ProjectRecord(id="p1", org_id="o1", name="checkout"))
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", project_id="p1"))

    repo.delete_project(org_id="o1", name="checkout")

    assert repo.list_projects(org_id="o1") == []
    got = repo.get_run("r1")
    assert got is not None
    assert got.project_id is None  # SET NULL by FK — run history retained, association cleared


def test_create_project_source_none_does_not_clobber_existing_binding() -> None:
    repo = _repo()
    repo.create_project(
        ProjectRecord(id="p1", org_id="o1", name="checkout", source={"kind": "file"})
    )
    # Re-registering with source=None (the default) must not wipe out an existing binding.
    repo.create_project(ProjectRecord(id="p1", org_id="o1", name="checkout"))
    got = repo.get_project(org_id="o1", name="checkout")
    assert got is not None
    assert got.source == {"kind": "file"}


def test_delete_project_removes_binding_but_keeps_run_history() -> None:
    repo = _repo()
    repo.create_project(ProjectRecord(id="p1", org_id="o1", name="checkout"))
    repo.record_run(RunRecord(id="r1", org_id="o1", status="done", project_id="p1"))

    repo.delete_project(org_id="o1", name="checkout")

    assert repo.list_projects(org_id="o1") == []
    # Deregistering removes only the binding — the run history is retained (BE-0225).
    got = repo.get_run("r1")
    assert got is not None
    assert got.project_id == "p1"


def test_list_runs_filters_by_project() -> None:
    repo = _repo()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_run(
        RunRecord(
            id="a", org_id="o1", status="done", project_id="p1", created_at=base.replace(hour=1)
        )
    )
    repo.record_run(
        RunRecord(
            id="b", org_id="o1", status="done", project_id="p2", created_at=base.replace(hour=2)
        )
    )
    repo.record_run(
        RunRecord(
            id="c", org_id="o1", status="done", project_id="p1", created_at=base.replace(hour=3)
        )
    )

    assert [r.id for r in repo.list_runs(org_id="o1", project_id="p1")] == ["c", "a"]
    # No project filter → the whole org's run history, as before.
    assert len(repo.list_runs(org_id="o1")) == 3


def test_engine_from_url_builds_a_usable_engine() -> None:
    engine = engine_from_url("sqlite://")
    Base.metadata.create_all(engine)
    assert SqlRepository(engine).get_run("absent") is None


def test_repository_from_env_is_none_without_a_url(monkeypatch) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    assert repository_from_env() is None


def test_repository_from_env_builds_a_sql_repository(monkeypatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    assert isinstance(repository_from_env(), SqlRepository)


def test_repository_from_env_rejects_a_non_numeric_lease_timeout(monkeypatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_LEASE_TIMEOUT_SECONDS", "soon")
    with pytest.raises(ValueError, match="BAJUTSU_LEASE_TIMEOUT_SECONDS"):
        repository_from_env()


def test_repository_from_env_rejects_a_non_positive_attempt_cap(monkeypatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_LEASE_MAX_ATTEMPTS", "0")
    with pytest.raises(ValueError, match="BAJUTSU_LEASE_MAX_ATTEMPTS"):
        repository_from_env()


def test_repository_from_env_rejects_a_non_finite_lease_timeout(monkeypatch) -> None:
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_LEASE_TIMEOUT_SECONDS", "inf")  # slips past a bare `<= 0` check
    with pytest.raises(ValueError, match="finite"):
        repository_from_env()


# ---------------------------------------------------------------------------
# Job queue methods (BE-0106)
# ---------------------------------------------------------------------------


def test_enqueue_then_lease_returns_the_spec() -> None:
    repo = _repo()
    spec = {"cmd": ["bajutsu", "run"], "job_id": "j1"}
    repo.enqueue_job("j1", org_id="o1", spec=spec)
    leased = repo.lease_job("worker-1")
    assert leased is not None
    assert leased.id == "j1"
    assert leased.spec == spec


def test_lease_returns_none_when_queue_is_empty() -> None:
    assert _repo().lease_job("worker-1") is None


def test_lease_takes_oldest_first() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"n": 1})
    repo.enqueue_job("j2", org_id="o1", spec={"n": 2})
    first = repo.lease_job("w1")
    assert first is not None and first.id == "j1"
    second = repo.lease_job("w2")
    assert second is not None and second.id == "j2"
    assert repo.lease_job("w3") is None


def test_complete_job_stores_result() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    result = {"ok": True, "run_id": "r1", "summary": {"passed": 3}}
    repo.complete_job("j1", result=result)
    got = repo.get_job("j1")
    assert got is not None
    assert got["status"] == "done"
    assert got["result"] == result


def test_fail_job_stores_error() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    repo.fail_job("j1", error="crash")
    got = repo.get_job("j1")
    assert got is not None
    assert got["status"] == "failed"
    assert got["result"]["error"] == "crash"


def test_get_job_returns_none_for_missing() -> None:
    assert _repo().get_job("nope") is None


# ---------------------------------------------------------------------------
# DbQueueExecutor (BE-0106)
# ---------------------------------------------------------------------------


def test_db_executor_inserts_a_queued_job() -> None:
    repo = _repo()
    state = srv.ServeState(runs_dir=Path("/tmp/runs"))
    job = state.register(srv.Job(cmd=["bajutsu", "run"], udids=["U1"]))
    DbQueueExecutor(repo).dispatch(state, job)
    info = repo.get_job(job.id)
    assert info is not None
    assert info["status"] == "queued"
    leased = repo.lease_job("w1")
    assert leased is not None and leased.id == job.id


# ---------------------------------------------------------------------------
# PostCompletionLogBus (BE-0106)
# ---------------------------------------------------------------------------


class _FakeArtifactStore:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = files or {}

    def open_bytes(self, path: str) -> bytes | None:
        return self._files.get(path)


def test_post_completion_logbus_yields_log_after_done() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    repo.complete_job("j1", result={"ok": True, "runId": "20260702-1"})
    artifacts = _FakeArtifactStore({"20260702-1/console.log": b"line 1\nline 2\n"})
    bus = PostCompletionLogBus(repo, lambda _org: artifacts, poll_interval=0.01)
    lines = list(bus.stream("j1"))
    assert "line 1\n" in lines
    assert "line 2\n" in lines


def test_post_completion_logbus_heartbeats_while_queued() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    bus = PostCompletionLogBus(repo, poll_interval=0.01)
    it = bus.stream("j1", timeout=1.0)
    hb = next(it)
    assert hb is None  # heartbeat while still queued (timeout set → heartbeats emitted)


def test_post_completion_logbus_final_returns_result() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    bus = PostCompletionLogBus(repo, poll_interval=0.01)
    assert bus.final("j1") is None

    repo.lease_job("w1")
    repo.complete_job("j1", result={"ok": True, "runId": "r1"})
    final = bus.final("j1")
    assert final is not None
    import json

    assert json.loads(final)["ok"] is True

"""A cancel reaches a job wherever the split put it (BE-0449 unit 2).

A queued job is marked cancelled in the jobs table and never leased; a leased job is flagged, the
heartbeat hands the flag to its worker, and only that worker's cancelled result finishes the row.
The tests run on a file-backed SQLite, the store a local start uses, so a second `SqlRepository` on
the same file stands in for a server restart.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db._functions import engine_from_url
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, JobRecord
from bajutsu.serve.state import Job


def _file_repo(tmp_path: Path) -> SqlRepository:
    engine = engine_from_url(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _state(tmp_path: Path, repo: SqlRepository) -> srv.ServeState:
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def _status(repo: SqlRepository, job_id: str) -> str:
    info = repo.get_job(job_id)
    assert info is not None
    return str(info["status"])


# --- the repository -------------------------------------------------------------------------------


def test_a_cancelled_queued_job_is_never_leased(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    repo.enqueue_job("j2", org_id="o", spec={"cmd": []})
    assert repo.cancel_job("j1") == ("cancelled", True)
    leased = repo.lease_job("w1")
    assert leased is not None and leased.id == "j2"  # the older, cancelled job is skipped
    assert repo.lease_job("w1") is None
    assert _status(repo, "j1") == "cancelled"
    assert repo.finished_job_ids(["j1", "j2"]) == {"j1"}


def test_a_queued_cancel_survives_a_restart(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    # A fresh repository on the same file is what a restarted server opens.
    restarted = SqlRepository(engine_from_url(f"sqlite:///{tmp_path / 'queue.db'}"))
    assert restarted.cancel_job("j1") == ("cancelled", True)
    assert repo.lease_job("w1") is None
    info = repo.get_job("j1")
    assert info is not None and info["result"]["cancelled"] is True


def test_a_leased_cancel_keeps_the_lease_and_sets_the_flag(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    assert repo.cancel_requested("j1", "w1") is False
    assert repo.cancel_job("j1") == ("leased", True)
    assert _status(repo, "j1") == "leased"
    assert repo.heartbeat_job("j1", "w1") is True  # the worker keeps its lease while it stops
    assert repo.cancel_requested("j1", "w1") is True
    assert repo.cancel_requested("j1", "w2") is False  # only the holder is told


def test_a_cancel_of_a_finished_job_is_a_no_op(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    assert repo.complete_job("j1", {"ok": True}, worker_id="w1")
    assert repo.cancel_job("j1") == ("done", False)
    assert _status(repo, "j1") == "done"
    assert repo.cancel_job("missing") is None


def test_only_the_lease_holder_records_a_cancelled_outcome(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    assert repo.cancel_leased_job("j1", {"cancelled": True}, worker_id="w2") is False
    assert _status(repo, "j1") == "leased"
    assert repo.cancel_leased_job("j1", {"cancelled": True}, worker_id="w1") is True
    assert _status(repo, "j1") == "cancelled"


def test_a_reclaimed_lease_with_a_cancel_ends_cancelled_not_requeued(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    repo.cancel_job("j1")
    with Session(repo._engine) as session:
        row = session.get(JobRecord, "j1")
        assert row is not None
        row.leased_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    # The worker died while stopping: the job is not handed to another worker to run again.
    assert repo.reclaim_expired_leases(timedelta(seconds=1)) == []
    assert _status(repo, "j1") == "cancelled"
    assert repo.lease_job("w2") is None


# --- the operations -------------------------------------------------------------------------------


def test_cancel_operation_cancels_a_queued_job_and_closes_its_view(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    body, code = ops.cancel_job(state, "j1")
    assert code == 200
    assert body == {"cancelled": True, "status": "cancelled"}
    final = state.logbus.final("j1")
    assert final is not None
    assert json.loads(final)["cancelled"] is True
    # Cancelling again reports the terminal state and changes nothing.
    assert ops.cancel_job(state, "j1") == ({"cancelled": False, "status": "cancelled"}, 200)


def test_cancel_operation_marks_the_control_planes_job_too(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    job = state.register(Job(cmd=["bajutsu", "run"]))
    repo.enqueue_job(job.id, org_id="default", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    body, code = ops.cancel_job(state, job.id)
    assert (body, code) == ({"cancelled": True, "status": "leased"}, 200)
    assert job.cancelled is True
    assert job.lines[-1] == "cancelled"


def test_heartbeat_returns_cancel_once_a_leased_job_is_cancelled(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    assert ops.worker_heartbeat(state, "w1", "j1") == ({"ok": True}, 200)
    ops.cancel_job(state, "j1")
    assert ops.worker_heartbeat(state, "w1", "j1") == ({"ok": True, "cancel": True}, 200)
    assert ops.worker_heartbeat(state, "w2", "j1")[1] == 409


def test_a_cancelled_result_from_a_non_holder_is_refused(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    ops.cancel_job(state, "j1")
    result = {"ok": False, "cancelled": True}
    body = {"job_id": "j1", "worker_id": "w2", "result": result}
    assert ops.worker_result(state, body)[1] == 409
    assert _status(repo, "j1") == "leased"


def test_a_result_after_a_cancel_moves_the_row_to_cancelled(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    ops.cancel_job(state, "j1")
    result = {"ok": False, "cancelled": True, "status": "done"}
    assert ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result}) == (
        {"ok": True},
        200,
    )
    assert _status(repo, "j1") == "cancelled"
    final = state.logbus.final("j1")
    assert final is not None and json.loads(final)["cancelled"] is True
    # A cancel after the job finished is a no-op that reports where it ended.
    assert ops.cancel_job(state, "j1") == ({"cancelled": False, "status": "cancelled"}, 200)


def test_a_cancel_of_a_done_job_is_a_no_op(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": True}})
    assert ops.cancel_job(state, "j1") == ({"cancelled": False, "status": "done"}, 200)
    assert _status(repo, "j1") == "done"


def test_cancel_is_scoped_to_a_machines_org(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    # Another org's job reads as absent, not forbidden, as `job_view` answers it.
    assert ops.cancel_job(state, "j1", machine_org="other")[1] == 404
    assert _status(repo, "j1") == "queued"
    assert ops.cancel_job(state, "j1", machine_org="acme")[0]["cancelled"] is True


def test_in_process_cancel_is_unchanged_and_scoped(tmp_path: Path) -> None:
    # A job with no row is stopped through its own `Job` (on a worker, the process it spawned).
    repo = _file_repo(tmp_path)
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)
    job = state.register(Job(cmd=["bajutsu", "run"], org="acme"))
    assert ops.cancel_job(state, job.id, machine_org="other")[1] == 404
    assert job.cancelled is False
    assert ops.cancel_job(state, job.id) == ({"cancelled": True}, 200)
    assert job.cancelled is True
    assert ops.cancel_job(state, "missing")[1] == 404


# --- the registration-to-enqueue window ----------------------------------------------------------


def test_a_cancel_before_enqueue_reaches_the_row_on_dispatch(tmp_path: Path) -> None:
    # The registry holds the job before its row exists, so this cancel takes the in-process branch;
    # the dispatcher then carries it onto the row instead of queueing a job nobody wants.
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    job = state.register(Job(cmd=["bajutsu", "run"]))
    assert ops.cancel_job(state, job.id) == ({"cancelled": True}, 200)
    state.executor.dispatch(state, job)
    assert _status(repo, job.id) == "cancelled"
    assert repo.lease_job("w1") is None
    final = state.logbus.final(job.id)
    assert final is not None and json.loads(final)["cancelled"] is True


def test_a_row_that_appears_during_the_cancel_is_cancelled_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other order: the cancel's first read misses the row, the dispatcher enqueues and checks
    # `job.cancelled` before the cancel sets it, and only the cancel's re-read can catch the row.
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    job = state.register(Job(cmd=["bajutsu", "run"]))
    state.executor.dispatch(state, job)  # not cancelled yet, so the row stays queued
    real_get = repo.get_job
    reads = 0

    def first_read_misses(job_id: str) -> dict[str, Any] | None:
        nonlocal reads
        reads += 1
        return None if reads == 1 else real_get(job_id)

    monkeypatch.setattr(repo, "get_job", first_read_misses)
    assert ops.cancel_job(state, job.id) == ({"cancelled": True, "status": "cancelled"}, 200)
    monkeypatch.undo()
    assert _status(repo, job.id) == "cancelled"
    assert repo.lease_job("w1") is None


# --- org membership -------------------------------------------------------------------------------


def _orgs(repo: SqlRepository) -> None:
    for org, member in (("acme", "alice"), ("globex", "bob")):
        repo.ensure_org(org, slug=org, name=org)
        repo.upsert_user(member, org_id=org, github_login=member, email=f"{member}@x")
        repo.set_user_orgs(member, {org: "editor"})


def test_a_member_of_another_org_cannot_cancel_a_queued_job(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    _orgs(repo)
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    # Answered as absent rather than forbidden, so a sequential id confirms nothing.
    assert ops.cancel_job(state, "j1", actor="bob")[1] == 404
    assert _status(repo, "j1") == "queued"
    assert ops.cancel_job(state, "j1", actor="alice") == (
        {"cancelled": True, "status": "cancelled"},
        200,
    )


def test_a_viewer_of_the_jobs_org_cannot_cancel_it_from_an_org_they_edit(tmp_path: Path) -> None:
    # bob edits globex (his active org, which the route's role gate checked) but only views acme.
    repo = _file_repo(tmp_path)
    _orgs(repo)
    repo.set_user_orgs("bob", {"globex": "editor", "acme": "viewer"})
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    assert ops.cancel_job(state, "j1", actor="bob")[1] == 404
    assert _status(repo, "j1") == "queued"


def test_an_editor_of_the_jobs_org_cancels_it_from_another_active_org(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    _orgs(repo)
    repo.set_user_orgs("bob", {"globex": "editor", "acme": "editor"})
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    assert ops.cancel_job(state, "j1", actor="bob") == (
        {"cancelled": True, "status": "cancelled"},
        200,
    )


def test_a_viewer_membership_of_the_active_org_does_not_fall_back(tmp_path: Path) -> None:
    # A stored membership decides even for the active org; the fallback is for no row at all.
    repo = _file_repo(tmp_path)
    _orgs(repo)
    repo.set_user_orgs("alice", {"acme": "viewer"})
    state = _state(tmp_path, repo)
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    assert ops.cancel_job(state, "j1", actor="alice")[1] == 404


# --- reclaim --------------------------------------------------------------------------------------


def test_a_flagged_lease_at_the_attempt_cap_ends_cancelled_not_failed(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    repo.cancel_job("j1")
    _expire(repo, "j1")
    assert repo.reclaim_expired_leases(timedelta(seconds=1), max_attempts=1) == []
    assert _status(repo, "j1") == "cancelled"


def test_a_cancel_between_the_reclaim_scan_and_its_update_leaves_the_row(tmp_path: Path) -> None:
    # The reclaim read the flag as unset; a cancel landing before its UPDATE must not be re-queued
    # with the flag still set. The UPDATE misses, and the next sweep ends the job cancelled.
    repo = _file_repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    _expire(repo, "j1")
    flipped = False

    def cancel_before_the_update(
        conn: Any, cursor: Any, statement: str, *_args: Any, **_kw: Any
    ) -> None:
        nonlocal flipped
        # The reclaim's UPDATE is the one statement on `jobs` that writes `attempts`.
        if not flipped and statement.startswith("UPDATE jobs SET") and "attempts=" in statement:
            flipped = True
            # Raw DBAPI on the reclaim's own connection: inside its transaction, and no event loop.
            raw = conn.connection.dbapi_connection.cursor()
            raw.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = 'j1'")

    event.listen(repo._engine, "before_cursor_execute", cancel_before_the_update)
    try:
        assert repo.reclaim_expired_leases(timedelta(seconds=1)) == []
    finally:
        event.remove(repo._engine, "before_cursor_execute", cancel_before_the_update)
    assert flipped
    assert _status(repo, "j1") == "leased"
    assert repo.reclaim_expired_leases(timedelta(seconds=1)) == []
    assert _status(repo, "j1") == "cancelled"


def _expire(repo: SqlRepository, job_id: str) -> None:
    with Session(repo._engine) as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        row.leased_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()


def test_a_member_of_another_org_cannot_cancel_an_in_process_job(tmp_path: Path) -> None:
    repo = _file_repo(tmp_path)
    _orgs(repo)
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)
    job = state.register(Job(cmd=["bajutsu", "run"], org="acme"))
    assert ops.cancel_job(state, job.id, actor="bob")[1] == 404
    assert job.cancelled is False
    assert ops.cancel_job(state, job.id, actor="alice") == ({"cancelled": True}, 200)


def test_a_member_of_another_org_cannot_cancel_inside_the_enqueue_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _file_repo(tmp_path)
    _orgs(repo)
    state = _state(tmp_path, repo)
    job = state.register(Job(cmd=["bajutsu", "run"], org="acme"))
    state.executor.dispatch(state, job)
    real_get = repo.get_job
    reads = 0

    def first_read_misses(job_id: str) -> dict[str, Any] | None:
        nonlocal reads
        reads += 1
        return None if reads == 1 else real_get(job_id)

    monkeypatch.setattr(repo, "get_job", first_read_misses)
    assert ops.cancel_job(state, job.id, actor="bob")[1] == 404
    monkeypatch.undo()
    assert job.cancelled is False
    assert _status(repo, job.id) == "queued"


def test_a_member_cancels_after_switching_their_active_org_away(tmp_path: Path) -> None:
    # A job's org is frozen at dispatch, while the active org follows the member's switch; the
    # membership still admits the cancel.
    repo = _file_repo(tmp_path)
    _orgs(repo)
    repo.set_user_orgs("alice", {"acme": "editor", "globex": "editor"})
    assert repo.select_active_org("alice", "globex", role="editor")
    state = _state(tmp_path, repo)
    assert state.org_of("alice") == "globex"
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})
    assert ops.cancel_job(state, "j1", actor="alice") == (
        {"cancelled": True, "status": "cancelled"},
        200,
    )


def test_the_cancel_that_caused_a_dispatcher_transition_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cancel stops the in-memory job, then the dispatcher sees `job.cancelled` and moves the row
    # before the cancel's re-read. The row then reads as already cancelled, yet this request caused it.
    repo = _file_repo(tmp_path)
    state = _state(tmp_path, repo)
    job = state.register(Job(cmd=["bajutsu", "run"]))
    real_get = repo.get_job
    reads = 0

    def dispatch_between_the_reads(job_id: str) -> dict[str, Any] | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            return None
        state.executor.dispatch(state, job)  # the dispatcher wins the race to the row
        return real_get(job_id)

    monkeypatch.setattr(repo, "get_job", dispatch_between_the_reads)
    assert ops.cancel_job(state, job.id) == ({"cancelled": True, "status": "cancelled"}, 200)
    monkeypatch.undo()
    assert _status(repo, job.id) == "cancelled"

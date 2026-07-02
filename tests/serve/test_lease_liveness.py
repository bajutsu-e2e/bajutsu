"""Worker liveness and job re-queue (BE-0016, item 3).

BE-0106 moved job distribution to a Postgres `jobs` table leased over HTTP, but a worker that dies
mid-run left its job stuck in `leased` forever. These tests pin the re-queue contract: a lease with
no heartbeat past its timeout returns to `queued` (so another worker picks it up), a poison job that
keeps killing workers is `failed` once it hits the attempt cap, and a live heartbeat keeps a
legitimately long run from being reclaimed. All run against in-memory SQLite — no Mac, no Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, JobRecord


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _backdate_lease(repo: SqlRepository, job_id: str, *, seconds: float) -> None:
    """Push a job's `leased_at` into the past so a reclaim treats its lease as expired — the
    deterministic stand-in for a worker that stopped heart-beating `seconds` ago."""
    with Session(repo._engine) as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        row.leased_at = datetime.now(UTC) - timedelta(seconds=seconds)
        session.commit()


def _state(repo: SqlRepository, tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def test_reclaim_requeues_a_lease_past_its_timeout() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))

    assert requeued == ["j1"]
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "queued"


def test_reclaim_leaves_a_fresh_lease_alone() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")  # leased_at = now, well within the timeout

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))

    assert requeued == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"


def test_reclaim_fails_a_job_that_exhausts_its_attempts() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    # Two prior expiries already burned; the third and final attempt is what expires now.
    for _ in range(3):
        repo.lease_job("w1")
        _backdate_lease(repo, "j1", seconds=300)
        repo.reclaim_expired_leases(timeout=timedelta(seconds=120), max_attempts=3)

    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "failed"
    assert "error" in info["result"]


def test_heartbeat_renews_a_lease_so_reclaim_skips_it() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    assert repo.heartbeat_job("j1", "w1") is True

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))
    assert requeued == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"


def test_heartbeat_reports_a_lost_lease() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    # A worker that does not hold the lease (here w2, not the leaseholder w1) must be told so.
    assert repo.heartbeat_job("j1", "w2") is False
    # A finished job is no longer leasable either.
    repo.complete_job("j1", result={"ok": True})
    assert repo.heartbeat_job("j1", "w1") is False


def test_lease_reclaims_an_expired_lease_before_serving() -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    # The queue looks empty (j1 is leased), but a fresh lease should reclaim then hand it back out.
    leased = repo.lease_job("w2")
    assert leased is not None and leased.id == "j1"


def test_worker_heartbeat_endpoint_renews(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    payload, code = ops.worker_heartbeat(state, "w1", "j1")
    assert code == 200
    assert payload["ok"] is True


def test_worker_heartbeat_endpoint_reports_lost_lease(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    _payload, code = ops.worker_heartbeat(state, "w2", "j1")
    assert code == 409


def test_worker_heartbeat_endpoint_validates_input(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(repo, tmp_path)
    _payload, code = ops.worker_heartbeat(state, "", "j1")
    assert code == 400
    _payload, code = ops.worker_heartbeat(state, "w1", "")
    assert code == 400


def test_worker_heartbeat_endpoint_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_heartbeat(state, "w1", "j1")
    assert code == 503


def test_stale_worker_result_does_not_overwrite_the_winner(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)
    repo.reclaim_expired_leases(timeout=timedelta(seconds=120))  # j1 back to queued
    repo.lease_job("w2")  # the re-run's winner now holds the lease

    # w1 finally finishes and posts; it no longer owns the lease, so its result is dropped.
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": True, "runId": "stale"}}
    )
    assert code == 409
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"

    # w2 (the leaseholder) completes normally, and its result stands.
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w2", "result": {"ok": True, "runId": "winner"}}
    )
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done" and info["result"]["runId"] == "winner"


def test_worker_result_rejected_for_an_already_finished_job(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": True}})

    # A duplicate delivery of the same result must not re-open or overwrite a finished job.
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": False, "error": "late"}}
    )
    assert code == 409
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done"

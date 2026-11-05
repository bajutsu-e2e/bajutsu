"""Worker liveness and job re-queue (BE-0016, item 3).

BE-0106 moved job distribution to a Postgres `jobs` table leased over HTTP, but a worker that dies
mid-run left its job stuck in `leased` forever. These tests pin the re-queue contract: a lease with
no heartbeat past its timeout returns to `queued` (so another worker picks it up), a poison job that
keeps killing workers is `failed` once it hits the attempt cap, and a live heartbeat keeps a
legitimately long run from being reclaimed. All run against in-memory SQLite in the gate and, behind
the `postgres` marker, against a real Postgres service in the serve-db.yml lane (BE-0309).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, JobRecord


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
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


def test_reclaim_requeues_a_lease_past_its_timeout(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))

    assert requeued == ["j1"]
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "queued"


def test_reclaim_leaves_a_fresh_lease_alone(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")  # leased_at = now, well within the timeout

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))

    assert requeued == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"


def test_reclaim_fails_a_job_that_exhausts_its_attempts(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
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


def test_heartbeat_renews_a_lease_so_reclaim_skips_it(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    assert repo.heartbeat_job("j1", "w1") is True

    requeued = repo.reclaim_expired_leases(timeout=timedelta(seconds=120))
    assert requeued == []
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"


def test_heartbeat_reports_a_lost_lease(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    # A worker that does not hold the lease (here w2, not the leaseholder w1) must be told so.
    assert repo.heartbeat_job("j1", "w2") is False
    # A finished job is no longer leasable either.
    repo.complete_job("j1", result={"ok": True})
    assert repo.heartbeat_job("j1", "w1") is False


def test_lease_reclaims_an_expired_lease_before_serving(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _backdate_lease(repo, "j1", seconds=300)

    # The queue looks empty (j1 is leased), but a fresh lease should reclaim then hand it back out.
    leased = repo.lease_job("w2")
    assert leased is not None and leased.id == "j1"


@contextmanager
def _row_locked(repo: SqlRepository, job_id: str) -> Iterator[None]:
    """Hold *job_id*'s row lock from another connection — a leaser or heartbeat mid-transaction."""
    with repo._engine.connect() as conn:
        conn.execute(text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), {"id": job_id})
        try:
            yield
        finally:
            conn.rollback()


def _within[T](seconds: float, call: Callable[[], T]) -> T:
    """Run *call* on a thread and return its result, failing rather than hanging if it blocks."""
    out: dict[str, T] = {}
    thread = threading.Thread(target=lambda: out.setdefault("value", call()), daemon=True)
    thread.start()
    thread.join(timeout=seconds)
    assert not thread.is_alive(), "blocked on a locked row instead of skipping it"
    assert "value" in out  # the call raised on its thread
    return out["value"]


# SQLite has no row locks to skip — its writers serialize on the file (test_sqlite_queue_store) —
# so these run only in the serve-db.yml lane.
_postgres_only = pytest.mark.parametrize(
    "serve_engine", [pytest.param("postgresql", marks=pytest.mark.postgres)], indirect=True
)


@_postgres_only
def test_lease_skips_a_locked_candidate_instead_of_waiting(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.enqueue_job("j2", org_id="o1", spec={"cmd": []})

    # The oldest job is mid-claim elsewhere; this leaser moves on to the next one at once (BE-0449).
    with _row_locked(repo, "j1"):
        leased = _within(5, lambda: repo.lease_job("w1"))

    assert leased is not None and leased.id == "j2"
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "queued"


@_postgres_only
def test_reclaim_skips_a_locked_lease_instead_of_waiting(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    for job_id in ("j1", "j2"):
        repo.enqueue_job(job_id, org_id="o1", spec={"cmd": []})
        repo.lease_job("w1")
    # Backdated only once both are leased: a lease poll sweeps first, so backdating j1 before
    # leasing j2 would have that poll reclaim and re-lease j1, leaving j2 queued.
    for job_id in ("j1", "j2"):
        _backdate_lease(repo, job_id, seconds=300)

    # A heartbeat holds j1, so the sweep leaves that live worker's row alone and reclaims j2.
    with _row_locked(repo, "j1"):
        requeued = _within(5, lambda: repo.reclaim_expired_leases(timedelta(seconds=120)))

    assert requeued == ["j2"]
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "leased"


def test_worker_heartbeat_endpoint_renews(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    payload, code = ops.worker_heartbeat(state, "w1", "j1")
    assert code == 200
    assert payload["ok"] is True


def test_worker_heartbeat_endpoint_reports_lost_lease(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
    state = _state(repo, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")

    _payload, code = ops.worker_heartbeat(state, "w2", "j1")
    assert code == 409


def test_worker_heartbeat_endpoint_validates_input(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
    state = _state(repo, tmp_path)
    _payload, code = ops.worker_heartbeat(state, "", "j1")
    assert code == 400
    _payload, code = ops.worker_heartbeat(state, "w1", "")
    assert code == 400


def test_worker_heartbeat_endpoint_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_heartbeat(state, "w1", "j1")
    assert code == 503


def test_stale_worker_result_does_not_overwrite_the_winner(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
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


def test_worker_result_rejected_for_an_already_finished_job(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
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

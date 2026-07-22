"""Capability-routed leasing end to end at the repository + operations layer (BE-0166).

A worker leases only jobs whose required capabilities it advertises; a job no live worker can serve
stays queued and is surfaced as unroutable via the metrics snapshot. Exercised on in-memory SQLite,
no Simulator — the same routing logic the gate and production Postgres share.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, WorkerRecord


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _state(tmp_path: Path, repo: SqlRepository) -> srv.ServeState:
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def test_worker_leases_only_a_job_it_can_serve() -> None:
    repo = _repo()
    repo.enqueue_job("ios18-job", "o", {}, capabilities=["platform:ios", "ios18"])
    # An iOS-17-only worker cannot serve the ios18 job — the queue is empty *for it*.
    assert repo.lease_job("w17", ["platform:ios", "ios17"]) is None
    # Its own capable worker leases it.
    leased = repo.lease_job("w18", ["platform:ios", "ios18"])
    assert leased is not None and leased.id == "ios18-job"


def test_ipad_job_never_offered_to_an_iphone_only_worker() -> None:
    repo = _repo()
    repo.enqueue_job("ipad-job", "o", {}, capabilities=["platform:ios", "ipad"])
    assert repo.lease_job("iphone-worker", ["platform:ios", "iphone"]) is None
    leased = repo.lease_job("ipad-worker", ["platform:ios", "ipad", "iphone"])
    assert leased is not None and leased.id == "ipad-job"


def test_web_and_ios_jobs_never_cross() -> None:
    repo = _repo()
    repo.enqueue_job("web-job", "o", {}, capabilities=["platform:web"])
    repo.enqueue_job("ios-job", "o", {}, capabilities=["platform:ios"])
    # The web worker gets the web job, never the iOS one.
    web = repo.lease_job("web-worker", ["platform:web"])
    assert web is not None and web.id == "web-job"
    ios = repo.lease_job("mac-worker", ["platform:ios"])
    assert ios is not None and ios.id == "ios-job"


def test_lease_skips_an_unservable_older_job_for_a_servable_younger_one() -> None:
    repo = _repo()
    # The oldest queued job needs a capability this worker lacks; the lease must still find the
    # younger job it *can* serve rather than stopping at the head of the queue.
    repo.enqueue_job("old-web", "o", {}, capabilities=["platform:web"])
    repo.enqueue_job("new-ios", "o", {}, capabilities=["platform:ios"])
    leased = repo.lease_job("mac-worker", ["platform:ios"])
    assert leased is not None and leased.id == "new-ios"


def test_unannotated_job_leases_to_any_worker() -> None:
    repo = _repo()
    repo.enqueue_job("plain", "o", {})  # no capabilities → any worker
    leased = repo.lease_job("whatever", [])
    assert leased is not None and leased.id == "plain"


def test_metrics_counts_unroutable_queued_jobs() -> None:
    repo = _repo()
    repo.enqueue_job("routable", "o", {}, capabilities=["platform:ios"])
    repo.enqueue_job("unroutable", "o", {}, capabilities=["platform:android"])
    # Only a Mac worker is live; the android job matches no live worker.
    repo.register_worker("mac", ["platform:ios"])
    snap = repo.metrics_snapshot()
    assert snap.unroutable_queued == 1


def test_heartbeat_keeps_a_busy_worker_live_for_routability() -> None:
    # BE-0166: a worker busy on a run longer than the lease timeout polls `lease` only after it
    # finishes; the heartbeat must refresh its liveness so its capability's queued jobs are not
    # falsely counted unroutable while it works.
    repo = _repo()
    repo.register_worker("mac", ["platform:ios"])
    repo.enqueue_job("busy", "o", {}, capabilities=["platform:ios"])
    leased = repo.lease_job("mac", ["platform:ios"])
    assert leased is not None
    # Another ios job arrives; the only worker is mid-run. Age its registry row past the timeout,
    # then heartbeat — liveness is restored, so the queued job is not unroutable.
    repo.enqueue_job("next", "o", {}, capabilities=["platform:ios"])
    with repo._engine.connect() as conn:
        from sqlalchemy import update

        stale = datetime.now(UTC) - timedelta(hours=1)
        conn.execute(update(WorkerRecord).values(last_seen=stale))
        conn.commit()
    repo.touch_worker("mac")
    assert repo.metrics_snapshot().unroutable_queued == 0


def test_metrics_ignores_dead_workers_capabilities() -> None:
    repo = _repo()
    repo.enqueue_job("web-job", "o", {}, capabilities=["platform:web"])
    repo.register_worker("web", ["platform:web"])
    # Age the only web worker past the lease timeout so it is no longer "live".
    with repo._engine.connect() as conn:
        from sqlalchemy import update

        stale = datetime.now(UTC) - timedelta(hours=1)
        conn.execute(update(WorkerRecord).values(last_seen=stale))
        conn.commit()
    snap = repo.metrics_snapshot()
    # No *live* worker serves the web job now → it is unroutable.
    assert snap.unroutable_queued == 1


def test_reclaim_prunes_dead_worker_rows() -> None:
    # BE-0166: the workers registry stays bounded to the live pool — a worker not seen within the
    # timeout is pruned by the same reclaim sweep the lease path runs, so restarted workers don't
    # leak a row each.
    repo = _repo()
    repo.register_worker("gone", ["platform:ios"])
    with repo._engine.connect() as conn:
        from sqlalchemy import update

        conn.execute(update(WorkerRecord).values(last_seen=datetime.now(UTC) - timedelta(hours=1)))
        conn.commit()
    repo.reclaim_expired_leases(timedelta(seconds=120))
    with repo._engine.connect() as conn:
        from sqlalchemy import func, select

        assert conn.execute(select(func.count()).select_from(WorkerRecord)).scalar() == 0


def test_heartbeat_op_refreshes_worker_liveness(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(tmp_path, repo)
    repo.register_worker("w", ["platform:ios"])
    repo.enqueue_job("j", "o", {"job_id": "j"}, capabilities=["platform:ios"])
    leased = repo.lease_job("w", ["platform:ios"])
    assert leased is not None
    # Age the row, then heartbeat via the op — liveness is refreshed (no exception, worker stays live).
    with repo._engine.connect() as conn:
        from sqlalchemy import update

        conn.execute(update(WorkerRecord).values(last_seen=datetime.now(UTC) - timedelta(hours=1)))
        conn.commit()
    _payload, code = ops.worker_heartbeat(state, "w", "j")
    assert code == 200
    repo.enqueue_job("j2", "o", {}, capabilities=["platform:ios"])
    assert repo.metrics_snapshot().unroutable_queued == 0


def test_worker_lease_op_threads_capabilities_and_registers(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(tmp_path, repo)
    repo.enqueue_job(
        "ios18-job", "o", {"job_id": "ios18-job"}, capabilities=["platform:ios", "ios18"]
    )
    # An ios17 worker polls: it registers (so it counts toward routability) but leases nothing.
    payload, code = ops.worker_lease(state, "w17", ["platform:ios", "ios17"])
    assert code == 204
    assert repo.metrics_snapshot().unroutable_queued == 1  # no live worker serves ios18
    # A capable worker polls and leases it.
    payload, code = ops.worker_lease(state, "w18", ["platform:ios", "ios18"])
    assert code == 200 and payload["job_id"] == "ios18-job"


def test_worker_lease_op_rejects_a_malformed_capabilities_payload(tmp_path: Path) -> None:
    repo = _repo()
    state = _state(tmp_path, repo)
    repo.enqueue_job("plain", "o", {"job_id": "plain"})
    # A non-list capabilities value must not crash the lease — it advertises nothing.
    payload, code = ops.worker_lease(state, "w", capabilities="not-a-list")  # type: ignore[arg-type]
    assert code == 200 and payload["job_id"] == "plain"

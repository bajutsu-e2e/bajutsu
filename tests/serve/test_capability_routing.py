"""Capability-routed leasing end to end at the repository + operations layer (BE-0166).

A worker leases only jobs whose required capabilities it advertises; a job no live worker can serve
stays queued and is surfaced as unroutable via the metrics snapshot. Exercised on in-memory SQLite
in the gate and, behind the `postgres` marker, against a real Postgres service in the serve-db.yml
lane (BE-0309) — the same routing logic the gate and production Postgres share.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, update

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base, WorkerRecord


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _state(tmp_path: Path, repo: SqlRepository) -> srv.ServeState:
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def test_worker_leases_only_a_job_it_can_serve(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("ios18-job", "o", {}, capabilities=["platform:ios", "ios18"])
    # An iOS-17-only worker cannot serve the ios18 job — the queue is empty *for it*.
    assert repo.lease_job("w17", ["platform:ios", "ios17"]) is None
    # Its own capable worker leases it.
    leased = repo.lease_job("w18", ["platform:ios", "ios18"])
    assert leased is not None and leased.id == "ios18-job"


def test_ipad_job_never_offered_to_an_iphone_only_worker(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("ipad-job", "o", {}, capabilities=["platform:ios", "ipad"])
    assert repo.lease_job("iphone-worker", ["platform:ios", "iphone"]) is None
    leased = repo.lease_job("ipad-worker", ["platform:ios", "ipad", "iphone"])
    assert leased is not None and leased.id == "ipad-job"


def test_web_and_ios_jobs_never_cross(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("web-job", "o", {}, capabilities=["platform:web"])
    repo.enqueue_job("ios-job", "o", {}, capabilities=["platform:ios"])
    # The web worker gets the web job, never the iOS one.
    web = repo.lease_job("web-worker", ["platform:web"])
    assert web is not None and web.id == "web-job"
    ios = repo.lease_job("mac-worker", ["platform:ios"])
    assert ios is not None and ios.id == "ios-job"


def test_lease_skips_an_unservable_older_job_for_a_servable_younger_one(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    # The oldest queued job needs a capability this worker lacks; the lease must still find the
    # younger job it *can* serve rather than stopping at the head of the queue.
    repo.enqueue_job("old-web", "o", {}, capabilities=["platform:web"])
    repo.enqueue_job("new-ios", "o", {}, capabilities=["platform:ios"])
    leased = repo.lease_job("mac-worker", ["platform:ios"])
    assert leased is not None and leased.id == "new-ios"


def test_unannotated_job_leases_to_any_worker(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("plain", "o", {})  # no capabilities → any worker
    leased = repo.lease_job("whatever", [])
    assert leased is not None and leased.id == "plain"


def test_metrics_counts_unroutable_queued_jobs(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("routable", "o", {}, capabilities=["platform:ios"])
    repo.enqueue_job("unroutable", "o", {}, capabilities=["platform:android"])
    # Only a Mac worker is live; the android job matches no live worker.
    repo.register_worker("mac", ["platform:ios"])
    snap = repo.metrics_snapshot()
    assert snap.unroutable_queued == 1


def test_heartbeat_keeps_a_busy_worker_live_for_routability(
    serve_engine: Callable[..., Engine],
) -> None:
    # BE-0166: a worker busy on a run longer than the lease timeout polls `lease` only after it
    # finishes; the heartbeat must refresh its liveness so its capability's queued jobs are not
    # falsely counted unroutable while it works.
    repo = _repo(serve_engine)
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


def test_metrics_ignores_dead_workers_capabilities(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
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


def test_reclaim_prunes_dead_worker_rows(serve_engine: Callable[..., Engine]) -> None:
    # BE-0166: the workers registry stays bounded to the live pool — a worker not seen within the
    # timeout is pruned by the same reclaim sweep the lease path runs, so restarted workers don't
    # leak a row each.
    repo = _repo(serve_engine)
    repo.register_worker("gone", ["platform:ios"])
    with repo._engine.connect() as conn:
        from sqlalchemy import update

        conn.execute(update(WorkerRecord).values(last_seen=datetime.now(UTC) - timedelta(hours=1)))
        conn.commit()
    repo.reclaim_expired_leases(timedelta(seconds=120))
    with repo._engine.connect() as conn:
        from sqlalchemy import func, select

        assert conn.execute(select(func.count()).select_from(WorkerRecord)).scalar() == 0


def test_heartbeat_op_refreshes_worker_liveness(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
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


def test_worker_lease_op_threads_capabilities_and_registers(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
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


def test_worker_lease_op_rejects_a_malformed_capabilities_payload(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
    state = _state(tmp_path, repo)
    repo.enqueue_job("plain", "o", {"job_id": "plain"})
    # A non-list capabilities value must not crash the lease — it advertises nothing.
    payload, code = ops.worker_lease(state, "w", capabilities="not-a-list")
    assert code == 200 and payload["job_id"] == "plain"


# --- the environment axis (BE-0448) -------------------------------------------------------------


def test_a_device_farm_worker_leases_only_device_farm_jobs(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    # Older first: an unannotated job any plain worker could take, then a local iOS job.
    repo.enqueue_job("plain", "o", {})
    repo.enqueue_job("ios", "o", {}, capabilities=["platform:ios"])
    repo.enqueue_job("batch", "o", {}, capabilities=["environment:devicefarm"])
    # The Device Farm worker skips both local jobs, even the one requiring nothing.
    leased = repo.lease_job("df", ["environment:devicefarm"])
    assert leased is not None and leased.id == "batch"
    assert repo.lease_job("df", ["environment:devicefarm"]) is None


def test_a_plain_worker_never_leases_a_device_farm_job(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("batch", "o", {}, capabilities=["environment:devicefarm"])
    assert repo.lease_job("mac", ["platform:ios", "platform:android", "platform:web"]) is None


def test_unroutable_count_applies_the_environment_rule(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("plain", "o", {})
    repo.enqueue_job("batch", "o", {}, capabilities=["environment:devicefarm"])
    # Only a Device Farm worker is live: it cannot take the unannotated local job.
    repo.register_worker("df", ["environment:devicefarm"])
    assert repo.metrics_snapshot().unroutable_queued == 1
    assert repo.lacks_live_worker("plain")
    assert not repo.lacks_live_worker("batch")


def test_a_device_farm_job_is_unroutable_to_a_plain_worker(
    serve_engine: Callable[..., Engine],
) -> None:
    # A test of its own: on Postgres every engine the factory makes shares one schema.
    repo = _repo(serve_engine)
    repo.enqueue_job("batch", "o", {}, capabilities=["environment:devicefarm"])
    repo.register_worker("mac", ["platform:ios"])
    assert repo.metrics_snapshot().unroutable_queued == 1
    assert repo.lacks_live_worker("batch")


def test_a_worker_from_before_be_0467_leases_a_route_job_and_the_job_says_so(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    caps = ["platform:ios", "cap:ios-simulator:query"]
    repo.enqueue_job("j", "o", {"provenance": {"source": "x"}}, capabilities=caps)
    leased = repo.lease_job("legacy", ["platform:ios"])
    assert leased is not None
    # The stamp merges into the job's own provenance, which the run's report reads back.
    assert leased.spec["provenance"] == {"source": "x", "workerRouting": "unchecked"}


def test_a_route_aware_worker_leases_without_the_unchecked_stamp(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("j", "o", {}, capabilities=["platform:ios", "cap:ios-simulator:query"])
    assert repo.lease_job("dev", ["platform:ios", "cap:ios-device:query"]) is None
    leased = repo.lease_job("sim", ["platform:ios", "cap:ios-simulator:query"])
    assert leased is not None and "provenance" not in leased.spec


def test_live_worker_capabilities_lists_only_live_workers(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.register_worker("w", ["platform:web"])
    assert repo.live_worker_capabilities() == [["platform:web"]]


def _run_project(tmp_path: Path, scenario: str) -> srv.ServeState:
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "s.yaml").write_text(scenario, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  phone:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    runsOn: {ios: {kind: device}}\n"
        f"    paths: {{scenarios: {scn_dir}}}\n",
        encoding="utf-8",
    )
    return srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=tmp_path / "runs", cwd=tmp_path
    )


_LOCATE = "- name: locate\n  steps:\n    - setLocation: { lat: 1, lon: 2 }\n"


def test_start_run_refuses_a_job_no_live_worker_on_its_route_can_run(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    from bajutsu.common.capability.route_tokens import route_tokens

    repo = _repo(serve_engine)
    state = _run_project(tmp_path, _LOCATE)
    state.executor, state.repository = DbQueueExecutor(repo), repo
    # A live worker drives the real-device route, which cannot override the location.
    repo.register_worker("mac", ["platform:ios", "host:darwin", *route_tokens("ios-device")])
    payload, code = ops.start_run(state, {"scenario": "s.yaml", "target": "phone"})
    assert code == 400
    assert payload["missing"] == ["cap:ios-device:deviceControl.setLocation"]
    assert "step 1: setLocation" in payload["error"]


def test_start_run_queues_a_job_while_no_worker_drives_its_route(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    repo = _repo(serve_engine)
    state = _run_project(tmp_path, _LOCATE)
    state.executor, state.repository = DbQueueExecutor(repo), repo
    _, code = ops.start_run(state, {"scenario": "s.yaml", "target": "phone"})
    assert code == 200


def test_a_re_lease_by_a_checking_worker_clears_the_unchecked_stamp(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    caps = ["platform:ios", "cap:ios-simulator:query"]
    repo.enqueue_job("j", "o", {"provenance": {"source": "x"}}, capabilities=caps)
    assert repo.lease_job("legacy", ["platform:ios"]) is not None
    # The legacy worker dies; its lease expires and the sweep re-queues the job.
    with repo._engine.begin() as conn:  # age the lease past its timeout
        from bajutsu.serve.server.models import JobRecord

        conn.execute(update(JobRecord).values(leased_at=datetime.now(UTC) - timedelta(hours=1)))
    repo.reclaim_expired_leases(timedelta(seconds=120))
    leased = repo.lease_job("sim", ["platform:ios", "cap:ios-simulator:query"])
    assert leased is not None
    assert leased.spec["provenance"] == {"source": "x"}

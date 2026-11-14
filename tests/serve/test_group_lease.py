"""The grouped lease and its lease-time caps (BE-0484 unit 3).

A worker leases several queued jobs of one `run-set` group in one request, and the server's
concurrency caps count a group's jobs once they are leased rather than at registration. The
repository tests run against in-memory SQLite in the gate and, behind the `postgres` marker,
against a real Postgres service in the serve-db.yml lane (BE-0309).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.capabilities import UNCHECKED_PROVENANCE
from bajutsu.serve.operations.worker import MAX_LEASE_JOBS
from bajutsu.serve.server.db import ConcurrencyCaps, QueuedJob, SqlRepository, engine_from_url
from bajutsu.serve.server.models import Base, JobGroup, JobRecord

_CAPS = ["environment:devicefarm", "job:run-intent"]
_WORKER = [*_CAPS]


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _group(
    repo: SqlRepository,
    group_id: str,
    ids: list[str],
    *,
    caps: list[str] | dict[str, list[str]] | None = None,
    org: str = "o",
    actor: str | None = None,
    key: str | None = None,
    digest: str = "d",
) -> str | None:
    """Register *ids* as one group; *caps* maps an id to its own list where jobs differ."""
    per_job = caps if isinstance(caps, dict) else dict.fromkeys(ids, caps or _CAPS)
    jobs = [QueuedJob(id=i, spec={"n": i}, capabilities=per_job[i], actor=actor) for i in ids]
    return repo.register_job_group(
        group_id, org_id=org, key=key or group_id, digest=digest, compat_key="c", jobs=jobs
    )


def _statuses(repo: SqlRepository) -> dict[str, str]:
    with Session(repo._engine) as session:
        return dict(session.execute(select(JobRecord.id, JobRecord.status)).tuples().all())


def test_a_bundle_takes_the_oldest_jobs_of_one_group_up_to_max_jobs(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1", "2", "3"])

    leased = repo.lease_jobs("w1", _WORKER, max_jobs=2)

    assert [j.id for j in leased] == ["1", "2"]
    assert _statuses(repo) == {"1": "leased", "2": "leased", "3": "queued"}


def test_an_older_plain_job_leases_alone(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS)
    _group(repo, "g1", ["1", "2"])

    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=3)] == ["p"]
    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=3)] == ["1", "2"]


def test_a_bundle_never_mixes_two_groups(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1"])
    _group(repo, "g2", ["2", "3"])

    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=5)] == ["1"]


def test_a_routing_worker_bundles_one_route_key_and_the_scan_finds_a_younger_one(
    serve_engine: Callable[..., Engine],
) -> None:
    """The oldest job of a key the worker cannot serve never hides a younger key it can."""
    repo = _repo(serve_engine)
    a, b = [*_CAPS, "cap:a"], [*_CAPS, "cap:b"]
    _group(repo, "g1", ["1", "2", "3", "4"], caps={"1": a, "2": a, "3": b, "4": b})

    leased = repo.lease_jobs("w1", [*_WORKER, "cap:b"], max_jobs=5)

    assert [j.id for j in leased] == ["3", "4"]
    assert all(j.spec.get("provenance") is None for j in leased)


def test_a_worker_that_checks_no_routes_bundles_by_base_key(
    serve_engine: Callable[..., Engine],
) -> None:
    """Each job carries its own unchecked mark, not the anchor's."""
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1", "2"], caps={"1": _CAPS, "2": [*_CAPS, "cap:x"]})

    leased = repo.lease_jobs("w1", _WORKER, max_jobs=5)

    assert [j.id for j in leased] == ["1", "2"]
    assert leased[0].spec.get("provenance") is None
    assert leased[1].spec["provenance"] == UNCHECKED_PROVENANCE


def test_lease_job_still_hands_out_one_job(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1", "2"])

    leased = repo.lease_job("w1", _WORKER)

    assert leased is not None and leased.id == "1"
    assert _statuses(repo)["2"] == "queued"


@pytest.mark.parametrize(
    ("caps", "actor"),
    [
        (ConcurrencyCaps(total=2), None),
        (ConcurrencyCaps(per_org=2), None),
        (ConcurrencyCaps(per_user=2), "alice"),
    ],
)
def test_a_cap_stops_the_bundle_at_its_headroom(
    serve_engine: Callable[..., Engine], caps: ConcurrencyCaps, actor: str | None
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS, actor=actor)
    assert repo.lease_job("w1", _WORKER) is not None
    _group(repo, "g1", ["1", "2", "3"], actor=actor)

    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps)] == ["1"]
    assert repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps) == []


def test_a_cap_counts_only_its_own_org_and_actor(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("p", org_id="other", spec={}, capabilities=_CAPS, actor="bob")
    assert repo.lease_job("w1", _WORKER) is not None
    _group(repo, "g1", ["1", "2"], actor="alice")

    leased = repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=ConcurrencyCaps(per_org=2, per_user=2))

    assert [j.id for j in leased] == ["1", "2"]


def test_a_plain_job_still_leases_at_the_cap(serve_engine: Callable[..., Engine]) -> None:
    """Plain jobs keep their registration-time caps, so the lease does not count them again."""
    repo = _repo(serve_engine)
    caps = ConcurrencyCaps(total=1)
    _group(repo, "g1", ["1"])
    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps)] == ["1"]
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS)

    leased = repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps)

    assert [j.id for j in leased] == ["p"]


def test_a_queued_plain_job_holds_a_slot_only_while_a_live_worker_serves_it(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("web", org_id="o", spec={}, capabilities=["platform:web"])
    repo.enqueue_job("nobody", org_id="o", spec={}, capabilities=["platform:tv"])
    _group(repo, "g1", ["1", "2"])
    caps = ConcurrencyCaps(total=2)

    repo.register_worker("w1", _WORKER)
    assert [j.id for j in repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps)] == ["1", "2"]

    _group(repo, "g2", ["3", "4"])
    repo.register_worker("w2", ["platform:web"])
    assert repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=caps) == []


def test_concurrent_leasers_never_overshoot_a_cap(tmp_path: Path) -> None:
    """A file-backed SQLite store, so each thread leases on a connection of its own."""
    engine = engine_from_url(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    _group(repo, "g1", [str(i) for i in range(12)])
    leased: list[str] = []
    lock = threading.Lock()

    def lease(worker: str) -> None:
        for _ in range(3):
            jobs = repo.lease_jobs(worker, _WORKER, max_jobs=3, caps=ConcurrencyCaps(total=5))
            with lock:
                leased.extend(j.id for j in jobs)

    threads = [threading.Thread(target=lease, args=(f"w{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(leased) == len(set(leased)) == 5


def test_a_group_and_its_jobs_land_together(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)

    assert _group(repo, "g1", ["1", "2"], actor="alice") == "g1"

    with Session(repo._engine) as session:
        group = session.get(JobGroup, "g1")
        assert group is not None and (group.key, group.digest) == ("g1", "d")
        rows = session.scalars(select(JobRecord).order_by(JobRecord.id)).all()
    assert [(r.group_id, r.compat_key, r.actor) for r in rows] == [("g1", "c", "alice")] * 2
    assert all(r.route_key and r.base_key for r in rows)


def test_a_failed_job_write_leaves_no_group_and_no_job(serve_engine: Callable[..., Engine]) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("2", org_id="o", spec={})

    with pytest.raises(IntegrityError):
        _group(repo, "g1", ["1", "2"])

    assert repo.get_job_group("g1") is None
    assert _statuses(repo) == {"2": "queued"}


def test_a_repeated_key_returns_its_group_or_refuses_a_different_request(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1"], key="ci-7")

    assert _group(repo, "g2", ["2"], key="ci-7") == "g1"
    assert _group(repo, "g3", ["3"], key="ci-7", digest="other") is None
    assert _group(repo, "g4", ["4"], key="ci-7", org="elsewhere") == "g4"
    assert sorted(_statuses(repo)) == ["1", "4"]


def test_leased_group_counts_key_the_leased_group_rows_by_org_and_actor(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS, actor="alice")
    assert repo.lease_job("w1", _WORKER) is not None  # a plain lease is the registry's to count
    _group(repo, "g1", ["1", "2", "3"], actor="alice")
    assert len(repo.lease_jobs("w1", _WORKER, max_jobs=2)) == 2
    assert repo.lease_job("w1", _WORKER) is not None

    assert repo.leased_group_counts() == {("o", "alice"): 3}


def test_the_unroutable_count_weights_a_group_key_by_its_jobs(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1", "2", "3"])
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=["platform:tv"])
    repo.register_worker("w1", ["platform:web"])

    assert repo.metrics_snapshot().unroutable_queued == 4
    repo.register_worker("w2", _WORKER)
    assert repo.metrics_snapshot().unroutable_queued == 1


def _queued_state(serve_engine: Callable[..., Engine], tmp_path: Path) -> srv.ServeState:
    from bajutsu.serve.server.db_executor import DbQueueExecutor

    repo = _repo(serve_engine)
    return srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )


def test_a_lease_with_max_jobs_answers_a_list_of_todays_bodies(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _queued_state(serve_engine, tmp_path)
    assert isinstance(state.repository, SqlRepository)
    _group(state.repository, "g1", ["1", "2", "3"])

    payload, code = ops.worker_lease(state, "w1", _WORKER, max_jobs=2)

    assert code == 200
    assert [(b["job_id"], b["spec"], b["capabilities"]) for b in payload] == [
        ("1", {"n": "1"}, _CAPS),
        ("2", {"n": "2"}, _CAPS),
    ]
    single, code = ops.worker_lease(state, "w1", _WORKER)
    assert code == 200 and single["job_id"] == "3"
    assert ops.worker_lease(state, "w1", _WORKER, max_jobs=2) == ({}, 204)


def test_a_group_jobs_lease_body_names_its_group_and_compatibility_key(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _queued_state(serve_engine, tmp_path)
    assert isinstance(state.repository, SqlRepository)
    state.repository.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS)
    _group(state.repository, "g1", ["1"])

    plain, _ = ops.worker_lease(state, "w1", _WORKER)
    grouped, _ = ops.worker_lease(state, "w1", _WORKER)

    assert "group_id" not in plain and "compat_key" not in plain
    assert (grouped["group_id"], grouped["compat_key"]) == ("g1", "c")


def test_the_lease_applies_the_servers_caps_to_a_group(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _queued_state(serve_engine, tmp_path)
    assert isinstance(state.repository, SqlRepository)
    state.max_concurrent = 2
    _group(state.repository, "g1", ["1", "2", "3"])

    payload, _ = ops.worker_lease(state, "w1", _WORKER, max_jobs=5)

    assert [b["job_id"] for b in payload] == ["1", "2"]


# Past the ceiling, one poll could lease a whole fan-out to a worker that may die holding it.
@pytest.mark.parametrize("max_jobs", [0, -1, True, "2", MAX_LEASE_JOBS + 1])
def test_a_lease_refuses_a_malformed_max_jobs(
    serve_engine: Callable[..., Engine], tmp_path: Path, max_jobs: object
) -> None:
    state = _queued_state(serve_engine, tmp_path)

    payload, code = ops.worker_lease(state, "w1", _WORKER, max_jobs=max_jobs)

    assert code == 400 and "maxJobs" in payload["error"]


def test_an_unreadable_secret_store_fails_every_job_of_the_bundle(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bajutsu.serve.operations.worker as worker_ops

    state = _queued_state(serve_engine, tmp_path)
    assert isinstance(state.repository, SqlRepository)
    _group(state.repository, "g1", ["1", "2", "3"])

    def broken(_state: srv.ServeState, _org: str) -> dict[str, str]:
        raise RuntimeError("database is down")

    monkeypatch.setattr(worker_ops, "lease_secret_env", broken)

    assert ops.worker_lease(state, "w1", _WORKER, max_jobs=2) == ({}, 204)
    assert _statuses(state.repository) == {"1": "failed", "2": "failed", "3": "queued"}


def test_the_lease_route_reads_max_jobs(tmp_path: Path) -> None:
    from _shared import _post, _serve, queue_repository

    repo = queue_repository()
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)
    _group(repo, "g1", ["1", "2"])
    server, port = _serve(state)
    try:
        status, payload = _post(
            port,
            "/api/worker/lease",
            {"worker_id": "w1", "capabilities": _WORKER, "maxJobs": 2},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert [b["job_id"] for b in payload] == ["1", "2"]


def test_a_bundle_whose_secrets_cannot_be_read_fails_every_job_even_if_one_fail_errors(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bajutsu.serve.operations.worker as worker_ops

    state = _queued_state(serve_engine, tmp_path)
    repo = state.repository
    assert isinstance(repo, SqlRepository)
    _group(repo, "g1", ["1", "2"])

    def broken(_state: srv.ServeState, _org: str) -> dict[str, str]:
        raise RuntimeError("database is down")

    fail = repo.fail_job

    def flaky_fail(job_id: str, error: str, *, worker_id: str | None = None) -> bool:
        if job_id == "1":
            raise RuntimeError("write lost")
        return fail(job_id, error, worker_id=worker_id)

    monkeypatch.setattr(worker_ops, "lease_secret_env", broken)
    monkeypatch.setattr(repo, "fail_job", flaky_fail)

    assert ops.worker_lease(state, "w1", _WORKER, max_jobs=2) == ({}, 204)
    assert _statuses(repo) == {"1": "leased", "2": "failed"}


@pytest.mark.parametrize(("digest", "held"), [("d", "g1"), ("other", None)])
def test_a_key_taken_between_the_read_and_the_commit_resolves_as_a_repeat(
    serve_engine: Callable[..., Engine],
    monkeypatch: pytest.MonkeyPatch,
    digest: str,
    held: str | None,
) -> None:
    """The unique index catches the race the pre-read misses; the rollback leaves no job behind."""
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1"], key="ci-7")
    real = SqlRepository._group_by_key
    reads = iter([None])
    monkeypatch.setattr(
        SqlRepository,
        "_group_by_key",
        staticmethod(lambda session, org, key: next(reads, real(session, org, key))),
    )

    assert _group(repo, "g2", ["2"], key="ci-7", digest=digest) == held
    assert sorted(_statuses(repo)) == ["1"]


def test_an_actorless_group_is_never_held_by_the_per_user_cap(
    serve_engine: Callable[..., Engine],
) -> None:
    repo = _repo(serve_engine)
    _group(repo, "g1", ["1", "2", "3"])

    leased = repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=ConcurrencyCaps(per_user=1))

    assert [j.id for j in leased] == ["1", "2", "3"]


@pytest.mark.parametrize("name", ["", "acme", "x" * 4096])
def test_an_advisory_lock_key_fits_a_signed_32_bit_integer(name: str) -> None:
    from bajutsu.serve.server.db.sql_repository import _lock_key

    assert -(2**31) <= _lock_key(name) < 2**31


def test_the_state_counts_a_leased_group_job_against_a_plain_run(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _queued_state(serve_engine, tmp_path)
    assert isinstance(state.repository, SqlRepository)
    state.max_concurrent = 1
    _group(state.repository, "g1", ["1"])

    assert state.active_jobs() == 0
    assert state.repository.lease_job("w1", _WORKER) is not None
    assert state.active_jobs() == 1
    assert state.try_register(srv.Job(cmd=[])) is None


def test_a_full_scope_is_counted_once_per_lease_not_once_per_group(
    serve_engine: Callable[..., Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each count takes the database's write lock on SQLite, so a full cap must not repeat it."""
    repo = _repo(serve_engine)
    repo.enqueue_job("p", org_id="o", spec={}, capabilities=_CAPS)
    assert repo.lease_job("w1", _WORKER) is not None
    for group in ("g1", "g2", "g3"):
        _group(repo, group, [f"{group}-1"])
    claims: list[str] = []
    claim = repo._claim_bundle

    def spy(*args: object, **kwargs: object) -> object:
        claims.append(str(kwargs["group"]))
        return claim(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "_claim_bundle", spy)

    assert repo.lease_jobs("w1", _WORKER, max_jobs=5, caps=ConcurrencyCaps(total=1)) == []
    assert len(claims) == 1

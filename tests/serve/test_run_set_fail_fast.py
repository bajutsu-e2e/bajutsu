"""Tests for a `run-set` group's fail-fast stop (BE-0499).

A group registered with `failFast` cancels its unfinished jobs when its first job fails, and the
group read says why each stopped job stopped.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from _shared import queue_repository

from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import QueuedJob, SqlRepository
from bajutsu.serve.state import ServeState


def _group(
    tmp_path: Path, *, fail_fast: bool = True, repository: SqlRepository | None = None
) -> ServeState:
    """A queued group ``g1`` of jobs ``1``, ``2``, and ``3``."""
    state = ServeState(runs_dir=tmp_path / "runs", repository=repository or queue_repository())
    assert state.repository is not None
    state.repository.register_job_group(
        "g1",
        org_id="default",
        key="k1",
        digest="d",
        compat_key=None,
        jobs=[QueuedJob(id=i, spec={}) for i in ("1", "2", "3")],
        fail_fast=fail_fast,
    )
    return state


def _repo(state: ServeState) -> SqlRepository:
    assert isinstance(state.repository, SqlRepository)
    return state.repository


def _statuses(state: ServeState) -> list[str]:
    return [row["status"] for row in _repo(state).group_jobs("g1")]


def _reasons(state: ServeState) -> list[str | None]:
    return [job["cancelReason"] for job in ops.job_group_view(state, "g1")[0]["jobs"]]


@pytest.mark.parametrize(
    "result",
    [{"ok": False, "error": "device lost"}, {"exitCode": 1}],
    ids=["failed", "non-zero exit"],
)
def test_a_failing_job_cancels_its_queued_siblings(
    tmp_path: Path, result: dict[str, object]
) -> None:
    state = _group(tmp_path)
    repo = _repo(state)
    assert repo.lease_job("w1") is not None
    stopped: list[str] = []

    if "error" in result:
        assert repo.fail_job("1", "device lost", on_stopped=stopped.extend)
    else:
        assert repo.complete_job("1", result, on_stopped=stopped.extend)

    assert _statuses(state)[1:] == ["cancelled", "cancelled"]
    assert stopped == ["2", "3"]
    assert _reasons(state) == [None, "failFast", "failFast"]
    assert ops.job_group_view(state, "g1")[0]["conclusion"] == "failure"


@pytest.mark.parametrize(
    "result",
    [{"exitCode": 0}, {"exitCode": -15, "cancelled": True}],
    ids=["passed", "cancelled"],
)
def test_a_job_that_did_not_fail_stops_nothing(tmp_path: Path, result: dict[str, object]) -> None:
    state = _group(tmp_path)
    assert _repo(state).lease_job("w1") is not None

    assert _repo(state).complete_job("1", result)

    assert _statuses(state)[1:] == ["queued", "queued"]


def test_a_group_without_the_flag_runs_on_past_a_failure(tmp_path: Path) -> None:
    state = _group(tmp_path, fail_fast=False)
    assert _repo(state).lease_job("w1") is not None

    assert _repo(state).fail_job("1", "device lost")

    assert _statuses(state)[1:] == ["queued", "queued"]


def test_a_rejected_stale_result_stops_nothing(tmp_path: Path) -> None:
    state = _group(tmp_path)
    assert _repo(state).lease_job("w1") is not None

    assert not _repo(state).fail_job("1", "device lost", worker_id="stale")

    assert _statuses(state) == ["leased", "queued", "queued"]


def test_a_leased_sibling_is_flagged_for_its_worker(tmp_path: Path) -> None:
    state = _group(tmp_path)
    repo = _repo(state)
    assert repo.lease_job("w1") is not None
    assert repo.lease_job("w2") is not None
    stopped: list[str] = []

    assert repo.fail_job("1", "device lost", worker_id="w1", on_stopped=stopped.extend)

    assert _statuses(state) == ["failed", "leased", "cancelled"]
    assert repo.cancel_requested("2", "w2")
    assert stopped == ["3"]
    # The leased job has not stopped yet, so it reports no reason until its worker ends it.
    assert _reasons(state) == [None, None, "failFast"]
    assert repo.cancel_leased_job("2", {"exitCode": -15, "cancelled": True}, worker_id="w2")
    assert _reasons(state) == [None, "failFast", "failFast"]


def test_a_lease_that_expires_on_its_last_attempt_stops_the_group(tmp_path: Path) -> None:
    repo = queue_repository()
    expiring = SqlRepository(repo._engine, lease_timeout=timedelta(microseconds=1), max_attempts=1)
    state = _group(tmp_path, repository=expiring)
    assert expiring.lease_job("w1") is not None
    stopped: list[str] = []

    assert expiring.lease_jobs("w2", on_stopped=stopped.extend) == []

    assert _statuses(state) == ["failed", "cancelled", "cancelled"]
    assert stopped == ["2", "3"]


def test_the_lease_route_closes_the_streams_of_the_jobs_it_stopped(tmp_path: Path) -> None:
    repo = queue_repository()
    expiring = SqlRepository(repo._engine, lease_timeout=timedelta(microseconds=1), max_attempts=1)
    state = _group(tmp_path, repository=expiring)
    assert expiring.lease_job("w1") is not None

    assert ops.worker_lease(state, "w2") == ({}, 204)

    assert state.logbus.final("2") is not None and state.logbus.final("3") is not None


def test_the_result_route_closes_the_streams_of_the_jobs_it_stopped(tmp_path: Path) -> None:
    state = _group(tmp_path)
    assert _repo(state).lease_job("w1") is not None

    payload, status = ops.worker_result(
        state, {"job_id": "1", "worker_id": "w1", "result": {"ok": False, "error": "boom"}}
    )

    assert (payload, status) == ({"ok": True}, 200)
    assert state.logbus.final("2") is not None and state.logbus.final("3") is not None


def test_a_persons_cancel_keeps_its_reason_when_a_sibling_fails_later(tmp_path: Path) -> None:
    state = _group(tmp_path)
    repo = _repo(state)
    assert repo.lease_job("w1") is not None
    assert repo.lease_job("w2") is not None
    assert ops.cancel_job(state, "2")[0]["cancelled"]

    assert repo.fail_job("1", "device lost", worker_id="w1")
    assert repo.cancel_leased_job("2", {"exitCode": -15, "cancelled": True}, worker_id="w2")

    assert _reasons(state) == [None, "user", "failFast"]


def test_a_queued_job_a_person_cancels_reads_as_their_cancel(tmp_path: Path) -> None:
    state = _group(tmp_path, fail_fast=False)

    assert ops.cancel_job_group(state, "g1")[0]["cancelled"] == ["1", "2", "3"]

    assert _reasons(state) == ["user", "user", "user"]

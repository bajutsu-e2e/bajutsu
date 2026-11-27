"""Tests for the group events a `run-set` group records for its sinks (BE-0500 unit 3).

Each event lands in the store transaction that causes it, once per group and sink: `queued` with the
registration, `started` with the first lease, and `completed` with the change that leaves no job of
the group unfinished, whichever terminal path makes it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from _shared import queue_repository
from sqlalchemy import select
from sqlalchemy.orm import Session

from bajutsu.serve.server.db import QueuedJob, SqlRepository
from bajutsu.serve.server.models import GroupEvent

_NOTIFY = {
    "repository": "acme/app",
    "target": "demo",
    "sinks": [{"sink": "github", "headSha": "a" * 40}, {"sink": "other", "headSha": "a" * 40}],
}


def _repo(*, notify: dict[str, Any] | None = _NOTIFY, fail_fast: bool = False) -> SqlRepository:
    """A queued group ``g1`` of jobs ``1`` and ``2``."""
    repo: SqlRepository = queue_repository()
    repo.register_job_group(
        "g1",
        org_id="default",
        key="k1",
        digest="d",
        compat_key=None,
        jobs=[QueuedJob(id=i, spec={}) for i in ("1", "2")],
        fail_fast=fail_fast,
        notify=notify,
    )
    return repo


def _events(repo: SqlRepository) -> list[tuple[str, str]]:
    with Session(repo._engine) as session:
        rows = session.execute(
            select(GroupEvent.sink, GroupEvent.event).order_by(GroupEvent.sink, GroupEvent.id)
        ).all()
    return sorted((sink, event) for sink, event in rows)


def _of(repo: SqlRepository, event: str) -> list[str]:
    return [sink for sink, e in _events(repo) if e == event]


def test_registration_records_one_queued_event_per_sink() -> None:
    repo = _repo()

    assert _events(repo) == [("github", "queued"), ("other", "queued")]


def test_a_group_without_notify_records_no_event() -> None:
    repo = _repo(notify=None)
    leased = repo.lease_jobs("w1", max_jobs=2)
    for job in leased:
        assert repo.complete_job(job.id, {"exitCode": 0})

    assert _events(repo) == []


def test_a_retried_registration_records_nothing_new() -> None:
    repo = _repo()

    held = repo.register_job_group(
        "g2", org_id="default", key="k1", digest="d", compat_key=None, jobs=[], notify=_NOTIFY
    )

    assert held == "g1"
    assert _of(repo, "queued") == ["github", "other"]


def test_the_first_lease_records_started_once() -> None:
    repo = _repo()

    assert repo.lease_job("w1") is not None
    assert repo.lease_job("w2") is not None

    assert _of(repo, "started") == ["github", "other"]


def test_a_finished_job_with_a_sibling_unfinished_records_no_completion() -> None:
    repo = _repo()
    assert repo.lease_job("w1") is not None

    assert repo.complete_job("1", {"exitCode": 0})

    assert _of(repo, "completed") == []


@pytest.mark.parametrize("path", ["complete", "fail", "cancel leased"])
def test_the_last_job_to_finish_records_completed(path: str) -> None:
    repo = _repo()
    first, second = repo.lease_jobs("w1", max_jobs=2)
    assert repo.complete_job(first.id, {"exitCode": 0})

    if path == "complete":
        assert repo.complete_job(second.id, {"exitCode": 1})
    elif path == "fail":
        assert repo.fail_job(second.id, "device lost")
    else:
        assert repo.cancel_leased_job(second.id, {"cancelled": True}, worker_id="w1")

    assert _of(repo, "completed") == ["github", "other"]


def test_cancelling_the_last_queued_job_records_completed() -> None:
    repo = _repo()
    assert repo.lease_job("w1") is not None
    assert repo.complete_job("1", {"exitCode": 0})

    assert repo.cancel_job("2") == ("cancelled", True)

    assert _of(repo, "completed") == ["github", "other"]


def test_a_reclaim_that_fails_the_last_job_records_completed() -> None:
    repo = _repo()
    first, second = repo.lease_jobs("w1", max_jobs=2)
    assert repo.complete_job(first.id, {"exitCode": 0})

    repo.reclaim_expired_leases(timedelta(seconds=-1), max_attempts=1)

    assert repo.get_job(second.id)["status"] == "failed"  # type: ignore[index]
    assert _of(repo, "completed") == ["github", "other"]


def test_a_reclaim_that_cancels_the_last_flagged_job_records_completed() -> None:
    repo = _repo()
    first, second = repo.lease_jobs("w1", max_jobs=2)
    assert repo.complete_job(first.id, {"exitCode": 0})
    assert repo.cancel_job(second.id) == ("leased", True)

    repo.reclaim_expired_leases(timedelta(seconds=-1))

    assert repo.get_job(second.id)["status"] == "cancelled"  # type: ignore[index]
    assert _of(repo, "completed") == ["github", "other"]


def test_a_reclaim_that_stops_a_fail_fast_group_records_completed_once() -> None:
    repo = _repo(fail_fast=True)
    assert repo.lease_job("w1") is not None

    repo.reclaim_expired_leases(timedelta(seconds=-1), max_attempts=1)

    assert repo.get_job("2")["status"] == "cancelled"  # type: ignore[index]
    assert _of(repo, "completed") == ["github", "other"]


def test_a_reclaim_that_requeues_records_no_completion() -> None:
    repo = _repo()
    first, _ = repo.lease_jobs("w1", max_jobs=2)
    assert repo.complete_job(first.id, {"exitCode": 0})

    repo.reclaim_expired_leases(timedelta(seconds=-1), max_attempts=3)

    assert _of(repo, "completed") == []


def test_a_fail_fast_stop_that_cancels_the_rest_records_completed() -> None:
    repo = _repo(fail_fast=True)
    assert repo.lease_job("w1") is not None

    assert repo.fail_job("1", "device lost")

    assert repo.get_job("2")["status"] == "cancelled"  # type: ignore[index]
    assert _of(repo, "completed") == ["github", "other"]


def test_completion_is_recorded_once() -> None:
    repo = _repo()
    first, second = repo.lease_jobs("w1", max_jobs=2)
    assert repo.complete_job(first.id, {"exitCode": 0})
    assert repo.complete_job(second.id, {"exitCode": 0})

    assert not repo.complete_job(second.id, {"exitCode": 0})
    assert repo.cancel_job(second.id) == ("done", False)

    assert _of(repo, "completed") == ["github", "other"]


def test_a_clean_finish_takes_no_group_lock_when_the_group_notifies_nobody() -> None:
    repo = _repo(notify=None, fail_fast=True)

    with Session(repo._engine) as session:
        assert repo._lock_group(session, "1", failing=False) is None
        assert repo._lock_group(session, "1", failing=True) == ("g1", True)


def test_a_notifying_group_is_locked_on_every_finish() -> None:
    repo = _repo()

    with Session(repo._engine) as session:
        assert repo._lock_group(session, "1", failing=False) == ("g1", False)


def test_a_lease_records_started_once_whatever_the_bundle_size() -> None:
    from sqlalchemy import event as sa_event

    repo = _repo()
    inserts: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *_rest: Any) -> None:
        if statement.lstrip().upper().startswith("INSERT") and "group_events" in statement:
            inserts.append(statement)

    sa_event.listen(repo._engine, "before_cursor_execute", count)

    assert len(repo.lease_jobs("w1", max_jobs=2)) == 2

    assert len(inserts) == 1
    assert _of(repo, "started") == ["github", "other"]


def test_the_group_keeps_its_notify_selection() -> None:
    repo = _repo()

    group = repo.get_job_group("g1")

    assert group is not None and group["notify"] == _NOTIFY

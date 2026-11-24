"""Tests for the loop that delivers group events to their sinks (BE-0500 unit 6).

Delivery runs after the verdict: it claims a due row the way a worker claims a job, delivers one
group and sink's events in order, retries a failure with a bounded backoff, and abandons a row it
cannot deliver, counting each abandonment by format. It never changes a job, a group, or a verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _shared import queue_repository
from sqlalchemy import select
from sqlalchemy.orm import Session

from bajutsu.serve.group_notify import GroupSink, delivery
from bajutsu.serve.group_notify.delivery import ABANDONED, MAX_ATTEMPTS, deliver_once
from bajutsu.serve.server.db import GroupEventClaim, QueuedJob, SqlRepository
from bajutsu.serve.server.models import GroupEvent

_SHA = "a" * 40
_SINKS = {"github": GroupSink(name="github", format="github-check")}


def _notify(sha: str = _SHA, sinks: tuple[str, ...] = ("github",)) -> dict[str, Any]:
    return {
        "repository": "acme/app",
        "target": "demo",
        "sinks": [{"sink": s, "headSha": sha} for s in sinks],
    }


def _register(
    repo: SqlRepository, group: str, notify: dict[str, Any], jobs: tuple[str, ...]
) -> None:
    repo.register_job_group(
        group,
        org_id="default",
        key=group,
        digest="d",
        compat_key=None,
        jobs=[QueuedJob(id=i, spec={}) for i in jobs],
        notify=notify,
    )


class _Adapter:
    """Records each delivery; fails while ``failing``, else returns ``ref-<event>``."""

    def __init__(self) -> None:
        self.calls: list[GroupEventClaim] = []
        self.failing = False

    def __call__(self, claim: GroupEventClaim, sink: GroupSink) -> str | None:
        self.calls.append(claim)
        if self.failing:
            raise RuntimeError("GitHub is down")
        return f"ref-{claim.event}"


def _pass(
    repo: SqlRepository,
    adapter: _Adapter,
    *,
    at: timedelta = timedelta(seconds=1),
    snapshots: list[str] | None = None,
    sinks: dict[str, GroupSink] = _SINKS,
) -> list[tuple[str, str]]:
    def snapshot_for(claim: GroupEventClaim) -> dict[str, Any]:
        if snapshots is not None:
            snapshots.append(claim.group_id)
        return {"conclusion": "success", "total": 1, "passed": 1, "rows": []}

    before = len(adapter.calls)
    moment = datetime.now(UTC) + at
    deliver_once(repo, sinks, {"github-check": adapter}, snapshot_for, clock=lambda: moment)
    return [(c.event, c.reference or "") for c in adapter.calls[before:]]


def _states(repo: SqlRepository, sink: str = "github") -> dict[tuple[str, str], str]:
    with Session(repo._engine) as session:
        rows = session.execute(
            select(GroupEvent.group_id, GroupEvent.event, GroupEvent.state).where(
                GroupEvent.sink == sink
            )
        ).all()
    return {(g, e): s for g, e, s in rows}


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    ABANDONED.clear()


def test_events_deliver_in_order_each_with_the_reference_the_last_returned() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    adapter = _Adapter()

    assert _pass(repo, adapter) == [("queued", "")]
    leased = repo.lease_job("w1")
    assert leased is not None
    assert _pass(repo, adapter) == [("started", "ref-queued")]
    assert repo.complete_job(leased.id, {"exitCode": 0})
    assert _pass(repo, adapter) == [("completed", "ref-started")]
    assert set(_states(repo).values()) == {"delivered"}


def test_the_adapter_sees_when_the_event_happened() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    adapter = _Adapter()
    registered = datetime.now(UTC)

    _pass(repo, adapter, at=timedelta(minutes=5))

    assert abs((adapter.calls[0].occurred_at - registered).total_seconds()) < 5


def test_a_later_event_waits_for_an_earlier_one_still_pending() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    assert repo.lease_job("w1") is not None
    adapter = _Adapter()
    adapter.failing = True

    assert _pass(repo, adapter) == [("queued", "")]
    assert _pass(repo, adapter, at=timedelta(hours=1)) == [("queued", "")]


def test_a_failure_retries_after_its_backoff() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    adapter = _Adapter()
    adapter.failing = True
    _pass(repo, adapter)
    adapter.failing = False

    first_retry = delivery.backoff(1)
    assert _pass(repo, adapter, at=first_retry - timedelta(seconds=2)) == []
    assert _pass(repo, adapter, at=first_retry + timedelta(seconds=2)) == [("queued", "")]


def test_the_backoff_grows_and_is_bounded() -> None:
    delays = [delivery.backoff(n) for n in range(1, MAX_ATTEMPTS + 3)]

    assert delays == sorted(delays)
    assert delays[-1] == delays[-2]


def test_the_last_failed_attempt_abandons_the_row_and_counts_it() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    assert repo.lease_job("w1") is not None
    adapter = _Adapter()
    adapter.failing = True

    for day in range(1, MAX_ATTEMPTS + 1):
        _pass(repo, adapter, at=timedelta(days=day))
    adapter.failing = False

    assert _states(repo)[("g1", "queued")] == "abandoned"
    assert ABANDONED == {"github-check": 1}
    assert _pass(repo, adapter, at=timedelta(days=MAX_ATTEMPTS + 1)) == [("started", "")]


def test_a_completed_snapshot_is_built_once_across_retries() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    adapter = _Adapter()
    _pass(repo, adapter)
    leased = repo.lease_job("w1")
    assert leased is not None
    _pass(repo, adapter)
    assert repo.complete_job(leased.id, {"exitCode": 0})
    snapshots: list[str] = []
    adapter.failing = True

    _pass(repo, adapter, snapshots=snapshots)
    adapter.failing = False
    _pass(repo, adapter, at=timedelta(hours=1), snapshots=snapshots)

    assert snapshots == ["g1"]
    assert adapter.calls[-1].snapshot == {
        "conclusion": "success",
        "total": 1,
        "passed": 1,
        "rows": [],
    }


def test_a_claimed_row_is_not_claimed_again_until_its_claim_expires() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    now = datetime.now(UTC) + timedelta(seconds=1)

    first = repo.claim_group_events(["github"], now=now, hold=timedelta(minutes=1))
    second = repo.claim_group_events(["github"], now=now, hold=timedelta(minutes=1))
    later = repo.claim_group_events(
        ["github"], now=now + timedelta(minutes=2), hold=timedelta(minutes=1)
    )

    assert [c.event for c in first] == ["queued"]
    assert second == []
    assert [c.event for c in later] == ["queued"]
    assert not repo.settle_group_event(first[0].id, first[0].token, state="delivered")


def test_queued_rows_for_one_commit_and_sink_deliver_one_at_a_time() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    _register(repo, "g2", _notify(), ("2",))
    _register(repo, "g3", _notify(sha="b" * 40), ("3",))
    now = datetime.now(UTC) + timedelta(seconds=1)

    claimed = repo.claim_group_events(["github"], now=now, hold=timedelta(minutes=1))

    assert sorted(c.group_id for c in claimed) == ["g1", "g3"]
    first = next(c for c in claimed if c.group_id == "g1")
    assert repo.claim_group_events(["github"], now=now, hold=timedelta(minutes=1)) == []
    assert repo.settle_group_event(first.id, first.token, state="delivered", reference="7")
    assert [
        c.group_id for c in repo.claim_group_events(["github"], now=now, hold=timedelta(minutes=1))
    ] == ["g2"]


def test_a_queued_claim_yields_to_an_earlier_sibling_another_pass_took_at_once() -> None:
    from sqlalchemy import update

    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    _register(repo, "g2", _notify(), ("2",))
    now = datetime.now(UTC) + timedelta(seconds=1)
    # Both passes read the other's row unclaimed: this one took g2 while another took g1.
    with Session(repo._engine) as session:
        session.execute(
            update(GroupEvent)
            .where(GroupEvent.group_id == "g1")
            .values(claimed_until=now + timedelta(minutes=1), claim_token="other")
        )
        session.execute(
            update(GroupEvent)
            .where(GroupEvent.group_id == "g2")
            .values(claimed_until=now + timedelta(minutes=1), claim_token="mine")
        )
        session.commit()
        row = session.scalars(select(GroupEvent).where(GroupEvent.group_id == "g2")).one()
        mine = GroupEventClaim(
            id=row.id,
            token="mine",
            group_id="g2",
            org_id="default",
            sink="github",
            event="queued",
            occurred_at=row.occurred_at,
            attempts=0,
            reference=None,
            snapshot=None,
            notify=_notify(),
        )

        kept = repo._yield_contended(session, [mine], now)

    assert kept == []
    assert _states(repo)[("g2", "queued")] == "pending"
    assert [
        c.group_id
        for c in repo.claim_group_events(
            ["github"], now=now + timedelta(minutes=2), hold=timedelta(minutes=1)
        )
    ] == ["g1"]


def test_the_loop_runs_passes_on_a_daemon_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    ran = threading.Event()
    monkeypatch.setattr(delivery, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(delivery, "deliver_once", lambda *_a, **_k: ran.set())

    thread = delivery.start_delivery_loop(queue_repository(), _SINKS, {}, lambda _c: {})

    assert thread is not None and thread.daemon
    assert ran.wait(5)


def test_a_failed_pass_does_not_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    calls: list[int] = []
    second = threading.Event()

    def flaky(*_a: object, **_k: object) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is down")
        second.set()

    monkeypatch.setattr(delivery, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(delivery, "deliver_once", flaky)

    delivery.start_delivery_loop(queue_repository(), _SINKS, {}, lambda _c: {})

    assert second.wait(5)


def test_no_sink_starts_no_loop() -> None:
    assert delivery.start_delivery_loop(queue_repository(), {}, {}, lambda _c: {}) is None


def test_a_process_claims_only_the_sinks_it_defines() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(sinks=("github", "elsewhere")), ("1",))
    adapter = _Adapter()

    assert _pass(repo, adapter) == [("queued", "")]
    assert _states(repo)[("g1", "queued")] == "delivered"
    assert _states(repo, "elsewhere")[("g1", "queued")] == "pending"
    assert ABANDONED == {}


def test_a_row_no_process_defines_is_abandoned_once_it_outlives_the_backoff() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(sinks=("elsewhere",)), ("1",))
    adapter = _Adapter()

    _pass(repo, adapter, at=delivery.ABANDON_AFTER - timedelta(minutes=1))
    assert ABANDONED == {}

    _pass(repo, adapter, at=delivery.ABANDON_AFTER + timedelta(minutes=1))
    assert ABANDONED == {"unknown": 1}
    assert _states(repo, "elsewhere")[("g1", "queued")] == "abandoned"
    assert adapter.calls == []


def test_an_orphan_sweep_naming_no_sink_abandons_nothing() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    later = datetime.now(UTC) + delivery.ABANDON_AFTER + timedelta(minutes=1)

    assert repo.abandon_orphan_group_events([], before=later) == []
    assert _states(repo)[("g1", "queued")] == "pending"


def test_each_claim_is_held_from_the_moment_it_is_taken() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    _register(repo, "g2", _notify(sha="b" * 40), ("2",))
    start = datetime.now(UTC) + timedelta(seconds=1)
    # One read for the orphan sweep, then one before each claim.
    ticks = iter([start, start, start + timedelta(minutes=10), start + timedelta(minutes=20)])
    holds: list[datetime] = []

    def slow(claim: GroupEventClaim, sink: GroupSink) -> str | None:
        with Session(repo._engine) as session:
            until = session.scalar(
                select(GroupEvent.claimed_until).where(GroupEvent.id == claim.id)
            )
        assert until is not None
        holds.append(until.replace(tzinfo=UTC) if until.tzinfo is None else until)
        return None

    deliver_once(repo, _SINKS, {"github-check": slow}, lambda _c: {}, clock=lambda: next(ticks))

    assert holds == [
        start + delivery.CLAIM_HOLD,
        start + timedelta(minutes=10) + delivery.CLAIM_HOLD,
    ]


def test_a_store_error_on_one_claim_does_not_stop_the_pass() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    _register(repo, "g2", _notify(sha="b" * 40), ("2",))
    adapter = _Adapter()
    settle = repo.settle_group_event
    broken = {"once": True}

    def flaky_settle(*args: Any, **kwargs: Any) -> bool:
        if broken.pop("once", False):
            raise RuntimeError("database blip")
        return bool(settle(*args, **kwargs))

    repo.settle_group_event = flaky_settle

    _pass(repo, adapter)

    assert sorted(c.group_id for c in adapter.calls) == ["g1", "g2"]


def test_a_delivery_whose_claim_lapsed_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    repo.settle_group_event = lambda *_a, **_k: False

    _pass(repo, _Adapter())

    assert "after its claim lapsed" in caplog.text
    assert "g1" in caplog.text


def test_a_snapshot_whose_claim_lapsed_is_logged_and_still_delivered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    leased = repo.lease_job("w1")
    assert leased is not None
    assert repo.complete_job(leased.id, {"exitCode": 0})
    repo.save_group_event_snapshot = lambda *_a, **_k: False

    delivered = [e for _ in range(3) for e, _r in _pass(repo, _Adapter())]

    assert "completed" in delivered
    assert "could not store the snapshot of completed" in caplog.text


def test_delivery_never_touches_the_jobs() -> None:
    repo = queue_repository()
    _register(repo, "g1", _notify(), ("1",))
    adapter = _Adapter()
    adapter.failing = True

    _pass(repo, adapter)

    job = repo.get_job("1")
    assert job is not None and job["status"] == "queued"


def test_the_metric_counts_abandonments_by_format() -> None:
    from bajutsu.serve.operations.metrics import render_metrics
    from bajutsu.serve.state import ServeState

    ABANDONED["github-check"] += 2

    text, _ = render_metrics(ServeState(runs_dir=Path("runs")))

    assert "# TYPE bajutsu_group_events_abandoned_total counter" in text
    assert 'bajutsu_group_events_abandoned_total{format="github-check"} 2' in text

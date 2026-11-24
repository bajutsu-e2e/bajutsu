"""Deliver pending group events to their sinks, after the verdict and apart from it (BE-0500)."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bajutsu.serve import oplog

from .group_sink import GroupSink

if TYPE_CHECKING:
    from bajutsu.serve.server.db import GroupEventClaim, Repository

_log = logging.getLogger("bajutsu.serve.group_notify")

# An adapter delivers one claimed event and returns the reference later events of the same group and
# sink address (a check run id), or None to keep the one it was given. Raising asks for a retry.
Adapter = Callable[["GroupEventClaim", GroupSink], "str | None"]
SnapshotFor = Callable[["GroupEventClaim"], dict[str, Any]]

MAX_ATTEMPTS = 8
_FIRST_DELAY = timedelta(seconds=15)
_MAX_DELAY = timedelta(minutes=15)
# Past the worst case of one delivery (six GitHub calls at a 30-second timeout each), so a slow
# delivery never loses its claim to another process; a dead process's rows resume after it.
CLAIM_HOLD = timedelta(minutes=5)
# The most rows one pass delivers; each is claimed just before its delivery, not all up front.
_PASS_LIMIT = 20
POLL_SECONDS = 5.0


def backoff(attempts: int) -> timedelta:
    """How long a row waits after its *attempts*-th failed delivery."""
    delay: timedelta = _FIRST_DELAY * 2 ** (attempts - 1)
    return min(delay, _MAX_DELAY)


# A row whose sink no process defines is abandoned once it outlives every retry a defined one gets,
# so this also bounds a rollout: a replica on the old sink file abandons rows for a sink an updated
# replica has just gained once this has passed since the event.
ABANDON_AFTER = sum((backoff(n) for n in range(1, MAX_ATTEMPTS + 1)), timedelta())

# Abandoned rows by sink format, `unknown` for a sink no definition names; rendered as
# `bajutsu_group_events_abandoned_total`. Per process, as a Prometheus counter is.
ABANDONED: Counter[str] = Counter()


def deliver_once(
    repository: Repository,
    sinks: Mapping[str, GroupSink],
    adapters: Mapping[str, Adapter],
    snapshot_for: SnapshotFor,
    *,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """One delivery pass: abandon orphaned rows, then deliver every due row this process may claim.

    Args:
        repository: The store holding the group events.
        sinks: The sinks this process defines; it claims rows of these alone.
        adapters: The adapter for each sink format.
        snapshot_for: Builds a `completed` event's snapshot the first time its row is claimed.
        clock: The current time, read again before each claim so every claim is held from the
            moment it is taken; injectable for tests.
    """
    read = clock or (lambda: datetime.now(UTC))
    for sink in repository.abandon_orphan_group_events(list(sinks), before=read() - ABANDON_AFTER):
        ABANDONED["unknown"] += 1
        oplog.log_event(
            _log,
            "group_event.abandoned",
            f"no sink file on this process defines group-notify sink {sink!r}",
            level=logging.WARNING,
            sink=sink,
            format="unknown",
        )
    for _ in range(_PASS_LIMIT):
        now = read()
        claims = repository.claim_group_events(list(sinks), now=now, hold=CLAIM_HOLD, limit=1)
        if not claims:
            return
        claim = claims[0]
        try:
            _deliver(repository, sinks[claim.sink], adapters, snapshot_for, claim, now)
        except Exception:  # a store error on one row leaves the rest of the pass to run
            _log.exception("group-notify could not settle event %s", claim.id)


def _deliver(
    repository: Repository,
    sink: GroupSink,
    adapters: Mapping[str, Adapter],
    snapshot_for: SnapshotFor,
    claim: GroupEventClaim,
    now: datetime,
) -> None:
    try:
        if claim.event == "completed" and claim.snapshot is None:
            snapshot = snapshot_for(claim)
            repository.save_group_event_snapshot(claim.id, claim.token, snapshot)
            claim = replace(claim, snapshot=snapshot)
        reference = adapters[sink.format](claim, sink)
    except Exception as e:  # any adapter failure is a retry, never a crash of the loop
        attempts = claim.attempts + 1
        reason = f"{type(e).__name__}: {e}"
        if attempts >= MAX_ATTEMPTS:
            repository.settle_group_event(claim.id, claim.token, state="abandoned")
            ABANDONED[sink.format] += 1
            oplog.log_event(
                _log,
                "group_event.abandoned",
                f"gave up delivering {claim.event} of group {claim.group_id} to {sink.name!r}: "
                f"{reason}",
                level=logging.WARNING,
                sink=sink.name,
                format=sink.format,
                group=claim.group_id,
            )
        else:
            repository.settle_group_event(
                claim.id, claim.token, state="pending", next_attempt_at=now + backoff(attempts)
            )
            oplog.log_event(
                _log,
                "group_event.retry",
                f"delivering {claim.event} of group {claim.group_id} to {sink.name!r} failed: "
                f"{reason}",
                level=logging.INFO,
                sink=sink.name,
                group=claim.group_id,
                attempts=attempts,
            )
        return
    repository.settle_group_event(claim.id, claim.token, state="delivered", reference=reference)


def start_delivery_loop(
    repository: Repository,
    sinks: Mapping[str, GroupSink],
    adapters: Mapping[str, Adapter],
    snapshot_for: SnapshotFor,
) -> threading.Thread | None:
    """Run `deliver_once` every few seconds on a daemon thread; None when no sink is defined."""
    if not sinks:
        return None

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(POLL_SECONDS):
            try:
                deliver_once(repository, sinks, adapters, snapshot_for)
            except Exception as e:  # a store blip ends one pass, not the loop
                oplog.log_event(
                    _log,
                    "group_event.pass_failed",
                    f"a group-notify delivery pass failed: {type(e).__name__}: {e}",
                    level=logging.ERROR,
                )

    thread = threading.Thread(target=loop, name="group-notify", daemon=True)
    thread.start()
    return thread

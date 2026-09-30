"""The actuations a driver has performed since the last drain."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from .actuation import Actuation
from .drained import Drained

# The accumulator's cap, sized well above the worst case for one drained step: a `scroll` spends up to
# `maxScrolls` gestures (default 15, author-settable with no ceiling) and an Android `tap` can add
# three more swipes bringing its target on screen. It exists for the consumers that never drain — the
# crawl, `record`'s replay, the conformance suite — which would otherwise accumulate one record per
# gesture for a whole session.
MAX_RECORDS = 512


class ActuationLog:
    """The actuations a driver has performed since the last drain.

    Bounded (see `MAX_RECORDS`) so an undraining consumer keeps the most recent records instead of
    growing with the session. Dropping is counted, not silent: the earliest gestures of a step are
    exactly what "the scroll never reached its target" needs to show.
    """

    def __init__(
        self, maxlen: int = MAX_RECORDS, *, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._records: deque[Actuation] = deque(maxlen=maxlen)
        self._dropped = 0
        # Injectable so a test can put the stamp on the same epoch as its fake runner clock. The
        # default must stay `time.monotonic`: the runner converts the stamp with the scenario's
        # `wall_offset_s`, which is only meaningful against `RealClock`'s own epoch.
        self._now = now

    def record(self, actuation: Actuation) -> None:
        """Append one actuation, discarding the oldest if the log is already full.

        Stamps `Actuation.at` here, the one place every driver's actuation passes through, so no
        backend has to remember to time its own gestures. A record arriving with `at` already set
        keeps it.
        """
        if actuation.at is None:
            actuation = replace(actuation, at=self._now())
        if len(self._records) == self._records.maxlen:
            self._dropped += 1
        self._records.append(actuation)

    def settle(self, accepted: bool) -> None:
        """Stamp the most recent record with the answer the platform just gave.

        A record is written before its transport answers, so a gesture that failed still shows what it
        aimed at. On the two channels that *can* refuse and be retried, this is how a refused attempt
        stops reading as one that landed — without it, a stale-retried tap leaves three identical
        records and nothing saying which one the device honored. A no-op on an empty log, so a driver
        that settles without having recorded cannot corrupt the previous step's last record: the drain
        already took it.
        """
        if self._records:
            self._records[-1] = replace(self._records[-1], accepted=accepted)

    def drain(self) -> Drained:
        """Everything recorded since the last drain, oldest first, emptying the log."""
        out = Drained(records=list(self._records), dropped=self._dropped)
        self._records.clear()
        self._dropped = 0
        return out

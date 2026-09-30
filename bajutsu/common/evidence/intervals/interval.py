"""A running interval capture, finalized into its artifact file when it stops."""

from __future__ import annotations

import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ._null_proc import _NullProc
from .proc import Proc

PROVIDER = "simctl"


# How long `stop()` waits for the signalled process to exit before a hard `kill()`. A log stream ends
# the instant it sees the stop signal, so a short grace is plenty. A screen recording is different: on
# the stop signal `recordVideo` / `screenrecord` still has to flush and mux the whole clip to disk
# ("Writing to disk"), which scales with the recording's length and the host's load. Killing it
# mid-write truncates the mp4 so it has no `moov` atom (unplayable) and — worse on iOS — leaves the
# simulator's host-recording session held, so every later capture fails with "Host recording is
# already in progress". So video gets a generous finalize window; the kill stays only as a last resort.
_STOP_TIMEOUT = 10.0


@dataclass
class Interval:
    """A running interval capture; `stop()` finalizes the artifact file.

    `_transform`, if set, post-processes the captured file after the process stops
    (e.g. parse a raw log stream into a structured trace) and returns the final path.
    """

    kind: str  # "video" | "deviceLog" | "appTrace"
    path: Path
    provider: str = PROVIDER
    # The time.monotonic() instant the capture was confirmed to have begun, for the runner to anchor
    # step/network report timestamps to instead of the moment the process was merely spawned. The
    # confirmation strength differs by provider: iOS takes simctl at its word when it reports its
    # first frame processed (`Recording started` on stderr, the signal its own `--help` names);
    # Android confirms only that the device-side process exists yet (a weaker signal, but still real
    # and much earlier than a guess — the app hasn't launched at that point either). None when no
    # confirmation was attempted or it never succeeded — callers must treat that as "no better
    # information than before", not as zero.
    true_start: float | None = None
    # Whether a requested start confirmation succeeded, or None when none was requested (BE-0354).
    # `true_start` alone cannot answer that — it is None both for a capture nobody confirmed and for
    # one whose confirmation timed out — and only the second says the device's capture pipeline is
    # not producing. The recovery-rung choice reads it; nothing on the verdict path does.
    start_confirmed: bool | None = None
    # The `time.monotonic()` instant the recorder was started, before any confirmation wait. Unlike
    # `true_start` this is never in doubt, which is what makes it the bound `stop()` sanity-checks a
    # measured duration against: no faithful recording can be longer than the span it was open for.
    spawned_at: float | None = None
    # The `time.monotonic()` instant this recording's *own* timeline begins, filled in by `stop()`
    # as "the instant the recording ended, minus the finished file's duration". Every
    # `true_start` above is a proxy — a recorder's own start line, a device-side pid, a browser page
    # that exists — and each fires at its own distance from the moment the recorder actually began
    # producing frames, so a report anchored to one seeks off by that distance. This is the
    # recorder's own answer instead of a proxy for it. None when the duration could not be read or
    # is not a wall-clock measure, which leaves the proxy in charge.
    measured_start: float | None = None
    # Whether this recording runs until `stop()` *returns* rather than ending the moment the stop
    # signal lands. A subprocess recorder stops at the signal and then spends its finalize (and, on
    # Android, a pull off the device) writing a clip it already captured; the Playwright lane instead
    # records right up to the context close that `stop()` performs. The two need different end
    # instants, and using one for the other shifts `measured_start` by that whole tail.
    stops_when_stop_returns: bool = False
    # Whether the finished file's duration runs all the way to the stop, which is what makes
    # `ended_at - duration` the first frame. simctl's mp4 does: its movie duration spans spawn to
    # SIGINT even over a static screen. Playwright's webm does too, repeating the last frame until
    # the context closes. Android's `screenrecord` does not: it encodes a frame only when the screen
    # changes and its mp4 ends at the last one, so a static tail before the stop makes that
    # subtraction late by the whole tail — measured at 0.8s on an emulator, well inside the window
    # `_measured_start` checks, so nothing downstream could catch it. Such a recorder leaves
    # `measured_start` unset and anchors on its start confirmation instead.
    duration_spans_stop: bool = True
    _proc: Proc = field(repr=False, default_factory=_NullProc)
    _stop_signal: int = signal.SIGTERM
    _stop_timeout: float = _STOP_TIMEOUT
    _transform: Callable[[Path], Path] | None = field(default=None, repr=False)

    def stop(self) -> Path:
        before = time.monotonic()
        self._proc.stop(self._stop_signal, self._stop_timeout)
        ended_at = time.monotonic() if self.stops_when_stop_returns else before
        path = self._transform(self.path) if self._transform is not None else self.path
        if self.kind == "video" and self.duration_spans_stop:
            # Imported in the method, not at module load: `_functions` builds intervals from this
            # class, and rule 5 breaks the cycle the split creates on this side.
            from ._functions import _measured_start

            self.measured_start = _measured_start(path, ended_at, self.spawned_at)
        return path

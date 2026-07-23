"""The two post-launch readiness waits the environments share, over one deadline skeleton (BE-0256).

`_await_ready` (the device/web families) and `_await_boot` (Android) are both condition waits — no
fixed up-front sleep — built on `base.deadline_ticks`, the same monotonic-deadline/exponential-backoff
loop `base.wait_until` runs (BE-0118). Each keeps its own poll body and return type; only the loop
skeleton is shared, so there is no second hand-rolled deadline implementation to drift.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Literal

from bajutsu import adb
from bajutsu.doctor import namespace_of
from bajutsu.drivers import base
from bajutsu.evidence.network import TransitionSource, _no_transitions
from bajutsu.platform_lifecycle.protocols import ReadinessResult

_logger = logging.getLogger(__name__)

# A readyWhen selector is a usable readiness signal only if it carries a per-element condition;
# positional-only fields (`index`, `within`) match every element via find_all, so they fall back to
# the element-count heuristic rather than declaring the app ready on the first element.
_READY_MATCH_KEYS = ("id", "idMatches", "label", "labelMatches", "traits", "value")


def _await_boot(env: adb.Env, timeout: float = 60.0, poll: float = 0.5) -> None:
    """Wait until the device reports `sys.boot_completed`, polling to a bounded deadline (a condition wait at `poll` intervals, not a fixed up-front sleep).

    The Android peer of `simctl bootstatus`: `getprop sys.boot_completed` is polled to a bounded
    deadline. `boot_completed` treats a device adb can't yet see as "not booted" and retries it (no
    unbounded `adb wait-for-device` block), but lets a missing `adb` binary propagate so `start`
    fails fast with a clean error instead of spinning here. An already-booted device returns on the
    first poll; if the deadline passes with no device, the launch sequence proceeds and fails loudly
    on the first `pm clear` / `am start` with a clean `DeviceError`, rather than hanging here.
    """
    for _ in base.deadline_ticks(timeout, poll):
        if env.boot_completed():
            return


def _await_ready(
    driver: base.Driver,
    timeout: float = 10.0,
    poll_init: float = 0.1,
    poll_max: float = 0.5,
    *,
    ready_sel: base.Selector | None = None,
    id_namespaces: list[str] | None = None,
    transitions: TransitionSource = _no_transitions,
) -> ReadinessResult:
    """Poll until the launched app has rendered its first screen.

    Readiness is decided by the strongest signal available, in order:

    - `transitions` (BE-0310): an app linking `BajutsuKit`'s screen-transition observer that has
      reported a `UIAccessibility.screenChangedNotification` since this wait started — a positive
      signal from the transition itself, strongest because it needs no heuristic. A target that
      doesn't link the SDK (or whose cold-launch first screen posts no notification — an open
      empirical question this rung doesn't assume either way) reports none, and falls through
      unchanged to the rungs below.
    - `ready_sel` (a target's `readyWhen`): wait for that element to appear — the signal for an app
      whose first interactive screen is a modal over always-present chrome, where a count heuristic
      would return before the modal presents.
    - `id_namespaces` (a target's `idNamespaces`): wait for any element whose id belongs to a declared
      namespace. On a slow cold boot the device query can return SpringBoard (the Home screen's app
      icons) before the app foregrounds — 2+ *off-namespace* elements that a bare count would wrongly
      accept, letting the first scenario step race the real launch and time out. Requiring an
      in-namespace element proves the app itself is on screen.
    - neither: fall back to "more than the app root element" (any 2+ elements).

    Uses exponential backoff via `base.deadline_ticks`: the first poll is short (the app is often
    ready quickly) and subsequent intervals double up to `poll_max`, reducing wasted subprocess calls
    when the app takes longer to start.

    Returns:
        Whether the app became ready, which signal decided it (`screenChanged` / `readyWhen` /
        `namespace` / `count`, or `timeout` when the deadline passed first), and the elapsed time —
        recorded on a first-wait timeout so the failure is diagnosable from artifacts (BE-0231).
        Callers that only need the side effect (the relaunch paths) may ignore the return.
    """
    start = time.monotonic()
    # Use the selector only when it has a per-element condition; otherwise (None, empty, or
    # positional-only like `index`) fall back to the namespace/count heuristics — an all-matching
    # selector would return on a single element, weaker than "in-namespace" or "2+".
    match_sel = ready_sel if ready_sel and any(k in ready_sel for k in _READY_MATCH_KEYS) else None
    declared = set(id_namespaces or ())
    signal: Literal["readyWhen", "namespace", "count"] = (
        "readyWhen" if match_sel is not None else "namespace" if declared else "count"
    )
    for _ in base.deadline_ticks(timeout, poll_init, poll_max):
        # Only a transition received since this wait started counts — one left over from a prior
        # launch (a mid-scenario relaunch reuses the same collector) must not satisfy readiness for
        # a launch it predates. A collector only ever appends in receive order, so the most recent
        # transition is always its last element: if that one doesn't clear `start`, none do.
        reported = transitions()
        if reported and reported[-1][1] >= start:
            # Diagnostic only (BE-0310 Unit 5): confirms the signal actually decided readiness on a
            # real device, so on-device verification needs no extra instrumentation to observe it.
            _logger.debug("readiness satisfied by the screenChanged signal")
            return ReadinessResult(True, "screenChanged", time.monotonic() - start)
        try:
            elements = driver.query()
            if match_sel is not None:
                ready = len(base.find_all(elements, match_sel)) >= 1
            elif declared:
                ready = any(
                    el["identifier"] is not None and namespace_of(el["identifier"]) in declared
                    for el in elements
                )
            else:
                ready = len(elements) >= 2
            if ready:
                return ReadinessResult(True, signal, time.monotonic() - start)
        except (OSError, subprocess.CalledProcessError, ValueError):
            # The app is still coming up: a query before the UI exists can fail (no device
            # yet / empty tree / CLI hiccup). These are expected transient startup errors —
            # swallow them and keep polling until the deadline.
            pass
    return ReadinessResult(False, "timeout", time.monotonic() - start)

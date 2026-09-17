"""The handler seams a step loop calls out through, and their no-op defaults."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.common.drivers import base
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario import Relaunch

from .alert_event import AlertEvent

# Returns the network exchanges observed so far (for `request` assertions / waits).
NetworkSource = Callable[[], list[NetworkExchange]]
# Performs an in-scenario app relaunch (terminate + launch). Injected by the runner so the
# orchestrator stays backend-agnostic; None means relaunch is unavailable (e.g. fake driver).
RelaunchFn = Callable[[Relaunch], None]
# Receives a human-readable progress line (e.g. "step 2/5: tap home.title") as the run advances.
# Injected from the CLI (`--progress`) so the web UI can stream per-scenario/step progress; None
# (the default everywhere) keeps the pipeline silent.
ProgressFn = Callable[[str], None]
# Reads the wall clock (epoch seconds), stamped once per scenario so every recorded timestamp is an
# absolute instant that still means something after the process exits (BE-0348). Deliberately not a
# method on `Clock`: a wall clock can jump backward on an NTP correction, so nothing that decides
# whether a wait timed out may read it, and keeping it a separate injected callable also spares every
# clock double in the suite a method none of them need. Injectable so a test can hold it fixed.
WallClock = Callable[[], float]


# on_blocked(driver) -> the AlertEvent it dismissed if it cleared a blocking condition
# (e.g. a system alert), so the step/expect is worth retrying; else None. `record` / `crawl` bind
# this to the vision guard's `SystemAlertGuard.dismiss`. `run` calls `AlertGuardConfig` (below)
# directly instead, not through this alias: since BE-0418 its own call clears more than one alert
# per call, a shape this single-event contract cannot report.
BlockedHandler = Callable[[base.Driver], "AlertEvent | None"]

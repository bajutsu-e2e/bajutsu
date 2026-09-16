"""The seam for letting a fresh app launch re-arm a backend's app-crash poll bound (BE-0424)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AppCrashPollResettable(Protocol):
    """A backend whose bounded app-crash confirmation poll can be reset for a fresh launch.

    `AdbDriver.app_crash_signal()` polls Android's `ApplicationExitInfo` history only up to a bound,
    the first time it cannot confirm a crash in one scenario — every later probe in that same
    scenario reads the history once and answers immediately, since it has nothing new to wait out.
    `run` gets this reset for free (a fresh driver per lease), but `crawl` builds one driver for its
    entire walk, so a lifecycle path that relaunches the app outside the driver's own actuators —
    `AndroidEnvironment.relauncher()`, `crawl_reset()` — calls `reset_exit_info_poll()` to let the
    next probe pay the full bound again. The same narrow-opt-in shape `SettledCacheInvalidator`
    already establishes for the same reason: not implementing this means "no such bound to reset",
    which keeps every other backend (`FakeDriver`, Playwright, XCUITest) exactly as before.
    """

    def reset_exit_info_poll(self) -> None: ...

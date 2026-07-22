"""A short-lived iOS read session outside the runner-reuse pool (BE-0290).

idb read the accessibility tree with no resident runner (BE-0019), so `doctor`'s screen scoring and
`serve`'s live capture/enrich could bring an iOS driver up with no lifecycle to manage. With idb
retired (BE-0290), XCUITest is the sole iOS backend and needs an `xcodebuild test-without-building`
runner — and both surfaces run outside the runner-reuse pool (BE-0291), so neither can lean on that
amortization. This brings a runner up cold and hands the caller the environment to tear down when the
read is done: a one-shot `with` for `doctor`, a session-held teardown for `serve`. The runner launches
the app via `XCUIApplication`, so the read reflects a fresh launch of the target's bundle id.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from bajutsu import simctl
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.platform_lifecycle.environments.xcuitest_live import is_webdriver_endpoint
from bajutsu.platform_lifecycle.factories import environment_for
from bajutsu.platform_lifecycle.protocols import RunEnvironment
from bajutsu.scenario import Preconditions


def open_ios_read_driver(
    udid: str, eff: Effective, env_run: simctl.RunFn = simctl._real_run
) -> tuple[base.Driver, RunEnvironment]:
    """Start a short-lived XCUITest runner; return its driver and the environment to tear down.

    The caller owns teardown — `env.teardown(driver, eff)` — so the runner subprocess never leaks.
    Prefer `ios_read_session` for a one-shot read; use this directly when the driver must outlive the
    call (a live `serve` session that tears down on close).

    Args:
        udid: The device handle; `booted` (or any simctl handle) is resolved to the concrete udid,
            while a WebDriver endpoint URL is passed through to the live environment unchanged.
    """
    # A WebDriver endpoint is routed live (a running session, not a runner) and must not be
    # simctl-resolved; every other handle resolves through simctl to a concrete Simulator udid.
    resolved = udid if is_webdriver_endpoint(udid) else simctl.resolve_udid(udid, env_run)
    env = environment_for("xcuitest", resolved, env_run)
    driver = env.start(eff, Preconditions())
    return driver, env


@contextmanager
def ios_read_session(
    udid: str, eff: Effective, env_run: simctl.RunFn = simctl._real_run
) -> Iterator[base.Driver]:
    """A one-shot XCUITest read: bring the runner up, yield the driver, always tear it down."""
    driver, env = open_ios_read_driver(udid, eff, env_run)
    try:
        yield driver
    finally:
        env.teardown(driver, eff)

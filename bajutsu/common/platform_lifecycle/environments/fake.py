"""The test/headless backend: no device lifecycle, just the fake driver; otherwise device-style."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bajutsu.common import backends
from bajutsu.common.config import Effective
from bajutsu.common.config.preconditions import ResolvedPreconditions
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator import DeviceControl, RelaunchFn
from bajutsu.common.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.common.scenario import Relaunch, Scenario
from bajutsu.crawl import Reset


class FakeEnvironment(_DeviceEnvironment):
    """The test/headless backend: no device lifecycle, just the fake driver; otherwise device-style."""

    def resolve_device(self, udid: str) -> str:
        # The fake driver ignores the udid, so probing `simctl` for a booted device here would only
        # tie a device-free run to the host's Simulator state (issue #2158).
        return udid

    def captures_video(self) -> bool:
        return False  # no device to record: the fake backend has nothing to capture

    def start(
        self,
        eff: Effective,  # noqa: ARG002  # Environment shape
        pre: ResolvedPreconditions,  # noqa: ARG002
        *,
        extra_env: Mapping[str, str] | None = None,  # noqa: ARG002
        record_video_dir: Path | None = None,  # noqa: ARG002
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        # No device, so no mechanism to apply `permissions`. Preflight normally rejects a scenario
        # naming one before this is ever reached, but preflight is skippable (a lease driven
        # directly, `capabilities=None` in runner/pipeline.py) — so this is the runtime backstop,
        # the same shape gestures.py's `_require_multi_touch` is for an unsupported gesture.
        if permissions:
            raise base.UnsupportedAction("permissions is not supported on the fake driver")
        return backends.make_driver(self._actuator, self._udid)

    # The device-style base reaches a Simulator through simctl and narrows the target to iOS; the
    # fake backend has neither, so it must answer these on any platform's target (issue #2208).

    def relauncher(
        self,
        eff: Effective,  # noqa: ARG002  # Environment shape
        scenario: Scenario,  # noqa: ARG002
        driver: base.Driver,  # noqa: ARG002
        *,
        extra_env: Mapping[str, str] | None = None,  # noqa: ARG002
    ) -> RelaunchFn:
        # Every lease builds this, so building it must succeed; only a scenario that actually
        # relaunches is refused, rather than silently no-op'ing (determinism first).
        def relaunch(opts: Relaunch) -> None:  # noqa: ARG001  # RelaunchFn shape; refuses instead
            raise base.UnsupportedAction("relaunch is not supported on the fake driver")

        return relaunch

    def controller(self, eff: Effective) -> DeviceControl | None:  # noqa: ARG002  # Environment shape
        return None  # no device to control; the runner reads None as "no device control"

    def teardown(self, driver: base.Driver, eff: Effective) -> None:  # noqa: ARG002  # Environment shape
        return None  # nothing was launched on a device, so there is nothing to terminate

    def crawl_reset(self, eff: Effective) -> Reset:  # noqa: ARG002  # Environment shape
        def reset(driver: base.Driver) -> None:  # noqa: ARG001  # Reset shape; refuses instead
            raise base.UnsupportedAction("crawl reset is not supported on the fake driver")

        return reset

    def start_member(
        self,
        eff: Effective,
        pre: ResolvedPreconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        permissions: Mapping[str, str] | None = None,
        install: bool = True,  # noqa: ARG002  # nothing is ever installed on the fake backend
    ) -> base.Driver:
        # No device to share, so a member comes up exactly as the group's first one did; the fake
        # backend advertises `Capability.DEVICE_GROUP` so the runner's group path runs end to end.
        return self.start(eff, pre, extra_env=extra_env, permissions=permissions)

    def install_member(self, eff: Effective, *, keep_data: bool) -> None:  # noqa: ARG002  # Environment shape
        return None  # no device, so nothing to install; the step's lifecycle bookkeeping still runs

    def end_member(self, driver: base.Driver, eff: Effective) -> None:  # noqa: ARG002  # Environment shape
        # Nothing was launched on a device, so there is nothing to stop.
        return None

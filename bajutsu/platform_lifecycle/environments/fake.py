"""The test/headless backend: no device lifecycle, just the fake driver; otherwise device-style."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bajutsu import backends
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.scenario import Preconditions


class FakeEnvironment(_DeviceEnvironment):
    """The test/headless backend: no device lifecycle, just the fake driver; otherwise device-style."""

    def captures_video(self) -> bool:
        return False  # no device to record: the fake backend has nothing to capture

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        # No device, so no mechanism to apply `permissions`. Preflight normally rejects a scenario
        # naming one before this is ever reached, but preflight is skippable (a lease driven
        # directly, `capabilities=None` in runner/pipeline.py) — so this is the runtime backstop,
        # the same shape gestures.py's `_require_multi_touch` is for an unsupported gesture.
        if permissions:
            raise base.UnsupportedAction("permissions is not supported on the fake driver")
        return backends.make_driver(self._actuator, self._udid)

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
    ) -> base.Driver:
        return backends.make_driver(self._actuator, self._udid)

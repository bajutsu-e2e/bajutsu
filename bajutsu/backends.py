"""Backend selection and driver construction.

The backend list is ordered most-stable-first; the actuator is the first one that
is available in this environment (e.g. RocketSim needs a GUI, idb is headless).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from bajutsu.drivers import base
from bajutsu.drivers.idb import IdbDriver
from bajutsu.drivers.rocketsim import RocketSimDriver
from bajutsu.idmap import IdMap

KNOWN = ("rocketsim", "idb")

# Which executable backs each backend (used by the default availability check).
_EXECUTABLE = {"rocketsim": "rocketsim", "idb": "idb"}


def default_available(backend: str) -> bool:
    """Available if the backend's executable is on PATH (a coarse first check)."""
    exe = _EXECUTABLE.get(backend)
    return exe is not None and shutil.which(exe) is not None


def select_actuator(backends: list[str], available: Callable[[str], bool] = default_available) -> str:
    """First available backend in stability order."""
    for b in backends:
        if b in KNOWN and available(b):
            return b
    raise RuntimeError(f"no available actuator among {backends}")


def make_driver(backend: str, udid: str, idmap: IdMap | None = None) -> base.Driver:
    if backend == "rocketsim":
        # rocketsim has no accessibilityIdentifier in its protocol; the idmap
        # recovers them. idb gets identifiers natively, so it ignores the idmap.
        return RocketSimDriver(udid, idmap=idmap)
    if backend == "idb":
        return IdbDriver(udid)
    raise ValueError(f"unknown backend: {backend!r}")

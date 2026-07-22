"""Per-platform app lifecycle behind one Protocol (BE-0009 Phase 0), split into a package (BE-0256).

The `Environment` seam and its "not applicable" contract live in `protocols.py`; the two readiness
waits in `readiness.py`; the `DeviceControl` factories in `device_control.py`; the `relaunch`-step
factories in `relaunchers.py`; one concrete implementer per module under `environments/`; and the
`environment_for` factory in `factories.py`. This package root re-exports the public names, so every
existing `from bajutsu.platform_lifecycle import …` / `from bajutsu import platform_lifecycle` import
keeps working unchanged.
"""

from __future__ import annotations

from bajutsu.platform_lifecycle.device_control import android_device_control, device_control
from bajutsu.platform_lifecycle.environments.android import AndroidEnvironment
from bajutsu.platform_lifecycle.environments.fake import FakeEnvironment
from bajutsu.platform_lifecycle.environments.web import WebEnvironment
from bajutsu.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.platform_lifecycle.factories import environment_for
from bajutsu.platform_lifecycle.protocols import (
    CrawlEnvironment,
    Environment,
    ProvisionProfile,
    ReadinessResult,
    RunEnvironment,
)

# Only `_await_ready` still needs the flat re-export path: `runner/launch.py` reaches it via
# `from bajutsu.platform_lifecycle import _await_ready`. The other private names (`_DeviceEnvironment`,
# `_await_boot`, `_web_relauncher`) are reached only through their submodules, so they stay there.
from bajutsu.platform_lifecycle.readiness import _await_ready
from bajutsu.platform_lifecycle.relaunchers import device_relauncher

__all__ = [
    "AndroidEnvironment",
    "CrawlEnvironment",
    "Environment",
    "FakeEnvironment",
    "ProvisionProfile",
    "ReadinessResult",
    "RunEnvironment",
    "WebEnvironment",
    "XcuitestEnvironment",
    "_await_ready",
    "android_device_control",
    "device_control",
    "device_relauncher",
    "environment_for",
]

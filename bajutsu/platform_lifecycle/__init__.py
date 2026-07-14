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
from bajutsu.platform_lifecycle.environments.ios import IosEnvironment, _DeviceEnvironment
from bajutsu.platform_lifecycle.environments.web import WebEnvironment
from bajutsu.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.platform_lifecycle.factories import environment_for
from bajutsu.platform_lifecycle.protocols import (
    CrawlEnvironment,
    Environment,
    ReadinessResult,
    RunEnvironment,
)
from bajutsu.platform_lifecycle.readiness import _await_boot, _await_ready
from bajutsu.platform_lifecycle.relaunchers import _web_relauncher, device_relauncher

__all__ = [
    "AndroidEnvironment",
    "CrawlEnvironment",
    "Environment",
    "FakeEnvironment",
    "IosEnvironment",
    "ReadinessResult",
    "RunEnvironment",
    "WebEnvironment",
    "XcuitestEnvironment",
    "_DeviceEnvironment",
    "_await_boot",
    "_await_ready",
    "_web_relauncher",
    "android_device_control",
    "device_control",
    "device_relauncher",
    "environment_for",
]

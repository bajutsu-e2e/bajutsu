"""Shared types for the run pipeline: the per-scenario device Lease and the injected callables.

No run logic here, so launch / pool / pipeline can all import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import EvidenceSink
from bajutsu.evidence.network import Collector
from bajutsu.orchestrator import AlertGuardConfig, DeviceControl, RelaunchFn, SkippedCapture
from bajutsu.scenario import Scenario
from bajutsu.webview import DomSource

# Builds the in-scenario relaunch function for a scenario (given its live driver).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]

# Selects the alert-guard config for one scenario (None = no guard for it). The CLI sets this so
# each scenario's `dismissAlerts` (default on, optional button policy / poll interval) decides
# whether — and how — the guard runs; the orchestrator stays oblivious to the per-scenario choice.
AlertGuardFor = Callable[[Scenario], AlertGuardConfig | None]


@dataclass
class Lease:
    """A leased device for one scenario run.

    It bundles the live driver with the per-device resources bound to that device. `release()`
    terminates the app and returns the device to the pool.

    `collector` is None when network collection is off; otherwise it observes the leased
    device's traffic (iOS: the app POSTs to an HTTP receiver; web: Playwright events),
    cleared per scenario by the run loop.
    """

    driver: base.Driver
    sink: EvidenceSink
    relaunch: RelaunchFn | None
    control: DeviceControl | None
    collector: Collector | None
    release: Callable[[], None]
    udid: str = ""  # the leased device, recorded on each RunResult (parallel-split attribution)
    # The leased device's model / OS runtime, recorded on the result for the report's
    # Environment tab (empty when the simulator catalog couldn't be read).
    device_name: str = ""
    device_runtime: str = ""
    # Provenance for the network artifact: "collector" (the actuator's own app-side receiver) or
    # "<backend> (fallback)" when a same-platform read-only provider supplied it (BE-0020).
    collector_provider: str = "collector"
    # Evidence kinds no eligible backend could supply, disclosed per scenario (BE-0020).
    skipped_captures: list[SkippedCapture] = field(default_factory=list)
    # WebView bridge for hybrid apps (BE-0037). None when the app has no WebView or the platform
    # doesn't support the bridge (e.g. web/Playwright — it already has DOM access).
    webview_bridge: DomSource | None = None


# Leases a free device for one scenario (blocking until one frees up): launches the app
# and returns the Lease the run loop drives, then release()s.
LeaseFn = Callable[[Effective, Scenario], Lease]

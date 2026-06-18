"""Shared types for the run pipeline: the per-scenario device Lease and the injected callables.

No run logic here, so launch / pool / pipeline can all import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import EvidenceSink
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import BlockedHandler, DeviceControl, RelaunchFn
from bajutsu.scenario import Scenario

# Builds the in-scenario relaunch function for a scenario (given its live driver).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]

# Selects the alert-guard handler for one scenario (None = no guard for it). The CLI sets this
# so each scenario's `dismissAlerts` (default on, optional instruction) decides whether — and
# how — the vision guard runs; the orchestrator stays oblivious to the per-scenario choice.
OnBlockedFor = Callable[[Scenario], "BlockedHandler | None"]


@dataclass
class Lease:
    """A leased device for one scenario run: its live driver plus the per-device
    resources bound to that device. `release()` terminates the app and returns the
    device to the pool.

    `collector` is None when network collection is off; otherwise it is the device's
    own receiver (the app POSTs to it), cleared per scenario by the run loop.
    """

    driver: base.Driver
    sink: EvidenceSink
    relaunch: RelaunchFn | None
    control: DeviceControl | None
    collector: NetworkCollector | None
    release: Callable[[], None]
    udid: str = ""  # the leased device, recorded on each RunResult (parallel-split attribution)
    # The leased device's model / OS runtime, recorded on the result for the report's
    # Environment tab (empty when the simulator catalog couldn't be read).
    device_name: str = ""
    device_runtime: str = ""


# Leases a free device for one scenario (blocking until one frees up): launches the app
# and returns the Lease the run loop drives, then release()s.
LeaseFn = Callable[[Effective, Scenario], Lease]


def _no_net() -> list[NetworkExchange]:
    return []

"""Run pipeline — execute scenarios through a device pool and write the report.

The pool leases a device per scenario, bundling the live driver with that device's per-device
resources (evidence sink, relaunch, device control, network collector); a single-device run is
a pool of one. Split by concern (BE-0043): `types` (the Lease), `launch` (bring a device up),
`pool` (lease devices + per-device control/relaunch), and `pipeline` (run + report). The public
API is re-exported here, so `from bajutsu.runner import ...` is unchanged.
"""

from __future__ import annotations

from bajutsu.runner.launch import ReadinessResult, _await_ready, launch_driver
from bajutsu.runner.pipeline import run_all, run_and_report, run_matrix_and_report
from bajutsu.runner.pool import device_control, device_pool, device_relauncher
from bajutsu.runner.types import AlertGuardFor, Lease, LeaseFn, RelaunchFactory

__all__ = [
    "AlertGuardFor",
    "Lease",
    "LeaseFn",
    "ReadinessResult",
    "RelaunchFactory",
    "_await_ready",
    "device_control",
    "device_pool",
    "device_relauncher",
    "launch_driver",
    "run_all",
    "run_and_report",
    "run_matrix_and_report",
]

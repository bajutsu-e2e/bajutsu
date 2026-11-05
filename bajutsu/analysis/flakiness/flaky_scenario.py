from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.devices.os import DeviceOS


@dataclass(frozen=True)
class FlakyScenario:
    """One scenario's cross-run stability at a fixed fingerprint, on one OS — the ranked unit."""

    scenario_hash: str  # the runs' shared `provenance.scenarioHash`, half the grouping key
    name: str  # a representative scenario name from the runs' summaries (for display / linking)
    # The OS these runs ran on, parsed from the record's `device_runtime` (BE-0358) — the other half
    # of the key. None when the run recorded none, or when its scenarios spanned OS versions.
    device_os: DeviceOS | None
    runs: int  # runs observed at this fingerprint inside the window
    passed: int  # runs that passed
    failed: int  # runs that failed
    flip_rate: float  # 2 * min(passed, failed) / runs — 0 when consistent, 1 at a 50/50 split
    classification: str  # flaky | deterministic | unproven (see `audit.classify_stability`)
    representative_pass_run_id: str | None  # newest passing run, for linking to its evidence
    representative_fail_run_id: str | None  # newest failing run, for linking to its evidence
    # The target and the run's `runsOn` coordinates (BE-0452), the rest of the key: a failure on one
    # model never reads as flakiness of the scenario. None for a run that named no target. The
    # coordinates are empty for a run no condition formed (every run recorded before them included),
    # and `*` for a run whose results ran on two or more sets of coordinates.
    target: str | None = None
    coordinates: str = ""

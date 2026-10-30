"""The target's resolved run-behavior defaults (BE-0177, BE-0252)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.scenario import CleanupRule, Interrupt, Step, SystemAlertHandlingField


@dataclass(frozen=True)
class RunDefaults:
    """Per-app run-behavior defaults (BE-0177), grouped out of `Effective` (BE-0252).

    The layer the run consults when neither a CLI flag nor the scenario sets the value.
    `system_alert_handling` takes the on-disk shapes a scenario's own field does (`False`, a
    policy, or None = built-in on with the default dismissive labels); `network` is the concrete
    built-in default when unset.
    """

    system_alert_handling: SystemAlertHandlingField = None
    network: bool = True
    ios_tip_kit_handling: bool = False
    # App-wide interstitial-screen handlers (BE-0314), prepended to a scenario's own `interrupts`.
    # Empty when the target config declares none.
    interrupts: list[Interrupt] = field(default_factory=list)
    # App-wide setup/cleanup phases (BE-0392), from `hooks.setup` / `hooks.cleanup`. `setup` is
    # prepended to a scenario's own, `cleanup` appended after it — the merge orders `pipeline.py`
    # applies at the `run_scenario` call site. Empty when the target config declares neither.
    setup: list[Step] = field(default_factory=list)
    cleanup: list[CleanupRule] = field(default_factory=list)
    # The target's `run.ignoredSettings` (BE-0467): setting key -> the reason it runs without it.
    ignored_settings: dict[str, str] = field(default_factory=dict)

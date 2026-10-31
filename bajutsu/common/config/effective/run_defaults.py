"""The target's resolved run-behavior defaults (BE-0177, BE-0252)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.scenario import CleanupRule, Interrupt, Step, SystemAlertInterrupt


@dataclass(frozen=True)
class RunDefaults:
    """Per-app run-behavior defaults (BE-0177), grouped out of `Effective` (BE-0252).

    The layer the run consults when neither a CLI flag nor the scenario sets the value. The target's
    `hooks.interrupts` is one list of three forms (BE-0473); it is split here by form, because each
    form layers with the scenario's own entries by its own rule.
    """

    # The target's `systemAlert` entries, in order: the prompts the reactive guard answers when the
    # scenario declares nothing for them. Empty leaves the guard on with no answers.
    system_alerts: list[SystemAlertInterrupt] = field(default_factory=list)
    network: bool = True
    # Whether the target's `interrupts` carries `- tipKit: dismiss`.
    ios_tip_kit_handling: bool = False
    # App-wide interstitial-screen handlers (BE-0314), the screen entries of `hooks.interrupts`,
    # prepended to a scenario's own. Empty when the target config declares none.
    interrupts: list[Interrupt] = field(default_factory=list)
    # App-wide setup/cleanup phases (BE-0392), from `hooks.setup` / `hooks.cleanup`. `setup` is
    # prepended to a scenario's own, `cleanup` appended after it — the merge orders `pipeline.py`
    # applies at the `run_scenario` call site. Empty when the target config declares neither.
    setup: list[Step] = field(default_factory=list)
    cleanup: list[CleanupRule] = field(default_factory=list)
    # The target's `runsOn.ignoredSettings` (BE-0467): setting key -> the reason it runs without it.
    ignored_settings: dict[str, str] = field(default_factory=dict)

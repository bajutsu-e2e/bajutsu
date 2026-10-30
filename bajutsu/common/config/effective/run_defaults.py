"""The target's resolved run-behavior defaults (BE-0177, BE-0252)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.scenario import CleanupRule, InterruptEntry, Step


@dataclass(frozen=True)
class RunDefaults:
    """Per-app run-behavior defaults (BE-0177), grouped out of `Effective` (BE-0252).

    The layer the run consults when neither a CLI flag nor the scenario sets the value. `network` is
    the concrete built-in default when unset.
    """

    network: bool = True
    # The target's `hooks.interrupts` (BE-0314, BE-0473), every form: its screen entries are
    # prepended to a scenario's own, and its `systemAlert` and `tipKit` entries are the answers a
    # scenario's entry for the same prompt overrides. Empty when the target config declares none.
    interrupts: list[InterruptEntry] = field(default_factory=list)
    # App-wide setup/cleanup phases (BE-0392), from `hooks.setup` / `hooks.cleanup`. `setup` is
    # prepended to a scenario's own, `cleanup` appended after it — the merge orders `pipeline.py`
    # applies at the `run_scenario` call site. Empty when the target config declares neither.
    setup: list[Step] = field(default_factory=list)
    cleanup: list[CleanupRule] = field(default_factory=list)
    # The target's `run.ignoredSettings` (BE-0467): setting key -> the reason it runs without it.
    ignored_settings: dict[str, str] = field(default_factory=dict)

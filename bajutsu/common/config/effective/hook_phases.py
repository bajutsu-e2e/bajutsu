"""The target's resolved external-command hooks — the four lists and their sandbox (BE-0491)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from bajutsu.common.config.schema.hook_command import HookCommand, HookSandbox
from bajutsu.common.config.schema.hooks import COMMAND_LISTS

# The two lists a run meets once, around every scenario, and the two it meets per scenario.
RUN_LISTS: tuple[str, ...] = ("beforeRun", "afterRun")
SCENARIO_LISTS: tuple[str, ...] = ("beforeScenario", "afterScenario")


@dataclass(frozen=True)
class HookPhases:
    """One target's `hooks.beforeRun` / `afterRun` / `beforeScenario` / `afterScenario` commands.

    Grouped out of `Effective` the way `RunDefaults` groups the step-shaped hooks, so a run path can
    ask one object whether this target runs any command at all. Empty on a target that declares none,
    which is every target that did not declare `services.server` before BE-0491 removed it.
    """

    before_run: list[HookCommand] = field(default_factory=list)
    after_run: list[HookCommand] = field(default_factory=list)
    before_scenario: list[HookCommand] = field(default_factory=list)
    after_scenario: list[HookCommand] = field(default_factory=list)
    sandbox: HookSandbox | None = None

    @property
    def lists(self) -> dict[str, list[HookCommand]]:
        """The four lists keyed by the name a report block and a log path use.

        Keyed off `COMMAND_LISTS` rather than restating the names, so the lifecycle order lives in
        one place and a fifth list cannot be added here and forgotten there.
        """
        by_name = {
            "beforeRun": self.before_run,
            "beforeScenario": self.before_scenario,
            "afterScenario": self.after_scenario,
            "afterRun": self.after_run,
        }
        return {name: by_name[name] for name in COMMAND_LISTS}

    @property
    def any_command(self) -> bool:
        """Whether this target declares any hook command at all."""
        return any(self.lists.values())

    def entries(self) -> Iterator[tuple[str, int, HookCommand]]:
        """Every declared entry as `(list name, index in that list, entry)`.

        The policy check walks this rather than one list at a time: a command the worker refuses
        fails the run before any device work, whether or not its `on` will match the verdict.
        """
        for list_name, entries in self.lists.items():
            for index, entry in enumerate(entries):
                yield list_name, index, entry

    def named(self) -> dict[str, HookCommand]:
        """The entries that carry a `name`, keyed by it — the ones a token can read."""
        return {e.name: e for _, _, e in self.entries() if e.name is not None}

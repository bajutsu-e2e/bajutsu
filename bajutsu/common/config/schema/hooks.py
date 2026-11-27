"""The steps that wrap every scenario of a target, as written under `hooks:` (BE-0452)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

from pydantic import Field, ValidationInfo, model_validator

from bajutsu.common.scenario import (
    CleanupRule,
    Interrupt,
    InterruptEntry,
    Step,
    check_interrupt_entries,
    screen_entries,
)

from ._model import _Model
from .hook_command import (
    HookCommand,
    HookSandbox,
    HookWhen,
    reject_outcome_on_before,
    reject_run_level_exclude_only,
)

# The four external-command lists (BE-0491), in the order one run meets them.
COMMAND_LISTS: tuple[str, ...] = ("beforeRun", "beforeScenario", "afterScenario", "afterRun")

# Where a step nests further steps, as (model attribute path, YAML key path). `_nested_steps` walks
# the models by the first and a template file's `use` rewrite (BE-0471) walks raw YAML by the second,
# so a container added here reaches both. A target group (BE-0437) is never flattened in a config, so
# its own `steps` count too.
STEP_CONTAINERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("steps",), ("steps",)),
    (("if_", "then"), ("if", "then")),
    (("if_", "else_"), ("if", "else")),
    (("for_each", "steps"), ("forEach", "steps")),
    (("web", "steps"), ("web", "steps")),
    (("app", "steps"), ("app", "steps")),
)


def _nested_steps(steps: list[Step]) -> Iterator[Step]:
    for step in steps:
        yield step
        for attrs, _ in STEP_CONTAINERS:
            nested: Any = step
            for attr in attrs:
                nested = getattr(nested, attr, None)
            yield from _nested_steps(nested or [])


class Hooks(_Model):
    """`hooks`: the steps that wrap every scenario of the target.

    `setup` runs ahead of a scenario's own setup and `cleanup` behind its own, so a scenario releases
    what it created before the app-wide teardown closes around it; `interrupts` lists every handler
    the target applies on its own, layered under the scenario's (BE-0473). The names match the scenario's own `setup` and `cleanup`, so one pair of words
    names the phases at both levels.

    A step may call a component with `use:`, resolved when the config loads against the config
    file's directory and kept inside the suite root (BE-0174); `with` values substitute then, and
    `${secrets.*}` and `${vars.*}` still resolve when the step runs.
    """

    setup: list[Step] = Field(default_factory=list)
    cleanup: list[CleanupRule] = Field(default_factory=list)
    # The three forms a scenario's own `interrupts` takes (BE-0473): a screen, an OS prompt for the
    # reactive guard, and Apple's TipKit popover.
    interrupts: list[InterruptEntry] = Field(default_factory=list)
    # The four external-command lists (BE-0491). Unlike `setup` / `cleanup`, which hold scenario
    # steps, these hold commands the machine running the run executes: data a scenario needs seeded,
    # a file placed, or the app's own server brought up — the bring-up `services.server` once did.
    before_run: list[HookCommand] = Field(default_factory=list, alias="beforeRun")
    after_run: list[HookCommand] = Field(default_factory=list, alias="afterRun")
    before_scenario: list[HookCommand] = Field(default_factory=list, alias="beforeScenario")
    after_scenario: list[HookCommand] = Field(default_factory=list, alias="afterScenario")
    # How the container those commands run in is built, for a worker whose policy sandboxes them.
    sandbox: HookSandbox | None = None

    @property
    def command_lists(self) -> dict[str, list[HookCommand]]:
        """The four external-command lists keyed by the name a report and a log path use."""
        return {
            "beforeRun": self.before_run,
            "beforeScenario": self.before_scenario,
            "afterScenario": self.after_scenario,
            "afterRun": self.after_run,
        }

    @model_validator(mode="after")
    def _check_command_lists(self) -> Self:
        # A `before` entry has no verdict to answer, and a name shared by two entries of one target
        # would make `${hooks.<name>.<key>}` ambiguous. Uniqueness *across* targets is checked where
        # the run knows which targets it drives, since one target cannot see another's config here.
        reject_outcome_on_before(self.before_run, "hooks.beforeRun")
        reject_outcome_on_before(self.before_scenario, "hooks.beforeScenario")
        reject_run_level_exclude_only(self.before_run, "hooks.beforeRun")
        reject_run_level_exclude_only(self.after_run, "hooks.afterRun")
        seen: dict[str, str] = {}
        for list_name, entries in self.command_lists.items():
            for entry in entries:
                if entry.name is None:
                    continue
                if (first := seen.get(entry.name)) is not None:
                    raise ValueError(
                        f"hooks.{list_name}: two entries are named {entry.name!r} (also in "
                        f"hooks.{first}); a hook name must be unique so ${{hooks.{entry.name}.*}} "
                        "names one command"
                    )
                seen[entry.name] = list_name
        return self

    def conditions(self) -> Iterator[tuple[str, HookWhen]]:
        """Every `when` this block declares, each with where it sits (BE-0497, BE-0513)."""
        for list_name, entries in self.command_lists.items():
            for i, entry in enumerate(entries):
                if entry.when is not None:
                    yield f"hooks.{list_name}[{i}].when", entry.when
        for i, step in enumerate(self.setup):
            if step.when is not None:
                yield f"hooks.setup[{i}].when", step.when
        for i, rule in enumerate(self.cleanup):
            if rule.when is not None:
                yield f"hooks.cleanup[{i}].when", rule.when

    @property
    def screens(self) -> list[Interrupt]:
        """The screen entries of `interrupts`, in order."""
        return screen_entries(self.interrupts)

    def _all_steps(self) -> list[Step]:
        return [
            *_nested_steps(self.setup),
            *(s for rule in self.cleanup for s in _nested_steps(rule.steps)),
            *(s for entry in self.screens for s in _nested_steps(entry.steps)),
        ]

    @model_validator(mode="after")
    def _resolve_components(self, info: ValidationInfo) -> Self:
        # `group:` folds steps under a report heading of one scenario, and `setMocks` arms the
        # control channel `run` sets up per scenario (BE-0365 unit 4); neither has a meaning for every
        # scenario of a target, so both fail here rather than reach the step loop unarmed.
        self._reject_scenario_only(self._all_steps(), "")
        if not any(s.use is not None for s in self._all_steps()):
            return self
        context = info.context if isinstance(info.context, dict) else {}
        config_path = context.get("config_path")
        if not isinstance(config_path, Path):
            raise ValueError(  # noqa: TRY004 - Pydantic turns only ValueError into a field error
                "hooks call a component (`use`), which resolves against the config file's "
                "directory, but this config was loaded without its path"
            )
        suite_root = context.get("suite_root")
        root = suite_root if isinstance(suite_root, Path) else config_path.parent
        # Imported here: the resolver reaches the scenario loader, which this schema module keeps
        # off its import path at load.
        from bajutsu.common.scenario import ComponentResolver, expand_steps

        resolver = ComponentResolver({}, root=root, base=config_path.parent, source=None)
        try:
            self.setup = expand_steps(self.setup, resolver)
            for rule in self.cleanup:
                rule.steps = expand_steps(rule.steps, resolver)
            for entry in self.screens:
                entry.steps = expand_steps(entry.steps, resolver)
        except OSError as e:
            raise ValueError(f"hooks: cannot read a component: {e}") from e
        self._reject_scenario_only(self._all_steps(), " (through a component)")
        return self

    @staticmethod
    def _reject_scenario_only(steps: list[Step], how: str) -> None:
        if any(s.group is not None or s.report_group is not None for s in steps):
            raise ValueError(
                f"hooks cannot use a group (`group`){how}: a group belongs to one scenario's report"
            )
        if any(s.set_mocks is not None for s in steps):
            raise ValueError(
                f"hooks cannot use setMocks{how}: a stub table belongs to one scenario, so "
                "replace it from that scenario's own steps"
            )

    @model_validator(mode="after")
    def _no_target_in_interrupts(self) -> Self:
        # An entry here already belongs to the target this block configures, so a `target` on it
        # could only repeat that name or contradict it (BE-0438). Its recovery steps are checked
        # too, nested ones included: a target config never passes through the scenario-side
        # `_check_target_requirements`, so an unchecked `steps[0].target: ios` under a web target
        # would load cleanly and then dispatch to the ios runner.
        check_interrupt_entries(self.interrupts, "hooks.interrupts")
        for entry in self.screens:
            if entry.target is not None:
                raise ValueError(
                    "hooks.interrupts entries cannot set `target`: the entry already belongs to "
                    "the target this block configures"
                )
            if any(s.target is not None for s in _nested_steps(entry.steps)):
                raise ValueError(
                    "a step under hooks.interrupts cannot set `target`: recovery steps run on the "
                    "target this block configures"
                )
        return self

    @model_validator(mode="after")
    def _blocks_at_setup_top(self) -> Self:
        # A conditional block (BE-0513) is decided once, before the scenario's first step, so it
        # may stand only where that decision is whole: the top level of `setup`. Run after the
        # component pass, so a block a component produced is held to the same rule.
        top = {id(step) for step in self.setup}
        for step in self._all_steps():
            if step.when is not None and id(step) not in top:
                raise ValueError(
                    "hooks: a conditional block (`when` with `steps`) belongs at the top level of "
                    "hooks.setup; it cannot sit inside a block, a target group, `cleanup`, an "
                    "`interrupts` entry, or an `if` / `forEach` step"
                )
        return self

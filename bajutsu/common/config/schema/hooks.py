"""The steps that wrap every scenario of a target, as written under `hooks:` (BE-0452)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Self

from pydantic import Field, ValidationInfo, model_validator

from bajutsu.common.scenario import AfterRule, Interrupt, Step

from ._model import _Model


def _nested_steps(steps: list[Step]) -> Iterator[Step]:
    for step in steps:
        yield step
        # A target group (BE-0437) is never flattened in a config, so its own steps count too.
        yield from _nested_steps(step.steps or [])
        if step.if_ is not None:
            yield from _nested_steps(step.if_.then)
            yield from _nested_steps(step.if_.else_ or [])
        if step.for_each is not None:
            yield from _nested_steps(step.for_each.steps)
        if step.web is not None:
            yield from _nested_steps(step.web.steps)
        if step.app is not None:
            yield from _nested_steps(step.app.steps)


class Hooks(_Model):
    """`hooks`: the steps that wrap every scenario of the target.

    `setup` runs ahead of a scenario's own setup and `cleanup` behind its own, so a scenario releases
    what it created before the app-wide teardown closes around it; `interrupts` are checked before
    the scenario's. Until BE-0452 unit 5 renames the phases, they run as a scenario's `before` and
    `after`, and a `cleanup` rule still takes `on: error`.

    A step may call a component with `use:`, resolved when the config loads against the config
    file's directory and kept inside the suite root (BE-0174); `with` values substitute then, and
    `${secrets.*}` and `${vars.*}` still resolve when the step runs.
    """

    setup: list[Step] = Field(default_factory=list)
    cleanup: list[AfterRule] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)

    def _all_steps(self) -> list[Step]:
        return [
            *_nested_steps(self.setup),
            *(s for rule in self.cleanup for s in _nested_steps(rule.steps)),
            *(s for entry in self.interrupts for s in _nested_steps(entry.steps)),
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
            for entry in self.interrupts:
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
        for entry in self.interrupts:
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

"""A `hooks` component: one preparation and its teardown in a file of their own (BE-0512).

A target applies one under `targets.<name>.hooks.use` and a scenario under its own `hooks.use`.
Both expand it while the files load, into the command lists and step phases that already exist, so
the run loop sees ordinary entries and steps: no model and no wait join the run (prime directives 1
and 2).
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.hook_command import (
    HookCommand,
    HookWhen,
    invalid_hook_name,
    reject_outcome_on_before,
    reject_run_level_exclude_only,
)
from bajutsu.common.scenario.models.steps import CleanupRule, Step

# A key the component cannot carry, and why.
_REFUSED = {
    "use": "a component cannot apply another `hooks` component",
    "interrupts": "dialog handlers are no pair of preparation and teardown; keep them in the "
    "target's or the scenario's own `interrupts`",
    "sandbox": "the container belongs to the target, in `targets.<name>.hooks.sandbox`",
}


class HookComponent(_Model):
    """The file a `hooks.use` entry names: its parameters, commands, and steps.

    `params` are the names a caller supplies through `with`, read as `${params.<name>}` in a
    command's `cmd`, the values of its `env`, and the steps.
    """

    params: list[str] = Field(default_factory=list)
    before_run: list[HookCommand] = Field(default_factory=list, alias="beforeRun")
    after_run: list[HookCommand] = Field(default_factory=list, alias="afterRun")
    before_scenario: list[HookCommand] = Field(default_factory=list, alias="beforeScenario")
    after_scenario: list[HookCommand] = Field(default_factory=list, alias="afterScenario")
    setup: list[Step] = Field(default_factory=list)
    cleanup: list[CleanupRule] = Field(default_factory=list)

    @property
    def command_lists(self) -> dict[str, list[HookCommand]]:
        """The four command lists keyed by the name the config writes them under."""
        return {
            "beforeRun": self.before_run,
            "beforeScenario": self.before_scenario,
            "afterScenario": self.after_scenario,
            "afterRun": self.after_run,
        }

    @model_validator(mode="before")
    @classmethod
    def _name_the_refused(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, why in _REFUSED.items():
                if key in data:
                    raise ValueError(f"a hooks component cannot hold `{key}`: {why}")
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not any(self.command_lists.values()) and not self.setup and not self.cleanup:
            raise ValueError(
                "a hooks component needs an entry in one of beforeRun, beforeScenario, setup, "
                "cleanup, afterScenario, or afterRun"
            )
        reject_outcome_on_before(self.before_run, "beforeRun")
        reject_outcome_on_before(self.before_scenario, "beforeScenario")
        reject_run_level_exclude_only(self.before_run, "beforeRun")
        reject_run_level_exclude_only(self.after_run, "afterRun")
        seen: set[str] = set()
        for list_name, entries in self.command_lists.items():
            for entry in entries:
                if entry.name is None:
                    continue
                if entry.name in seen:
                    raise ValueError(
                        f"{list_name}: two entries are named {entry.name!r}; a hook name must be "
                        "unique so one token names one command"
                    )
                seen.add(entry.name)
        if len(set(self.params)) != len(self.params):
            raise ValueError(f"`params` names a parameter twice: {self.params}")
        return self


class HookUse(_Model):
    """One entry of a `hooks.use` list: which component to apply, and how."""

    # The component file, resolved from the file that holds this entry.
    hook: str
    with_: dict[str, str] = Field(default_factory=dict, alias="with")
    # Tells two applications of one component apart: outside the component its names read as
    # `<as>.<name>`. With `hook`, it is also what identifies an application when the `use` lists of
    # `defaults`, templates, and the target join.
    as_: str | None = Field(default=None, alias="as")
    when: HookWhen | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.hook.strip():
            raise ValueError("a hooks.use entry's `hook` cannot be empty")
        if self.as_ is not None and (why := invalid_hook_name(self.as_)) is not None:
            raise ValueError(f"a hooks.use entry's `as` {why}")
        if "when" in self.model_fields_set:
            # TODO(BE-0512): unit 4 gives an application its own tag condition; until then a `when`
            # here would be read by nothing, and the component would run for every scenario.
            raise ValueError(
                "a hooks.use entry cannot set `when` yet; put the condition on the component's "
                "command entries instead"
            )
        return self

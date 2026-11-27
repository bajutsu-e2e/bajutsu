"""The external commands one scenario runs around itself, as written under its `hooks:` (BE-0501)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.hook_command import HookCommand, reject_outcome_on_before
from bajutsu.common.scenario.models.hook_component import HookUse

# A target's `hooks` key a scenario's block cannot carry, and where the author writes it instead.
_MISPLACED = {
    "beforeRun": "`targets.<name>.hooks`: a scenario is not a run",
    "afterRun": "`targets.<name>.hooks`: a scenario is not a run",
    "sandbox": "`targets.<name>.hooks`: the container belongs to the target",
    "setup": "the scenario's own top-level `setup`",
    "cleanup": "the scenario's own top-level `cleanup`",
    "interrupts": "the scenario's own top-level `interrupts`",
}


class ScenarioHooks(_Model):
    """`hooks` of a scenario: the commands that run around that one scenario.

    They run outside the app session, inside the target's own lists: after the target's
    `beforeScenario` and ahead of the launch, then ahead of the target's `afterScenario`.
    """

    before_scenario: list[HookCommand] = Field(default_factory=list, alias="beforeScenario")
    after_scenario: list[HookCommand] = Field(default_factory=list, alias="afterScenario")
    # `hooks` components this scenario applies (BE-0512). The scenario loader expands them into the
    # two lists above and the scenario's own `setup` / `cleanup`, then empties this list.
    use: list[HookUse] = Field(default_factory=list)

    @property
    def lists(self) -> dict[str, list[HookCommand]]:
        """The two lists keyed by the name a report block and a log path use."""
        return {"beforeScenario": self.before_scenario, "afterScenario": self.after_scenario}

    @model_validator(mode="before")
    @classmethod
    def _name_the_right_place(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, where in _MISPLACED.items():
                if key in data:
                    raise ValueError(
                        f"a scenario's `hooks` cannot hold `{key}`; write it in {where}"
                    )
        return data

    @model_validator(mode="after")
    def _check_entries(self) -> Self:
        reject_outcome_on_before(self.before_scenario, "hooks.beforeScenario")
        seen: set[str] = set()
        for list_name, entries in self.lists.items():
            for entry in entries:
                if entry.name is None:
                    continue
                if entry.name in seen:
                    raise ValueError(
                        f"hooks.{list_name}: two entries are named {entry.name!r}; a hook name must "
                        f"be unique so ${{hooks.{entry.name}.*}} names one command"
                    )
                seen.add(entry.name)
        return self

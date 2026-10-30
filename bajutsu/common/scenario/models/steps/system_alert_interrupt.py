"""One `interrupts` entry that answers a named OS prompt (BE-0473)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.system_alerts import (
    SystemAlertChoice,
    SystemAlertPrompt,
    alert_surfaces,
)


class SystemAlertInterrupt(_Model):
    """An `interrupts` entry answering one named OS prompt wherever it surfaces (BE-0406, BE-0473).

    `systemAlert` and `choice` reuse the vocabulary of the proactive `handleSystemAlert` step, so the
    same entry grants or denies the prompt under any locale `bajutsu.common.scenario.system_alerts`
    covers. The reactive guard identifies the alert on screen from the prompt's own labels, and an
    alert no entry names is left alone rather than answered by a guessed button.

    `choice: leave` answers nothing: a scenario writes it to turn off the answer its target gives
    that prompt, naming the prompt it leaves so a prompt the target adds later still applies.
    """

    system_alert: SystemAlertPrompt = Field(alias="systemAlert")
    choice: SystemAlertChoice | Literal["leave"]
    # How often (seconds) the guard polls SpringBoard while a wait is pending (BE-0315). Guard-wide
    # at runtime: the first layer that sets one wins, and within a layer the smallest does, since a
    # shorter interval satisfies every entry that asked for a longer one.
    poll_interval: float | None = Field(default=None, alias="pollInterval")
    # How long (seconds) a guarded wait tolerates a frozen screen before it stops; 0 turns the stop
    # off. Describes the app, so a scenario entry that omits it inherits the target entry's value for
    # the same prompt.
    frozen_screen_timeout: float | None = Field(default=None, alias="frozenScreenTimeout")

    @field_validator("poll_interval")
    @classmethod
    def _positive_interval(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("pollInterval must be positive")
        return v

    @field_validator("frozen_screen_timeout")
    @classmethod
    def _non_negative_frozen_timeout(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("frozenScreenTimeout must not be negative (0 turns the stop off)")
        return v

    @model_validator(mode="after")
    def _frozen_timeout_needs_in_tree_prompt(self) -> Self:
        # The frozen-screen stop arms only for a prompt the app's own tree carries and the guard
        # answers, so the value on any other prompt, or on one left, would load and never apply.
        if self.frozen_screen_timeout is not None and self.choice == "leave":
            raise ValueError(
                f"frozenScreenTimeout has no effect on a prompt the guard leaves "
                f"({self.system_alert}, choice: leave); remove it"
            )
        if (
            self.frozen_screen_timeout is not None
            and not alert_surfaces(self.system_alert)["in_tree"]
        ):
            raise ValueError(
                f"frozenScreenTimeout applies only to a prompt answered in the app's own tree "
                f"(such as savePassword), not to {self.system_alert}"
            )
        return self

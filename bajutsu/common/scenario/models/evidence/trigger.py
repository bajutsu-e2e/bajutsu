"""When a capture fires: always, on failure, or on a named step outcome."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import _exactly_one, _Model, reject_error_outcome


class Trigger(_Model):
    """A `capturePolicy` trigger that fires its `CaptureRule` when a condition holds.

    The condition is exactly one of `action` / `event` / `result`; `idMatches` narrows an
    `action` trigger to a specific element ID.
    """

    action: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    event: Literal["screenChanged"] | None = None
    # `failure` since BE-0452, the word a `cleanup` rule's `on` uses for the scenario's own outcome.
    result: Literal["failure"] | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _renamed_error(cls, value: Any) -> Any:
        return reject_error_outcome(value, "capturePolicy `result`")

    @model_validator(mode="after")
    def _one(self) -> Self:
        _exactly_one(self, ("action", "event", "result"), "§9 A")
        if self.id_matches is not None and self.action is None:
            raise ValueError("idMatches requires action (§9 A)")
        return self

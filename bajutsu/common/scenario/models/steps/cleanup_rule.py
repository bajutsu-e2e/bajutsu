"""One `cleanup` entry: the teardown steps for one scenario outcome (BE-0392, BE-0452)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from bajutsu.common.scenario.models._base import _Model, reject_error_outcome
from bajutsu.common.scenario.models.hook_command import HookOrigin, HookWhen

from .step import Step


class CleanupRule(_Model):
    """One `cleanup` entry (BE-0392): the teardown ``steps`` to run for one scenario outcome.

    ``on`` names the outcome this entry answers — ``always``, or the scenario's own machine-checked
    verdict (``success`` / ``failure``). ``failure`` is the word ``capturePolicy``'s
    ``Trigger.result`` spells for a failed outcome too (there one step's, here the whole
    scenario's), so one word names a failed outcome everywhere (BE-0452); ``always`` is the one
    addition, for cleanup that does not depend on the outcome at all. More than one entry may carry
    the same ``on`` and they compose in declaration order, the same way two ``capturePolicy`` rules
    may share a trigger. The steps share the enclosing scenario's ``vars.*``, so an entry can delete
    the very record an earlier ``http`` step's ``saveBody`` captured.
    """

    on: Literal["always", "success", "failure"]
    steps: list[Step] = Field(default_factory=list)
    # Runs the rule only where the condition holds (BE-0513), decided before the scenario's first
    # step from the facts a hook command reads; `on` still answers the verdict afterwards.
    when: HookWhen | None = None
    # Set by `hooks` component expansion (BE-0512) on a rule a component added.
    origin: Annotated[HookOrigin | None, SkipJsonSchema()] = Field(default=None, alias="_origin")

    @property
    def needs_preparation(self) -> bool:
        """Whether this rule tears down what its component's own preparation made (BE-0512)."""
        return self.origin is not None and self.origin.prepares

    @model_validator(mode="after")
    def _when_not_null(self) -> Self:
        if "when" in self.model_fields_set and self.when is None:
            # `when:` with its body commented out would otherwise widen the rule to every target.
            raise ValueError("a cleanup rule's `when` cannot be null; leave it out to always run")
        return self

    @field_validator("on", mode="before")
    @classmethod
    def _renamed_error(cls, value: Any) -> Any:
        return reject_error_outcome(value, "cleanup `on`")

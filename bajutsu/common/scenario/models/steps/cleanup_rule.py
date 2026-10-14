"""One `cleanup` entry: the teardown steps for one scenario outcome (BE-0392, BE-0452)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from bajutsu.common.scenario.models._base import _Model, reject_error_outcome

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

    @field_validator("on", mode="before")
    @classmethod
    def _renamed_error(cls, value: Any) -> Any:
        return reject_error_outcome(value, "cleanup `on`")

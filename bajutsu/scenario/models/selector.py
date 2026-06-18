"""The element selector — how a step or assertion addresses a UI element."""

from __future__ import annotations

from typing import Self, cast

from pydantic import Field, model_validator

from bajutsu.drivers import base
from bajutsu.scenario.models._base import _Model


class Selector(_Model):
    """How to address an element. Provided fields are combined with AND."""

    id: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    label: str | None = None
    label_matches: str | None = Field(default=None, alias="labelMatches")
    traits: list[str] | None = None
    value: str | None = None
    within: Selector | None = None
    index: int | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.model_dump(exclude_none=True, by_alias=True):
            raise ValueError("selector requires at least one condition (§5)")
        return self

    def as_selector(self) -> base.Selector:
        """Convert to the TypedDict consumed by base.resolve_unique."""
        return cast("base.Selector", self.model_dump(exclude_none=True, by_alias=True))


Selector.model_rebuild()  # resolve the `within: Selector` self-reference

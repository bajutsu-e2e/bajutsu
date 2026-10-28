"""The `attribute` assertion: one named HTML attribute of one element (BE-0466)."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import field_validator, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.selector import Selector

# The HTML attribute-name syntax: one or more characters other than controls, whitespace, quotes,
# `>`, `/`, and `=`. The name is never lowercased — it reaches `getAttribute` as written, which
# matches case-insensitively on HTML elements and exactly on SVG / MathML ones, as the DOM does.
_ATTRIBUTE_NAME = re.compile(r"[^\s\"'>/=\x00-\x1f\x7f]+")


class AttributeMatch(_Model):
    """`attribute`: exactly one of equals / contains / matches / `absent: true`, on attribute `name`.

    Read on demand by the web driver (`Driver.read_attribute`) when the assertion is evaluated, so
    the element snapshot never carries attributes no assertion names.
    """

    sel: Selector
    name: str
    equals: str | None = None
    contains: str | None = None
    matches: str | None = None  # regex over the attribute value
    # `true` alone: "must not be absent" is what equals / contains / matches already say.
    absent: Literal[True] | None = None

    @field_validator("name")
    @classmethod
    def _html_name(cls, v: str) -> str:
        if not _ATTRIBUTE_NAME.fullmatch(v):
            raise ValueError(f"attribute name {v!r} is not a valid HTML attribute name")
        return v

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        ops = (self.equals, self.contains, self.matches, self.absent)
        if sum(o is not None for o in ops) != 1:
            raise ValueError(
                "attribute requires exactly one of equals/contains/matches/absent (§6.4)"
            )
        return self

"""The `group` step: a named run of consecutive steps, folded together in report.html."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model

from .step import Step


class Group(_Model):
    """A named run of steps, expanded in place before `run`.

    Purely an authoring/report convenience: `expand_components` replaces a `group` step with its
    own `steps`, tagging each with `name` for `report.html` to fold. `run` never sees `group`.
    """

    name: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)

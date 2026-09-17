"""A reusable, parameterized sequence of steps."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.steps import Step


def is_component_file_ref(ref: str) -> bool:
    """Whether a `use` ref names a component *file* rather than a file-scoped name (BE-0422).

    A `/` or a `.yaml` / `.yml` suffix marks a path; anything else is a bare name. The two read as
    distinct on sight, so one `component:` field carries both without new syntax on `use`. Lives
    here, beside the model, so the resolver that dispatches on it and the validator that keeps an
    unreachable key out of `ScenarioFile.components` share one definition.
    """
    return "/" in ref or ref.endswith((".yaml", ".yml"))


class Component(_Model):
    """A reusable, parameterized sequence of steps.

    `params` are the names a caller must supply via `use: { with: {...} }`; the steps reference them
    as `${params.<name>}`.
    """

    params: list[str] = Field(default_factory=list)
    steps: list[Step]

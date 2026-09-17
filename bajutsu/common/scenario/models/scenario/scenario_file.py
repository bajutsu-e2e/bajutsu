"""A scenario file: its file-level description plus the scenarios it defines."""

from __future__ import annotations

from pydantic import Field, field_validator

from bajutsu.common.scenario.models._base import _Model

from .component import Component, is_component_file_ref
from .scenario import Scenario

# The scenario file's schema version, mirroring the report manifest's SCHEMA_VERSION (BE-0119).
# Bump only for a load-breaking change: removing a required field's meaning, or a change an older
# bajutsu would misinterpret rather than merely reject. A purely additive optional field needs no
# bump — an older bajutsu simply lacks the new behavior. `load_scenario_file` compares a file's
# declared `schema` against this before validating, so a newer file fails with a clear upgrade path
# instead of an opaque extra="forbid" error.
SCHEMA_VERSION = 1


class ScenarioFile(_Model):
    """A scenario file: an optional file-level `description` plus the scenarios it defines.

    Two on-disk forms are accepted: the bare list of scenarios (no file description), or a
    `{description: "...", scenarios: [...]}` mapping. Only the mapping form can carry
    `components:`, since the bare-list form has nowhere to put it.
    """

    # Named `schema` on disk (aliased to avoid shadowing BaseModel.schema). A file omitting it is
    # implicitly version 1; the version gate in load_scenario_file runs before this field validates
    # (BE-0119).
    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    description: str | None = None
    # File-scoped components (BE-0422): a `use: { component: <bare name> }` in this file resolves
    # here instead of opening a second file. The map never merges across a suite, so a name declared
    # here is invisible to every other file; cross-file reuse stays BE-0030's path-ref job.
    components: dict[str, Component] = Field(default_factory=dict)
    scenarios: list[Scenario]

    @field_validator("components")
    @classmethod
    def _reject_path_shaped_names(cls, value: dict[str, Component]) -> dict[str, Component]:
        """Reject a key `use` could only ever read as a path, instead of letting it sit unreachable.

        A `use` ref dispatches on its own shape, so a path-shaped key is not merely dead — a real
        file of that name resolves in its place and the declared steps never run. Refusing the key
        at load time keeps an ambiguous ref failing immediately rather than taking whichever
        matched.
        """
        bad = sorted(name for name in value if is_component_file_ref(name))
        if bad:
            raise ValueError(
                f"components: keys must be bare names, but {bad} hold a '/' or a .yaml / .yml "
                "suffix — a `use` ref of that shape always resolves as a file"
            )
        return value

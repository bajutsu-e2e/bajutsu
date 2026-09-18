"""One machine check in a scenario — exactly one kind may be set."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _exactly_one, _Model
from bajutsu.common.scenario.models.selector import Selector

from .clipboard_match import ClipboardMatch
from .count_match import CountMatch
from .event_match import EventMatch
from .exists import Exists
from .golden_match import GoldenMatch
from .request_match import RequestMatch
from .response_schema_match import ResponseSchemaMatch
from .text_match import TextMatch
from .visual_match import VisualMatch


class Assertion(_Model):
    """One machine check. Exactly one kind may be set."""

    exists: Exists | None = None
    value: TextMatch | None = None
    label: TextMatch | None = None
    count: CountMatch | None = None
    enabled: Selector | None = None
    disabled: Selector | None = None
    selected: Selector | None = None
    request: RequestMatch | None = None
    event: EventMatch | None = None
    request_sequence: list[RequestMatch] | None = Field(
        default=None, alias="requestSequence", min_length=1
    )
    response_schema: ResponseSchemaMatch | None = Field(default=None, alias="responseSchema")
    visual: VisualMatch | None = None
    clipboard: ClipboardMatch | None = None
    golden: GoldenMatch | None = None
    # Provenance (BE-0044): the natural-language phrase this check was normalized from. Not one of
    # the assertion kinds (`_ASSERTION_KINDS`), so it doesn't disturb the one-kind rule; `run`
    # ignores it.
    from_: str | None = Field(default=None, alias="from")
    # Which target this assertion checks (BE-0428) — only legal on a top-level `expect` entry, never
    # on one reached through an inline `assert:` list or an `if`'s `condition`, where the enclosing
    # step's own `target` already fixes it (`_check_target_requirements` enforces both rules). Not a
    # kind either, for the same reason `from_` isn't.
    target: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        _exactly_one(self, _ASSERTION_KINDS, "§6.4")
        return self


# The assertion-kind field names, derived from the model so a new kind is declared in exactly one
# place — adding an `Assertion` field — instead of also appending to a parallel hand-maintained
# tuple (a per-kind merge-conflict point). `from_` is provenance (BE-0044) and `target` is routing
# (BE-0428); neither is a kind.
_ASSERTION_KINDS = tuple(f for f in Assertion.model_fields if f not in ("from_", "target"))

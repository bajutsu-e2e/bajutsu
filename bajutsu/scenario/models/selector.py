"""The element selector — how a step or assertion addresses a UI element."""

from __future__ import annotations

from typing import Self, cast

from pydantic import Field, model_validator

from bajutsu.drivers import base
from bajutsu.scenario.models._base import _Model


class Selector(_Model):
    """How to address an element. Provided fields are combined with AND.

    `id` / `idMatches` accept a single value or a list of candidates; a list matches an element whose
    identifier equals (or glob-matches) *any* candidate — an OR. This lets one shared scenario carry
    every platform's form of an id (`id: [stable.refresh, stable_refresh]`), so it runs unchanged
    where the native id syntax differs — e.g. Android's `android:id`, which allows neither `.` nor
    `-`, surfaces `stable.refresh` as `stable_refresh` (BE-0221). Ambiguity is unchanged: 2+ matching
    elements on screen still fail fast.

    **List the canonical (dotted SPEC) form first.** OR matching is order-independent, but single-id
    consumers take the *first* candidate as the representative — `first_id()`, `audit.referenced_ids`
    coverage bucketing (`namespace_of` splits on `.`), and the XCUITest / Playwright codegen emitters.
    Leading with the dotted SPEC id keeps their output correct; an underscore-first list would still
    resolve at runtime but skew coverage and generate the non-portable id.
    """

    id: str | list[str] | None = None
    id_matches: str | list[str] | None = Field(default=None, alias="idMatches")
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
        # Reject a malformed candidate list (empty / blank / non-canonical-first) the same way a
        # config `readyWhen` is checked — shared in `base` so the rule holds wherever a selector is
        # authored (BE-0221). See `base.validate_id_candidates` for why the order is enforced.
        base.validate_id_candidates("id", self.id)
        base.validate_id_candidates("idMatches", self.id_matches)
        return self

    def as_selector(self) -> base.Selector:
        """Convert to the TypedDict consumed by base.resolve_unique."""
        return cast("base.Selector", self.model_dump(exclude_none=True, by_alias=True))

    def first_id(self) -> str | None:
        """The primary id candidate (the first when `id` is a list), or None (BE-0221).

        For single-id consumers — triage rename suggestions, capturePolicy `on:` matching, the
        WebView host — that want one representative id rather than the whole OR set. Selectors list
        the canonical (dotted SPEC) form first (see the class docstring), so "first" is the portable id.
        """
        if self.id is None:
            return None
        return self.id if isinstance(self.id, str) else self.id[0]


Selector.model_rebuild()  # resolve the `within: Selector` self-reference

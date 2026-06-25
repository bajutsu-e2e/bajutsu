"""Machine-checkable assertions and the wait conditions that reuse them.

Covers the network-traffic matcher, existence/text/count/state checks, visual regression,
and the `Assertion` aggregator that selects exactly one kind.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from bajutsu.scenario.models._base import _ASSERTION_KINDS, _exactly_one, _Model
from bajutsu.scenario.models.selector import Selector


class _EndpointMatch(_Model):
    """The endpoint criteria shared by `RequestMatch` and `EventMatch`.

    Pins which exchange to match by `url` (exact full URL), `urlMatches` (regex/substring; query
    strings live here), or just the `path` (`pathMatches` for a regex over the path), optionally
    AND-ed with `method`. Subclasses add their own extra criteria (status / body) and require at
    least one criterion overall.
    """

    method: str | None = None
    url: str | None = None  # exact full URL (the endpoint)
    url_matches: str | None = Field(
        default=None, alias="urlMatches"
    )  # regex/substring over the URL
    path: str | None = None  # exact path (query ignored)
    path_matches: str | None = Field(default=None, alias="pathMatches")  # regex over path

    def _endpoint_is_empty(self) -> bool:
        """Whether no endpoint criterion (method / url / urlMatches / path / pathMatches) is set."""
        return all(
            v is None
            for v in (self.method, self.url, self.url_matches, self.path, self.path_matches)
        )


class RequestMatch(_EndpointMatch):
    """Network-traffic matcher, shared by the `request` assertion and `until: { request: ... }`.

    The fields (method / url / urlMatches / path / pathMatches / status / bodyMatches) are AND-ed;
    `count` is how many exchanges matched — exact for the assertion, a lower bound for the wait.
    The endpoint can be pinned by `url` (exact full URL) or `urlMatches` (regex/substring; query
    strings live here), or just the `path`; `bodyMatches` checks the request body. At least one
    match field is required.
    """

    status: int | None = None
    body_matches: str | None = Field(
        default=None, alias="bodyMatches"
    )  # regex/substring over request body
    count: int | None = None

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if self._endpoint_is_empty() and self.status is None and self.body_matches is None:
            raise ValueError(
                "request requires at least one of method/url/urlMatches/path/pathMatches/status/bodyMatches"
            )
        return self


class Gone(_Model):
    """`until: { gone: <Selector> }` — wait until a selector no longer matches any element."""

    gone: Selector


class WaitRequest(_Model):
    """`until: { request: <RequestMatch> }` — wait until a matching network exchange has been observed.

    Requires the run's network collector to be active.
    """

    request: RequestMatch


class Wait(_Model):
    """`wait` step — block until a selector appears (`for`) or a condition holds (`until`).

    Bounded by `timeout`; always a condition wait, never a fixed sleep.
    """

    for_: Selector | None = Field(default=None, alias="for")
    # settled = wait until the screen stops changing (best-effort; for transition settle)
    until: Literal["screenChanged", "settled"] | Gone | WaitRequest | None = None
    timeout: float

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.for_ is None) == (self.until is None):
            raise ValueError("wait requires exactly one of 'for' or 'until' (§6.3)")
        return self


class Exists(_Model):
    """`exists: { <selector>, negate? }` (selector inline, optional negate)."""

    sel: Selector
    negate: bool = False

    @model_validator(mode="before")
    @classmethod
    def _inline(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sel" not in data:
            d = dict(data)
            negate = d.pop("negate", False)
            return {"sel": d, "negate": negate}
        return data


class TextMatch(_Model):
    """`value` / `label`: exactly one of equals / contains / matches."""

    sel: Selector
    equals: str | None = None
    contains: str | None = None
    matches: str | None = None

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.contains, self.matches)) != 1:
            raise ValueError("value/label requires exactly one of equals/contains/matches (§6.4)")
        return self


class CountMatch(_Model):
    """`count`: exactly one of equals / atLeast / atMost."""

    sel: Selector
    equals: int | None = None
    at_least: int | None = Field(default=None, alias="atLeast")
    at_most: int | None = Field(default=None, alias="atMost")

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.at_least, self.at_most)) != 1:
            raise ValueError("count requires exactly one of equals/atLeast/atMost (§6.4)")
        return self


class CountOp(_Model):
    """A count comparison with no element selector — exactly one of equals / atLeast / atMost.

    The element-free counterpart to `CountMatch`, for aggregating over the network timeline (e.g. an
    `event`'s multiplicity) rather than over screen elements.
    """

    equals: int | None = None
    at_least: int | None = Field(default=None, alias="atLeast")
    at_most: int | None = Field(default=None, alias="atMost")

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.at_least, self.at_most)) != 1:
            raise ValueError("count requires exactly one of equals/atLeast/atMost (§6.4)")
        return self


class EventMatch(_EndpointMatch):
    """An analytics / telemetry event the app *sent* (BE-0048).

    Matched over the captured request timeline by endpoint (url / urlMatches / path / pathMatches /
    method, AND-ed, same meaning as `RequestMatch`) and structured request-body fields (`body`: each
    given key must be present in the JSON request body and equal — compared as text — the given
    value). `count` is the expected multiplicity (default: at least one). At least one of an endpoint
    criterion or `body` is required, so an event always pins *something*.
    """

    body: dict[str, str] = Field(default_factory=dict)
    count: CountOp | None = None

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if self._endpoint_is_empty() and not self.body:
            raise ValueError(
                "event requires at least one of method/url/urlMatches/path/pathMatches/body (§6.4)"
            )
        return self


class ExcludeRegion(_Model):
    """A rectangular region to ignore during visual comparison (e.g. status bar, clock)."""

    x: float
    y: float
    w: float
    h: float


class ResponseSchemaMatch(_Model):
    """Validate a captured response body against a stored JSON Schema (BE-0048).

    `request` selects the exchange whose response is checked (reusing the request matcher); `schema`
    is the schema file, resolved against the app's schemas dir. `schema_path` carries the value (the
    field is aliased `schema` to avoid shadowing pydantic's own `schema` attribute).
    """

    request: RequestMatch
    schema_path: str = Field(alias="schema")


class VisualMatch(_Model):
    """Visual regression assertion — compare a screenshot to a baseline image."""

    baseline: str
    threshold: float = 0.0  # allowed diff percentage (0.0 = exact match)
    exclude: list[ExcludeRegion] | None = None


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
    # Provenance (BE-0044): the natural-language phrase this check was normalized from. Not one of
    # the assertion kinds (`_ASSERTION_KINDS`), so it doesn't disturb the one-kind rule; `run`
    # ignores it.
    from_: str | None = Field(default=None, alias="from")

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        _exactly_one(self, _ASSERTION_KINDS, "§6.4")
        return self

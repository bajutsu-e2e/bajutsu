"""The `event` assertion: an analytics event the app sent (BE-0048)."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from ._endpoint_match import _EndpointMatch
from .count_op import CountOp


class EventMatch(_EndpointMatch):
    """An analytics / telemetry event the app *sent* (BE-0048).

    Matched over the captured request timeline by endpoint (url / urlMatches / path / pathMatches /
    method, AND-ed, same meaning as `RequestMatch`) and the `query` / `body` key/value dictionaries
    (BE-0463; a flat `body` of top-level text equalities is still its simplest form). `count` is the
    expected multiplicity (default: at least one). At least one of an endpoint criterion, `query`,
    or `body` is required, so an event always pins *something*.
    """

    count: CountOp | None = None

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if self._endpoint_is_empty() and self._dictionaries_are_empty():
            raise ValueError(
                "event requires at least one of method/url/urlMatches/path/pathMatches/query/body (§6.4)"
            )
        return self

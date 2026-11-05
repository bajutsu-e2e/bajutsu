"""One deterministic network stub the app under test calls instead of the real service."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.assertions import RequestMatch

from .mock_response import MockResponse


class Mock(_Model):
    """A deterministic network stub.

    When an outgoing request matches `match`, BajutsuKit returns `respond` instead of hitting the
    network (so tests don't depend on a live server). `match` reuses the request matcher's
    request-side fields (method / url / urlMatches / path / pathMatches / bodyMatches); status /
    count do not apply here, and `query` / `body` are refused (see `_no_dictionaries`).
    """

    match: RequestMatch
    respond: MockResponse = Field(default_factory=MockResponse)

    @model_validator(mode="after")
    def _no_dictionaries(self) -> Self:
        # The in-app BajutsuKit matcher would ignore these fields and stub more traffic than the
        # scenario says, so they are refused until a later item teaches it the grammar (BE-0463).
        if self.match.query is not None or self.match.body is not None:
            raise ValueError(
                "mock match does not support query/body dictionaries yet; "
                "use urlMatches/bodyMatches for a mock"
            )
        return self

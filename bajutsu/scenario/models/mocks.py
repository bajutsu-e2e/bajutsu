"""Deterministic network stubs.

A mock matches an outgoing request (reusing the request-side fields of the traffic matcher) and
returns a canned response instead of hitting the network.
"""

from __future__ import annotations

from pydantic import Field

from bajutsu.scenario.models._base import _Model
from bajutsu.scenario.models.assertions import RequestMatch


class MockResponse(_Model):
    """The canned response a mock returns (defaults to an empty 200)."""

    status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    delay_ms: float | None = Field(default=None, alias="delayMs")  # artificial latency


class Mock(_Model):
    """A deterministic network stub.

    When an outgoing request matches `match`, BajutsuKit returns `respond` instead of hitting the
    network (so tests don't depend on a live server). `match` reuses the request matcher's
    request-side fields (method / url / urlMatches / path / pathMatches / bodyMatches); status /
    count do not apply here.
    """

    match: RequestMatch
    respond: MockResponse = Field(default_factory=MockResponse)

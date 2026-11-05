"""One request and response the app under test reported."""

from __future__ import annotations

import json
from functools import cached_property
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field


class NetworkExchange(BaseModel):
    """One request/response the app reported.

    Extra keys from the SDK are ignored (forward-compatible); field names accept their JSON aliases.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    method: str = ""
    url: str = ""
    path: str = ""  # path only (no query), for matching
    status: int | None = None
    request_headers: dict[str, str] = Field(default_factory=dict, alias="requestHeaders")
    response_headers: dict[str, str] = Field(default_factory=dict, alias="responseHeaders")
    request_body: str | None = Field(default=None, alias="requestBody")
    response_body: str | None = Field(default=None, alias="responseBody")
    started_at: float | None = Field(default=None, alias="startedAt")
    duration_ms: float | None = Field(default=None, alias="durationMs")
    mocked: bool = False  # served by a bajutsu mock stub (not a real network call)

    # Cached because the `query` / `body` matchers (BE-0463) run once per matcher-exchange pair
    # and on every `until: {request}` poll; an exchange is never mutated after capture.
    @cached_property
    def query_params(self) -> dict[str, list[str]]:
        """The URL-decoded query parameters, every occurrence kept; `?debug` reads as `""`."""
        return parse_qs(urlsplit(self.url).query, keep_blank_values=True)

    @cached_property
    def request_json_object(self) -> dict[str, Any] | None:
        """The request body parsed as a JSON object, or None when absent, not JSON, or not an object."""
        if self.request_body is None:
            return None
        try:
            parsed = json.loads(self.request_body)
        except (ValueError, RecursionError):  # a pathologically deep body is still "not JSON" here
            return None
        return parsed if isinstance(parsed, dict) else None

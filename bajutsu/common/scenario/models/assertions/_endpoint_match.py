"""The endpoint criteria the request and event assertions share."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model

from ._dict_matcher import check_dict_matcher


class _EndpointMatch(_Model):
    """The endpoint and dictionary criteria shared by `RequestMatch` and `EventMatch`.

    Pins which exchange to match by `url` (exact full URL), `urlMatches` (regex/substring over the
    URL), or just the `path` (`pathMatches` for a regex over the path), optionally AND-ed with
    `method`. `query` and `body` are key/value dictionaries over the URL-decoded query string and
    the parsed JSON request body (BE-0463). Subclasses add their own extra criteria and require at
    least one criterion overall.
    """

    method: str | None = None
    url: str | None = None  # exact full URL (the endpoint)
    url_matches: str | None = Field(
        default=None, alias="urlMatches"
    )  # regex/substring over the URL
    path: str | None = None  # exact path (query ignored)
    path_matches: str | None = Field(default=None, alias="pathMatches")  # regex over path
    # `Any`, not a typed union: a typed `str | bool` value would coerce an unquoted `1` to `True`
    # and hide the mistake the grammar check exists to report.
    query: dict[str, Any] | None = None
    body: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_dictionaries(self) -> Self:
        check_dict_matcher(self.query, "query")
        check_dict_matcher(self.body, "body")
        return self

    def _endpoint_is_empty(self) -> bool:
        """Whether no endpoint criterion (method / url / urlMatches / path / pathMatches) is set."""
        return all(
            v is None
            for v in (self.method, self.url, self.url_matches, self.path, self.path_matches)
        )

    def _dictionaries_are_empty(self) -> bool:
        """Whether neither dictionary criterion (query / body) is set."""
        return self.query is None and self.body is None

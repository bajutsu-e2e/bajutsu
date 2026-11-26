"""The `http` action: issue a request, for test-data setup or a webhook trigger."""

from __future__ import annotations

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model

_EXTRACT_BODY_REMOVED = (
    "http: extractBody was removed (BE-0516). Keep the response with `saveBody: <name>`, then add "
    'a `parseJson: { from: "${vars.<name>}", extract: [ { var, path } ] }` step after this one; '
    "the path grammar is unchanged"
)


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation; a `parseJson` step reads
    single values out of it.
    """

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")

    @model_validator(mode="before")
    @classmethod
    def _no_extract_body(cls, data: object) -> object:
        # Named here so an old scenario is told what to write instead, not just "extra field".
        if isinstance(data, dict) and "extractBody" in data:
            raise ValueError(_EXTRACT_BODY_REMOVED)
        return data

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

    ``timeout`` (BE-0515), in seconds, turns the step into a condition wait: the request repeats until
    the response carries ``status`` or the deadline passes.
    """

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    save_body: str | None = Field(default=None, alias="saveBody")

    @model_validator(mode="before")
    @classmethod
    def _no_extract_body(cls, data: object) -> object:
        # Named here so an old scenario is told what to write instead, not just "extra field".
        if isinstance(data, dict) and "extractBody" in data:
            raise ValueError(_EXTRACT_BODY_REMOVED)
        return data

    @model_validator(mode="after")
    def _check_timeout(self) -> HttpRequest:
        # Safe as model validation: a literal GET/HEAD survives
        # `${...}` substitution unchanged, so rebuilding a loaded step can never newly fail it.
        if self.timeout is None:
            return self
        if self.status is None:
            raise ValueError("http: timeout requires status (the response status to wait for)")
        # Case-sensitive, as HTTP methods are: the value reaches the wire verbatim.
        if self.method not in ("GET", "HEAD"):
            raise ValueError(
                f"http: timeout requires method GET or HEAD, got {self.method!r} "
                "(repeating another method can create data twice)"
            )
        return self

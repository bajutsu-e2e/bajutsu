"""The `http` action: issue a request, for test-data setup or a webhook trigger."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model


class ExtractField(_Model):
    """One `extractBody` entry: a JSON `path` in the response body, stored as `vars.<var>` (BE-0440)."""

    var: str
    path: str


def first_duplicate_var(names: Iterable[str], save_body: str | None) -> str | None:
    """The first name in *names* that repeats, or that equals *save_body* — or None (BE-0440).

    Shared by `HttpRequest.duplicate_extract_var` (the load-time scenario check, against each
    entry's as-written `var`) and the runner's `http` handler (its own re-check against the
    substituted `var` strings): a `${vars.*}` / `${secrets.*}` token in a `var` can only reveal a
    collision once substituted, so both call sites need the same rule.
    """
    seen = {save_body} if save_body is not None else set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation. ``extractBody`` (BE-0440) pulls
    one or more named fields out of a JSON response body, by path, into ``vars.*`` — independent of
    ``saveBody``, which a step may set alongside it, neither, or both.
    """

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")
    extract_body: list[ExtractField] | None = Field(default=None, alias="extractBody")

    def duplicate_extract_var(self) -> str | None:
        """The first `var` that collides — repeated in `extractBody`, or equal to `saveBody` — or None."""
        if not self.extract_body:
            return None
        return first_duplicate_var((field.var for field in self.extract_body), self.save_body)

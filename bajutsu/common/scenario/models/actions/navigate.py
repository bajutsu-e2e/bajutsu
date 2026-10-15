"""The `navigate` action: open a URL mid-scenario on a backend that can (BE-0464)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import GetJsonSchemaHandler, model_serializer, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from bajutsu.common.scenario.models._base import _Model

_ALLOWED_SCHEMES = ("http", "https")


def navigate_url_problem(url: str) -> str | None:
    """Why *url* cannot be a `navigate` target, or None when it can (BE-0464).

    Shared by the load-time scenario check (`_check_navigate_urls`, for a value with no `${...}`
    token) and the runner's `navigate` handler (its re-check after substitution), so a token that
    expands to a `javascript:` or `file:` reference meets the same rule as one written literally.
    A relative reference (no scheme) passes: the driver resolves it against the target's `baseUrl`.
    Resolution alone guards nothing, since `urljoin` returns an absolute `javascript:` unchanged.
    """
    stripped = url.strip()
    if not stripped:
        return "navigate needs a non-empty URL"
    scheme = urlsplit(stripped).scheme.lower()
    if scheme and scheme not in _ALLOWED_SCHEMES:
        return f"navigate only opens http/https URLs or a path relative to baseUrl, got {url!r}"
    return None


class Navigate(_Model):
    """`navigate: <url>` — load *url* in the current page, resolved against the target's `baseUrl`.

    The YAML value is a plain string; `url` is only how the model stores it. A mapping is rejected,
    so `{url: ...}` never becomes a public shape a later option would have to stay compatible with.
    """

    url: str

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"url": data}
        if isinstance(data, cls):
            return data
        raise ValueError("navigate takes a URL string, e.g. `navigate: /settings`")

    # Dumps back to the plain string the file wrote, so every dump-and-revalidate path
    # (`_interp_step`, `serialize`, a saved run's reload) round-trips through `_shorthand`.
    @model_serializer
    def _as_string(self) -> str:
        return self.url

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"title": "Navigate", "description": cls.__doc__, "type": "string", "minLength": 1}

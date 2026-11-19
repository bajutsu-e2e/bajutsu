"""The `openUrl` action: hand a URL to the operating system mid-scenario on a mobile backend (BE-0487)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import GetJsonSchemaHandler, model_serializer, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from bajutsu.common.scenario.models._base import _Model

# None of these names a destination inside an app, so handing one to the OS opens nothing a scenario
# could assert on — and `javascript:` / `file:` would reach a browser's own page or the file system.
_REJECTED_SCHEMES = ("javascript", "data", "file")


def open_url_problem(url: str) -> str | None:
    """Why *url* cannot be an `openUrl` target, or None when it can (BE-0487).

    Shared by the load-time scenario check (for a value with no `${...}` token) and the runner's
    `openUrl` handler (its re-check after substitution), as `navigate_url_problem` is for `navigate`.
    Unlike `navigate`, a value with no scheme is refused: a mobile backend has no `baseUrl` to
    resolve a relative reference against.
    """
    stripped = url.strip()
    if not stripped:
        return "openUrl needs a non-empty URL"
    scheme = urlsplit(stripped).scheme.lower()
    if not scheme:
        return f"openUrl needs an absolute URL with a scheme, such as myapp://path, got {url!r}"
    if scheme in _REJECTED_SCHEMES:
        return f"openUrl does not open {scheme}: URLs, got {url!r}"
    if any(c in stripped for c in "\n\r\t"):
        # `urlsplit` drops these before reading the scheme, yet adb's verdict is read from
        # `am start`'s output, which echoes the URL: a line break there would forge one of its lines.
        return f"openUrl needs a URL with no line break or tab, got {url!r}"
    return None


class OpenUrl(_Model):
    """`openUrl: <url>` — hand *url* to the OS for the app the driver addresses right now.

    The YAML value is a plain string; `url` is only how the model stores it. `Step` rejects a
    mapping, so `{url: ...}` never becomes a public shape, while `OpenUrl(url=...)` still constructs.
    """

    url: str

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        return {"url": data} if isinstance(data, str) else data

    # Dumps back to the plain string, so every dump-and-revalidate path round-trips (see `Navigate`).
    @model_serializer
    def _as_string(self) -> str:
        return self.url

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # The schema speaks to a scenario author, so it states the authored form, not the model's.
        return {
            "title": "OpenUrl",
            "description": "Hand an absolute URL (any scheme but javascript:, data:, or file:) to "
            "the operating system for the app the scenario drives at that point (iOS and Android).",
            "type": "string",
            "minLength": 1,
        }

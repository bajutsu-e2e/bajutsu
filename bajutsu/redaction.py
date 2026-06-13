"""Redaction — mask secrets in captured evidence before it is written.

Driven by the resolved `Redact` config (header names, body/field names, and
accessibility labels). Free-text evidence (the device log, app trace) is scrubbed
by key→value patterns; the element tree is scrubbed structurally (an element's
value is masked when its label is configured, or when the value itself contains a
masked key). Images (screenshots/video) cannot be masked and are left as-is.
"""

from __future__ import annotations

import re
from typing import Any

from bajutsu.drivers import base
from bajutsu.scenario import Redact

PLACEHOLDER = "[REDACTED]"


def _patterns(keys: list[str]) -> list[re.Pattern[str]]:
    """For each key, patterns that capture the key (group 1) and consume its value."""
    pats: list[re.Pattern[str]] = []
    for key in keys:
        k = re.escape(key)
        # JSON: "key": "value"  or  "key": value
        pats.append(re.compile(rf'("{k}"\s*:\s*)("(?:[^"\\]|\\.)*"|[^\s,}}\]]+)', re.I))
        # query / key=value
        pats.append(re.compile(rf"(?i)\b({k}\s*=\s*)[^\s&;,\"]+"))
        # header-ish: key: value-to-end-of-line
        pats.append(re.compile(rf"(?i)\b({k}\s*:\s*).+"))
    return pats


class Redactor:
    """Applies a `Redact` config to evidence. A no-op when nothing is configured.

    `values` are literal secret values (resolved from the environment) masked wherever
    they appear — this catches a secret the app echoes into a log / element / response,
    which key-based patterns alone would miss. Longest values are masked first so a value
    that is a substring of another does not leave a partial leak."""

    def __init__(self, redact: Redact | None, values: list[str] | None = None) -> None:
        redact = redact or Redact()
        self._keys: list[str] = [*redact.headers, *redact.fields]
        self._header_names: set[str] = {h.lower() for h in redact.headers}
        self._labels: set[str] = set(redact.labels)
        self._patterns = _patterns(self._keys)
        self._values: list[str] = sorted({v for v in (values or []) if v}, key=len, reverse=True)

    @property
    def active(self) -> bool:
        return bool(self._keys or self._labels or self._values)

    def redact_text(self, text: str) -> str:
        """Mask the value after any configured key, and any literal secret value, in free
        text (logs/traces)."""
        for pattern in self._patterns:
            text = pattern.sub(lambda m: m.group(1) + PLACEHOLDER, text)
        for value in self._values:
            text = text.replace(value, PLACEHOLDER)
        return text

    def redact_elements(self, elements: list[base.Element]) -> list[base.Element]:
        """Mask element values: fully when the label is configured, otherwise scrub
        the label/value text in case a secret is embedded there."""
        if not self.active:
            return elements
        out: list[base.Element] = []
        for el in elements:
            new = dict(el)
            if new.get("label") in self._labels:
                new["value"] = PLACEHOLDER
            else:
                for field in ("value", "label"):
                    raw = new.get(field)
                    if isinstance(raw, str) and raw:
                        new[field] = self.redact_text(raw)
            out.append(new)  # type: ignore[arg-type]
        return out

    def redact_exchange(self, exchange: dict[str, Any]) -> dict[str, Any]:
        """Mask secrets in one network-exchange dict: a header value is masked whole when
        its name is a configured header (else scrubbed as free text), and the url / bodies
        are scrubbed as free text so query params and body fields (token / password) are
        caught — which a whole-JSON text pass misses, since bodies are escaped strings."""
        if not self.active:
            return exchange
        out = dict(exchange)
        for key in ("requestHeaders", "responseHeaders"):
            headers = out.get(key)
            if isinstance(headers, dict):
                out[key] = {
                    k: PLACEHOLDER
                    if str(k).lower() in self._header_names
                    else self.redact_text(str(v))
                    for k, v in headers.items()
                }
        for key in ("url", "requestBody", "responseBody"):
            value = out.get(key)
            if isinstance(value, str):
                out[key] = self.redact_text(value)
        return out

"""The `push` action: deliver a simulated push notification to the app."""

from __future__ import annotations

import json
from typing import Any

from pydantic import model_validator

from bajutsu.common.scenario.models._base import _Model

# The largest payload `simctl push` accepts, measured as below; it also keeps the Android broadcast's
# `adb shell` argument well inside the shell's length limit (BE-0498).
PUSH_PAYLOAD_LIMIT = 4096


def _unserializable_path(value: object, path: str) -> str | None:
    """The key path of the first value under *value* that `json.dumps` cannot encode, if any."""
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):  # a date key: `json.dumps` rejects the key itself
                return where
            found = _unserializable_path(item, where)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            found = _unserializable_path(item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return path or "(payload)"
    return None


def push_payload_size(payload: dict[str, Any]) -> int:
    """The size a push payload counts against `PUSH_PAYLOAD_LIMIT`.

    The measure is the length of the default `json.dumps` text, the same text the iOS path hands to
    `simctl push`. That text escapes every character above code point 127, so characters equal bytes.

    Raises:
        ValueError: a value `json.dumps` cannot encode, such as the `datetime.date` an unquoted
            `2024-01-01` loads as. The message names the key path and never the value, and it is a
            `ValueError` because Pydantic turns that, not a `TypeError`, into a validation error.
    """
    try:
        return len(json.dumps(payload))
    except (TypeError, ValueError):
        where = _unserializable_path(payload, "")
        raise ValueError(
            f"push payload value at {where!r} is not JSON-serializable; quote it as a string"
        ) from None


class Push(_Model):
    """Deliver a simulated push notification to the app under test.

    Carries an Apple Push Notification service (APNs) payload, e.g. `{"aps": {"alert": "..."}}`. On
    iOS, `simctl push` delivers it. On Android, bajutsu maps it to a title, a body, and a data map,
    and BajutsuAndroid's in-app push receiver delivers that (BE-0498). The payload must carry an
    `aps` object, `aps.alert` must be a string or an object with string `title` / `body`, and the
    payload must stay within `PUSH_PAYLOAD_LIMIT` (see `push_payload_size`).
    """

    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check_payload(self) -> Push:
        if "aps" not in self.payload:
            raise ValueError("push payload needs an `aps` key, as simctl push requires")
        aps = self.payload["aps"]
        if not isinstance(aps, dict):
            raise ValueError(  # noqa: TRY004 - Pydantic turns only ValueError into a field error
                "push payload `aps` must be an object"
            )
        alert = aps.get("alert")
        if alert is not None and not isinstance(alert, str | dict):
            raise ValueError("push payload `aps.alert` must be a string or an object")
        if isinstance(alert, dict):
            for key in ("title", "body"):
                if key in alert and not isinstance(alert[key], str):
                    raise ValueError(f"push payload `aps.alert.{key}` must be a string")
        size = push_payload_size(self.payload)
        if size > PUSH_PAYLOAD_LIMIT:
            raise ValueError(f"push payload is {size:,} bytes; the limit is {PUSH_PAYLOAD_LIMIT:,}")
        return self

"""One-shot device actions.

Gestures, text entry, relaunch, location/push, http, and the device-control steps. Each is a step
payload validated on its own; the `Step` aggregator that selects exactly one lives in `steps.py`.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from bajutsu.scenario.models._base import Point, _Model
from bajutsu.scenario.models.selector import Selector


def _check_regex(pattern: str, field: str) -> None:
    """Reject an uncompilable regex at load time, so a typo is a scenario error, not a mid-run crash."""
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{field} is not a valid regex: {e}") from e


class LongPress(_Model):
    """`longPress` action — press and hold a selector for `duration` seconds."""

    sel: Selector
    duration: float


class Pinch(_Model):
    """Two-finger magnify. scale > 1 zooms in, 0 < scale < 1 zooms out."""

    sel: Selector
    scale: float

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.scale <= 0:
            raise ValueError("pinch scale must be positive (>1 zooms in, <1 zooms out) (§6.2)")
        return self


class Rotate(_Model):
    """Two-finger rotation. radians > 0 rotates clockwise."""

    sel: Selector
    radians: float


class TypeText(_Model):
    """`type` action — enter text, optionally into a selector and optionally submitting after."""

    text: str
    into: Selector | None = None
    submit: bool = False


class Swipe(_Model):
    """`swipe` action — by `direction` on an element (`on`), or between two points (`from`/`to`)."""

    on: Selector | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    from_: Point | None = Field(default=None, alias="from")
    to: Point | None = None

    @model_validator(mode="after")
    def _form(self) -> Self:
        sel_fields = self.on is not None or self.direction is not None
        pt_fields = self.from_ is not None or self.to is not None
        if sel_fields and pt_fields:
            raise ValueError("swipe cannot mix {on,direction} with {from,to} (§6.2)")
        if self.on is not None and self.direction is not None:
            return self
        if self.from_ is not None and self.to is not None:
            return self
        raise ValueError("swipe requires either {on,direction} or {from,to} completely (§6.2)")


class Relaunch(_Model):
    """`relaunch` action — restart the app process, optionally overriding its launch env/args."""

    env: dict[str, str] | None = None
    args: list[str] | None = None


class SetLocation(_Model):
    """Override the simulated device's GPS location (simctl location set)."""

    lat: float
    lon: float


class Push(_Model):
    """Deliver a simulated push notification (simctl push) to the app under test.

    Carries this APNs payload, e.g. `{"aps": {"alert": "..."}}`.
    """

    payload: dict[str, Any]


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation.
    """

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")


class VarTarget(_Model):
    """`into: { var: <name> }` — the `${vars.<name>}` slot a step writes its produced value to."""

    var: str


class Totp(_Model):
    """`totp` — generate an RFC 6238 time-based one-time password into `${vars.*}` (BE-0046).

    `secret` is the shared base32 key (commonly `${secrets.*}`); the current code is written to
    `into.var` for a later `type` / `assert` to consume. Local and deterministic — no LLM, no
    network, no scripting escape hatch.
    """

    secret: str
    into: VarTarget


class EmailMatch(_Model):
    """Which message `email` waits for: recipient and/or subject, AND-ed. At least one is required."""

    to: str | None = None
    subject: str | None = None
    subject_matches: str | None = Field(default=None, alias="subjectMatches")

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if self.to is None and self.subject is None and self.subject_matches is None:
            raise ValueError("email.match needs at least one of: to / subject / subjectMatches")
        if self.subject_matches is not None:
            _check_regex(self.subject_matches, "email.match.subjectMatches")
        return self


class EmailExtract(_Model):
    """Pull a value from the matched message body into `${vars.<var>}` via a regex.

    `bodyMatches` is a regex; its first capturing group (or the whole match, if it has none) is the
    value written to `var`. A matched message whose body the regex does not hit fails the step.
    """

    var: str
    body_matches: str = Field(alias="bodyMatches")

    @model_validator(mode="after")
    def _valid_regex(self) -> Self:
        _check_regex(self.body_matches, "email.extract.bodyMatches")
        return self


class Email(_Model):
    """`email` — poll a mailbox until a matching message arrives, extract a value into `${vars.*}`.

    `match` selects the awaited message, `extract` pulls the value, and `timeout` (seconds, required)
    bounds the poll — a condition wait, never a fixed sleep (BE-0046). The mailbox endpoint lives in
    config (`targets.<name>.mailbox`), so the scenario stays app-agnostic and credential-free.
    """

    match: EmailMatch
    extract: EmailExtract
    timeout: float = Field(gt=0)


class ClearKeychain(_Model):
    """Reset the Simulator's keychain (saved passwords, certificates)."""


class ClearClipboard(_Model):
    """Clear the Simulator's pasteboard."""


class SetClipboard(_Model):
    """Seed the Simulator's pasteboard with text (simctl pbcopy), for paste flows."""

    text: str


class Background(_Model):
    """Send the app to the background by pressing the Home button (simctl ui home)."""


class Foreground(_Model):
    """Resume a backgrounded app to the foreground (simctl launch, without terminating it).

    The other half of `background`. It adds no settle sleep — any wait after resuming is the
    scenario's own condition wait.
    """


class OverrideStatusBar(_Model):
    """Override the Simulator's status bar for deterministic screenshots.

    All fields are optional; only the provided fields are overridden.
    """

    time: str | None = None
    battery_level: int | None = Field(default=None, alias="batteryLevel")
    battery_state: str | None = Field(default=None, alias="batteryState")
    cellular_bars: int | None = Field(default=None, alias="cellularBars")
    wifi_bars: int | None = Field(default=None, alias="wifiBars")


class ClearStatusBar(_Model):
    """Remove any status bar overrides (restore the live status bar)."""

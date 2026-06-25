"""One-shot device actions: gestures, text entry, relaunch, location/push, http, and the
device-control steps. Each is a step payload validated on its own; the `Step` aggregator that
selects exactly one lives in `steps.py`."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from bajutsu.scenario.models._base import Point, _Model
from bajutsu.scenario.models.selector import Selector


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
    """Deliver a simulated push notification (simctl push) with this APNs payload
    (e.g. {"aps": {"alert": "..."}}) to the app under test."""

    payload: dict[str, Any]


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation."""

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")


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
    scenario's own condition wait."""


class OverrideStatusBar(_Model):
    """Override the Simulator's status bar for deterministic screenshots.

    All fields are optional; only the provided fields are overridden."""

    time: str | None = None
    battery_level: int | None = Field(default=None, alias="batteryLevel")
    battery_state: str | None = Field(default=None, alias="batteryState")
    cellular_bars: int | None = Field(default=None, alias="cellularBars")
    wifi_bars: int | None = Field(default=None, alias="wifiBars")


class ClearStatusBar(_Model):
    """Remove any status bar overrides (restore the live status bar)."""

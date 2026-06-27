"""idb backend (headless, coordinate-based).

Parses `idb ui describe-all` JSON into normalized Elements and acts via
`idb ui tap/text/swipe`. idb has no semantic tap, so a tap resolves the target's
frame center first. The JSON key names follow fb-idb's describe-all output,
validated on-device against fb-idb (iPhone 17 Pro, recent iOS); re-check them if
the installed idb version changes the schema.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

from bajutsu.drivers import base

RunFn = Callable[[list[str]], str]

# A short dwell on every tap. A zero-duration `idb ui tap` presses and releases in
# the same instant, which a UISwitch's gesture recognizer does not register — so a
# coordinate tap on a real Toggle never flips it. A brief hold actuates the switch
# while staying far below any long-press threshold, so plain buttons/rows behave
# identically. (Surfaced by the sample app's ctrl.toggle.)
_TAP_DURATION_S = 0.1


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- command builders ---


def describe_all_cmd(udid: str) -> list[str]:
    """The `idb` argv that dumps the accessibility tree as JSON."""
    return ["idb", "ui", "describe-all", "--udid", udid, "--json"]


def tap_cmd(udid: str, x: float, y: float) -> list[str]:
    """The `idb` argv that taps the point (x, y)."""
    return [
        "idb",
        "ui",
        "tap",
        "--udid",
        udid,
        _num(x),
        _num(y),
        "--duration",
        str(_TAP_DURATION_S),
    ]


def swipe_cmd(udid: str, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    """The `idb` argv that drags from (x1, y1) to (x2, y2)."""
    # A finite duration makes it a real drag; an instantaneous swipe isn't recognized
    # as a pan/drag gesture by SwiftUI.
    return [
        "idb",
        "ui",
        "swipe",
        "--udid",
        udid,
        _num(x1),
        _num(y1),
        _num(x2),
        _num(y2),
        "--duration",
        "0.2",
    ]


def text_cmd(udid: str, text: str) -> list[str]:
    """The `idb` argv that types text into the focused field."""
    return ["idb", "ui", "text", "--udid", udid, text]


def screenshot_cmd(udid: str, path: str) -> list[str]:
    """The argv that writes a Simulator screenshot to path (via simctl)."""
    # idb's own frame capture is unreliable ("No Image available to encode"),
    # so screenshot via simctl, which is always available on the Simulator.
    return ["xcrun", "simctl", "io", udid, "screenshot", path]


def _num(v: float) -> str:
    return str(round(v))  # idb ui tap/swipe require integer coordinates


# --- describe-all parsing ---


def _norm_type(t: str) -> str:
    t = t.removeprefix("AX")
    return t[:1].lower() + t[1:] if t else t


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _traits(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    type_ = item.get("type") or item.get("role")
    if isinstance(type_, str) and type_:
        out.append(_norm_type(type_))
    if item.get("enabled") is False:
        out.append(base.Trait.NOT_ENABLED)
    if item.get("selected") is True:
        out.append(base.Trait.SELECTED)
    return out


def _frame(item: dict[str, Any]) -> base.Frame:
    f = item.get("frame") or {}
    return (
        float(f.get("x", 0)),
        float(f.get("y", 0)),
        float(f.get("width", 0)),
        float(f.get("height", 0)),
    )


def _to_element(item: dict[str, Any]) -> base.Element:
    label = item.get("AXLabel") if item.get("AXLabel") is not None else item.get("label")
    value = item.get("AXValue") if item.get("AXValue") is not None else item.get("value")
    return {
        "identifier": _str_or_none(item.get("AXUniqueId")),
        "label": _str_or_none(label),
        "value": _str_or_none(value),
        "traits": _traits(item),
        "frame": _frame(item),
    }


def parse_describe_all(text: str) -> list[base.Element]:
    """Parse describe-all output (a JSON array, or newline-delimited JSON objects)."""
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [_to_element(json.loads(line)) for line in text.splitlines() if line.strip()]
    items = data if isinstance(data, list) else [data]
    return [_to_element(it) for it in items if isinstance(it, dict)]


class IdbDriver:
    """Driver implementation for the iOS Simulator via idb."""

    name = "idb"

    # During a SwiftUI screen transition idb intermittently returns a near-empty
    # accessibility tree (observed: a single element with no identifier) even though
    # the screen has visually rendered. These bound a short retry so query() rides
    # over that transient without masking a genuinely sparse screen for long.
    _READY_MIN = 2  # a tree this size or larger is treated as settled
    _EMPTY_RETRIES = 5  # extra describe-all attempts on a degenerate tree
    _EMPTY_BACKOFF_S = 0.05  # base delay; doubles each attempt up to the cap
    _EMPTY_BACKOFF_MAX_S = 0.2  # cap on a single backoff (total added <= ~0.75s, bounded)

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        self.udid = udid
        self._run = run
        self._max_seen = 0  # richest tree seen on this device; gates the empty retry

    def query(self) -> list[base.Element]:
        """describe-all, parsed and normalized into Elements.

        idb sometimes returns a near-empty tree mid-transition; once a richer tree
        has been seen on this device, a degenerate result is retried a bounded number
        of times so a single-shot assertion or wait does not act on the transient
        snapshot. A screen that has only ever been sparse (max seen < _READY_MIN) is
        returned as-is, so a genuinely small screen is never masked.
        """
        els = self._describe()
        for i in range(self._EMPTY_RETRIES):
            if not self._is_transient_empty(els):
                break
            time.sleep(self._empty_backoff(i))
            els = self._describe()
        self._max_seen = max(self._max_seen, len(els))
        return els

    def _empty_backoff(self, attempt: int) -> float:
        """Exponential backoff for the transient-empty retry: base * 2**attempt, capped.

        Recovers fast when the empty clears on the first retry and spaces out later, while
        the cap keeps the total added wait within the previous fixed bound.
        """
        return min(float(self._EMPTY_BACKOFF_S * (2**attempt)), self._EMPTY_BACKOFF_MAX_S)

    def _describe(self) -> list[base.Element]:
        return parse_describe_all(self._run(describe_all_cmd(self.udid)))

    def _is_transient_empty(self, els: list[base.Element]) -> bool:
        """Whether a result looks like idb's mid-transition empty tree rather than a real screen.

        Fewer than _READY_MIN elements, but only once a richer tree has been observed
        (so the first sparse screen seen is taken at face value).
        """
        return len(els) < self._READY_MIN and self._max_seen >= self._READY_MIN

    def _resolve(self, sel: base.Selector, timeout: float = 3.0, poll: float = 0.2) -> base.Element:
        # Real-device trees can be transiently empty during transitions; retry
        # not-found while keeping ambiguity fail-fast.
        deadline = time.monotonic() + timeout
        while True:
            try:
                return base.resolve_unique(self.query(), sel)
            except base.ElementNotFound:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll)

    def _center(self, sel: base.Selector) -> base.Point:
        el = self._resolve(sel)
        x, y, w, h = el["frame"]
        return (x + w / 2, y + h / 2)

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._run(tap_cmd(self.udid, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        # idb has no double-tap; two quick taps at the same point register as one.
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))
        self._run(tap_cmd(self.udid, x, y))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction(
            "pinch は multiTouch が必要; idb は単一タッチ（HID に複数指イベントがない）"
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction(
            "rotate は multiTouch が必要; idb は単一タッチ（HID に複数指イベントがない）"
        )

    def long_press(self, sel: base.Selector, duration: float) -> None:
        x, y = self._center(sel)
        self._run(
            ["idb", "ui", "tap", "--udid", self.udid, _num(x), _num(y), "--duration", str(duration)]
        )

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def type_text(self, text: str) -> None:
        self._run(text_cmd(self.udid, text))

    def wait_for(self, sel: base.Selector, timeout: float, poll: float = 0.2) -> bool:
        """Poll until at least one element matches `sel`, or `timeout` elapses.

        Returns whether the selector was found. Polls rather than checking once so the
        caller's timeout is honoured on a real device, where the element may render
        slightly after the call (mirroring the orchestrator's condition-wait discipline).
        """
        deadline = time.monotonic() + timeout
        while True:
            if len(base.find_all(self.query(), sel)) >= 1:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll)

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    # No semantic tap and no native network monitoring. Exposed as a class constant so the preflight
    # (BE-0082) can read it via `backends.capabilities_for` without constructing a driver.
    CAPABILITIES = frozenset(
        {base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.SCREENSHOT}
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

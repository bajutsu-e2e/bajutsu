"""idb backend (headless, coordinate-based).

Parses `idb ui describe-all` JSON into normalized Elements and acts via
`idb ui tap/text/swipe`. idb has no semantic tap, so a tap resolves the target's
frame center first. The JSON key names follow fb-idb's describe-all output and
should be validated against the installed idb version.
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
    return ["idb", "ui", "describe-all", "--udid", udid, "--json"]


def tap_cmd(udid: str, x: float, y: float) -> list[str]:
    return ["idb", "ui", "tap", "--udid", udid, _num(x), _num(y), "--duration", str(_TAP_DURATION_S)]


def swipe_cmd(udid: str, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    # A finite duration makes it a real drag; an instantaneous swipe isn't recognized
    # as a pan/drag gesture by SwiftUI.
    return ["idb", "ui", "swipe", "--udid", udid,
            _num(x1), _num(y1), _num(x2), _num(y2), "--duration", "0.2"]


def text_cmd(udid: str, text: str) -> list[str]:
    return ["idb", "ui", "text", "--udid", udid, text]


def screenshot_cmd(udid: str, path: str) -> list[str]:
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
    name = "idb"

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        self.udid = udid
        self._run = run

    def query(self) -> list[base.Element]:
        return parse_describe_all(self._run(describe_all_cmd(self.udid)))

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
        self._run(["idb", "ui", "tap", "--udid", self.udid, _num(x), _num(y), "--duration", str(duration)])

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def type_text(self, text: str) -> None:
        self._run(text_cmd(self.udid, text))

    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    def capabilities(self) -> set[str]:
        # No semantic tap and no native network monitoring.
        return {base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.SCREENSHOT}

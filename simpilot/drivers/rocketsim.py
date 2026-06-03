"""RocketSim backend (semantic tap; preferred actuator for stability).

RocketSim can tap by identifier/label directly (no coordinates), so it sits at the
top of the stability ladder. We still resolve uniqueness ourselves first, then
hand the identifier to RocketSim's semantic tap (coordinate fall-back if the
element has no id).

NOTE: RocketSim's CLI surface and its `rs/1` JSON schema are assumed here and must
be confirmed against the actual tool; the parser and command builders are the
likely points to adjust.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from simpilot.drivers import base

RunFn = Callable[[list[str]], str]


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- command builders (assumed; confirm against the real CLI) ---


def elements_cmd(udid: str) -> list[str]:
    return ["rocketsim", "elements", "--agent", "--udid", udid]


def tap_id_cmd(udid: str, identifier: str) -> list[str]:
    return ["rocketsim", "tap", "--udid", udid, "--id", identifier]


def tap_xy_cmd(udid: str, x: float, y: float) -> list[str]:
    return ["rocketsim", "tap", "--udid", udid, "--x", _num(x), "--y", _num(y)]


def swipe_cmd(udid: str, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    return ["rocketsim", "swipe", "--udid", udid, _num(x1), _num(y1), _num(x2), _num(y2)]


def type_cmd(udid: str, text: str) -> list[str]:
    return ["rocketsim", "type", "--udid", udid, text]


def screenshot_cmd(udid: str, path: str) -> list[str]:
    return ["rocketsim", "screenshot", "--udid", udid, path]


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


# --- elements parsing ---


def _frame(value: Any) -> base.Frame:
    if isinstance(value, list) and len(value) == 4:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    f = value or {}
    return (
        float(f.get("x", 0)),
        float(f.get("y", 0)),
        float(f.get("width", 0)),
        float(f.get("height", 0)),
    )


def _to_element(item: dict[str, Any]) -> base.Element:
    return {
        "identifier": item.get("identifier"),
        "label": item.get("label"),
        "value": item.get("value"),
        "traits": list(item.get("traits") or []),
        "frame": _frame(item.get("frame")),
    }


def parse_elements(text: str) -> list[base.Element]:
    """Parse RocketSim's agent elements output (an array, or { elements: [...] })."""
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    items = data.get("elements", []) if isinstance(data, dict) else data
    return [_to_element(it) for it in items if isinstance(it, dict)]


class RocketSimDriver:
    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        self.udid = udid
        self._run = run

    def query(self) -> list[base.Element]:
        return parse_elements(self._run(elements_cmd(self.udid)))

    def tap(self, sel: base.Selector) -> None:
        el = base.resolve_unique(self.query(), sel)
        identifier = el["identifier"]
        if identifier is not None:
            self._run(tap_id_cmd(self.udid, identifier))  # semantic tap (most stable)
        else:
            x, y, w, h = el["frame"]
            self._run(tap_xy_cmd(self.udid, x + w / 2, y + h / 2))

    def long_press(self, sel: base.Selector, duration: float) -> None:
        el = base.resolve_unique(self.query(), sel)
        x, y, w, h = el["frame"]
        self._run(["rocketsim", "longpress", "--udid", self.udid,
                   "--x", _num(x + w / 2), "--y", _num(y + h / 2), "--duration", str(duration)])

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def type_text(self, text: str) -> None:
        self._run(type_cmd(self.udid, text))

    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    def capabilities(self) -> set[str]:
        return {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.NETWORK,
            base.Capability.SCREENSHOT,
        }

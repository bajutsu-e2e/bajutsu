"""RocketSim backend (coordinate actuation over the real `rs/1` agent CLI).

RocketSim's agent protocol exposes role / label / value / frame and an *ephemeral*
element id — but **no accessibilityIdentifier** (verified on-device, 2026-06). So,
unlike idb (whose `describe-all` carries `AXUniqueId`), RocketSim cannot resolve
bajutsu's id-first selectors on its own. Two consequences shape this driver:

1. Identifiers are recovered by an `IdMap` applied in `query()` (role/label/value
   matchers per app; see `bajutsu/idmap.py`). The rest of the stack then resolves
   `{id: ...}` selectors exactly as it does for idb.
2. Actuation is by **frame-center coordinates** (`rocketsim interact tap <x> <y>`),
   not RocketSim's `--id` semantic tap (that `--id` means the ephemeral id, which
   is useless across snapshots). This puts RocketSim on the same rung as idb.

Real CLI shape (confirmed on-device): `rocketsim elements --agent-mode debug`,
`rocketsim interact tap|type|swipe|long-press`, `rocketsim screenshot`. A concrete
UDID is required (`booted` is a simctl-only alias; resolve it before constructing).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from bajutsu import idmap as idmap_mod
from bajutsu.drivers import base

RunFn = Callable[[list[str]], str]


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- command builders (confirmed against the real rocketsim CLI) ---


def elements_cmd(udid: str) -> list[str]:
    # debug mode carries frames + role + value (nav/act are label-only summaries).
    return ["rocketsim", "elements", "--agent-mode", "debug", "--udid", udid]


def tap_cmd(udid: str, x: float, y: float) -> list[str]:
    return ["rocketsim", "interact", "tap", _num(x), _num(y), "--udid", udid]


def swipe_cmd(udid: str, x1: float, y1: float, x2: float, y2: float) -> list[str]:
    return ["rocketsim", "interact", "swipe",
            "--from", f"{_num(x1)},{_num(y1)}", "--to", f"{_num(x2)},{_num(y2)}",
            "--duration", "0.2", "--udid", udid]


def longpress_cmd(udid: str, x: float, y: float, duration: float) -> list[str]:
    return ["rocketsim", "interact", "long-press", _num(x), _num(y),
            "--duration", str(duration), "--udid", udid]


def type_cmd(udid: str, text: str) -> list[str]:
    return ["rocketsim", "interact", "type", text, "--udid", udid]


def screenshot_cmd(udid: str, path: str) -> list[str]:
    # `rocketsim screenshot` writes a PNG to stdout; simctl writes straight to a
    # file and is always available, so reuse it (same choice as the idb backend).
    return ["xcrun", "simctl", "io", udid, "screenshot", path]


def _num(v: float) -> str:
    return str(round(v))  # interact coordinates are taken as integers


# --- elements parsing (rs/1 debug format) ---


def _frame(value: Any) -> base.Frame:
    # debug: [[x, y], [w, h]]; tolerate flat [x, y, w, h] and {x, y, width, height}.
    if isinstance(value, list) and len(value) == 2 and all(isinstance(p, list) for p in value):
        (x, y), (w, h) = value
        return (float(x), float(y), float(w), float(h))
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
    # No accessibilityIdentifier exists in the agent protocol; the IdMap fills it.
    role = item.get("role")
    return {
        "identifier": None,
        "label": item.get("label"),
        "value": item.get("value"),
        "traits": [role] if isinstance(role, str) and role else [],
        "frame": _frame(item.get("frame")),
    }


def parse_elements(text: str) -> list[base.Element]:
    """Parse `elements --agent-mode debug`: `{ data: { elements: [...] }, ok, rs }`.

    Also tolerates a bare `{ elements: [...] }` or a top-level array (for tests).
    """
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        container = data.get("data", data)
        items = container.get("elements", []) if isinstance(container, dict) else []
    else:
        items = data
    return [_to_element(it) for it in items if isinstance(it, dict)]


class RocketSimDriver:
    name = "rocketsim"

    def __init__(
        self, udid: str, run: RunFn = _real_run, idmap: idmap_mod.IdMap | None = None
    ) -> None:
        self.udid = udid
        self._run = run
        self._idmap = idmap or {}

    def query(self) -> list[base.Element]:
        els = parse_elements(self._run(elements_cmd(self.udid)))
        return idmap_mod.apply(els, self._idmap)

    def _center(self, sel: base.Selector) -> base.Point:
        x, y, w, h = base.resolve_unique(self.query(), sel)["frame"]
        return (x + w / 2, y + h / 2)

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._run(tap_cmd(self.udid, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._run(tap_cmd(self.udid, x, y))
        self._run(tap_cmd(self.udid, x, y))

    def long_press(self, sel: base.Selector, duration: float) -> None:
        x, y = self._center(sel)
        self._run(longpress_cmd(self.udid, x, y, duration))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        # The interact CLI is single-touch (tap/swipe/long-press only). Fail clearly
        # rather than approximate; pinch/rotate go through codegen -> XCUITest.
        raise base.UnsupportedAction("pinch は multiTouch 対応 backend が必要（rocketsim CLI は単一タッチ）")

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction("rotate は multiTouch 対応 backend が必要（rocketsim CLI は単一タッチ）")

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def type_text(self, text: str) -> None:
        self._run(type_cmd(self.udid, text))

    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    def capabilities(self) -> set[str]:
        # Coordinate actuation only: no semantic tap (no usable identifier), no
        # native condition wait (the run loop polls query()), no multi-touch.
        return {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
        }

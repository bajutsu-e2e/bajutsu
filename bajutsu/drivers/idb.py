"""idb backend (headless, coordinate-based).

Parses `idb ui describe-all` JSON into normalized Elements and acts via
`idb ui tap/text/swipe`. idb has no semantic tap, so a tap resolves the target's
frame center first. The JSON key names follow fb-idb's describe-all output,
validated on-device against fb-idb (iPhone 17 Pro, recent iOS); re-check them if
the installed idb version changes the schema.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any

from bajutsu import simctl
from bajutsu.drivers import base

RunFn = Callable[[list[str]], str]

_StableKey = tuple[tuple[str, base.Frame], ...]

# A short dwell on every tap. A zero-duration `idb ui tap` presses and releases in
# the same instant, which a UISwitch's gesture recognizer does not register — so a
# coordinate tap on a real Toggle never flips it. A brief hold actuates the switch
# while staying far below any long-press threshold, so plain buttons/rows behave
# identically. (Surfaced by the sample app's ctrl.toggle.)
_TAP_DURATION_S = 0.1
_UDID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validated_udid(udid: str) -> str:
    # `booted` (idb's "current device" alias) already satisfies _UDID_RE, so no special case.
    # Raise simctl.DeviceError (not a bare ValueError) so a bad --udid surfaces as the CLI's
    # clean exit-2 device fault, the same boundary adb's _checked_serial uses.
    v = udid.strip()
    if _UDID_RE.fullmatch(v):
        return v
    raise simctl.DeviceError(f"invalid udid: {udid!r}")


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- command builders ---


def describe_all_cmd(udid: str) -> list[str]:
    """The `idb` argv that dumps the accessibility tree as JSON."""
    return ["idb", "ui", "describe-all", "--udid", _validated_udid(udid), "--json"]


def tap_cmd(udid: str, x: float, y: float) -> list[str]:
    """The `idb` argv that taps the point (x, y)."""
    return [
        "idb",
        "ui",
        "tap",
        "--udid",
        _validated_udid(udid),
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
        _validated_udid(udid),
        _num(x1),
        _num(y1),
        _num(x2),
        _num(y2),
        "--duration",
        "0.2",
    ]


def _type_text_via_companion(udid: str, text: str) -> None:
    """Type `text` into the focused field over the fb-idb gRPC client (BE-0155).

    fb-idb's `idb ui text` takes the text as a required positional argument, so a secret
    or OTP typed through the CLI sits on the `idb` process's argv, where a co-tenant on the
    host could read it via `ps`/`/proc`. The CLI subcommand is only a thin wrapper around
    `client.text()`, so we call that directly instead: the value travels to `idb_companion`
    over gRPC and never lands on any command line.

    Runs its own event loop via `asyncio.run`: the idb driver is synchronous and is only
    ever called from threads with no running loop (the runner and the crawl workers).
    """
    import shutil

    companion_path = shutil.which("idb_companion")
    if companion_path is None:
        # Fail fast and legibly rather than letting a None path surface as an opaque
        # error deep inside fb-idb. idb_companion is a separate Homebrew formula. Checked
        # before the fb-idb import below so this stays meaningful even where the idb extra
        # isn't installed (e.g. the deterministic gate, which carries no backend deps).
        raise RuntimeError(
            "idb_companion not found on PATH — install it with "
            "`brew install facebook/fb/idb-companion`"
        )

    import asyncio
    import logging

    from idb.grpc.management import ClientManager

    async def _send() -> None:
        manager = ClientManager(
            companion_path=companion_path,
            logger=logging.getLogger("bajutsu.idb.companion"),
        )
        async with manager.from_udid(udid=udid) as client:
            await client.text(text=text)

    asyncio.run(_send())


def screenshot_cmd(udid: str, path: str) -> list[str]:
    """The argv that writes a Simulator screenshot to path (via simctl)."""
    # idb's own frame capture is unreliable ("No Image available to encode"),
    # so screenshot via simctl, which is always available on the Simulator.
    return ["xcrun", "simctl", "io", _validated_udid(udid), "screenshot", path]


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
    _SETTLE_MAX_POLLS = 3  # extra reads after initial comparison when frames are moving
    _SETTLE_POLL_S = 0.05  # interval between settle reads; describe-all provides natural spacing

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        # Validate once at the object boundary so every use of self.udid is covered — the argv
        # builders and the gRPC companion path (_type_text_via_companion) alike, plus any future
        # use site — without each having to remember to wrap it.
        self.udid = _validated_udid(udid)
        self._run = run
        self._max_seen = 0  # richest tree seen on this device; gates the empty retry
        self._last_stable_key: _StableKey | None = None

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
        self._last_stable_key = self._stable_key(els)
        return els

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection is unchanged, or give up.

        Compares (identifier, frame) only — ignoring volatile value, traits,
        label — so data changes on a static screen do not trigger extra polls.
        The first call (no cached key) returns immediately; a cached-match
        also returns in one query.  Only a cache miss starts the bounded poll.
        """
        prev_key = self._last_stable_key
        tree = self.query()
        key = self._last_stable_key
        if prev_key is None or key == prev_key:
            return tree
        for _ in range(self._SETTLE_MAX_POLLS):
            time.sleep(self._SETTLE_POLL_S)
            tree = self.query()
            new_key = self._last_stable_key
            if new_key == key:
                return tree
            key = new_key
        return tree

    @staticmethod
    def _stable_key(els: list[base.Element]) -> _StableKey:
        """Identifier-frame projection for settle: ignores volatile value/traits/label."""
        return tuple(sorted((e["identifier"] or "", e["frame"]) for e in els))

    def _empty_backoff(self, attempt: int) -> float:
        """Exponential backoff for the transient-empty retry: base * 2**attempt, capped.

        Recovers fast when the empty clears on the first retry and spaces out later, while
        the cap keeps the total added wait within the previous fixed bound.
        """
        # 2.0** (not 2**) keeps the result a float: mypy types int**int as Any (it is float
        # for a negative exponent), which would leak through min() as an Any return.
        return min(self._EMPTY_BACKOFF_S * 2.0**attempt, self._EMPTY_BACKOFF_MAX_S)

    def _describe(self) -> list[base.Element]:
        return parse_describe_all(self._run(describe_all_cmd(self.udid)))

    def _is_transient_empty(self, els: list[base.Element]) -> bool:
        """Whether a result looks like idb's mid-transition empty tree rather than a real screen.

        Fewer than _READY_MIN elements, but only once a richer tree has been observed
        (so the first sparse screen seen is taken at face value).
        """
        return len(els) < self._READY_MIN and self._max_seen >= self._READY_MIN

    def _resolve(
        self,
        sel: base.Selector,
        timeout: float = 3.0,
        poll: float = 0.2,
        *,
        initial_tree: list[base.Element] | None = None,
    ) -> base.Element:
        # Real-device trees can be transiently empty during transitions; retry
        # not-found while keeping ambiguity fail-fast.
        deadline = time.monotonic() + timeout
        tree = initial_tree if initial_tree is not None else self.query()
        while True:
            try:
                return base.resolve_unique(tree, sel)
            except base.ElementNotFound:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll)
                tree = self.query()

    def _center(self, sel: base.Selector) -> base.Point:
        tree = self._settle()
        el = self._resolve(sel, initial_tree=tree)
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
            [
                "idb",
                "ui",
                "tap",
                "--udid",
                _validated_udid(self.udid),
                _num(x),
                _num(y),
                "--duration",
                str(duration),
            ]
        )

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(swipe_cmd(self.udid, frm[0], frm[1], to[0], to[1]))

    def type_text(self, text: str) -> None:
        # Via _type_text (a patchable class attribute), so tests can intercept the value
        # without a companion, and so it never touches argv — see _type_text_via_companion.
        self._type_text(self.udid, text)

    _type_text = staticmethod(_type_text_via_companion)

    def wait_for(self, sel: base.Selector) -> bool:
        """Single-shot: whether `sel` matches the current screen (BE-0118).

        The deadline poll lives in the shared `base.wait_until`, so the timeout is honoured
        identically on every backend.
        """
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path))

    # No semantic tap and no native network monitoring. The whole device-control family
    # (`DEVICE_CONTROL_ALL`) because the iOS Simulator lifecycle wires a real simctl-backed
    # `DeviceControl` for idb runs (BE-0128; per-operation tokens since BE-0212). Exposed as a
    # class constant so the preflight (BE-0082) can read it via `backends.capabilities_for` without
    # constructing a driver.
    CAPABILITIES = (
        frozenset(
            {
                base.Capability.QUERY,
                base.Capability.ELEMENTS,
                base.Capability.SCREENSHOT,
            }
        )
        | base.DEVICE_CONTROL_ALL
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

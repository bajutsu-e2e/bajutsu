"""adb backend (headless, coordinate-based) — the architectural twin of idb.

Parses `uiautomator dump` XML into normalized Elements and acts via `adb shell input tap/swipe/text`.
adb has no semantic tap, so a tap resolves the target's frame center first — the same frame-center
round-trip idb performs. Like idb's near-empty tree during a SwiftUI transition, `uiautomator dump`
intermittently yields a null-root/empty result mid-transition, so this reuses idb's
*resolve-with-retry, fail-ambiguity-fast* discipline unchanged: retry a bounded number of times, and
still fail immediately on an ambiguous (2+) match rather than tapping whatever matched first.

The XML attribute names follow UI Automator's `uiautomator dump` schema; the selector mapping is
`resource-id` (id, package prefix stripped) → `identifier`, `text` → `label`, `content-desc` →
`value`, and the widget `class` (plus enabled/selected/checked state) → `traits`. The value channel
is `content-desc`, not `text`, because the showcase mirrors its assertion state value into
`content-desc` (SPEC §2.1: a `uiautomator dump` exposes `content-desc` but not Compose's
`stateDescription`), while `text` carries the visible label — the Android peer of iOS's
accessibilityLabel / accessibilityValue split. Tuned against the Android showcase on an emulator
(BE-0007 Unit 7): with `text` → `value` a `value` assertion read the visible string ("Matches: 5",
"Not favorited") instead of the mirrored value ("5", "off").
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from bajutsu import adb, intervals
from bajutsu.drivers import base

RunFn = Callable[[list[str]], str]

_StableKey = tuple[tuple[str, base.Frame], ...]

# uiautomator's bounds attribute, e.g. "[0,100][200,220]".
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _strip_pkg(resource_id: str) -> str | None:
    """The local id from a UI Automator resource-id: `com.app:id/foo` → `foo`.

    Native `android:id`s carry the `<package>:id/` prefix; a Compose `testTag` surfaced via
    `testTagsAsResourceId` has none, so it passes through verbatim (`stable.refresh`). Matching is
    exact on the local name — no `.`↔`_` normalization, which could conflate distinct ids and break
    determinism (the Views `stable.refresh`→`stable_refresh` case is left to a scenario variant).
    """
    # `or None` maps both an absent resource-id and a malformed one with no local name
    # (`com.app:id/`) to None, so the identifier is never an empty string that no selector matches.
    return resource_id.rsplit("/", 1)[-1] or None


def _norm_class(class_name: str) -> str:
    """Widget class to a trait token: `android.widget.Button` → `button` (idb's `_norm_type` shape)."""
    simple = class_name.rsplit(".", 1)[-1]
    return simple[:1].lower() + simple[1:] if simple else simple


def _bounds(raw: str) -> base.Frame:
    m = _BOUNDS.search(raw or "")
    if not m:
        return (0.0, 0.0, 0.0, 0.0)
    x1, y1, x2, y2 = (float(v) for v in m.groups())
    return (x1, y1, x2 - x1, y2 - y1)


def _traits(node: ET.Element) -> list[str]:
    out: list[str] = []
    cls = node.get("class") or ""
    if cls:
        out.append(_norm_class(cls))
    if node.get("enabled") == "false":
        out.append(base.Trait.NOT_ENABLED)
    # A UI Automator checkbox/switch reports its state as `checked`; a list selection as `selected`.
    if node.get("selected") == "true" or node.get("checked") == "true":
        out.append(base.Trait.SELECTED)
    return out


def _to_element(node: ET.Element) -> base.Element:
    desc = node.get("content-desc") or ""
    text = node.get("text") or ""
    return {
        # `text` is the visible label; `content-desc` is where the showcase mirrors the assertion
        # value (SPEC §2.1). `label` falls back to `content-desc` for an element that carries only a
        # content description (an icon-only control), so it is never left blank when one exists.
        "identifier": _strip_pkg(node.get("resource-id") or ""),
        "label": text or desc or None,
        "value": desc or None,
        "traits": _traits(node),
        "frame": _bounds(node.get("bounds") or ""),
    }


def parse_hierarchy(text: str) -> list[base.Element]:
    """Parse `uiautomator dump` output into Elements (empty on a null-root/garbled dump).

    `exec-out uiautomator dump /dev/tty` prints the `<hierarchy>` XML, sometimes wrapped in a status
    line ("UI hierarchy dumped to: …") or replaced by "null root node returned by
    UiTestAutomationBridge" mid-transition. The XML is sliced out by its tags so the surrounding
    chatter is ignored; a missing/unparseable tree yields `[]`, which the transient-empty retry
    rides over.
    """
    start = text.find("<hierarchy")
    end = text.rfind("</hierarchy>")
    if start == -1 or end == -1:
        return []
    try:
        # The dump is UI Automator's own output over our adb subprocess — a DTD/entity-free tree of
        # attribute-only <node>s — not attacker-supplied XML, so the stdlib parser is safe here.
        root = ET.fromstring(text[start : end + len("</hierarchy>")])  # noqa: S314
    except ET.ParseError:
        return []
    # Every `<node>` is an element; the `<hierarchy>` root itself is not a UI node.
    return [_to_element(n) for n in root.iter("node")]


class AdbDriver:
    """Driver implementation for the Android emulator via adb + UI Automator."""

    name = "adb"

    # `uiautomator dump` intermittently returns a null-root/empty tree mid-transition even though the
    # screen has rendered. These bound a short retry so query() rides over that transient without
    # masking a genuinely sparse screen for long — the exact analogue of idb's near-empty tree.
    _READY_MIN = 2  # a tree this size or larger is treated as settled
    _EMPTY_RETRIES = 5  # extra dump attempts on a degenerate tree
    _EMPTY_BACKOFF_S = 0.05  # base delay; doubles each attempt up to the cap
    _EMPTY_BACKOFF_MAX_S = 0.2  # cap on a single backoff (total added <= ~0.75s, bounded)
    _SETTLE_MAX_POLLS = 3  # extra reads after initial comparison when frames are moving
    _SETTLE_POLL_S = 0.05  # interval between settle reads

    def __init__(self, serial: str, run: RunFn = adb._real_run) -> None:
        self.serial = serial
        self._run = run
        self._max_seen = 0  # richest tree seen on this device; gates the empty retry
        self._last_stable_key: _StableKey | None = None

    def query(self) -> list[base.Element]:
        """Dump the UI Automator hierarchy, parsed and normalized into Elements.

        A null-root/empty dump mid-transition is retried a bounded number of times once a richer tree
        has been seen, so a single-shot assertion or wait does not act on the transient snapshot; a
        screen that has only ever been sparse is returned as-is (never masked).
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

    def _describe(self) -> list[base.Element]:
        return parse_hierarchy(self._run(adb.dump_cmd(self.serial)))

    def _is_transient_empty(self, els: list[base.Element]) -> bool:
        return len(els) < self._READY_MIN and self._max_seen >= self._READY_MIN

    def _empty_backoff(self, attempt: int) -> float:
        # 2.0** keeps the result a float (int**int types as Any under mypy, leaking through min()).
        return min(self._EMPTY_BACKOFF_S * 2.0**attempt, self._EMPTY_BACKOFF_MAX_S)

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection is unchanged, or give up (idb's logic).

        Compares (identifier, frame) only — ignoring volatile value/traits/label — so data changes on
        a static screen do not trigger extra polls. The first call (no cached key) returns
        immediately; only a cache miss starts the bounded poll.
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

    def _resolve(
        self,
        sel: base.Selector,
        timeout: float = 3.0,
        poll: float = 0.2,
        *,
        initial_tree: list[base.Element] | None = None,
    ) -> base.Element:
        # Retry not-found across a transient-empty tree while keeping ambiguity fail-fast.
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
        self._run(adb.tap_cmd(self.serial, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._run(adb.tap_cmd(self.serial, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        # No native double-tap; two quick taps at the same point.
        x, y = self._center(sel)
        self._run(adb.tap_cmd(self.serial, x, y))
        self._run(adb.tap_cmd(self.serial, x, y))

    def long_press(self, sel: base.Selector, duration: float) -> None:
        # `input` has no press-and-hold, so a zero-length swipe with a duration acts as a long press.
        x, y = self._center(sel)
        self._run(adb.swipe_cmd(self.serial, x, y, x, y, round(duration * 1000)))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1]))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction(
            "pinch は multiTouch が必要; adb `input` は単一タッチ（複数指ジェスチャがない）"
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction(
            "rotate は multiTouch が必要; adb `input` は単一タッチ（複数指ジェスチャがない）"
        )

    def type_text(self, text: str) -> None:
        # Feed the `input text` command to `adb shell` over stdin, not on the argv, so a secret / OTP
        # never lands in the adb process command line where `ps` could read it (BE-0155). Routed
        # through a class-level attribute so tests can patch it, mirroring idb's `_run_text`.
        self._run_text(adb.shell_cmd(self.serial), adb.text_script(text))

    @staticmethod
    def _run_text(cmd: list[str], script: str) -> None:
        subprocess.run(cmd, input=script, capture_output=True, text=True, check=True)

    def wait_for(self, sel: base.Selector) -> bool:
        """Single-shot: whether `sel` matches the current screen (BE-0118).

        The deadline poll lives in the shared `base.wait_until`, so the timeout is honoured
        identically on every backend.
        """
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        adb.Env(self.serial, run=self._run).screenshot(path)

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:
        """A whole-scenario interval recording via adb, or None for an unsupported kind.

        The device pool hands this to the `FileSink` so the same backend-independent `capture` policy
        that drives the simctl providers on iOS drives the adb ones here — Android is not `simctl`, so
        it routes through this driver-supplied seam rather than the sink's simctl path (idb, which has
        no such method, leaves the seam None and takes the simctl path). `video` records via
        `screenrecord` (pulled off the device on stop); `deviceLog` streams `logcat`. `appTrace` has
        no adb analogue, so it returns None.
        """
        if kind == "video":
            return intervals.start_screenrecord(self.serial, path, run=self._run)
        if kind == "deviceLog":
            return intervals.start_logcat(self.serial, path)
        return None

    # No semantic tap and no native network monitoring — the lean end of the capability model,
    # alongside idb. Of the device-control family it advertises only `setLocation` + `clipboard`,
    # the operations the emulator can honor (BE-0211); the per-operation tokens (BE-0212) let it
    # declare exactly that subset, so preflight admits those steps and fails the rest fast. A class
    # constant so the preflight (BE-0082) reads it via `backends.capabilities_for` with no device.
    CAPABILITIES = frozenset(
        {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
            base.Capability.DC_SET_LOCATION,
            base.Capability.DC_CLIPBOARD,
        }
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

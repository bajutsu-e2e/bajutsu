"""adb backend (headless, coordinate-based) — the architectural twin of idb.

Parses `uiautomator dump` XML into normalized Elements and acts via `adb shell input tap/swipe/text`.
adb has no semantic tap, so a tap resolves the target's frame center first — the same frame-center
round-trip idb performs. Like idb's near-empty tree during a SwiftUI transition, `uiautomator dump`
intermittently yields a null-root/empty result mid-transition, so this reuses idb's
*resolve-with-retry, fail-ambiguity-fast* discipline unchanged: retry a bounded number of times, and
still fail immediately on an ambiguous (2+) match rather than tapping whatever matched first.

The XML attribute names follow UI Automator's `uiautomator dump` schema; the selector mapping is
`resource-id` (id, package prefix stripped) → `identifier`, `text` → `label`, `content-desc` →
`value`, and the widget `class` (plus `clickable` and enabled/selected/checked state) → `traits`. The value channel
is `content-desc`, not `text`, because the showcase mirrors its assertion state value into
`content-desc` (SPEC §2.1: a `uiautomator dump` exposes `content-desc` but not Compose's
`stateDescription`), while `text` carries the visible label — the Android peer of iOS's
accessibilityLabel / accessibilityValue split. Tuned against the Android showcase on an emulator
(BE-0007 Unit 7): with `text` → `value` a `value` assertion read the visible string ("Matches: 5",
"Not favorited") instead of the mirrored value ("5", "off").

A `clickable` node also carries the `button` trait, and a clickable node with no own `text`/
`content-desc` derives its `label` from its descendants' text — so a Compose `NavigationBarItem`
(a clickable `android.view.View` whose caption lives in a child `TextView`) resolves the shared
cross-backend tab selector `{ label, traits: [button] }` (BE-0107), the same way iOS reaches a tab:
the adb driver catching up to that established contract (BE-0223). Here `button` means *tappable*
(the node responds to a tap), which is broader than idb's `button`, derived from the widget type
itself — so a bare `traits: [button]` matches any tappable row or container; pair it with a `label`
(as every shared scenario does) to address one control.
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
from bajutsu.elements import screen_size_from_elements

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


def _derived_label(node: ET.Element) -> str | None:
    """The accessible name of a labelless control, joined from its descendants' visible text.

    A Compose `NavigationBarItem` (and any icon-plus-caption control) dumps as a clickable node
    with no own `text`/`content-desc`; its visible caption lives in a child `TextView`. Mirroring
    how an accessibility service names a focusable container, the control's label is its
    descendants' text in document order — so a tab is addressable by its caption ("Log"), the same
    way the XCUITest backend exposes each tab as a label-bearing button (BE-0107).

    A nested clickable descendant is its own control (it independently gains the `button` trait and
    derives its own label), so its subtree is skipped rather than folded into this label — which
    also keeps two nested clickables from both deriving the same joined text (BE-0223).

    Only `text` is folded in, not `content-desc`: `content-desc` is this driver's *value* channel
    (SPEC §2.1 mirrors assertion state into it), so pulling it into the label would risk a mirrored
    value bleeding into the name. This is a deliberate limit — an icon-only caption carried solely
    in `content-desc` (no `TextView`) is not a showcase pattern, and would need the value/label
    split reconciled first.
    """
    parts: list[str] = []

    def collect(parent: ET.Element) -> None:
        for child in parent:
            if child.get("clickable") == "true":
                continue  # a separate control; its text belongs to its own element
            if text := child.get("text"):
                parts.append(text)
            collect(child)

    collect(node)
    return " ".join(parts) or None


def _traits(node: ET.Element) -> list[str]:
    out: list[str] = []
    cls = node.get("class") or ""
    if cls:
        out.append(_norm_class(cls))
    # A clickable node is tappable, so it carries the button trait — the shared cross-backend tab
    # selector `{ label, traits: [button] }` (BE-0107) resolves on adb because a Compose
    # NavigationBarItem dumps as a clickable `android.view.View`, whose class alone ("view") never
    # yields it (BE-0223). Note this `button` means "tappable", broader than idb's, which comes from
    # the widget type — so a bare `traits: [button]` matches any tappable node; pair it with a label.
    # Guarded so a widget already mapped to `button` by class (a Views Button) is not tagged twice.
    if node.get("clickable") == "true" and base.Trait.BUTTON not in out:
        out.append(base.Trait.BUTTON)
    if node.get("enabled") == "false":
        out.append(base.Trait.NOT_ENABLED)
    # A UI Automator checkbox/switch reports its state as `checked`; a list selection as `selected`.
    if node.get("selected") == "true" or node.get("checked") == "true":
        out.append(base.Trait.SELECTED)
    return out


def _to_element(node: ET.Element) -> base.Element:
    desc = node.get("content-desc") or ""
    text = node.get("text") or ""
    # `text` is the visible label; `content-desc` is where the showcase mirrors the assertion value
    # (SPEC §2.1). `label` falls back to `content-desc` for an element that carries only a content
    # description (an icon-only control). A clickable control with neither derives its label from
    # its descendants' text (BE-0223); derivation is scoped to clickable nodes so non-interactive
    # layout containers stay label-less rather than flooding the tree with synthetic labels.
    label: str | None = text or desc
    if not label and node.get("clickable") == "true":
        label = _derived_label(node)
    return {
        "identifier": _strip_pkg(node.get("resource-id") or ""),
        "label": label or None,
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
    # Scroll-into-view (BE-0210): an action target that resolves to nothing in the current viewport
    # is scrolled toward and re-queried a bounded number of times before failing — a condition wait,
    # not a fixed sleep. Direction is always upward (content up, revealing rows below) — the
    # acceptance scenarios all scroll down a list, so this covers the common case. A target above
    # the current viewport exhausts retries and fails deterministically; bidirectional scroll is a
    # follow-up when a scenario needs it.
    _RESOLVE_TIMEOUT_S = 3.0  # the initial no-scroll resolve deadline (rides transient trees)
    _SCROLL_RETRIES = 3  # scroll-and-re-query attempts before a deterministic not-found failure
    _SCROLL_FROM_FRAC = 0.7  # swipe start, as a fraction of screen height
    _SCROLL_TO_FRAC = 0.3  # swipe end (< start ⇒ upward ⇒ content scrolls up)

    def __init__(self, serial: str, run: RunFn = adb._real_run) -> None:
        self.serial = adb._checked_serial(serial)
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
        try:
            el = self._resolve(sel, timeout=self._RESOLVE_TIMEOUT_S, initial_tree=tree)
        except base.ElementNotFound:
            # Not in the current viewport — scroll toward it and re-query (BE-0210). An ambiguous
            # match still fails fast: only not-found triggers a scroll, so `resolve_unique`'s
            # AmbiguousSelector propagates unchanged. The settled tree seeds the first scroll so it
            # is oriented on stable frames rather than a fresh (possibly mid-transition) read.
            el = self._scroll_into_view(sel, tree)
        x, y, w, h = el["frame"]
        return (x + w / 2, y + h / 2)

    def _scroll_into_view(self, sel: base.Selector, tree: list[base.Element]) -> base.Element:
        """Scroll toward `sel` and re-query, bounded by `_SCROLL_RETRIES`, then fail deterministically.

        A condition wait, not a fixed sleep: each attempt swipes once (default up), then re-reads
        via `_settle` so the scroll's fling has stopped before the tree is resolved (a bare read
        right after the swipe can miss an element still sliding in, over-scrolling past it), and
        retries the unique resolve. A selector that never renders still raises ElementNotFound.
        """
        for _ in range(self._SCROLL_RETRIES):
            self._scroll_toward(tree)
            tree = self._settle()
            try:
                return base.resolve_unique(tree, sel)
            except base.ElementNotFound:
                continue
        raise base.ElementNotFound(f"一致なし（scroll しても見つからず）: {sel!r}")

    def _scroll_toward(self, tree: list[base.Element]) -> None:
        w, h = screen_size_from_elements(tree)
        if w <= 0 or h <= 0:
            # A degenerate/empty tree gives no screen extent to swipe across; a zero-length or
            # edge-column swipe would be a silent no-op that burns the retry budget and then fails
            # with a misleading "not found after scroll". Fail loudly with the real cause (BE-0210).
            raise base.ElementNotFound("scroll 不可（要素ツリーが空。UI Automator が要素を返さず）")
        cx = w / 2
        self.swipe((cx, h * self._SCROLL_FROM_FRAC), (cx, h * self._SCROLL_TO_FRAC))

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._run(adb.tap_cmd(self.serial, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._run(adb.tap_cmd(self.serial, p[0], p[1]))

    def double_tap(self, sel: base.Selector) -> None:
        # No native double-tap; both taps go through one `adb shell` round-trip so the adb transport
        # round-trip does not sit between them and overrun the double-tap window (BE-0210).
        x, y = self._center(sel)
        self._run(adb.double_tap_cmd(self.serial, x, y))

    def long_press(self, sel: base.Selector, duration: float) -> None:
        # `input` has no press-and-hold, so a zero-length swipe with a duration acts as a long press.
        x, y = self._center(sel)
        self._run(adb.swipe_cmd(self.serial, x, y, x, y, round(duration * 1000)))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1]))

    def back(self) -> None:
        # The true system back: a KEYCODE_BACK key event. Android has no on-screen "back" element to
        # tap (unlike iOS's OS back button), so this is a key event, not a coordinate — BE-0210.
        self._run(adb.keyevent_cmd(self.serial, adb.KEYCODE_BACK))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction(
            "pinch は multiTouch が必要; adb `input` は単一タッチ（複数指ジェスチャがない）"
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction(
            "rotate は multiTouch が必要; adb `input` は単一タッチ（複数指ジェスチャがない）"
        )

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; Android ネイティブに <select> はない"
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

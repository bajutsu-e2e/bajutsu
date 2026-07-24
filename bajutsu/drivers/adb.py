"""adb backend (headless, coordinate-based).

Parses `uiautomator dump` XML into normalized Elements and acts via `adb shell input tap/swipe/text`.
adb has no semantic tap, so a tap resolves the target's frame center first — a coordinate
round-trip. Like a device tree that goes near-empty during a screen transition, `uiautomator dump`
intermittently yields a null-root/empty result mid-transition, so this reuses the shared
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
(the node responds to a tap), which is broader than a `button` trait derived from the widget type
itself — so a bare `traits: [button]` matches any tappable row or container; pair it with a `label`
(as every shared scenario does) to address one control.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from bajutsu import adb
from bajutsu.drivers import base
from bajutsu.drivers.coordinate_tree import CoordinateTreeDriver
from bajutsu.elements import screen_size_from_elements
from bajutsu.evidence import intervals

RunFn = Callable[[list[str]], str]

# A resident UI Automator server (BE-0245) returns the hierarchy over an already-open channel,
# skipping the ~2.4 s per-invocation `uiautomator dump` startup. Its response is UI Automator's own
# XML, unchanged, so `parse_hierarchy` consumes it identically — only the transport differs, which is
# why a fetch is just "give me the current dump text": Callable[[], str].
HierarchyFetch = Callable[[], str]

logger = logging.getLogger("bajutsu.adb.resident")


class AdbResidentError(RuntimeError):
    """The resident hierarchy channel failed to answer a read.

    An infrastructure failure, kept distinct from a test outcome (like `XcuitestChannelError`): the
    driver catches it, logs loudly, and degrades to the `uiautomator dump` subprocess rather than
    reading a failed channel as an empty screen.
    """


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
    """Widget class to a trait token: `android.widget.Button` → `button`."""
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
    # yields it (BE-0223). Note this `button` means "tappable", broader than a `button` derived from
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


def slice_hierarchy_root(text: str) -> ET.Element | None:
    """Slice the `<hierarchy>` XML out of a UI Automator dump and parse its root, or `None`.

    UI Automator output — over the adb subprocess or the resident channel — can be wrapped in a
    status line ("UI hierarchy dumped to: …") or replaced by "null root node returned by
    UiTestAutomationBridge" mid-transition. The XML is located by its `<hierarchy>` tags so the
    surrounding chatter is ignored; a missing or unparseable tree yields `None`, letting each caller
    apply its own degrade (`parse_hierarchy` an empty list, the resident path the original text).
    """
    start = text.find("<hierarchy")
    end = text.rfind("</hierarchy>")
    if start == -1 or end == -1:
        return None
    try:
        # The dump is UI Automator's own output over our channel — a DTD/entity-free tree of
        # attribute-only <node>s — not attacker-supplied XML, so the stdlib parser is safe here.
        return ET.fromstring(text[start : end + len("</hierarchy>")])  # noqa: S314
    except ET.ParseError:
        return None


def parse_hierarchy(text: str) -> list[base.Element]:
    """Parse `uiautomator dump` output into Elements (empty on a null-root/garbled dump).

    `exec-out uiautomator dump /dev/tty` prints the `<hierarchy>` XML; a missing/unparseable tree
    yields `[]`, which the transient-empty retry rides over.
    """
    root = slice_hierarchy_root(text)
    if root is None:
        return []
    # Every `<node>` is an element; the `<hierarchy>` root itself is not a UI node.
    return [_to_element(n) for n in root.iter("node")]


class AdbDriver(CoordinateTreeDriver):
    """Driver implementation for the Android emulator via adb + UI Automator.

    The transient-empty retry, exponential backoff, stable-key projection, and not-found resolve loop
    live in `CoordinateTreeDriver` (the reusable coordinate-backend core); this class supplies adb's
    own describe (`uiautomator dump` / resident channel + XML), its wall-clock `_settle`, the
    scroll-into-view and `sendevent` paths, and its actuators.
    """

    name = "adb"

    # Settle is bounded by wall-clock, not a fixed read count (BE-0245). BE-0234 Unit 3 set a 3-poll
    # cap with `_SETTLE_POLL_S = 0` on the premise that the ~2.4s dump read itself paced the loop, so
    # three reads spanned ~7s — long enough for a fling to stop. The resident channel's ~0.1s read
    # (BE-0245) breaks that premise: three fast reads span a fraction of a second and a still-moving
    # tree passes as settled, so a tap fires on a stale coordinate. Bounding by elapsed time instead
    # keeps the settle window spanning a real animation whatever the read costs: the loop polls until
    # two consecutive reads share a frame projection, or `_SETTLE_DEADLINE_S` elapses. A stable screen
    # still settles in a single read (the first `query()` matches the cached key); only a genuinely-
    # animating screen polls, and `_SETTLE_POLL_S` is a small non-zero cadence so a fast read does not
    # busy-spin (on the dump path the read dwarfs it).
    # Set comfortably above the ~2.4s `uiautomator dump` read so the slow (fallback/dump) path still
    # gets several attempts inside the window — the deadline is checked before each read, so a value
    # near the read latency would grant only one extra poll and shrink the settle window below the
    # old 3-read/~7s span. A fast resident read (~0.1s) simply returns early on stability.
    _SETTLE_DEADLINE_S = 8.0  # ceiling on waiting for the tree to stop moving (spans a fling)
    _SETTLE_POLL_S = 0.1  # inter-read cadence on a fast channel; negligible against the dump read
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

    def __init__(
        self,
        serial: str,
        run: RunFn = adb._real_run,
        *,
        fetch_hierarchy: HierarchyFetch | None = None,
    ) -> None:
        super().__init__()
        self.serial = adb._checked_serial(serial)
        self._run = run
        # When set, reads go through the resident channel and fall back to `uiautomator dump` only on
        # failure (BE-0245). Unset (the default) keeps today's dump-every-read behavior exactly.
        self._fetch_hierarchy = fetch_hierarchy
        # Lazily resolved once for the sendevent double-tap path (BE-0208): whether adbd is root and
        # which node is the touchscreen. `_touch_probed` distinguishes "not yet looked" from "looked,
        # found nothing" so a device with no touchscreen is not re-probed on every double-tap.
        self._is_root: bool | None = None
        self._touch_dev: adb.TouchDevice | None = None
        self._touch_probed = False

    def _describe(self) -> list[base.Element]:
        return parse_hierarchy(self._read_source())

    def _read_source(self) -> str:
        """The raw hierarchy dump text: the resident channel when available, else `uiautomator dump`.

        Both sources speak UI Automator's own XML, so the caller (`parse_hierarchy`) is unchanged
        (BE-0245). A resident-channel failure degrades to the dump subprocess with a loud warning —
        never silently, so a slower fallback read stays visible — leaving the backend no worse off
        than the dump-every-read path it replaces. The failure latches: the channel is disabled after
        the first fault so the rest of the lease reads via dump without re-logging or re-paying the
        connect timeout on every read.
        """
        if self._fetch_hierarchy is not None:
            try:
                return self._fetch_hierarchy()
            except AdbResidentError as exc:
                logger.warning(
                    "resident hierarchy read failed (%s); falling back to `uiautomator dump` "
                    "for the rest of this lease",
                    exc,
                )
                self._fetch_hierarchy = None
        return self._run(adb.dump_cmd(self.serial))

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection stops changing, or give up.

        Compares (identifier, frame) only — ignoring volatile value/traits/label — so data changes on
        a static screen do not trigger extra polls. The first call (no cached key) returns
        immediately; only a cache miss starts the poll. The poll is bounded by a wall-clock deadline,
        not a fixed read count, so it spans a real animation whatever the read costs — the resident
        channel's fast read (BE-0245) would otherwise collapse the window and let a still-moving tree
        pass as settled.
        """
        prev_key = self._last_stable_key
        tree = self.query()
        key = self._last_stable_key
        if prev_key is None or key == prev_key:
            return tree
        deadline = time.monotonic() + self._SETTLE_DEADLINE_S
        while time.monotonic() < deadline:
            time.sleep(self._SETTLE_POLL_S)
            tree = self.query()
            new_key = self._last_stable_key
            if new_key == key:
                return tree
            key = new_key
        return tree

    def _center(self, sel: base.Selector) -> base.Point:
        point, _ = self._center_with_screen(sel)
        return point

    def _center_with_screen(self, sel: base.Selector) -> tuple[base.Point, base.Point]:
        """The target's frame center and the screen extent, both in tree (pixel) coordinates.

        The screen extent lets the sendevent double-tap scale a center into the touch device's raw
        range (BE-0208); it is constant across a scroll, so the settled tree gives it even when the
        target itself was only reached by scrolling.
        """
        frame, screen = self._resolve_frame_and_screen(sel)
        return base.frame_center(frame), screen

    def _resolve_frame_and_screen(self, sel: base.Selector) -> tuple[base.Frame, base.Point]:
        """The target's frame and the screen extent, both in tree (pixel) coordinates.

        Shared by the center-based actuators (tap / double-tap) and the two-finger gestures (BE-0232),
        which need the frame's size, not just its center.
        """
        tree = self._settle()
        try:
            el = self._resolve(sel, timeout=self._RESOLVE_TIMEOUT_S, initial_tree=tree)
        except base.ElementNotFound:
            # Not in the current viewport — scroll toward it and re-query (BE-0210). An ambiguous
            # match still fails fast: only not-found triggers a scroll, so `resolve_unique`'s
            # AmbiguousSelector propagates unchanged. The settled tree seeds the first scroll so it
            # is oriented on stable frames rather than a fresh (possibly mid-transition) read.
            el = self._scroll_into_view(sel, tree)
        return el["frame"], screen_size_from_elements(tree)

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
        # adb has no native double-tap. `input tap ; input tap` chains both taps in one round-trip,
        # but each `input` starts a JVM, so the gap still overruns the platform's double-tap window
        # (BE-0210). On a rooted device with a discoverable touchscreen, a raw `sendevent` sequence
        # closes that gap (BE-0208); otherwise fall back to `input tap`, so a non-rooted device is
        # never worse off than before.
        point, screen = self._center_with_screen(sel)
        dev = self._touch_device() if self._rooted() else None
        if dev is not None:
            raw_x, raw_y = adb.scale_to_touch(point, screen, dev)
            self._run(adb.sendevent_double_tap_cmd(self.serial, dev.path, raw_x, raw_y))
        else:
            self._run(adb.double_tap_cmd(self.serial, point[0], point[1]))

    def _rooted(self) -> bool:
        """Whether adbd runs as root (`id -u` is 0), cached — a precondition for `sendevent`."""
        if self._is_root is None:
            try:
                self._is_root = self._run(adb.id_u_cmd(self.serial)).strip() == "0"
            except (subprocess.CalledProcessError, OSError):
                self._is_root = False
        return self._is_root

    def _touch_device(self) -> adb.TouchDevice | None:
        """The touchscreen node from `getevent -lp`, probed once and cached (None if none / failure)."""
        if not self._touch_probed:
            self._touch_probed = True
            try:
                self._touch_dev = adb.parse_touch_device(
                    self._run(adb.getevent_probe_cmd(self.serial))
                )
            except (subprocess.CalledProcessError, OSError):
                self._touch_dev = None
        return self._touch_dev

    def long_press(self, sel: base.Selector, duration: float) -> None:
        # `input` has no press-and-hold, so a zero-length swipe with a duration acts as a long press.
        x, y = self._center(sel)
        self._run(adb.swipe_cmd(self.serial, x, y, x, y, round(duration * 1000)))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._run(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1]))

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # An adb `input swipe` with a finite duration is a real drag, which scrolls, so a directional
        # scroll is just a swipe.
        self.swipe(frm, to)

    def back(self) -> None:
        # The true system back: a KEYCODE_BACK key event. Android has no on-screen "back" element to
        # tap (unlike iOS's OS back button), so this is a key event, not a coordinate — BE-0210.
        self._run(adb.keyevent_cmd(self.serial, adb.KEYCODE_BACK))

    def pinch(self, sel: base.Selector, scale: float) -> None:
        # Two contacts spread from / close to the target centre by `scale`, driven as a raw two-slot
        # `sendevent` sweep (BE-0232) — the machinery the double-tap established, one slot to two.
        self._two_finger_gesture(sel, "pinch", lambda c, half: adb.pinch_contacts(c, half, scale))

    def rotate(self, sel: base.Selector, radians: float) -> None:
        # Two contacts sweep a diameter of the target through `radians` about its centre (BE-0232).
        self._two_finger_gesture(
            sel, "rotate", lambda c, half: adb.rotate_contacts(c, half, radians)
        )

    def _two_finger_gesture(
        self,
        sel: base.Selector,
        action: str,
        contacts: Callable[
            [base.Point, float], tuple[tuple[base.Point, base.Point], tuple[base.Point, base.Point]]
        ],
    ) -> None:
        """Drive a two-finger gesture: resolve the target, then emit the raw two-slot sweep (BE-0232).

        A rooted device with a discoverable touchscreen is required. Unlike the double-tap there is no
        single-touch approximation of two fingers, so a missing precondition fails loudly with a clear
        `UnsupportedAction` naming the root requirement — never a degraded gesture that silently passes.
        """
        if not self._rooted():
            raise base.UnsupportedAction(
                f"{action} は rooted device が必要; 二本指ジェスチャに単一タッチの代替は無い"
                "（sendevent で /dev/input に書き込むため root が要る）"
            )
        dev = self._touch_device()
        if dev is None:
            raise base.UnsupportedAction(
                f"{action} 不可（touchscreen node が getevent に見つからず、二本指の接点を撃てない）"
            )
        frame, screen = self._resolve_frame_and_screen(sel)
        # gesture_anchor keeps both fingers (and a ~2x pinch-out) inside the target (BE-0251).
        cx, cy, half = base.gesture_anchor(frame)
        if half <= 0:
            # A zero-size frame collapses both contacts onto the centre — a zero-travel sequence the
            # platform reads as a tap, not a gesture, so the mirrored value never flips and the wait
            # times out with a misleading cause. Fail loudly with the real one, as `_scroll_toward`
            # does for a degenerate screen extent (BE-0232).
            raise base.UnsupportedAction(
                f"{action} 不可（対象の frame が退化しており二本指の接点を配置できない）: {sel!r}"
            )
        start, end = contacts((cx, cy), half)
        raw_start = (
            adb.scale_to_touch(start[0], screen, dev),
            adb.scale_to_touch(start[1], screen, dev),
        )
        raw_end = (adb.scale_to_touch(end[0], screen, dev), adb.scale_to_touch(end[1], screen, dev))
        self._run(adb.sendevent_gesture_cmd(self.serial, dev.path, raw_start, raw_end))

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; Android ネイティブに <select> はない"
        )

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # BE-0316 is iOS-only: Android surfaces a system permission dialog in the topmost-window
        # dump, so an ordinary `tap` already reaches it. Preflight rejects the step before any device
        # work (adb never advertises HANDLE_SYSTEM_ALERT); this is the mid-run backstop.
        raise base.UnsupportedAction(
            "handleSystemAlert は iOS 専用; Android のシステムダイアログは通常の tap で操作できる"
        )

    def type_text(self, text: str) -> None:
        # Feed the `input text` command to `adb shell` over stdin, not on the argv, so a secret / OTP
        # never lands in the adb process command line where `ps` could read it (BE-0155). Routed
        # through a class-level attribute so tests can patch it.
        self._run_text(adb.shell_cmd(self.serial), adb.text_script(text))

    @staticmethod
    def _run_text(cmd: list[str], script: str) -> None:
        subprocess.run(cmd, input=script, capture_output=True, text=True, check=True)

    def delete_text(self, count: int) -> None:
        # `count` backspaces (KEYCODE_DEL) in one `input keyevent` call. The orchestrator focuses the
        # field first, so the deletes land in it (BE-0265).
        self._run(adb.keyevents_cmd(self.serial, [adb.KEYCODE_DEL] * count))

    def select_all(self) -> None:
        # Ctrl+A selects the focused field's whole content (BE-0265).
        self._run(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_A]))

    def copy_selection(self) -> None:
        # Ctrl+C copies the active selection to the clipboard, read back by the `clipboard` assertion.
        self._run(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_C]))

    def screenshot(self, path: str) -> None:
        adb.Env(self.serial, run=self._run).screenshot(path)

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:
        """A whole-scenario interval recording via adb, or None for an unsupported kind.

        The device pool hands this to the `FileSink` so the same backend-independent `capture` policy
        that drives the simctl providers on iOS drives the adb ones here — Android is not `simctl`, so
        it routes through this driver-supplied seam rather than the sink's simctl path (the iOS backend,
        which has no such method, leaves the seam None and takes the simctl path). `video` records via
        `screenrecord` (pulled off the device on stop); `deviceLog` streams `logcat`. `appTrace` has
        no adb analogue, so it returns None.
        """
        if kind == "video":
            return intervals.start_screenrecord(self.serial, path, run=self._run)
        if kind == "deviceLog":
            return intervals.start_logcat(self.serial, path)
        return None

    # No semantic tap and no native network monitoring — the lean end of the capability model.
    # Of the device-control family it advertises only `setLocation` + `clipboard`:
    # `setLocation` over the emulator console (BE-0211), `clipboard` over an ordered `am broadcast`
    # to the app's in-app receiver (BajutsuAndroid, BE-0233); adb declares it because the backend can
    # drive it given a cooperating app. The
    # per-operation tokens (BE-0212) let it declare exactly that subset, so preflight admits those
    # steps and fails the rest fast. A class constant so the preflight (BE-0082) reads it via
    # `backends.capabilities_for` with no device. `multiTouch` is declared statically here too, so
    # `gestures_multitouch` is admitted on adb; the rooted-device precondition for the two-finger
    # `sendevent` sweep is enforced at actuation time (`_two_finger_gesture`), not in the set, so on a
    # non-rooted device the gesture step fails fast with a clear `UnsupportedAction` (BE-0232).
    # `network` is deliberately NOT declared here even though adb captures traffic (BE-0283): that
    # token means *native* driver observation (only Playwright has it), and `capability_preflight`
    # leaves `network` ungated precisely because the app-side collector satisfies it without a backend
    # advertising it — the same accommodation the iOS backend relies on. Declaring it would wrongly claim native
    # observation and is not needed for a `request` assertion to run on adb.
    CAPABILITIES = (
        frozenset(
            {
                base.Capability.QUERY,
                base.Capability.ELEMENTS,
                base.Capability.SCREENSHOT,
                base.Capability.MULTI_TOUCH,
                base.Capability.TEXT_SELECTION,
                base.Capability.DC_SET_LOCATION,
                base.Capability.DC_CLIPBOARD,
            }
        )
        | base.ANDROID_PERMISSION_CAPABILITIES
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

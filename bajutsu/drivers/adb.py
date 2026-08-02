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
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from bajutsu import adb
from bajutsu.drivers import base
from bajutsu.drivers.coordinate_tree import CoordinateTreeDriver, StableKey
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


# `wm size` prints "Physical size: WxH" and, when the display has been resized, an "Override size:
# WxH" that is the effective resolution — the override wins when present (BE-0326).
_WM_SIZE = re.compile(r"^\s*(Physical|Override)\s+size:\s*(\d+)x(\d+)\s*$", re.MULTILINE)


def _parse_wm_size(out: str) -> base.Point:
    """Parse `adb shell wm size` output to the display `(w, h)` in pixels.

    Prefers an Override size over the Physical size; fails loudly (determinism first) rather than
    guessing a viewport if the output carries neither.
    """
    physical: base.Point | None = None
    override: base.Point | None = None
    for label, w, h in _WM_SIZE.findall(out or ""):
        if label == "Override":
            override = (float(w), float(h))
        else:
            physical = (float(w), float(h))
    size = override or physical
    if size is None:
        raise ValueError(f"could not parse `wm size` output: {out!r}")
    return size


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


@dataclass
class _Catchup:
    """One pan's outstanding read-lag barrier: has the tree published the gesture yet?

    Android moves the content before it publishes the accessibility update naming the new frames, so a
    read taken in between describes the pre-pan screen. `AdbDriver._advance_catchup` folds each read
    into this state and closes the barrier once the tree has demonstrably caught up.
    """

    pre_key: StableKey  # the projection the screen had when the pan fired
    deadline: float  # wall-clock ceiling on waiting for the pan to show up
    key: StableKey | None  # the newest non-degenerate projection seen since
    since: float  # when `key` was first seen — the dwell is measured from here


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
    _SCROLL_DURATION_MS = 600  # the `scroll` pan duration: long enough to drag, not fling (BE-0326)
    # Ceiling on waiting for a read to catch up with a gesture that already moved the content. One
    # number for one phenomenon, shared by two consumers: `read_lag()` hands it to the `scroll` loop
    # (BE-0326), and `_await_catchup` spends it on the actuator path. Android publishes the
    # accessibility update *after* the gesture has applied, so a read taken in between describes the
    # pre-gesture screen — self-consistently, for over a second.
    #
    # Sized from two independent measurements on the CI emulator. From the `scroll` end-of-content
    # failure: of 14 steps whose first read looked unchanged, 12 showed the change on a re-read well
    # inside this budget, and six full repeats of the scroll conformance tests then passed. The
    # remaining 2 changed only once the gesture was re-issued, so they are not explained by read lag
    # alone — a longer budget would not have helped them, and whatever they are (a `_region_signature`
    # blind to motion behind an element taller than the viewport is the leading candidate) is tracked
    # separately. From `smoke (adb)`'s intermittent `gestures` failure: a `swipe` moved the Log form
    # 73px, and four consecutive reads spanning 1.2s past the gesture still reported the pre-swipe
    # frames, so `long_press` aimed 10px below the target's real bottom edge and pressed the gap.
    #
    # Generous on purpose, because it is only ever spent on a read that still matches the pre-gesture
    # screen: a read that already caught up costs nothing.
    _READ_LAG_S = 4.0
    # How long a changed projection must hold before it counts as caught up (see `_advance_catchup`).
    # Above the widest tear the failing run showed: its post-press read had `log.submit` republished
    # but every frame below it still pre-pan, and the next read 0.37s later was whole — so a dwell
    # under that could return the torn frames. Comfortably inside `_READ_LAG_S`, and paid only on a
    # read that was still describing the pre-pan screen.
    _CATCHUP_DWELL_S = 0.5

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
        # The true display size (BE-0326), resolved once via `wm size`; the resolution is fixed for a run.
        self._screen: base.Point | None = None
        # The outstanding read-lag barrier for a pan, if any (see `_advance_catchup`). Closed by the
        # first read that shows the pan and holds, so a run whose tree keeps up never waits.
        self._catchup: _Catchup | None = None
        # Whether the cached projection still describes the screen. False after anything actuates, so
        # a pan re-reads its catch-up baseline instead of inheriting a pre-actuation one
        # (`_pan_baseline`). Actuators clear it by routing through `_act`, and every read sets it,
        # rather than each actuator clearing it by hand: an actuator added later that reached for
        # `_run` directly would otherwise silently reintroduce a stale baseline. Read-only commands
        # (`screenshot`, `wm size`) stay on `_run`, so they neither clear nor re-set it.
        self._tree_current = False

    def _act(self, args: list[str]) -> str:
        """Issue an adb command that changes the screen, marking the cached projection stale.

        Every actuator goes through this rather than `_run` directly, so "did the screen move since the
        last read?" has one owner. `_pan_baseline` needs that answer: a pan whose catch-up baseline
        predates an actuation is worse than no baseline at all, because the first post-pan read moves
        off it and the barrier credits the pan as published. `test_every_actuator_invalidates_the_cached_tree`
        guards the set, so an actuator added later cannot quietly keep using `_run`.
        """
        self._tree_current = False
        return self._run(args)

    def _describe(self) -> list[base.Element]:
        return parse_hierarchy(self._read_source())

    def _read_source(self) -> str:
        """The raw hierarchy dump text: the resident channel when available, else `uiautomator dump`.

        Both sources speak UI Automator's own XML, so the caller (`parse_hierarchy`) is unchanged
        (BE-0245). A resident-channel failure degrades to the dump subprocess with a loud warning —
        never silently, so a slower fallback read stays visible — leaving the backend no worse off
        than the dump-every-read path it replaces. The failure latches: the channel is disabled after
        the first fault so the rest of the lease reads via dump without re-logging or re-paying the
        connect timeout on every read. The fetch itself tears the resident server down on that fault
        (`ResidentServer.start`), releasing the device's single UiAutomation session so the dump
        fallback is a clean degrade rather than one poisoned by a wedged-but-alive server.
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

    def _record_tree(self, els: list[base.Element]) -> list[base.Element]:
        els = super()._record_tree(els)
        self._tree_current = True  # this read describes the screen as it is now
        self._advance_catchup(els)
        return els

    def _pan_baseline(self) -> StableKey | None:
        """The projection to measure a pan's catch-up against: the screen as it stands right now.

        Read afresh when something has actuated since the last read. A baseline predating that
        actuation already differs from whatever the next read shows, so the first post-pan read would
        move off it, `_advance_catchup` would credit that as the pan being published, and the pan's own
        lag would go unwaited — the fix would silently not apply. The common paths cost nothing extra:
        a directional `swipe` and a `drag` resolve their endpoints from a `query()` in the same step,
        and `_scroll_toward` runs straight after a `_settle`. Only a pan reached with no fresh read
        (the coordinate form `swipe: { from, to }`, or one preceded by a screenshot capture) pays one.

        A pan still waiting to publish is drained first, because re-reading cannot rescue that case: the
        read itself would return the pre-pan screen, so the new baseline would predate the earlier pan
        and the earlier pan's publish would later be mistaken for this one's. Two pans back-to-back is
        not hypothetical — it is the shape of the scenario this fix targets, whose consecutive `swipe`
        steps resolve their endpoints through `query()` and never reach `_settle`, so nothing else
        drains the barrier between them. A single pan pays nothing: `_catchup` is None and
        `_await_catchup` returns at once.
        """
        self._await_catchup()
        if not self._tree_current:
            self.query()
        return self._last_stable_key

    def _arm_catchup(self, pre_key: StableKey | None) -> None:
        """Open a catch-up barrier for the pan that just fired, measured against `pre_key`.

        Called after the gesture returns, so the budget starts when the content actually stopped. With
        no projection to compare against there is nothing to detect, and nothing is armed.
        """
        if pre_key is not None:
            now = time.monotonic()
            self._catchup = _Catchup(pre_key, now + self._READ_LAG_S, pre_key, now)

    def _advance_catchup(self, els: list[base.Element]) -> None:
        """Fold one read into the pending pan's barrier, closing it once the tree has caught up.

        Runs on **every** read, not only the ones `_await_catchup` issues, so the reads the runner
        already takes between a pan and the next actuator — a `wait`, an `assert`, a post-step capture
        — close the barrier and a run whose tree keeps up waits for nothing.

        A read counts as caught up only once its projection differs from the pre-pan one *and* has
        held for `_CATCHUP_DWELL_S`. Differing alone is not enough, because the catch-up is not
        atomic: Android republishes node bounds one node at a time, so a read taken mid-catch-up is
        *torn* (some frames new, the rest still pre-pan). Closing the barrier on a torn read would
        hand the next actuator a partly-stale tree with no dwell left to ride the tear out, and
        `_settle`'s two-equal-reads poll would accept it — the same failure this fix exists to stop,
        reached by another door. Requiring the dwell is BE-0245's "bound by elapsed time, not read
        count" applied to the catch-up itself. A tear outlasting the dwell would still get through;
        the dwell is sized above the widest one observed, not proven impossible.

        A degenerate read is ignored outright: its projection differs from every real one, so crediting
        it would spend the barrier on a tree the read path is itself still retrying.
        """
        catchup = self._catchup
        if catchup is None or self._is_transient_empty(els):
            return
        key = self._last_stable_key
        now = time.monotonic()
        if key != catchup.key:
            catchup.key, catchup.since = key, now
        if key != catchup.pre_key and now - catchup.since >= self._CATCHUP_DWELL_S:
            self._catchup = None

    def _await_catchup(self) -> None:
        """Re-read until the pending actuation shows in the tree, or its lag budget is spent.

        The one thing the two-consecutive-equal-reads settle below cannot do on its own: a lagging
        Android tree is *self-consistently* lagging, so any number of reads agree with each other and
        agree on the pre-actuation frames. What separates a caught-up read from a stale one is
        `_advance_catchup`'s test, which this drives reads until; the wall-clock budget bounds the wait
        when an actuation legitimately changed no frame (a pan already at the end of the content, or a
        tap that only moved a mirrored value — BE-0332 arms the barrier for center-resolving taps too).
        """
        while (catchup := self._catchup) is not None:
            if time.monotonic() >= catchup.deadline:
                # Loudly, not silently: the actuator may be about to resolve a coordinate from a tree
                # that never published the last actuation, which is the failure whose bare `expect`
                # mismatch cost a full artifact investigation to explain. Both causes are named because
                # the driver cannot tell them apart, and the benign one is routine — an actuation that
                # moves no frame (a pan at the end of the content, or a tap that only changes a mirrored
                # value) never differs the projection, so the barrier can only end here. The message
                # names the actuator neutrally because BE-0332 arms this for taps, not only pans;
                # asserting the lag would send an investigator after a bug that never happened.
                logger.warning(
                    "read lag: the last gesture did not change the projection within %.1fs — either "
                    "the tree never published it, or it moved no frame (e.g. a pan already at the end "
                    "of the content, or a tap that changed only a mirrored value). Resolving from the "
                    "current screen",
                    self._READ_LAG_S,
                )
                self._catchup = None
                return
            time.sleep(self._SETTLE_POLL_S)
            self.query()  # `_advance_catchup` closes the barrier once the tree has caught up

    def _settle(self) -> list[base.Element]:
        """Wait until the tree's identifier-frame projection stops changing, or give up.

        Compares (identifier, frame) only — ignoring volatile value/traits/label — so data changes on
        a static screen do not trigger extra polls. The first call (no cached key) returns
        immediately; only a cache miss starts the poll. The poll is bounded by a wall-clock deadline,
        not a fixed read count, so it spans a real animation whatever the read costs — the resident
        channel's fast read (BE-0245) would otherwise collapse the window and let a still-moving tree
        pass as settled.

        A pending pan is waited out first (`_await_catchup`), then the stability poll runs as before.
        Both halves are needed: the first gets past a wholly pre-pan tree, and the second gets past
        the *torn* tree that the catch-up passes through — Android republishes node bounds one node at
        a time, so the read that first differs can still carry most of the old frames.
        """
        self._await_catchup()
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

    def _actuate_centered(self, args: list[str]) -> None:
        """Actuate a command whose target was just resolved, then open a read-lag barrier for it.

        A center-resolving tap can change the layout (open a menu, expand a row, advance a stepper),
        and Android publishes that update a beat after the actuation returns — so without a barrier the
        next actuator's `_settle` accepts the still-pre-tap tree and resolves against stale frames, the
        `gestures` long-press flake (BE-0332 Unit 2). The just-resolved tree is the baseline, so no
        extra read is paid; a tap that moves nothing visible spends the budget exactly as a pan at the
        end of the content does. `tap_point` is deliberately excluded: it resolves no selector, so it
        has no target-from-a-layout to postdate, and arming there would steal a following pan's fresh
        baseline (`_pan_baseline`).

        Precondition: the caller has just resolved its target through `_center*` → `_settle`, which
        both drained any outstanding prior barrier (`_await_catchup`) and populated `_last_stable_key`
        with the layout resolved against. That is why the baseline is `_last_stable_key` directly, not
        a fresh `_pan_baseline()` read — the resolve already paid for both. A center actuator wired here
        without that preceding resolve would leave `_last_stable_key` None and silently arm nothing.
        """
        pre_key = self._last_stable_key
        self._act(args)
        self._arm_catchup(pre_key)

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._actuate_centered(adb.tap_cmd(self.serial, x, y))

    def tap_point(self, p: base.Point) -> None:
        self._act(adb.tap_cmd(self.serial, p[0], p[1]))

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
            cmd = adb.sendevent_double_tap_cmd(self.serial, dev.path, raw_x, raw_y)
        else:
            cmd = adb.double_tap_cmd(self.serial, point[0], point[1])
        self._actuate_centered(cmd)

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
        self._actuate_centered(adb.swipe_cmd(self.serial, x, y, x, y, round(duration * 1000)))

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        pre_key = self._pan_baseline()
        self._act(adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1]))
        self._arm_catchup(pre_key)

    def viewport(self) -> base.Point:
        # The true display size in raw pixels (BE-0326). A lazy list (RecyclerView / LazyColumn) keeps
        # a few buffered rows either side of the viewport in the a11y tree, so
        # `screen_size_from_elements` overshoots the screen and the `scroll` stop condition would
        # misjudge an off-screen center as on-screen; `wm size` reports the real display. Cached: the
        # resolution is fixed for a run.
        if self._screen is None:
            self._screen = _parse_wm_size(self._run(adb.wm_size_cmd(self.serial)))
        return self._screen

    def read_lag(self) -> float:
        # How long a read may describe the screen as it was before the last gesture (BE-0326). Android
        # publishes the accessibility update *after* the scroll has moved the content, so a `query()`
        # taken in between returns the pre-scroll tree: on the CI emulator every step that looked
        # unchanged had in fact moved the screen's pixels. `waitForIdle` plus the resident channel's
        # two-identical-dumps barrier does not close that window (BE-0245) — both dumps can land before
        # the update and agree with each other — so the `scroll` loop is told to keep re-reading rather
        # than call the first unchanged read the end of content. Only ever spent on a region that looks
        # stopped, never on a step that landed.
        return self._READ_LAG_S

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A non-inertial pan (BE-0326): `input swipe` over a longer duration than the default drag
        # keeps the list moving with the finger and stopping when the gesture ends, so the scroll
        # leaves no fling momentum. A short swipe over the same distance flings — its post-lift
        # travel varies by device, which is exactly the non-determinism the `scroll` action removes.
        pre_key = self._pan_baseline()
        self._act(
            adb.swipe_cmd(self.serial, frm[0], frm[1], to[0], to[1], self._SCROLL_DURATION_MS)
        )
        self._arm_catchup(pre_key)

    def back(self) -> None:
        # The true system back: a KEYCODE_BACK key event. Android has no on-screen "back" element to
        # tap (unlike iOS's OS back button), so this is a key event, not a coordinate — BE-0210.
        self._act(adb.keyevent_cmd(self.serial, adb.KEYCODE_BACK))

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
        # A zoom or a rotation moves every frame on screen just as a pan does, so it carries the same
        # publish lag and takes the same catch-up barrier. `_resolve_frame_and_screen` above already
        # read the tree, so the baseline costs nothing here.
        pre_key = self._pan_baseline()
        self._act(adb.sendevent_gesture_cmd(self.serial, dev.path, raw_start, raw_end))
        self._arm_catchup(pre_key)

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

    def system_alert_labels(self) -> list[str]:
        # No SpringBoard on Android; the reactive guard's native path never runs here (BE-0315).
        return []

    def type_text(self, text: str) -> None:
        # Feed the `input text` command to `adb shell` over stdin, not on the argv, so a secret / OTP
        # never lands in the adb process command line where `ps` could read it (BE-0155). Routed
        # through a class-level attribute so tests can patch it.
        self._tree_current = False  # `_run_text` bypasses `_act`; see its docstring
        self._run_text(adb.shell_cmd(self.serial), adb.text_script(text))

    @staticmethod
    def _run_text(cmd: list[str], script: str) -> None:
        subprocess.run(cmd, input=script, capture_output=True, text=True, check=True)

    def delete_text(self, count: int) -> None:
        # `count` backspaces (KEYCODE_DEL) in one `input keyevent` call. The orchestrator focuses the
        # field first, so the deletes land in it (BE-0265).
        self._act(adb.keyevents_cmd(self.serial, [adb.KEYCODE_DEL] * count))

    def select_all(self) -> None:
        # Ctrl+A selects the focused field's whole content (BE-0265).
        self._act(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_A]))

    def copy_selection(self) -> None:
        # Ctrl+C copies the active selection to the clipboard, read back by the `clipboard` assertion.
        self._act(adb.keycombination_cmd(self.serial, [adb.KEYCODE_CTRL_LEFT, adb.KEYCODE_C]))

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

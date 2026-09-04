"""Driver abstraction — the linchpin shared by every backend, real or fake.

Frozen first because everything else depends on it:
- common types Point / Element / Selector
- the Driver Protocol (only the actuator performs actions)
- selector resolution (the determinism core): a single action requires a unique
  match, and an ambiguous match (2+) raises AmbiguousSelector to rule out
  nondeterminism structurally.
"""

from __future__ import annotations

import fnmatch
import functools
import math
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, runtime_checkable

if TYPE_CHECKING:
    from bajutsu.common.evidence.network import Collector


@functools.lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Cached re.compile — avoids recompiling the same pattern on every poll iteration."""
    return re.compile(pattern)


# The iOS navigation bar's OS-provided back button (accessibility identifier "BackButton"). iOS has
# no hardware/system back, so the iOS backend (XCUITest) navigates back by tapping it — a
# platform convention, not app-specific — so the id lives in one shared place (BE-0210).
OS_BACK_BUTTON = "BackButton"

# Coordinates in points: x, y.
Point = tuple[float, float]
# frame: x, y, w, h in points.
Frame = tuple[float, float, float, float]


class Capability:
    """Capability names returned by Driver.capabilities().

    Used to pick the actuator and resolve fallbacks. A backend with SEMANTIC_TAP
    actuates more stably (no coordinates involved).
    """

    QUERY = "query"
    SEMANTIC_TAP = "semanticTap"  # tap directly by id/label (no coordinates; most stable)
    CONDITION_WAIT = "conditionWait"  # native condition waiting
    NETWORK = "network"  # native network monitoring
    SCREENSHOT = "screenshot"
    ELEMENTS = "elements"
    MULTI_TOUCH = (
        "multiTouch"  # two-finger gestures (pinch / rotate); a single-touch backend lacks it
    )
    WEBVIEW = "webView"  # DOM query/tap inside an embedded WKWebView (BE-0037)
    SELECT_OPTION = "selectOption"  # set a native <select> by value; web only (BE-0191)
    # `select`/`copy` on the focused field (BE-0265). A backend that can select and copy natively
    # advertises this; a coordinate-only backend with no select-all handle does not and raises
    # UnsupportedAction — the same actuate-or-raise promise as MULTI_TOUCH (BE-0280). `delete` /
    # `clear` need no token: every backend actuates `delete_text` (a run of backspaces).
    TEXT_SELECTION = "textSelection"
    # Tap a button on an out-of-process iOS SpringBoard permission prompt by a native accessibility
    # query, deterministically (BE-0316). Only the resident-runner XCUITest backend advertises it:
    # SpringBoard alert access is an on-device XCUITest capability, not a simctl operation, so it is a
    # top-level token like MULTI_TOUCH rather than a `deviceControl.*` one. Android reaches a system
    # dialog through an ordinary `tap`, and the web backend has no OS-level prompt, so neither needs it.
    HANDLE_SYSTEM_ALERT = "handleSystemAlert"
    # Set a wheel-style picker (`UIPickerView`, a wheel-mode `UIDatePicker`) to an exact value
    # (BE-0356). Only the resident-runner XCUITest backend advertises it: a picker wheel is an
    # iOS control, and XCUITest's `adjust(toPickerWheelValue:)` is what makes landing on a named
    # row deterministic — the mirror image of SELECT_OPTION, which only the web backend can honor.
    PICKER_WHEEL = "pickerWheel"
    # Dismiss a blocking Apple TipKit tip — an in-app popover the framework, not the app, builds —
    # by its TipKit-internal dismiss region. Only the XCUITest backend advertises it: TipKit is an
    # iOS framework, and the identifier it exposes is the driver's knowledge to hold, not the
    # orchestrator's. Unlike HANDLE_SYSTEM_ALERT the tip is in-process, so this needs no runner route
    # — but it stays a token so an iOS-only identifier never reaches the backend-agnostic core.
    HANDLE_TIPKIT_TIP = "handleTipkitTip"
    # The `DeviceControl` family, one token per operation (BE-0212, split from the coarse
    # `deviceControl` of BE-0128). A backend advertises exactly the operations it can honor, so
    # preflight gates each device-control step on its own operation — the Android emulator backs
    # setLocation + clipboard but not the rest. Operations that always ship together share a token
    # (the clipboard read/write/clear trio; background/foreground; the status-bar override/clear pair).
    DC_SET_LOCATION = "deviceControl.setLocation"
    DC_CLIPBOARD = "deviceControl.clipboard"  # setClipboard / getClipboard / clearClipboard
    DC_PUSH = "deviceControl.push"
    DC_CLEAR_KEYCHAIN = "deviceControl.clearKeychain"
    DC_APP_LIFECYCLE = "deviceControl.appLifecycle"  # background / foreground
    DC_STATUS_BAR = "deviceControl.statusBar"  # overrideStatusBar / clearStatusBar
    # `permissions` (BE-0276) is gated per-service, not by one token: iOS and Android honor
    # different subsets of the shared vocabulary (iOS has no TCC service for `notifications`), so a
    # single `deviceControl.permissions` token could not tell preflight which services are actually
    # supported. See `permission_capability` / `PERMISSION_SERVICES` below.


# The permission vocabulary a scenario's `permissions` field may name (BE-0276); imported directly
# by `bajutsu.common.scenario.models.scenario.Scenario`'s `permissions` field validator rather than
# duplicated there, since the scenario models already depend on this module.
PERMISSION_SERVICES: tuple[str, ...] = (
    "location",
    "camera",
    "microphone",
    "contacts",
    "photos",
    "calendar",
    "notifications",
)


def permission_capability(service: str) -> str:
    """The per-service device-control token for a permission service (BE-0276).

    One token per vocabulary entry rather than a single `deviceControl.permissions` token, so a
    backend that honors only part of the vocabulary (iOS: everything but `notifications`) can
    advertise exactly that subset and preflight names the unsupported service individually.
    """
    return f"deviceControl.permissions.{service}"


# The whole `DeviceControl` family as a set of per-operation tokens (BE-0212). A backend that backs
# the entire family (xcuitest, via the iOS Simulator lifecycle) advertises this in one shot;
# one that backs a subset (Android) lists only its operations' tokens.
DEVICE_CONTROL_ALL = frozenset(
    {
        Capability.DC_SET_LOCATION,
        Capability.DC_CLIPBOARD,
        Capability.DC_PUSH,
        Capability.DC_CLEAR_KEYCHAIN,
        Capability.DC_APP_LIFECYCLE,
        Capability.DC_STATUS_BAR,
    }
)

# The permission services iOS's `simctl privacy` backs — every vocabulary entry but `notifications`
# (iOS notification authorization is not part of TCC — Transparency, Consent, and Control — the
# database `simctl privacy` drives). Provided by xcuitest, which wires a real
# simctl-backed `DeviceControl` via the iOS Simulator lifecycle (mirrors `DEVICE_CONTROL_ALL`).
IOS_PERMISSION_CAPABILITIES = frozenset(
    permission_capability(s) for s in PERMISSION_SERVICES if s != "notifications"
)

# The permission services Android's `pm grant`/`pm revoke` backs — the full vocabulary, including
# `notifications` (`POST_NOTIFICATIONS` is a runtime permission since API 33).
ANDROID_PERMISSION_CAPABILITIES = frozenset(permission_capability(s) for s in PERMISSION_SERVICES)


class Element(TypedDict):
    """A single on-screen element, normalized from a device backend's output."""

    identifier: str | None
    label: str | None
    traits: list[str]
    value: str | None
    frame: Frame
    # The element's real front-to-back position, measured by the app itself through the opt-in
    # app-side hook (BE-0355) — never derived from this list's own document order, which is only the
    # paint-order proxy `topmost_at_point` below already warns about. Diagnostic only: no selector
    # matches on it and no occlusion check reads it, so `is_tappable` / `topmost_at_point` /
    # XCUITest's `isHittable` are unaffected. `None` is an honest absence — a backend with no such
    # hook, or an app that has not opted in — rather than a wrong guess. UIKit screens (through
    # BajutsuKit's responder) and Android `View` screens in an opted-in app report a value; every
    # other backend, and every app that has not opted in, reports `None` (BE-0355).
    # `_collapse_identical_duplicates`'s content key deliberately omits it: two candidates that
    # agree on every other reported field still collapse, however far apart they measure.
    nativeZ: float | None


def native_z_from_json(value: object) -> float | None:
    """Read a persisted `nativeZ` back off JSON, degrading anything unrepresentable to `None`.

    The one rule every reader of a written `elements.json` or golden file shares, so a value that
    round-trips through evidence means the same thing as one straight off a driver. `nativeZ` is
    diagnostic only (BE-0355) and no assertion reads it, so a malformed value degrades to the same
    honest absence an uninstrumented app reports instead of failing a load that would otherwise
    succeed. `bool` is excluded deliberately: it is an `int` subclass, and `True` is not a position.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        z = float(value)
    except OverflowError:  # JSON holds an arbitrary-precision int; a float cannot
        return None
    # `json.loads` accepts the non-standard `NaN` / `Infinity` literals; neither is a position, and
    # a NaN compares false against every value including itself, so degrade both the same way.
    return z if math.isfinite(z) else None


class Trait:
    """Normalized accessibility traits.

    Drivers normalize at least the following to these common tokens. `NOT_ENABLED` and
    `SELECTED` back state assertions and `BUTTON` / `LINK` the `traits` selector and doctor
    check; `OTHER` instead backs `resolve_unique`'s ambiguity filtering (below).
    """

    BUTTON = "button"
    LINK = "link"
    NOT_ENABLED = "notEnabled"  # disabled state (enabled / disabled assertions)
    SELECTED = "selected"  # selected / toggled state (selected assertion)
    OTHER = "other"  # generic/unclassified element (e.g. iOS's catch-all XCUIElementTypeOther)
    # A field the platform itself marks secret, so redaction masks its value with no configuration
    # (BE-0331). The token is XCUITest's own type name because iOS already reported it; web and
    # Android map their own source onto it (`input[type=password]`, the node's `password` flag), so
    # one construct means the same thing on every backend. Pinned by the conformance suite.
    SECURE_TEXT_FIELD = "secureTextField"


class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND.

    The stable selector is `id` (non-localized, data-derived). `label` /
    `labelMatches` are auxiliary; `index` is a last resort (flaky).
    """

    # `id` / `idMatches` accept a single value or a list of candidates; a list matches an element
    # whose identifier equals (or glob-matches) *any* candidate — an OR (BE-0221). This lets one
    # shared scenario carry every platform's form of an id (`[stable.refresh, stable_refresh]`) so it
    # runs unchanged where the native id syntax differs (Android `android:id` can't hold `.`/`-`).
    # Ambiguity is unchanged: 2+ matching elements still fail fast in `resolve_unique`.
    id: str | list[str]  # exact accessibilityIdentifier (first choice)
    idMatches: str | list[str]  # glob pattern (assumes multiple matches, e.g. "*.submit")
    label: str  # exact accessibilityLabel (auxiliary / disambiguation only)
    labelMatches: str  # substring / regex over label
    traits: list[str]  # narrow by type (e.g. ["button"])
    value: str  # accessibility value match
    within: Selector  # scope to a parent (needs a hierarchical query; not implemented)
    index: int  # nth of multiple matches (last resort; flaky)


@dataclass(frozen=True)
class DrainedInterruptions:
    """One drain's worth of what the interruption monitor did: what it tapped, and what it declined.

    `declined` is the button lists of alerts the policy governed but no rule identified — a monitor
    that declines still has to answer XCUITest's alert (its own default button, unchanged), so this
    is not a second dismissal outcome, only a record of what the tap was never asked to be. Reported
    so the caller can fail the step/expect that met one, naming what was on screen, rather than
    letting the run continue as if nothing had answered on the scenario's behalf (BE-0406 Unit 2b).
    """

    tapped: list[str]
    declined: list[list[str]]


@runtime_checkable
class InterruptionPolicyTarget(Protocol):
    """A backend that answers an alert interrupting one of its *own* interactions, by our policy.

    A narrow opt-in, like `ViewportProvider` / `ActuationReporter`: a backend that does not implement
    it is simply never asked, and the run is otherwise unchanged. Only XCUITest needs it. XCUITest
    resolves an out-of-process alert that interrupts an interaction *before* it synthesizes that
    interaction, and with nothing installed it answers using the alert's own default button — the
    opposite of the least-destructive policy the guard applies, and invisible to the run's report.

    `set_interruption_policy` hands over the labels `AlertGuardConfig` has already resolved (a rule's
    identifying label set with the label it taps) and whether the guard governs this scenario at all,
    so the decision stays in the orchestrator and the backend only applies it. `drain_interruptions`
    takes back what it answered and what it declined, so a dismissal reaches the report as an
    `AlertEvent` and an undeclared interruption can fail the step/expect that met it, rather than
    either happening silently (BE-0406).
    """

    def set_interruption_policy(
        self, rules: Sequence[tuple[frozenset[str], str]], governs: bool
    ) -> None:
        """Hand the backend the buttons it may press on an interrupting alert."""
        ...

    def drain_interruptions(self) -> DrainedInterruptions:
        """What the backend answered and declined since the last call, oldest first in each."""
        ...


@runtime_checkable
class Driver(Protocol):
    """Common interface for every backend.

    Actions (tap/type/swipe/wait/query) are performed by the actuator only. On a
    backend without semantic tap (a coordinate-only backend), the abstraction resolves the frame
    center via query() / resolve_unique() and taps by coordinates.
    """

    # Backend identifier (e.g. "xcuitest", "fake"). Recorded in the run
    # manifest and shown in the report so a run says which actuator drove it.
    name: str

    def query(self) -> list[Element]: ...
    def tap(self, sel: Selector) -> None: ...
    # Whether `sel` resolves to exactly one element that is actually reachable at its own point —
    # not covered by another on-screen element, or refused by the platform's own hit-test — realized
    # the idiomatic way per backend (iOS: native `isHittable`; web: a `document.elementFromPoint`
    # hit-test; adb: a document-order geometric check, `topmost_at_point` below). A pure query: it
    # never actuates and never scrolls, so `tap` can call it once to guard the actuation and the
    # scroll-recovery loop (`scroll_until_tappable`) can call it again, repeatedly, with no side
    # effects. `resolve_unique`'s own selector-ambiguity contract is unchanged by this — an ambiguous
    # `sel` still raises `AmbiguousSelector` immediately rather than being folded into `False`.
    def is_tappable(self, sel: Selector) -> bool: ...
    def tap_point(self, p: Point) -> None: ...  # raw coordinate tap (system alerts, etc.)
    def double_tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    # A directional scroll gesture — reveal off-screen content by travelling `frm`→`to` (BE-0227).
    # Distinct from `swipe`, which is a raw pointer drag for its own sake (a canvas / map pan / drag
    # handle). Most backends realize a scroll with a real drag, so they delegate `scroll` to `swipe`;
    # the web backend cannot (a mouse drag does not scroll a page), so it dispatches a wheel event
    # (desktop) or a touch drag (touch context) instead. The `swipe` handler routes the directional
    # form here and the coordinate form to `swipe`, so the coordinate form stays a literal drag.
    #
    # The step is non-inertial (BE-0326): content travels with the gesture and stops where it ends,
    # leaving no momentum carry. A fling's post-lift travel depends on scroll physics and frame
    # rate, which makes a target land above the fold on a fast device and below it on a slow one —
    # the non-determinism the `scroll` action removes by re-querying after each bounded step. Web is
    # already non-inertial; adb pans with a long-duration `input swipe`; XCUITest holds the drag at
    # its end before lifting. The `scroll` action's bounded re-query loop relies on this contract,
    # pinned by the driver conformance suite (BE-0114).
    def scroll(self, frm: Point, to: Point) -> None: ...
    # Navigate back one level, each backend using its platform-correct primitive (BE-0210):
    # Android's system back key, iOS's on-screen OS back button, the browser's history.
    def back(self) -> None: ...
    # Two-finger gestures. scale > 1 zooms in, < 1 zooms out; radians > 0 rotates
    # clockwise. Only backends advertising MULTI_TOUCH support these.
    def pinch(self, sel: Selector, scale: float) -> None: ...
    def rotate(self, sel: Selector, radians: float) -> None: ...
    def type_text(self, text: str) -> None: ...
    # Text-editing primitives on the currently focused field (the orchestrator focuses it with a
    # `tap` first, the same contract `type_text` relies on) — BE-0265. `delete_text` removes `count`
    # characters from the end (backspace-equivalent); `select_all` selects the whole content;
    # `copy_selection` copies the active selection to the clipboard. A backend that can't select or
    # copy natively (a single-touch / coordinate-only backend) raises UnsupportedAction rather than
    # faking it, mirroring the multi-touch gestures — codegen→XCUITest is the iOS path.
    def delete_text(self, count: int) -> None: ...
    def select_all(self) -> None: ...
    def copy_selection(self) -> None: ...
    # Set a native `<select>` (resolved by `sel`) to the option whose value is `option`. Web-only:
    # a `<select>` has no native counterpart on iOS / Android, so those backends raise
    # UnsupportedAction (BE-0191).
    def select_option(self, sel: Selector, option: str) -> None: ...
    # Move the picker wheel resolved by `sel` to the row whose value is `value` (BE-0356). iOS-only:
    # a wheel exposes no addressable row, so only XCUITest's `adjust(toPickerWheelValue:)` can land
    # on one deterministically; a backend without the PICKER_WHEEL capability raises
    # UnsupportedAction. A value the wheel does not carry raises ElementNotFound rather than leaving
    # the wheel wherever it happened to stop.
    def set_picker_value(self, sel: Selector, value: str) -> None: ...
    # Tap a button on an out-of-process iOS SpringBoard permission prompt (BE-0316), resolving `sel`
    # (label-based only) against the alert's buttons within `timeout`. A backend without the
    # HANDLE_SYSTEM_ALERT capability raises UnsupportedAction; preflight (capability_preflight.py)
    # rejects the scenario before any device work, so this raise is only the mid-run backstop.
    def handle_system_alert(self, sel: Selector, timeout: float) -> None: ...
    # A single, non-blocking read of the SpringBoard alert's button labels — [] when no alert is up
    # (BE-0315). The reactive `systemAlertHandling` guard polls this to learn whether a prompt is showing
    # and which buttons it offers, then taps a policy-named one via `handle_system_alert`. It shares
    # the HANDLE_SYSTEM_ALERT capability (a backend without it returns []), so it never adds a route
    # of its own — the query is BE-0316's `/systemAlert/query`, read here without the tap.
    def system_alert_labels(self) -> list[str]: ...
    # Dismiss a blocking TipKit tip if one is up; True when one was dismissed, False when none was
    # showing. Which node identifies a tip is the driver's business — the caller gets a boolean, so
    # the orchestrator's guard stays free of any iOS-specific identifier. Gated on
    # HANDLE_TIPKIT_TIP: a backend without it returns False rather than raising, since both callers
    # (the post-failure retry and the mid-wait gate) run opportunistically, where "no tip here" and
    # "this backend has no tips" call for the same no-op. `tree`, when given, is a snapshot the
    # caller already holds: the mid-wait gate asks on every poll tick, so letting it answer "no tip"
    # off the poll's own tree keeps the common case free instead of doubling the wait's query load.
    def dismiss_blocking_tip(self, tree: list[Element] | None = None) -> bool: ...
    # Single-shot by contract (BE-0118): whether `sel` matches the *current* screen,
    # checked once. A backend never loops here — the shared `wait_until` owns the
    # deadline poll, so a caller's timeout means the same real seconds on every backend.
    def wait_for(self, sel: Selector) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...


@runtime_checkable
class EvidenceProvider(Protocol):
    """A read-only evidence source from a non-actuator backend (BE-0020).

    A multi-backend run keeps actuation on the one actuator and may consult another same-platform
    backend *read-only* to fill an evidence gap the actuator lacks (e.g. a backend with no native
    network capture, so a same-platform backend supplies it). The narrow surface — `capabilities` plus observation
    methods only, never `tap` / `type` / `swipe` / `wait` / `query` — makes "the fallback never
    actuates" a type-level fact rather than a convention.
    """

    name: str

    def capabilities(self) -> set[str]: ...
    def network_collector(self, mocks: list[object] | None = None) -> Collector: ...


@runtime_checkable
class ViewportProvider(Protocol):
    """A backend that can report its true viewport size, for the `scroll` stop condition (BE-0326).

    `scroll` stops when the target's frame center lands inside the viewport, so it needs the real
    viewport bounds — and the queried tree cannot supply them, because a lazy list keeps buffered
    off-screen rows in the tree (and the web tree keeps off-screen DOM nodes), so
    `screen_size_from_elements` overshoots the screen and would judge an off-screen center as
    on-screen. Each backend reports the real viewport its own way: Playwright via `window.innerWidth`
    / `window.innerHeight`, adb via `wm size`, XCUITest via the runner's app-window `frame`, and
    `FakeDriver` from its in-memory scrollable model. The handler falls back to
    `screen_size_from_elements` for any backend that does not implement this, so it stays a narrow
    opt-in rather than a `Driver` requirement.
    """

    # The viewport size `(w, h)`; its origin is the coordinate space's `(0, 0)`.
    def viewport(self) -> Point: ...


@runtime_checkable
class ReadLagProvider(Protocol):
    """A backend whose `query()` can describe the screen as it was *before* the last actuation.

    Most backends read synchronously enough that a fresh `query()` already reflects the action just
    performed, so `scroll` can treat one unchanged region as proof the content stopped. Android breaks
    that assumption: the gesture moves the list, and the accessibility update naming the new frames is
    published afterwards, so a read taken in between returns the pre-scroll tree even though the
    content has already moved. Left unhandled, that late tree becomes a spurious "end of content"
    failure.

    A backend that can lag reports the budget `scroll` should give a step's result before concluding
    the region really stopped. Not implementing this means "my reads do not lag", which keeps the
    end-of-content failure immediate — so the fast, synchronous backends (`FakeDriver`, Playwright,
    XCUITest) pay nothing and stay exactly as fail-fast as before. A narrow opt-in, like
    `ViewportProvider` above, rather than a `Driver` requirement.
    """

    # Seconds to wait for a read to catch up with the last actuation; only ever spent when the region
    # looks unchanged, and never on the path where the step visibly landed.
    def read_lag(self) -> float: ...


@runtime_checkable
class ReadOrderProvider(Protocol):
    """A backend that can tell whether its last `query()` postdates the last actuation (BE-0332 Unit 3).

    `ReadLagProvider` above bounds the wait for a lagging read with a wall-clock budget; this answers
    the ordering question directly. Android's resident reader stamps each read with the device-clock
    time of the most recent accessibility event it has seen, and the driver takes a device-clock mark
    before each gesture, so it knows the moment a read reflects device state *after* the action — no
    host-to-device clock skew, because both marks are the device's.

    No production caller reads this through the protocol today. The `extract` poll used to release
    early on a confirmed order, until that release was found to accept a stale value — the mark says
    an accessibility event postdates the gesture, not that the property being copied out has been
    republished — so `extract` now keeps its wall-clock budget unconditionally. The driver's own
    catch-up barrier is the remaining ordering consumer, and it reads the backend's device mark
    directly rather than through here. The protocol stays declared because the driver conformance
    suite (BE-0114) checks the marked-read contract against the real backend, and because narrowing
    the barrier to the reads that still need it is an open unit of the device-side actuation item — a
    live contract without a live caller, not a leftover. A backend that cannot answer simply does not
    implement it, and every poll keeps its wall-clock budget unchanged — the same narrow opt-in as
    `ReadLagProvider` and `ViewportProvider`.
    """

    # Whether a read has positively postdated the last actuation, confirmed by the backend's device
    # mark. True only on that confirmation, and reset by the next actuation — deliberately not merely
    # "nothing is pending", so an actuation the backend could not mark (no device event, a stale-tree
    # timeout, a channel without the mark) reads false.
    def read_postdates_actuation(self) -> bool: ...


@runtime_checkable
class SettledReadProvider(Protocol):
    """A backend whose reads need settling before a coordinate is resolved from one for actuation.

    Every selector-addressed actuator the adb driver owns — `tap`, `double_tap`, `long_press`,
    `pinch`, `rotate` — resolves its target through the driver's own settle, which waits out the
    catch-up barrier so the frame a touch aims at comes from a tree the device published after the
    previous gesture. A directional `swipe` and a `drag` cannot: their endpoints are computed above
    the driver (`_directional_endpoints`), which only has `query()` to work with, and the driver
    receives two coordinates that no longer name an element. One unbarriered read there is enough to
    anchor a pan on the previous screen's frames — the failure mode `ReadLagProvider` above describes,
    reached by the one door the barrier does not cover.

    A backend that needs the settle exposes it here, so the handler can ask for an actuation-grade
    read rather than a bare one. Not implementing this means "a single `query()` is already good
    enough to actuate from", which keeps the synchronous backends (`FakeDriver`, Playwright,
    XCUITest) on exactly the read they take today. A narrow opt-in, like the three protocols above,
    rather than a `Driver` requirement.
    """

    # A tree fit to resolve an actuation target from: settled, and past any pending read-lag barrier.
    def settled_query(self) -> list[Element]: ...


@dataclass(frozen=True)
class RawSource:
    """The device's own reply behind a backend's last `_describe()`, untouched by bajutsu's processing.

    `text` is the reply exactly as the device/runner answered it — before any structural transform a
    backend applies and before `Element` normalization — so a diagnosis can tell "the device's own dump
    already looked wrong" apart from "bajutsu's own processing changed it". `parsed_input` is the same
    read *after* a backend's own structural transform of it, when that transform actually changed
    something: adb's resident channel strips SystemUI decor windows (`narrow_to_active_window`) before
    handing the result to `parse_hierarchy`, so `parsed_input` is what the parser actually consumed.
    `None` when the backend applies no such transform (the dump-subprocess path, XCUITest) or the
    transform left `text` unchanged — `text` alone already describes what was parsed. `suffix` names the
    format `text` is actually written in (adb: `.xml`; XCUITest's `GET /elements` body is undecoded JSON,
    so it sets `.json`) — carried here rather than hardcoded by the writer, so a future
    `RawSourceProvider` with a different dump format needs no edit outside the backend that produces it.
    Required, not defaulted: a default of `.xml` would let a future backend construct
    `RawSource(text=body)` and silently mislabel a non-XML dump, the exact bug this field exists to
    prevent — every producer must state its own format, or mypy catches the omission at the call site.
    """

    text: str
    suffix: str
    parsed_input: str | None = None


@runtime_checkable
class RawSourceProvider(Protocol):
    """A backend that retains the raw dump behind its last parsed tree, for the `rawTree` capture kind.

    Every coordinate-tree backend's frame computation is normally a black box once parsed into
    `Element`s: diagnosing whether a mismatch between the screen and a resolved coordinate comes from
    the device's own dump or from bajutsu's parsing of it needs the dump itself, which `_describe()`
    otherwise discards as a local variable the moment it is parsed. A backend that keeps it exposes
    this seam so `bajutsu/evidence/core.py`'s `write_raw_tree` can persist it alongside `elements.json`
    — opt-in (a scenario's `capture: [rawTree, ...]`), never in the default capture list, since it adds
    a same-sized text artifact per captured step. `AdbDriver` and `XcuitestDriver` implement it (the raw
    UI Automator dump, the raw `GET /elements` body). Not implementing this means "no raw dump to
    persist", which keeps every other backend (`FakeDriver`, Playwright) exactly as before — the same
    narrow opt-in as the protocols above (BE-0351).
    """

    def last_raw_source(self) -> RawSource | None: ...


@runtime_checkable
class SettledCacheInvalidator(Protocol):
    """A backend whose settle-proof cache must be dropped by something outside its own actuators.

    `AdbDriver._settle()` caches a key proven stable so a later call can skip re-polling — but that
    proof describes a specific screen, and only the driver's own actuators (`_act`, `_device_act`,
    `type_text`) know to invalidate it when they change one. An app relaunch or a crawl reset
    replaces the screen through the platform's own launch/kill commands, never through this driver,
    so nothing would otherwise tell the cache its proof no longer applies — and if the new screen's
    projection happens to coincide with the stale one (unremarkable: many scenarios start and end on
    the same home screen), `_settle` would trust a single read of a screen it never actually proved
    at rest. A lifecycle path that replaces the screen outside the driver calls
    `invalidate_settled_cache()` to close that door too. Not implementing this means "no such cache
    to invalidate", which keeps every other backend (`FakeDriver`, Playwright, XCUITest) exactly as
    before — the same narrow opt-in as the protocols above (BE-0351).
    """

    def invalidate_settled_cache(self) -> None: ...


@runtime_checkable
class BackendLifecycle(Protocol):
    """The full set of lifecycle hooks backends run around a single run (BE-0141).

    A run launches, tears down, and resets a backend, but those steps are platform-shaped: the web
    (Playwright) backend navigates / closes / resets a browser context, the XCUITest backend waits
    for its on-device runner to answer (and probes its health once during a cold spawn, BE-0319), and
    the fake backend needs none of them. These hooks are therefore split disjointly across backends —
    no single driver implements the whole set — so this is a *typing umbrella* for the call sites, not
    a conformance target: the `platform_lifecycle` environments reach each hook through
    `cast(BackendLifecycle, driver)` under the platform invariant that already scopes the driver,
    which turns "the hook exists" into a mypy-checked fact (a renamed or dropped hook fails
    `make check` instead of at runtime) without forcing a lifecycle-free backend to stub no-op methods. `@runtime_checkable`
    mirrors `EvidenceProvider`, but a structural `isinstance` holds only for a class implementing the
    whole set — which the concrete drivers, owning disjoint subsets, deliberately do not.
    """

    def navigate(self) -> None: ...
    def close(self) -> None: ...
    def reset_context(self) -> None: ...
    def await_ready(self, timeout: float = 10.0, poll: float = 0.1) -> None: ...
    def health_ready(self) -> bool: ...


# --- Selector resolution (the determinism core) ---


class SelectorError(Exception):
    """Selector resolution failed."""


class UnsupportedAction(Exception):
    """The actuator backend cannot perform this action.

    For example, a multi-touch gesture on a single-touch backend. The tool surfaces it as a step
    failure with a clear reason rather than letting it pass silently.
    """


class ManualStepRequired(UnsupportedAction):
    """A recorded `manual` takeover step has no deterministic run-time equivalent (BE-0185).

    Raised at `run` time so a human-takeover marker (a CAPTCHA, a biometric prompt) fails loudly and
    visibly with its label rather than a silent pass or a hang — the honest boundary for an operation
    only a human can perform. A subclass of `UnsupportedAction` so the run loop surfaces it as a
    clean, labeled step failure like any other action the environment cannot perform.
    """


class ElementNotFound(SelectorError):
    """No candidate matched. A wait times out; an immediate action fails."""


class AmbiguousSelector(SelectorError):
    """2+ candidates with no way to disambiguate; needs `within` or `index`."""


class ElementNotTappable(Exception):
    """The selector resolved uniquely, but the element could not be reached at its own point.

    Obstructed by another on-screen element, or the platform's own hit-test refused it — even
    after the bounded scroll safety net tried to clear the obstruction. Distinct from
    `SelectorError`: resolution succeeded. Only reachability failed.
    """


class BackendCrashError(RuntimeError):
    """The backend's driver process crashed mid-scenario and could not be recovered in place.

    Distinct from a test outcome and from a transient blip a driver's own retry absorbs: it names an
    honest "the backend died" — the resident XCUITest runner's XCTest host, an adb server, a browser
    process — where the crash outlived the driver's in-place recovery budget, so the current
    scenario's state is gone. The run pipeline treats it as backend infrastructure, not a verdict
    (prime directive 1): it discards the dead lease, leases a fresh device (a cold respawn), and
    re-runs the whole scenario from the start, bounded — a genuinely crash-inducing app still fails
    loudly once the retries are spent, so flakiness is never absorbed into a pass (BE-0049). Backends
    raise a subclass (e.g. `XcuitestRunnerCrashError`); the pipeline catches this base so the recovery
    stays backend-agnostic (prime directive 3).
    """


def id_candidates(v: str | list[str]) -> list[str]:
    """A single id/pattern or a list of OR candidates, normalized to a list (BE-0221)."""
    return [v] if isinstance(v, str) else v


def validate_id_candidates(field: str, value: str | list[str] | None) -> None:
    """Reject a malformed `id` / `idMatches` OR-candidate list; a no-op for a string or None (BE-0221).

    Shared by the scenario `Selector` model and config's `readyWhen` (a `base.Selector`) so a
    candidate list is checked the same way wherever it is authored. A list must be non-empty with no
    blank entry, and if it contains any dotted (SPEC-form) candidate, that candidate must lead:
    single-id consumers — the resolver's representative pick, `audit` coverage bucketing
    (`namespace_of` splits on `.`), the XCUITest / Playwright codegen emitters — take candidate[0], so
    a dotted-but-not-first list resolves fine at runtime but silently skews them. Failing at load
    beats debugging a skewed report. An all-underscore list (no dotted candidate) is accepted as-is.

    Raises:
        ValueError: the list is empty / has a blank entry, or a dotted candidate follows a
            non-dotted first one.
    """
    if not isinstance(value, list):
        return
    if not (value and all(c for c in value)):
        raise ValueError(f"{field} list must hold non-empty candidates (§5)")
    if "." not in value[0] and any("." in c for c in value[1:]):
        raise ValueError(f"{field} list must put the canonical (dotted) id first: {value!r} (§5)")


def matches(el: Element, sel: Selector) -> bool:
    """Whether an element satisfies a selector's per-element conditions (all AND-ed).

    Args:
        el: One element from a `query()` snapshot.
        sel: The selector to test. Only the per-element fields are checked here
            (`id` / `idMatches` / `label` / `labelMatches` / `traits` / `value`); `within` (a
            cross-element spatial constraint, resolved by `find_all`) and `index` (a positional
            pick among matches, applied by `resolve_unique`) are ignored. `id` / `idMatches` may be a
            list of candidates, satisfied when the element matches *any* one (BE-0221).

    Returns:
        True when every per-element field set on the selector matches the element.
    """
    ident = el["identifier"]
    if "id" in sel and ident not in id_candidates(sel["id"]):
        return False
    if "idMatches" in sel and not (
        ident is not None
        and any(fnmatch.fnmatchcase(ident, p) for p in id_candidates(sel["idMatches"]))
    ):
        return False
    if "label" in sel and el["label"] != sel["label"]:
        return False
    if "labelMatches" in sel and not (
        el["label"] is not None and _compile(sel["labelMatches"]).search(el["label"]) is not None
    ):
        return False
    if "traits" in sel and not set(sel["traits"]).issubset(el["traits"]):
        return False
    return not ("value" in sel and el["value"] != sel["value"])


# Single-entry cache: (list_id, list_ref, index_dict).
# Holding list_ref prevents GC so id() stays stable across lookups.
_cached_index: tuple[int, list[Element], dict[str | None, list[Element]]] | None = None


def _id_index(elements: list[Element]) -> dict[str | None, list[Element]]:
    """Build (or return cached) identifier -> elements index for a given list.

    The cache holds one entry keyed by ``id(elements)``; a new list auto-invalidates it.
    Multiple ``find_all`` calls on the same query() result (e.g. a multi-assertion step)
    share a single O(n) build and then do O(1) lookups.
    """
    global _cached_index  # noqa: PLW0603  # the single-entry memo is module state by design
    if _cached_index is not None and _cached_index[0] == id(elements):
        return _cached_index[2]
    idx: dict[str | None, list[Element]] = {}
    for el in elements:
        idx.setdefault(el["identifier"], []).append(el)
    _cached_index = (id(elements), elements, idx)
    return idx


def contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame is spatially contained in `outer`'s (edges inclusive)."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh


def find_all(elements: list[Element], sel: Selector) -> list[Element]:
    """Every element matching the selector — backs `idMatches` resolution and `count` assertions.

    Args:
        elements: One `query()` snapshot.
        sel: The selector to match. `within` scopes the result to elements spatially contained in
            a container the `within` selector resolves to: the accessibility tree is flat, so
            "parent" is geometric — a candidate qualifies when its frame sits inside a container's,
            and `within` may nest.

    Returns:
        The matching elements, in `elements` order.
    """
    base_sel = cast(Selector, {k: v for k, v in sel.items() if k != "within"})
    # Fast path: an id-only selector that resolves to a *single* id (a bare string or a one-element
    # candidate list) uses the cached index for O(1) lookup. A multi-candidate list (BE-0221) takes
    # the general scan, which matches in `elements` order across all candidates.
    single_id = (
        id_candidates(base_sel["id"])[0]
        if set(base_sel.keys()) == {"id"} and len(id_candidates(base_sel["id"])) == 1
        else None
    )
    if single_id is not None:
        found = list(_id_index(elements).get(single_id, []))
    else:
        found = [el for el in elements if matches(el, base_sel)]
    if "within" in sel:
        scopes = [parent["frame"] for parent in find_all(elements, sel["within"])]
        found = [el for el in found if any(contains(scope, el["frame"]) for scope in scopes)]
    return found


def deadline_ticks(
    timeout: float, poll_init: float, poll_max: float | None = None
) -> Iterator[None]:
    """Yield once per poll to a monotonic deadline, sleeping with capped backoff between ticks.

    The one deadline/backoff skeleton the condition waits share (BE-0118, BE-0256): `wait_until`
    here and the platform-lifecycle readiness waits (`await_ready` / `await_boot`) each run their
    own check body on every yield and decide what to return, while this owns only the monotonic
    deadline, the exponential backoff (`poll_init` doubling up to `poll_max`), and the
    never-sleep-past-the-deadline sleep — a condition wait with no fixed up-front sleep, so a
    `timeout` means the same real seconds regardless of the caller. A fixed interval is
    `poll_max is None` (or equal to `poll_init`); the first yield fires before any sleep.

    Args:
        timeout: Seconds from the first tick before the deadline passes.
        poll_init: The first inter-tick sleep, doubling each tick.
        poll_max: The backoff ceiling; a fixed `poll_init` interval when omitted.
    """
    ceiling = poll_init if poll_max is None else poll_max
    deadline = time.monotonic() + timeout
    poll = min(poll_init, ceiling)
    while True:
        yield
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))  # never sleep past the deadline
        poll = min(poll * 2, ceiling)


def wait_until(driver: Driver, sel: Selector, timeout: float, poll: float = 0.2) -> bool:
    """Poll `driver.wait_for(sel)` against a monotonic deadline until it matches.

    A condition wait with no fixed sleep, mirroring the orchestrator's discipline — it turns the
    backend's single-shot `wait_for` into a timeout-honouring wait over `deadline_ticks`, so a
    `timeout` means the same real seconds regardless of which backend drives.

    Args:
        driver: The backend whose single-shot `wait_for` is polled.
        sel: The selector to wait for.
        timeout: Seconds to keep polling before giving up.
        poll: Seconds slept between checks.

    Returns:
        True once the selector matches; False if `timeout` elapses first.

    Raises:
        ValueError: `poll` is negative (a caller error surfaced loudly rather than left to
            `time.sleep`'s opaque exception).
    """
    if poll < 0:
        raise ValueError(f"poll must be non-negative, got {poll}")
    return any(driver.wait_for(sel) for _ in deadline_ticks(timeout, poll))


def _collapse_identical_duplicates(candidates: list[Element]) -> list[Element]:
    """Collapse candidates that report identical content to one representative.

    A standard `UIAlertController` viewed through XCUITest sometimes registers a button twice in
    the accessibility tree — same identifier, label, traits, value, and frame, persisting for the
    alert's whole lifetime rather than settling to one on a re-read. Nothing distinguishes the two
    nodes, so `index` cannot pick the "real" one either: which of the two a run actually taps
    swaps between runs, stale-handle-failing whichever twin it didn't. Two candidates that differ
    in any reported field are not this case and stay separate, so a genuinely ambiguous selector
    still raises `AmbiguousSelector` below. `traits` is compared as a set (`matches` already treats
    it that way via `issubset`), so two reports of the same trait set in a different order are
    still the same content, not a difference to key on.

    `resolvableMatchingIndex` in `BajutsuKit/Sources/BajutsuRunner/PositionPath.swift` is the
    runner-side twin, collapsing the same artifact when a recorded handle is re-resolved at actuation
    time rather than in an `/elements` reply. The two key on the same fields on purpose: a field added
    to or dropped from this key has to move on that side too, or one of the paths starts guessing where
    the other fails loudly. The same fields, not the same comparison: `frame` goes into the key here as
    an exact tuple because these frames come out of one atomic snapshot, whereas that side allows a
    point of slack because it reads each candidate's frame in its own live call. Sync a field across
    the two, never that tolerance.
    """
    seen: dict[tuple[object, ...], Element] = {}
    for el in candidates:
        key = (
            el["identifier"],
            el["label"],
            tuple(sorted(set(el["traits"]))),
            el["value"],
            el["frame"],
        )
        seen.setdefault(key, el)
    return list(seen.values())


def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails rather than acting on
    "whatever matched first" — the determinism core (BE-0001). Candidates that report identical
    content (identifier, label, traits, value, and frame all equal — a known XCUITest duplicate
    registration for a standard `UIAlertController` button) are collapsed to one first, since
    nothing distinguishes them for the caller to disambiguate on; a genuinely different-content
    match still counts toward ambiguity.

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort, picking the nth of
            several content-distinct candidates (negative values count from the end) — with any
            `other`-trait ties among them dropped first, unless the selector itself targets
            `other` or every candidate is one — from the same filtered set the ambiguity count
            below reports, not the raw `find_all` result.

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more content-distinct candidates matched — with `other`-trait
            ties dropped first when the selector doesn't itself target `other` and at least one
            non-`other` candidate remains — and no `index` disambiguates.
    """
    candidates = _collapse_identical_duplicates(find_all(elements, sel))
    if len(candidates) > 1 and Trait.OTHER not in sel.get("traits", []):
        # A same-label/id tie is often a generic `other` wrapper duplicating a real element's
        # label (e.g. iOS's catch-all XCUIElementTypeOther) rather than a genuine ambiguity — drop
        # `other`-trait candidates before judging uniqueness, unless the selector explicitly asked
        # for `other` elements. Falls back to the full set when every candidate is `other`, so a
        # scenario that does target such an element still resolves (or still fails loud on a real
        # tie among them). This runs before the `index` branch below, so an index counts positions
        # in the same filtered set the ambiguity message below reports — not the raw `find_all`
        # result, where a dropped `other` would otherwise shift every later position by one.
        # Known trade-off: iOS's `other` also covers a real control of an XCUIElementType this
        # driver has not named (e.g. checkBox / radioButton / popUpButton / stepper / datePicker —
        # see XcuitestElementProvider.swift's typeName `default:` arm), not only the generic
        # wrapper. A tie between such a control and a classified sibling sharing its label silently
        # keeps the sibling instead of raising AmbiguousSelector. Only a same-selector tie is
        # affected — an unclassified control resolved on its own (no classified sibling sharing the
        # selector) is unaffected (docs/selectors.md).
        without_other = [c for c in candidates if Trait.OTHER not in c["traits"]]
        if without_other:
            candidates = without_other
    if "index" in sel:
        i = sel["index"]
        if not -len(candidates) <= i < len(candidates):
            raise ElementNotFound(f"index {i} は候補 {len(candidates)} 件の範囲外: {sel!r}")
        return candidates[i]
    if not candidates:
        raise ElementNotFound(f"一致なし: {sel!r}")
    if len(candidates) > 1:
        raise AmbiguousSelector(
            f"{len(candidates)} 件一致: {sel!r} — `within` か `index` で一意化が必要"
        )
    return candidates[0]


# --- Shared driver-side helpers (hoisted from the drivers to defeat per-backend drift, BE-0251) ---


@runtime_checkable
class Queryable(Protocol):
    """Just the current-screen read a wait needs — the query surface, not a full `Driver`.

    `default_wait_for` reads one screen and matches; a shared read base like `CoordinateTreeDriver`
    supplies exactly that without implementing the whole actuator surface, so typing the helper to
    this narrow protocol lets both a full `Driver` and such a base delegate to it.
    """

    def query(self) -> list[Element]: ...


def default_wait_for(driver: Queryable, sel: Selector) -> bool:
    """The single-shot `wait_for` body every real backend delegates to (BE-0118, BE-0251).

    Whether `sel` matches the driver's *current* screen, checked once — the shared `wait_until`
    owns the deadline poll, so a backend never loops here. Hoisted into one definition so the
    identical driver bodies can't silently diverge; a backend that can wait natively still overrides
    `wait_for` rather than calling this.

    Returns:
        True when at least one element matches the selector right now.
    """
    return len(find_all(driver.query(), sel)) >= 1


def frame_center(frame: Frame) -> Point:
    """The center point of an already-resolved element frame (BE-0251).

    Takes the resolved `(x, y, w, h)` so it stays pure geometry — each backend keeps its own
    selector-to-frame resolution and routes only the arithmetic through here.
    """
    x, y, w, h = frame
    return (x + w / 2, y + h / 2)


def topmost_at_point(elements: list[Element], point: Point, target: Element) -> Element | None:
    """The element (if any) that covers `point` and is not `target` itself or its descendant.

    Used where a backend has no native "is this point actually reachable" primitive (unlike iOS's
    `isHittable` or the web's `document.elementFromPoint`): document order — the order `elements`
    already comes in — is a paint-order proxy, a later element having been drawn after (so on top
    of) an earlier one in the ordinary case. A non-`None` result means an unrelated element
    genuinely covers `target`'s point; `None` means nothing does, as far as this proxy can tell.

    `target` must be one of the objects in `elements` (found by identity, `is`, not equality) — every
    caller resolves it from the very same tree it now re-scans. The search looks only *after*
    `target`'s own position, which is what makes a frame-containment check for the ancestor
    direction unnecessary: a real ancestor is always emitted *before* its descendants in a pre-order
    document walk, so it can never appear after `target` and never needs excluding by geometry. A
    naive full-list scan would have to guess "ancestor vs. an unrelated, larger overlay" from frame
    containment alone — indistinguishable, since `Element` carries no parent/child pointers — and
    that guess would misjudge the single most common real case this function exists for: a
    same-size-or-larger backdrop, sticky header, or toast drawn after (so on top of) a smaller
    target, which geometrically *contains* the target's frame exactly the way a real container
    would. Restricting the scan to same-or-later elements sidesteps that ambiguity entirely instead
    of resolving it wrong.

    A descendant (nested inside `target`'s own frame, e.g. an icon inside a button) is still excluded
    by containment (`contains(target frame, candidate frame)`) — tapping through it still taps
    `target`, and unlike an ancestor, a descendant always comes after `target`, so it is the one
    case this scan does need to filter out geometrically. This is a heuristic, not a real z-index:
    it can misjudge a layout whose actual paint order diverges from document order (e.g. an Android
    `View.elevation` reordering draw order without reordering the accessibility tree), and two
    unrelated elements sharing `target`'s exact frame are indistinguishable from a same-size
    wrapper/descendant pair — callers that rely on it should say so.
    """
    px, py = point
    try:
        after_target = next(i for i, el in enumerate(elements) if el is target) + 1
    except StopIteration:
        after_target = len(elements)  # not one of `elements` by identity — nothing to scan after it
    for el in reversed(elements[after_target:]):
        x, y, w, h = el["frame"]
        if not (x <= px <= x + w and y <= py <= y + h):
            continue
        if contains(target["frame"], el["frame"]):
            continue
        return el
    return None


# Above this many named descendants, a refused actuation fails rather than probing them: a container
# this crowded is a layout region, not a control with one actuatable child, and every probe a backend
# spends asking "is this one reachable" is a round trip.
MAX_REDIRECT_CANDIDATES = 4


def redirect_candidates(elements: list[Element], target: Element) -> list[Element]:
    """The named descendants a refused actuation on `target` could be redirected to, in document order.

    The mirror image of `topmost_at_point`: that function scans the same after-`target` slice and
    throws away exactly what this one keeps. A platform can report a container inflated over the
    control it wraps — a SwiftUI `Stepper` whose accessibility element spans its whole form row, say —
    and refuse a tap on the container while the control inside it is perfectly reachable. These are
    the elements a caller may then offer the platform instead.

    Three conditions, each ruling out a way the offer could be wrong:

    - **After `target` in document order.** `Element` carries no parent pointer, so geometry alone
      cannot tell a descendant from an ancestor or from an unrelated overlay that happens to enclose
      the same frame. A pre-order walk always emits an ancestor before its descendants, so the slice
      does the work no frame check can — the same reasoning `topmost_at_point` spells out.
    - **Inside `target`'s frame** (`contains`, edge-inclusive). An equal frame counts: a control
      registered twice at one place is a redirect target as legitimate as a smaller child, and it
      still has to satisfy the last condition.
    - **Carrying an identifier.** The offer is then always an element the caller could have named
      directly, which is what keeps a redirect from becoming a guess the scenario's author cannot
      predict — and what lets a refusal print the candidates it declined to choose between.

    `target` must be one of the objects in `elements`, found by identity (`is`) rather than equality,
    the way every caller already resolves it from the very tree it now re-scans. A `target` absent
    from the list has no descendants to offer, so the result is empty rather than an error.
    """
    try:
        after_target = next(i for i, el in enumerate(elements) if el is target) + 1
    except StopIteration:
        return []
    return [
        el
        for el in elements[after_target:]
        if el["identifier"] and contains(target["frame"], el["frame"])
    ]


def raise_if_covered(elements: list[Element], el: Element, sel: Selector) -> None:
    """Raise `ElementNotTappable` if `topmost_at_point` finds something covering `el`'s own point.

    Shared by every backend that falls back to the document-order proxy rather than a native
    hit-test (adb's two call sites, `FakeDriver`, `XcuitestLiveDriver`) — one place for the check,
    the message, and the covering element's own identifier/label/frame, so a failure names *what*
    covered the target instead of leaving a caller to reproduce the screen by hand to find out.
    """
    covering = topmost_at_point(elements, frame_center(el["frame"]), el)
    if covering is not None:
        cover = covering["identifier"] or covering["label"] or "<unnamed>"
        raise ElementNotTappable(
            f"element resolved but covered by another element "
            f"({cover!r} at {covering['frame']}): {sel!r}"
        )


def gesture_anchor(frame: Frame) -> tuple[float, float, float]:
    """A two-finger gesture's center and finger half-distance for a resolved frame (BE-0251).

    The half-distance is a quarter of the smaller side, so the two fingers (and a pinch-out up to
    ~2x) stay within the element's bounds rather than landing on a neighbour.

    Returns:
        `(cx, cy, half)` — the frame center and `min(w, h) / 4`.
    """
    x, y, w, h = frame
    return x + w / 2, y + h / 2, min(w, h) / 4

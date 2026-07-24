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
import re
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, runtime_checkable

if TYPE_CHECKING:
    from bajutsu.evidence.network import Collector


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
# by `bajutsu.scenario.models.scenario.Scenario`'s `permissions` field validator rather than
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


class Trait:
    """Normalized accessibility traits used by state assertions.

    Drivers normalize at least the following to these common tokens.
    """

    BUTTON = "button"
    LINK = "link"
    NOT_ENABLED = "notEnabled"  # disabled state (enabled / disabled assertions)
    SELECTED = "selected"  # selected / toggled state (selected assertion)


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
    # Tap a button on an out-of-process iOS SpringBoard permission prompt (BE-0316), resolving `sel`
    # (label-based only) against the alert's buttons within `timeout`. A backend without the
    # HANDLE_SYSTEM_ALERT capability raises UnsupportedAction; preflight (capability_preflight.py)
    # rejects the scenario before any device work, so this raise is only the mid-run backstop.
    def handle_system_alert(self, sel: Selector, timeout: float) -> None: ...
    # A single, non-blocking read of the SpringBoard alert's button labels — [] when no alert is up
    # (BE-0315). The reactive `dismissAlerts` guard polls this to learn whether a prompt is showing
    # and which buttons it offers, then taps a policy-named one via `handle_system_alert`. It shares
    # the HANDLE_SYSTEM_ALERT capability (a backend without it returns []), so it never adds a route
    # of its own — the query is BE-0316's `/systemAlert/query`, read here without the tap.
    def system_alert_labels(self) -> list[str]: ...
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
class BackendLifecycle(Protocol):
    """The full set of lifecycle hooks backends run around a single run (BE-0141).

    A run launches, tears down, and resets a backend, but those steps are platform-shaped: the web
    (Playwright) backend navigates / closes / resets a browser context, the XCUITest backend waits
    for its on-device runner to answer, and the fake backend needs none of them. The four hooks are therefore split disjointly
    across backends — no single driver implements all four — so this is a *typing umbrella* for the
    call sites, not a conformance target: the `platform_lifecycle` environments reach each hook through
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
    global _cached_index
    if _cached_index is not None and _cached_index[0] == id(elements):
        return _cached_index[2]
    idx: dict[str | None, list[Element]] = {}
    for el in elements:
        idx.setdefault(el["identifier"], []).append(el)
    _cached_index = (id(elements), elements, idx)
    return idx


def _contains(outer: Frame, inner: Frame) -> bool:
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
        found = [el for el in found if any(_contains(scope, el["frame"]) for scope in scopes)]
    return found


def deadline_ticks(
    timeout: float, poll_init: float, poll_max: float | None = None
) -> Iterator[None]:
    """Yield once per poll to a monotonic deadline, sleeping with capped backoff between ticks.

    The one deadline/backoff skeleton the condition waits share (BE-0118, BE-0256): `wait_until`
    here and the platform-lifecycle readiness waits (`_await_ready` / `_await_boot`) each run their
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


def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails rather than acting on
    "whatever matched first" — the determinism core (BE-0001).

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort, picking the nth of
            several candidates (negative values count from the end).

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more matched and no `index` disambiguates.
    """
    candidates = find_all(elements, sel)
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


def gesture_anchor(frame: Frame) -> tuple[float, float, float]:
    """A two-finger gesture's center and finger half-distance for a resolved frame (BE-0251).

    The half-distance is a quarter of the smaller side, so the two fingers (and a pinch-out up to
    ~2x) stay within the element's bounds rather than landing on a neighbour.

    Returns:
        `(cx, cy, half)` — the frame center and `min(w, h) / 4`.
    """
    x, y, w, h = frame
    return x + w / 2, y + h / 2, min(w, h) / 4

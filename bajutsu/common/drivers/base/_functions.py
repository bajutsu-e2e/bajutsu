"""Resolve a selector to exactly one element, and wait on a condition rather than a clock."""

from __future__ import annotations

import fnmatch
import functools
import math
import re
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from .ambiguous_selector import AmbiguousSelector
from .element_not_found import ElementNotFound
from .element_not_tappable import ElementNotTappable
from .selector import Selector
from .trait import Trait

if TYPE_CHECKING:
    from ._shared import Frame, Point
    from .driver import Driver
    from .element import Element
    from .queryable import Queryable


# The distinctive fragment of `resolve_unique`'s ambiguous-match message below, exported so
# `heuristic_triage_agent.py` can match on it instead of re-deriving its own copy of the wording —
# the two drifting apart would silently stop the "add `within` or `index`" triage hint from firing,
# with `make check` still green.
AMBIGUOUS_MATCH_MARKER = "elements matched"
# The same message's pre-translation (Japanese) wording. A run recorded before that translation
# still carries it verbatim in `steps[].reason`, so triage matches both markers — otherwise the
# hint silently stops firing on archived run history that spans the change.
LEGACY_AMBIGUOUS_MATCH_MARKER = "件一致"

# Single-entry cache: (list_id, list_ref, index_dict).
# Holding list_ref prevents GC so id() stays stable across lookups.
_cached_index: tuple[int, list[Element], dict[str | None, list[Element]]] | None = None


@functools.lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Cached re.compile — avoids recompiling the same pattern on every poll iteration."""
    return re.compile(pattern)


def permission_capability(service: str) -> str:
    """The per-service device-control token for a permission service (BE-0276).

    One token per vocabulary entry rather than a single `deviceControl.permissions` token, so a
    backend that honors only part of the vocabulary (iOS: everything but `notifications`) can
    advertise exactly that subset and preflight names the unsupported service individually.
    """
    return f"deviceControl.permissions.{service}"


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
            raise ElementNotFound(
                f"index {i} is out of range for {len(candidates)} candidates: {sel!r}"
            )
        return candidates[i]
    if not candidates:
        raise ElementNotFound(f"no match: {sel!r}")
    if len(candidates) > 1:
        raise AmbiguousSelector(
            f"{len(candidates)} {AMBIGUOUS_MATCH_MARKER} {sel!r} — "
            "add `within` or `index` to disambiguate"
        )
    return candidates[0]


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


# How far above the banner's own top edge the swipe ends, and the highest point it may end at — the
# same two distances `RunnerUITest.swift`'s interruption-monitor path already swipes a banner by
# (BE-0416), kept in step so the proactive guard's own dismiss travels the same distance.
_NOTIFICATION_BANNER_SWIPE_TRAVEL = 20.0
_NOTIFICATION_BANNER_SWIPE_TOP_MARGIN = 8.0


def notification_banner_swipe_points(frame: Frame) -> tuple[Point, Point] | None:
    """The `swipe` `frm`/`to` points that dismiss a notification banner measured at `frame` (BE-0416).

    `frame` is read from SpringBoard's own coordinate space (`Driver.notification_banner_frame`),
    while `swipe` resolves its points as an offset from the app under test's own origin. The two
    coincide at the physical screen's top-left in points for a foreground app that fills the screen
    (no split-view multitasking) — the same assumption the app's own frame-reading already makes
    throughout this driver seam — so the frame's raw numbers carry over unchanged, with no
    transform to apply.

    `None` when `frame` is caught mid-animation (not yet settled) and the resulting gesture would
    travel less than `_NOTIFICATION_BANNER_SWIPE_TRAVEL` — the distance `RunnerUITest.swift`'s
    own interruption-monitor path measured sufficient to dismiss a settled banner. The top-margin
    clamp below can otherwise leave a genuine but too-short gesture (or, at the extreme, one that
    travels *downward*) for a frame whose top edge sits at or above the screen's own top margin —
    a shape only a banner still sliding into place produces. The caller's next poll reads the frame
    again once it has settled, rather than attempting a gesture too weak to act on.

    Returns:
        `(frm, to)` — the banner's own center, and a point above its top edge (never past
        `_NOTIFICATION_BANNER_SWIPE_TOP_MARGIN`, where SpringBoard would instead claim the drag as
        its own notification-shade gesture).
    """
    x, y, w, h = frame
    cx, cy = x + w / 2, y + h / 2
    top = max(_NOTIFICATION_BANNER_SWIPE_TOP_MARGIN, y - _NOTIFICATION_BANNER_SWIPE_TRAVEL)
    if cy - top < _NOTIFICATION_BANNER_SWIPE_TRAVEL:
        return None
    return (cx, cy), (cx, top)

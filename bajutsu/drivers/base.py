"""Driver abstraction — the linchpin shared by every backend (idb / fake).

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
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, runtime_checkable

if TYPE_CHECKING:
    from bajutsu.network import Collector


@functools.lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Cached re.compile — avoids recompiling the same pattern on every poll iteration."""
    return re.compile(pattern)


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
    MULTI_TOUCH = "multiTouch"  # two-finger gestures (pinch / rotate); idb is single-touch
    WEBVIEW = "webView"  # DOM query/tap inside an embedded WKWebView (BE-0037)


class Element(TypedDict):
    """A single on-screen element, normalized from idb output."""

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

    id: str  # exact accessibilityIdentifier (first choice)
    idMatches: str  # glob pattern (assumes multiple matches, e.g. "*.submit")
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
    backend without semantic tap (e.g. idb), the abstraction resolves the frame
    center via query() / resolve_unique() and taps by coordinates.
    """

    # Backend identifier (e.g. "idb", "fake"). Recorded in the run
    # manifest and shown in the report so a run says which actuator drove it.
    name: str

    def query(self) -> list[Element]: ...
    def tap(self, sel: Selector) -> None: ...
    def tap_point(self, p: Point) -> None: ...  # raw coordinate tap (system alerts, etc.)
    def double_tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    # Two-finger gestures. scale > 1 zooms in, < 1 zooms out; radians > 0 rotates
    # clockwise. Only backends advertising MULTI_TOUCH support these.
    def pinch(self, sel: Selector, scale: float) -> None: ...
    def rotate(self, sel: Selector, radians: float) -> None: ...
    def type_text(self, text: str) -> None: ...
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
    backend *read-only* to fill an evidence gap the actuator lacks (e.g. idb has no native network,
    so a second iOS actuator supplies it). The narrow surface — `capabilities` plus observation
    methods only, never `tap` / `type` / `swipe` / `wait` / `query` — makes "the fallback never
    actuates" a type-level fact rather than a convention.
    """

    name: str

    def capabilities(self) -> set[str]: ...
    def network_collector(self, mocks: list[object] | None = None) -> Collector: ...


# --- Selector resolution (the determinism core) ---


class SelectorError(Exception):
    """Selector resolution failed."""


class UnsupportedAction(Exception):
    """The actuator backend cannot perform this action.

    For example, a multi-touch gesture on idb, which is single-touch. The tool surfaces it as a step
    failure with a clear reason rather than letting it pass silently.
    """


class ElementNotFound(SelectorError):
    """No candidate matched. A wait times out; an immediate action fails."""


class AmbiguousSelector(SelectorError):
    """2+ candidates with no way to disambiguate; needs `within` or `index`."""


def matches(el: Element, sel: Selector) -> bool:
    """Whether an element satisfies a selector's per-element conditions (all AND-ed).

    Args:
        el: One element from a `query()` snapshot.
        sel: The selector to test. Only the per-element fields are checked here
            (`id` / `idMatches` / `label` / `labelMatches` / `traits` / `value`); `within` (a
            cross-element spatial constraint, resolved by `find_all`) and `index` (a positional
            pick among matches, applied by `resolve_unique`) are ignored.

    Returns:
        True when every per-element field set on the selector matches the element.
    """
    if "id" in sel and el["identifier"] != sel["id"]:
        return False
    if "idMatches" in sel and not (
        el["identifier"] is not None and fnmatch.fnmatchcase(el["identifier"], sel["idMatches"])
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
    # Fast path: id-only selector uses cached index for O(1) lookup.
    if set(base_sel.keys()) == {"id"}:
        found = list(_id_index(elements).get(base_sel["id"], []))
    else:
        found = [el for el in elements if matches(el, base_sel)]
    if "within" in sel:
        scopes = [parent["frame"] for parent in find_all(elements, sel["within"])]
        found = [el for el in found if any(_contains(scope, el["frame"]) for scope in scopes)]
    return found


def wait_until(driver: Driver, sel: Selector, timeout: float, poll: float = 0.2) -> bool:
    """Poll `driver.wait_for(sel)` against a monotonic deadline until it matches.

    The one deadline loop every backend shares (BE-0118). Each `wait_for` is a single-shot
    check; this turns it into a timeout-honouring wait uniformly — a condition wait with no
    fixed sleep, mirroring the orchestrator's discipline — so a `timeout` means the same real
    seconds regardless of which backend drives.

    Args:
        driver: The backend whose single-shot `wait_for` is polled.
        sel: The selector to wait for.
        timeout: Seconds to keep polling before giving up.
        poll: Seconds slept between checks.

    Returns:
        True once the selector matches; False if `timeout` elapses first.
    """
    deadline = time.monotonic() + timeout
    while True:
        if driver.wait_for(sel):
            return True
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(poll, deadline - now))  # never sleep past the deadline


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

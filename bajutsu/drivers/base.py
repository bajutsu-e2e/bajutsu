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
import re
from typing import Protocol, TypedDict, cast, runtime_checkable

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
    SEMANTIC_TAP = "semanticTap"      # tap directly by id/label (no coordinates; most stable)
    CONDITION_WAIT = "conditionWait"  # native condition waiting
    NETWORK = "network"               # native network monitoring
    SCREENSHOT = "screenshot"
    ELEMENTS = "elements"
    MULTI_TOUCH = "multiTouch"        # two-finger gestures (pinch / rotate); idb is single-touch


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
    SELECTED = "selected"       # selected / toggled state (selected assertion)


class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND.

    The stable selector is `id` (non-localized, data-derived). `label` /
    `labelMatches` are auxiliary; `index` is a last resort (flaky).
    """

    id: str            # exact accessibilityIdentifier (first choice)
    idMatches: str     # glob pattern (assumes multiple matches, e.g. "*.submit")
    label: str         # exact accessibilityLabel (auxiliary / disambiguation only)
    labelMatches: str  # substring / regex over label
    traits: list[str]  # narrow by type (e.g. ["button"])
    value: str         # accessibility value match
    within: Selector  # scope to a parent (needs a hierarchical query; not implemented)
    index: int         # nth of multiple matches (last resort; flaky)


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
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...


# --- Selector resolution (the determinism core) ---


class SelectorError(Exception):
    """Selector resolution failed."""


class UnsupportedAction(Exception):
    """The actuator backend cannot perform this action (e.g. a multi-touch gesture
    on idb, which is single-touch). Surfaced as a step failure with a clear reason
    rather than silently passing."""


class ElementNotFound(SelectorError):
    """No candidate matched. A wait times out; an immediate action fails."""


class AmbiguousSelector(SelectorError):
    """2+ candidates with no way to disambiguate; needs `within` or `index`."""


def matches(el: Element, sel: Selector) -> bool:
    """Whether an element satisfies the per-element selector conditions (AND).

    `within` is a cross-element (spatial) constraint and is resolved by `find_all`,
    not here; it is ignored if present in `sel`.
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
        el["label"] is not None and re.search(sel["labelMatches"], el["label"]) is not None
    ):
        return False
    if "traits" in sel and not set(sel["traits"]).issubset(el["traits"]):
        return False
    return not ("value" in sel and el["value"] != sel["value"])


def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame is spatially contained in `outer`'s (edges inclusive)."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh


def find_all(elements: list[Element], sel: Selector) -> list[Element]:
    """All matching elements (for idMatches triggers and count assertions).

    `within` scopes the result to elements spatially contained in a parent the `within`
    selector resolves to. The accessibility tree is flat, so "parent" is geometric: the
    `within` selector picks one or more container elements and a candidate qualifies when
    its frame sits inside one of theirs. `within` may nest.
    """
    base_sel = cast(Selector, {k: v for k, v in sel.items() if k != "within"})
    found = [el for el in elements if matches(el, base_sel)]
    if "within" in sel:
        scopes = [parent["frame"] for parent in find_all(elements, sel["within"])]
        found = [el for el in found if any(_contains(scope, el["frame"]) for scope in scopes)]
    return found


def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve to exactly one element for a single action.

    - 0 matches -> ElementNotFound
    - 2+ matches -> AmbiguousSelector (rules out "tap whatever matched first")
    - only with `index` do we pick the nth of multiple candidates (last resort)
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

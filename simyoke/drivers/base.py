"""Driver abstraction — the linchpin shared by both backends (RocketSim / idb).

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
from typing import Protocol, TypedDict, runtime_checkable

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


class Element(TypedDict):
    """A single on-screen element, normalized from RocketSim / idb output."""

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
    within: "Selector"  # scope to a parent (needs a hierarchical query; not implemented)
    index: int         # nth of multiple matches (last resort; flaky)


@runtime_checkable
class Driver(Protocol):
    """Common interface for both backends.

    Actions (tap/type/swipe/wait/query) are performed by the actuator only. On a
    backend without semantic tap (e.g. idb), the abstraction resolves the frame
    center via query() / resolve_unique() and taps by coordinates.
    """

    def query(self) -> list[Element]: ...
    def tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...


# --- Selector resolution (the determinism core) ---


class SelectorError(Exception):
    """Selector resolution failed."""


class ElementNotFound(SelectorError):
    """No candidate matched. A wait times out; an immediate action fails."""


class AmbiguousSelector(SelectorError):
    """2+ candidates with no way to disambiguate; needs `within` or `index`."""


def matches(el: Element, sel: Selector) -> bool:
    """Whether an element satisfies all selector conditions (AND).

    `within` needs parent/child structure, so it is unsupported on the current
    flat Element.
    """
    if "within" in sel:
        raise NotImplementedError("`within` は階層クエリが必要（将来対応）")
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
    if "value" in sel and el["value"] != sel["value"]:
        return False
    return True


def find_all(elements: list[Element], sel: Selector) -> list[Element]:
    """All matching elements (for idMatches triggers and count assertions)."""
    return [el for el in elements if matches(el, sel)]


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

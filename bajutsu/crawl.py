"""Autonomous crawl engine core (BE-0038).

Breadth-first exploration of an app over the `Driver` abstraction, producing a screen map of
the reachable screens and the transitions between them. This is the deterministic engine only —
no AI and no Simulator wiring (those land in later slices). The determinism boundary is the
whole point: a screen's *identity* (its fingerprint) and the *order* in which candidate actions
are tried are both pure functions of the element tree, so a crawl of an unchanged app explores
the same way as far as the app's own non-determinism allows. AI never decides anything here.

Traversal is by **deterministic replay**, not in-place backtracking: app transitions are usually
irreversible, so to revisit a known screen the engine resets to a clean state and replays the
shortest recorded path to it (the same way `run` reaches any state), then takes the next untried
action. Every edge is therefore a replayable step, and every node already has a path to it.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from bajutsu.drivers import base
from bajutsu.record import shows_app_ui

# Controls a tap drives forward (navigation / activation).
TAP_TRAITS = frozenset({"button", "link"})
# Text inputs the crawl fills to satisfy a precondition (e.g. enabling a disabled submit button).
INPUT_TRAITS = frozenset({"textField", "searchField", "secureTextField"})
# Any interactive control — used by the structural fingerprint and blocked-control detection.
ACTIONABLE_TRAITS = TAP_TRAITS | INPUT_TRAITS

# Below this many id-bearing elements, the id set is too thin to identify a screen reliably,
# so fall back to a structural fingerprint (flagged as less stable). See DESIGN §5.
_MIN_IDS_FOR_ID_FINGERPRINT = 2

# Grid (in points) the structural fallback buckets element frames into, so tiny layout jitter
# doesn't split one screen into many fingerprints.
_FRAME_BUCKET = 32

Reset = Callable[[base.Driver], None]
Settle = Callable[[base.Driver], None]


@dataclass(frozen=True)
class Fingerprint:
    """A screen's identity. `kind` is "id" (stable, identifier-derived) or "structural"
    (the less-stable fallback for screens with too few accessibility identifiers)."""

    value: str
    kind: str


@dataclass(frozen=True)
class Action:
    """A replayable action against a screen. `kind` is "tap" or "type" (text input). The element
    is named by `target` (its accessibility identifier — stable, preferred) or, for an id-less
    element, by `label` (+ `index` to disambiguate duplicates). A "type" action carries the text
    to enter in `value`. All fields are hashable so an Action can key the frontier / tried set."""

    kind: str
    target: str = ""
    label: str | None = None
    index: int | None = None
    value: str | None = None

    @property
    def key(self) -> str:
        """Stable identity for de-duplication and the frontier: the id, else the label[#index]."""
        return self.target or f"@{self.label}#{0 if self.index is None else self.index}"

    def as_selector(self) -> base.Selector:
        if self.target:
            return {"id": self.target}
        sel: base.Selector = {}
        if self.label is not None:
            sel["label"] = self.label
        if self.index is not None:
            sel["index"] = self.index
        return sel

    def describe(self) -> str:
        what = self.target or (self.label or "?")
        if self.kind == "type" and self.value:
            return f"type {what}={self.value!r}"
        return f"{self.kind} {what}"

    def perform(self, driver: base.Driver) -> None:
        """Execute against the live screen: a type action focuses the field (tap) then enters its
        value; a tap just taps. Replayable because the selector is id- or label-based."""
        driver.tap(self.as_selector())
        if self.kind == "type":
            driver.type_text(self.value or "")


@dataclass(frozen=True)
class Node:
    """A discovered screen: its fingerprint, the identifiers present, the candidate action keys
    leaving it, and `blocked` — actionable controls present but disabled (known but un-pressable
    until a precondition is met)."""

    fingerprint: str
    kind: str
    ids: tuple[str, ...]
    actions: tuple[str, ...]
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    """A transition: taking `action` from screen `src` landed on screen `dst`."""

    src: str
    action: str
    dst: str


@dataclass(frozen=True)
class Crash:
    """A path (sequence of action descriptions) whose last action collapsed the app UI."""

    path: tuple[str, ...]


@dataclass
class ScreenMap:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    crashes: list[Crash] = field(default_factory=list)


# Fires after each change to the map (a new node, edge, or crash). Pure observation so a caller
# can stream the screen map as it grows (the web UI's live graph) — it never influences which
# screen is explored next or how a screen is identified, so the crawl stays deterministic.
OnEvent = Callable[[ScreenMap], None]

# Fires once per newly discovered screen, while the driver is still positioned on it — the moment
# to capture a per-screen artifact (a screenshot). Pure observation, like `OnEvent`.
OnNode = Callable[["Node"], None]


def _id_of(element: base.Element) -> str | None:
    return element.get("identifier")


def _traits(element: base.Element) -> set[str]:
    return set(element.get("traits") or [])


def _is_enabled(element: base.Element) -> bool:
    """Whether the control is interactive (not flagged `notEnabled`). A disabled button can't be
    driven forward by a tap, so the crawl skips it and reports it as blocked instead."""
    return base.Trait.NOT_ENABLED not in _traits(element)


def _input_value(element: base.Element) -> str:
    """A deterministic placeholder to type into a field (the `--guide off` path; an AI guide
    supplies context-appropriate values). Good enough to clear "must be non-empty" preconditions;
    validation-gated fields (a valid email, password rules) need the AI guide."""
    hint = f"{_id_of(element) or ''} {element.get('label') or ''}".lower()
    if "mail" in hint:
        return "test@example.com"
    if "secureTextField" in _traits(element):
        return "Test1234!"
    return "test"


def _fingerprint_token(element: base.Element) -> str:
    """An id-bearing element's contribution to the screen fingerprint: its id, plus a marker when
    its *interactive* state differs — disabled (`!`) or a filled input (`=`). An enabled,
    empty/non-input element contributes just its id, so screens with no such state hash exactly as
    the plain id set did. Folding these in makes "form empty / submit disabled" and "form filled /
    submit enabled" distinct screens, so the crawl explores behind a control it just enabled."""
    ident = _id_of(element) or ""
    suffix = ""
    if not _is_enabled(element):
        suffix += "!"
    if INPUT_TRAITS & _traits(element) and (element.get("value") or ""):
        suffix += "="
    return ident + suffix


def fingerprint(elements: list[base.Element]) -> Fingerprint:
    """Reduce a screen to a stable identity.

    Primary: the sorted set of accessibility identifiers (each tagged with its disabled/filled
    state — see `_fingerprint_token`), hashed — non-localized and data-independent, so it is
    stable across locales and minor content changes. Fallback (for screens with too few
    identifiers): a structural hash over the actionable elements' `(traits, frame-bucket)`, which
    is less stable and flagged as such.
    """
    ids = {i for el in elements if (i := _id_of(el))}
    if len(ids) >= _MIN_IDS_FOR_ID_FINGERPRINT:
        tokens = sorted({_fingerprint_token(el) for el in elements if _id_of(el)})
        return Fingerprint(_hash(tokens), "id")

    structure = sorted(
        f"{','.join(el.get('traits') or [])}@{_frame_bucket(el)}"
        for el in elements
        if ACTIONABLE_TRAITS & _traits(el)
    )
    return Fingerprint(_hash(structure), "structural")


def _frame_bucket(element: base.Element) -> tuple[int, int, int, int]:
    x, y, w, h = element.get("frame") or (0.0, 0.0, 0.0, 0.0)
    return (
        int(x // _FRAME_BUCKET),
        int(y // _FRAME_BUCKET),
        int(w // _FRAME_BUCKET),
        int(h // _FRAME_BUCKET),
    )


def _hash(parts: list[str]) -> str:
    # Full digest: a crawl may visit many screens across runs, so keep collision risk negligible.
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def candidate_actions(elements: list[base.Element]) -> list[Action]:
    """The deterministic guide (`--guide off`): tap each enabled, id-bearing button/link, and —
    to satisfy a precondition like a disabled submit — type a placeholder into the first empty,
    enabled text field (id order).

    Disabled controls (`notEnabled`) are skipped: tapping them is a no-op; they're reported
    separately as blocked. Id-less controls are skipped: replay needs a stable selector. Filling
    one field at a time (the first empty in id order) keeps the chain linear instead of exploring
    every fill-order permutation — once a field has a value it drops out and the next empty one
    becomes the candidate, until the form is complete and the gated control enables.
    """
    taps = sorted(
        {i for el in elements if (i := _id_of(el)) and _is_enabled(el) and TAP_TRAITS & _traits(el)}
    )
    actions = [Action("tap", target=t) for t in taps]
    empty_fields = sorted(
        (i, _input_value(el))
        for el in elements
        if (i := _id_of(el))
        and _is_enabled(el)
        and INPUT_TRAITS & _traits(el)
        and not (el.get("value") or "")
    )
    if empty_fields:
        fid, val = empty_fields[0]
        actions.append(Action("type", target=fid, value=val))
    return actions


def blocked_controls(elements: list[base.Element]) -> list[str]:
    """Ids of actionable controls present but disabled (`notEnabled`) — known yet un-pressable
    until a precondition is met. Reported on each node so the screen map can flag the gap."""
    return sorted(
        {
            i
            for el in elements
            if (i := _id_of(el)) and not _is_enabled(el) and ACTIONABLE_TRAITS & _traits(el)
        }
    )


def is_app_alive(elements: list[base.Element]) -> bool:
    """Whether the app's own UI is showing (not collapsed under a system overlay or crashed).
    Reuses record's public check so "app UI vs. collapsed tree" has a single definition."""
    return shows_app_ui(elements)


# A guide proposes the replayable actions to try from a screen. The default is the deterministic
# `candidate_actions`; an AI guide (BE-0038 `--guide ai`) proposes richer operations and realistic
# inputs. Either way the guide only chooses *what to try* — screen identity, transition/crash
# detection, and the screen map stay deterministic and AI-free, so the crawl is never a verdict.
Guide = Callable[[base.Driver, list[base.Element]], list[Action]]


def _deterministic_guide(_driver: base.Driver, elements: list[base.Element]) -> list[Action]:
    return candidate_actions(elements)


def _node_of(fp: Fingerprint, elements: list[base.Element], actions: list[Action]) -> Node:
    return Node(
        fingerprint=fp.value,
        kind=fp.kind,
        ids=tuple(sorted({i for el in elements if (i := _id_of(el))})),
        actions=tuple(a.key for a in actions),
        blocked=tuple(blocked_controls(elements)),
    )


def crawl(
    driver: base.Driver,
    reset: Reset,
    *,
    max_screens: int = 50,
    max_steps: int = 200,
    settle: Settle | None = None,
    guide: Guide | None = None,
    on_event: OnEvent | None = None,
    on_node: OnNode | None = None,
) -> ScreenMap:
    """Breadth-first crawl by deterministic replay.

    `reset` returns the app to a clean starting state (erase/boot/launch on a real device; in
    tests, restoring the start screen). `settle`, if given, waits for the screen to stabilize
    after an action (a condition wait — never a fixed sleep); it is omitted when the driver is
    synchronous. `guide` proposes the actions to try from a screen (default: the deterministic
    `candidate_actions`; an AI guide proposes richer operations) — it only chooses *what to try*,
    never what happened. `on_event`, if given, fires after each new node, edge, or crash so a
    caller can stream the growing screen map. `on_node`, if given, fires once per newly discovered
    screen while the driver is still on it (to capture a screenshot). Stops at `max_screens`
    distinct screens or `max_steps` actions, whichever first.
    """
    guide = guide or _deterministic_guide
    screen_map = ScreenMap()

    def observe() -> list[base.Element]:
        if settle is not None:
            settle(driver)
        return driver.query()

    def emit() -> None:
        if on_event is not None:
            on_event(screen_map)

    def discover(fp: Fingerprint, elements: list[base.Element]) -> list[Action]:
        # Driver is on this screen; ask the guide for its actions (once), record the node, and let
        # the on_node hook capture its screenshot — all before the next reset moves the app away.
        actions = guide(driver, elements)
        node = _node_of(fp, elements, actions)
        screen_map.nodes[fp.value] = node
        if on_node is not None:
            on_node(node)
        return actions

    reset(driver)
    start = observe()
    start_fp = fingerprint(start)
    shortest_path: dict[str, list[Action]] = {start_fp.value: []}
    frontier: deque[tuple[str, Action]] = deque(
        (start_fp.value, action) for action in discover(start_fp, start)
    )
    emit()
    tried: set[tuple[str, str]] = set()
    steps = 0

    while frontier and len(screen_map.nodes) < max_screens and steps < max_steps:
        src_fp, action = frontier.popleft()
        key = (src_fp, action.key)
        if key in tried:
            continue
        tried.add(key)

        # Reach the source screen by replaying the shortest known path from a clean start.
        reset(driver)
        observe()
        if not _replay(driver, shortest_path[src_fp], settle):
            continue

        steps += 1
        path = [*shortest_path[src_fp], action]
        try:
            action.perform(driver)
        except base.SelectorError:
            continue
        landed = observe()

        if not is_app_alive(landed):
            screen_map.crashes.append(Crash(tuple(a.describe() for a in path)))
            emit()
            continue

        dst_fp = fingerprint(landed)
        screen_map.edges.append(Edge(src_fp, action.describe(), dst_fp.value))
        if dst_fp.value not in screen_map.nodes:
            shortest_path[dst_fp.value] = path
            for next_action in discover(dst_fp, landed):
                frontier.append((dst_fp.value, next_action))
        emit()

    return screen_map


def _replay(driver: base.Driver, path: list[Action], settle: Settle | None) -> bool:
    """Re-walk a recorded path from the current (clean) state. Returns False if a step no longer
    resolves — the app changed under us — so the caller skips this frontier entry."""
    for action in path:
        try:
            action.perform(driver)
        except base.SelectorError:
            return False
        if settle is not None:
            settle(driver)
    return True


def screenmap_dict(screen_map: ScreenMap) -> dict[str, object]:
    """Serialize a screen map to a JSON-friendly dict (nodes sorted by fingerprint)."""
    return {
        "nodes": [
            {
                "fingerprint": node.fingerprint,
                "kind": node.kind,
                "ids": list(node.ids),
                "actions": list(node.actions),
                "blocked": list(node.blocked),
            }
            for node in sorted(screen_map.nodes.values(), key=lambda n: n.fingerprint)
        ],
        "edges": [{"src": e.src, "action": e.action, "dst": e.dst} for e in screen_map.edges],
        "crashes": [{"path": list(c.path)} for c in screen_map.crashes],
    }

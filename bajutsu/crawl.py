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

# Elements worth acting on. Tapping these can change the screen; static text / images do not.
ACTIONABLE_TRAITS = frozenset({"button", "link", "textField", "searchField"})

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
    """A replayable action against a screen. The first slice taps id-bearing elements only,
    so `target` is the element identifier and the action re-executes as `tap {id: target}`."""

    kind: str
    target: str

    def as_selector(self) -> base.Selector:
        return {"id": self.target}

    def describe(self) -> str:
        return f"{self.kind} {self.target}"


@dataclass(frozen=True)
class Node:
    """A discovered screen: its fingerprint, the identifiers present, and the candidate
    action targets leaving it."""

    fingerprint: str
    kind: str
    ids: tuple[str, ...]
    actions: tuple[str, ...]


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


def fingerprint(elements: list[base.Element]) -> Fingerprint:
    """Reduce a screen to a stable identity.

    Primary: the sorted set of accessibility identifiers, hashed — non-localized and
    data-independent, so it is stable across locales and minor content changes. Fallback (for
    screens with too few identifiers): a structural hash over the actionable elements'
    `(traits, frame-bucket)`, which is less stable and flagged as such.
    """
    ids = sorted({i for el in elements if (i := _id_of(el))})
    if len(ids) >= _MIN_IDS_FOR_ID_FINGERPRINT:
        return Fingerprint(_hash(ids), "id")

    structure = sorted(
        f"{','.join(el.get('traits') or [])}@{_frame_bucket(el)}"
        for el in elements
        if ACTIONABLE_TRAITS & set(el.get("traits") or [])
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
    """Actionable, id-bearing elements as replayable tap actions, in deterministic id order.

    Id-less actionable elements are skipped in this slice: replay needs a stable selector, and
    an id is the only one the engine can re-resolve after an erase/replay. Identifiers are
    de-duplicated so a repeated id doesn't bloat the frontier or the node's action list.
    """
    targets = sorted(
        {
            i
            for el in elements
            if (i := _id_of(el)) and ACTIONABLE_TRAITS & set(el.get("traits") or [])
        }
    )
    return [Action("tap", t) for t in targets]


def is_app_alive(elements: list[base.Element]) -> bool:
    """Whether the app's own UI is showing (not collapsed under a system overlay or crashed).
    Reuses record's public check so "app UI vs. collapsed tree" has a single definition."""
    return shows_app_ui(elements)


def _node_of(fp: Fingerprint, elements: list[base.Element]) -> Node:
    return Node(
        fingerprint=fp.value,
        kind=fp.kind,
        ids=tuple(sorted({i for el in elements if (i := _id_of(el))})),
        actions=tuple(a.target for a in candidate_actions(elements)),
    )


def crawl(
    driver: base.Driver,
    reset: Reset,
    *,
    max_screens: int = 50,
    max_steps: int = 200,
    settle: Settle | None = None,
    on_event: OnEvent | None = None,
    on_node: OnNode | None = None,
) -> ScreenMap:
    """Breadth-first crawl by deterministic replay.

    `reset` returns the app to a clean starting state (erase/boot/launch on a real device; in
    tests, restoring the start screen). `settle`, if given, waits for the screen to stabilize
    after an action (a condition wait — never a fixed sleep); it is omitted when the driver is
    synchronous. `on_event`, if given, fires after each new node, edge, or crash so a caller can
    stream the growing screen map. `on_node`, if given, fires once per newly discovered screen
    while the driver is still on it (to capture a screenshot). Stops at `max_screens` distinct
    screens or `max_steps` actions, whichever first.
    """
    screen_map = ScreenMap()

    def observe() -> list[base.Element]:
        if settle is not None:
            settle(driver)
        return driver.query()

    def emit() -> None:
        if on_event is not None:
            on_event(screen_map)

    def discovered(node: Node) -> None:
        # Driver is on `node`'s screen here; the hook captures its screenshot before any reset.
        if on_node is not None:
            on_node(node)

    reset(driver)
    start = observe()
    start_fp = fingerprint(start)
    start_node = _node_of(start_fp, start)
    screen_map.nodes[start_fp.value] = start_node
    discovered(start_node)
    emit()

    shortest_path: dict[str, list[Action]] = {start_fp.value: []}
    frontier: deque[tuple[str, Action]] = deque(
        (start_fp.value, action) for action in candidate_actions(start)
    )
    tried: set[tuple[str, str]] = set()
    steps = 0

    while frontier and len(screen_map.nodes) < max_screens and steps < max_steps:
        src_fp, action = frontier.popleft()
        key = (src_fp, action.target)
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
            driver.tap(action.as_selector())
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
            dst_node = _node_of(dst_fp, landed)
            screen_map.nodes[dst_fp.value] = dst_node
            shortest_path[dst_fp.value] = path
            for next_action in candidate_actions(landed):
                frontier.append((dst_fp.value, next_action))
            discovered(dst_node)
        emit()

    return screen_map


def _replay(driver: base.Driver, path: list[Action], settle: Settle | None) -> bool:
    """Re-walk a recorded path from the current (clean) state. Returns False if a step no longer
    resolves — the app changed under us — so the caller skips this frontier entry."""
    for action in path:
        try:
            driver.tap(action.as_selector())
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
            }
            for node in sorted(screen_map.nodes.values(), key=lambda n: n.fingerprint)
        ],
        "edges": [{"src": e.src, "action": e.action, "dst": e.dst} for e in screen_map.edges],
        "crashes": [{"path": list(c.path)} for c in screen_map.crashes],
    }

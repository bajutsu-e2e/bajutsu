"""Autonomous crawl engine core (BE-0038).

Breadth-first exploration of an app over the `Driver` abstraction, producing a screen map of
the reachable screens and the transitions between them. This is the deterministic engine only —
no AI and no Simulator wiring (those land in later slices). The determinism boundary is the
whole point: a screen's *identity* (its fingerprint) and the *order* in which candidate actions
are tried are both pure functions of the element tree, so a crawl of an unchanged app explores
the same way as far as the app's own non-determinism allows. AI never decides anything here.

Traversal is a **forward walk with deterministic replay for backtracking**: app transitions are
usually irreversible, so the engine keeps acting on the screen it is already on until that screen
has no untried action left, then — only to reach another unexplored screen — resets to a clean
state and replays a recorded path to it (the same way `run` reaches any state). Walking forward
avoids paying a reset/replay for every single action. Every edge is still a replayable step, and
every node keeps a recorded path to it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bajutsu.drivers import base
from bajutsu.record import shows_app_ui

# Controls a tap drives forward: navigation / activation, toggling a switch, or switching tabs.
TAP_TRAITS = frozenset({"button", "link", "switch", "tab"})
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
    """A replayable action against a screen. `kind` is "tap", "type" (text input), "fill" (enter
    several fields in one step, to cross a precondition that needs more than one field), or
    "tap_point" (tap a normalized [0,1] coordinate — for a control the accessibility tree can't
    address, e.g. a custom tab bar a vision guide located). The element is named by `target` (its
    accessibility identifier — stable, preferred) or, for an id-less element, by `label` (+ `index`
    to disambiguate duplicates); a "type" carries the text in `value`, a "fill" its (id, value)
    pairs in `fields`, a "tap_point" its (x, y) in `point` (`label` optional, for logging). All
    fields are hashable so an Action can key the frontier / tried set."""

    kind: str
    target: str = ""
    label: str | None = None
    index: int | None = None
    value: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    point: tuple[float, float] | None = None

    @property
    def key(self) -> str:
        """Stable identity for de-duplication and the frontier: the id, the label[#index], the
        fill's field set, or the normalized coordinate."""
        if self.kind == "fill":
            return "fill:" + ",".join(i for i, _ in self.fields)
        if self.kind == "tap_point" and self.point is not None:
            return f"@@{self.point[0]:.4f},{self.point[1]:.4f}"
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
        if self.kind == "fill":
            return f"fill {len(self.fields)} fields"
        if self.kind == "tap_point" and self.point is not None:
            if self.label:
                return f"tap tab {self.label!r}"
            return f"tap point ({self.point[0]:.2f}, {self.point[1]:.2f})"
        what = self.target or (self.label or "?")
        if self.kind == "type" and self.value:
            return f"type {what}={self.value!r}"
        return f"{self.kind} {what}"

    def perform(self, driver: base.Driver) -> None:
        """Execute against the live screen: a type action focuses the field (tap) then enters its
        value; a fill does that for each of its fields in order; a tap_point taps a coordinate (the
        normalized point scaled to the live screen size); a tap just taps. Replayable because every
        selector is id- or label-based and every coordinate is normalized to the screen."""
        if self.kind == "fill":
            for fid, val in self.fields:
                driver.tap({"id": fid})
                driver.type_text(val)
            return
        if self.kind == "tap_point" and self.point is not None:
            w, h = _screen_size(driver)
            driver.tap_point((self.point[0] * w, self.point[1] * h))
            return
        driver.tap(self.as_selector())
        if self.kind == "type":
            driver.type_text(self.value or "")


@dataclass(frozen=True)
class Node:
    """A discovered screen: its fingerprint, the identifiers present, the candidate action keys
    leaving it, `blocked` — actionable controls present but disabled (known but un-pressable until a
    precondition is met) — and `targets`: per candidate action, the on-screen rectangle it taps,
    normalized to [0,1] of the screen and keyed by the action's description, so the web UI can
    highlight on the screenshot where a transition's tap lands."""

    fingerprint: str
    kind: str
    ids: tuple[str, ...]
    actions: tuple[str, ...]
    blocked: tuple[str, ...] = ()
    targets: tuple[tuple[str, tuple[float, float, float, float]], ...] = ()


@dataclass(frozen=True)
class Edge:
    """A transition: taking `action` from screen `src` landed on screen `dst`. `alert` holds the
    OS-prompt button(s) the guard dismissed during this transition (empty when none) — so the
    graph can show that the step required tapping through a system alert."""

    src: str
    action: str
    dst: str
    alert: tuple[str, ...] = ()


@dataclass(frozen=True)
class Crash:
    """A path (sequence of action descriptions) whose last action collapsed the app UI."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class Alert:
    """An OS prompt that appeared mid-crawl and was dismissed by the alert guard: `path` is the
    action sequence that triggered it, `buttons` the dismiss button(s) tapped to clear it."""

    path: tuple[str, ...]
    buttons: tuple[str, ...]


@dataclass(frozen=True)
class Pruned:
    """A candidate operation skipped because the same operation was already claimed by another
    screen — a *global* control (e.g. a tab switch) the crawl explores once instead of from every
    screen that shows it. `src` is the screen where it was skipped, `action` its description, `key`
    its replay identity, `owner` the screen that did explore it, and `path` the replayable action
    sequence to reach `src` and perform the op (so a resume can re-walk to here). The WebUI shows
    these struck through, and a viewer can tap one to resume exploring that branch from `src`."""

    src: str
    action: str
    key: str
    owner: str
    path: tuple[Action, ...] = ()


@dataclass
class ScreenMap:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    crashes: list[Crash] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    # The exploration plan: still-untried operations per screen fingerprint (what the crawl will
    # try next), refreshed as it advances so a reader can visualize the frontier live.
    plan: dict[str, list[str]] = field(default_factory=dict)
    # Operations pruned as duplicate global controls (explored once from their owner screen).
    pruned: list[Pruned] = field(default_factory=list)
    # Why the crawl stopped: "completed" (frontier exhausted — everything reachable in the model
    # was explored), "max_screens", or "max_steps" (a budget was hit, so screens may remain).
    stop_reason: str = ""


# Fires after each change to the map (a new node, edge, or crash). Pure observation so a caller
# can stream the screen map as it grows (the web UI's live graph) — it never influences which
# screen is explored next or how a screen is identified, so the crawl stays deterministic.
OnEvent = Callable[[ScreenMap], None]

# Fires once per newly discovered screen, while the driver is still positioned on it — the moment
# to capture a per-screen artifact (a screenshot). Pure observation, like `OnEvent`.
OnNode = Callable[["Node"], None]


def _screen_size(driver: base.Driver) -> tuple[float, float]:
    """Full-screen size in points: the largest element frame (the app window). A vision-located
    coordinate is stored normalized to [0,1] and replayed against this, so it maps to point-space
    regardless of the device's pixel scale."""
    frames = [f for el in driver.query() if (f := el.get("frame"))]
    return (max((f[2] for f in frames), default=0.0), max((f[3] for f in frames), default=0.0))


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
    its *interactive* state differs — disabled (`!`), a filled input (`=`), or a selected/toggled
    control (`+`). An enabled, empty, unselected element contributes just its id, so screens with
    no such state hash exactly as the plain id set did. Folding these in makes the reachable
    states distinct (form empty vs filled, switch off vs on), so the crawl explores the
    combinations of control states — and can act on a control it just enabled."""
    ident = _id_of(element) or ""
    traits = _traits(element)
    suffix = ""
    if base.Trait.NOT_ENABLED in traits:
        suffix += "!"
    if INPUT_TRAITS & traits and (element.get("value") or ""):
        suffix += "="
    if base.Trait.SELECTED in traits:
        suffix += "+"
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
    """The deterministic guide (`--guide off`): the replayable operations to try from a screen.

    - Tap each enabled, id-bearing tab / button / link / switch, **tabs first** so the crawl
      switches through a tab bar before drilling into a tab's own content.
    - Type a placeholder into **each** empty, enabled text field — exploring the combinations of
      fill states, since a transition can depend on which fields are set (e.g. a per-field
      validation message), not just on all of them.
    - When two or more fields are empty, also offer one **compound fill** of them all. A single
      fill at a time can't cross a gate that needs several fields when an intermediate fill isn't
      observable (a masked password exposes no value, so filling it alone doesn't change the
      screen) — filling them together flips the gate in one observable step.

    Disabled controls (`notEnabled`) are skipped (tapping them is a no-op; they're reported as
    blocked instead); id-less controls are skipped (replay needs a stable selector).
    """
    # Order taps tabs-first, then the other controls: switching the whole view (a tab) is explored
    # before drilling into a tab's own content. Within each priority class, deterministic id order.
    tap_priority: dict[str, int] = {}
    for el in elements:
        i = _id_of(el)
        if i and _is_enabled(el) and TAP_TRAITS & _traits(el):
            pri = 0 if "tab" in _traits(el) else 1
            tap_priority[i] = min(tap_priority.get(i, 2), pri)
    taps = sorted(tap_priority, key=lambda i: (tap_priority[i], i))
    actions = [Action("tap", target=t) for t in taps]
    empty_fields = sorted(
        (i, _input_value(el))
        for el in elements
        if (i := _id_of(el))
        and _is_enabled(el)
        and INPUT_TRAITS & _traits(el)
        and not (el.get("value") or "")
    )
    actions += [Action("type", target=fid, value=val) for fid, val in empty_fields]
    if len(empty_fields) >= 2:
        actions.append(Action("fill", fields=tuple(empty_fields)))
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


@dataclass(frozen=True)
class GuideContext:
    """Side information for the guide about how this screen was reached — currently the OS-alert
    button(s) just dismissed to get here, so an AI guide can factor them into its next moves."""

    dismissed: tuple[str, ...] = ()


# A guide proposes the replayable actions to try from a screen, given how it was reached
# (`GuideContext`). The default is the deterministic `candidate_actions`; an AI guide (BE-0038
# `--guide ai`) proposes richer operations and realistic inputs. Either way the guide only chooses
# *what to try* — screen identity, transition/crash detection, and the screen map stay
# deterministic and AI-free, so the crawl is never a verdict.
Guide = Callable[[base.Driver, list[base.Element], GuideContext], list[Action]]

# Dismisses anything covering the app (an OS alert) and returns the button label(s) it tapped.
ClearBlocking = Callable[[base.Driver], list[str]]


def _deterministic_guide(
    _driver: base.Driver, elements: list[base.Element], _context: GuideContext
) -> list[Action]:
    return candidate_actions(elements)


def _node_of(fp: Fingerprint, elements: list[base.Element], actions: list[Action]) -> Node:
    return Node(
        fingerprint=fp.value,
        kind=fp.kind,
        ids=tuple(sorted({i for el in elements if (i := _id_of(el))})),
        actions=tuple(a.key for a in actions),
        blocked=tuple(blocked_controls(elements)),
        targets=_action_targets(elements, actions),
    )


def _bbox(
    frames: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    """The bounding box covering several element frames (used for a multi-field fill)."""
    x = min(f[0] for f in frames)
    y = min(f[1] for f in frames)
    return (x, y, max(f[0] + f[2] for f in frames) - x, max(f[1] + f[3] for f in frames) - y)


def _action_rect(
    elements: list[base.Element], action: Action
) -> tuple[float, float, float, float] | None:
    """The point-space rectangle the action taps: the target element's frame (by id, or by
    label + index for an id-less element), or the bounding box of a fill's fields. None when the
    element can't be located."""
    if action.kind == "fill":
        frames = [
            f
            for fid, _ in action.fields
            for el in elements
            if _id_of(el) == fid and (f := el.get("frame"))
        ]
        return _bbox(frames) if frames else None
    if action.target:
        return next(
            (f for el in elements if _id_of(el) == action.target and (f := el.get("frame"))), None
        )
    if action.label is not None:
        matches = [
            f for el in elements if el.get("label") == action.label and (f := el.get("frame"))
        ]
        idx = action.index or 0
        return matches[idx] if idx < len(matches) else None
    return None


def _action_targets(
    elements: list[base.Element], actions: list[Action]
) -> tuple[tuple[str, tuple[float, float, float, float]], ...]:
    """For each action, the rectangle it taps normalized to [0,1] of the screen, keyed by the
    action's description (matching the edge/plan strings the web UI shows) — so the UI can highlight
    where a transition's tap lands on the screenshot. A coordinate tap (a vision-located tab) yields
    a small box around its point; a tap/type/fill yields its element frame over the screen size."""
    w = max((f[2] for el in elements if (f := el.get("frame"))), default=0.0)
    h = max((f[3] for el in elements if (f := el.get("frame"))), default=0.0)
    if w <= 0 or h <= 0:
        return ()
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for a in actions:
        if a.kind == "tap_point" and a.point is not None:
            px, py = a.point
            out.append((a.describe(), (max(0.0, px - 0.06), max(0.0, py - 0.035), 0.12, 0.07)))
            continue
        rect = _action_rect(elements, a)
        if rect is not None:
            x, y, fw, fh = rect
            out.append((a.describe(), (x / w, y / h, fw / w, fh / h)))
    return tuple(out)


def crawl(
    driver: base.Driver,
    reset: Reset,
    *,
    max_screens: int = 50,
    max_steps: int = 200,
    settle: Settle | None = None,
    clear_blocking: ClearBlocking | None = None,
    guide: Guide | None = None,
    prune_global: bool = False,
    base_map: ScreenMap | None = None,
    seed_path: list[Action] | None = None,
    seed_ops: list[Action] | None = None,
    on_event: OnEvent | None = None,
    on_node: OnNode | None = None,
) -> ScreenMap:
    """Crawl by a forward walk, resetting + replaying only to backtrack to an unexplored screen.

    `reset` returns the app to a clean starting state (erase/boot/launch on a real device; in
    tests, restoring the start screen). `settle`, if given, waits for the screen to stabilize
    after an action (a condition wait — never a fixed sleep); it is omitted when the driver is
    synchronous. `clear_blocking`, if given, dismisses anything covering the app (e.g. an OS
    alert) at each observation, so a system prompt isn't mistaken for a crash. `guide` proposes
    the actions to try from a screen (default: the deterministic `candidate_actions`; an AI guide
    proposes richer operations) — it only chooses *what to try*, never what happened. `on_event`,
    if given, fires after each new node, edge, or crash so a caller can stream the growing screen
    map. `on_node`, if given, fires once per newly discovered
    screen while the driver is still on it (to capture a screenshot). Stops at `max_screens`
    distinct screens or `max_steps` actions, whichever first.
    """
    guide = guide or _deterministic_guide
    # Resume continues an existing map (`base_map`): replay `seed_path` back to a pruned branch's
    # screen, then explore from it with `seed_ops` as that screen's frontier, appending findings.
    screen_map = base_map if base_map is not None else ScreenMap()
    dismissed: list[str] = []  # OS-alert buttons cleared during the most recent observe()

    def observe() -> list[base.Element]:
        if settle is not None:
            settle(driver)
        # Dismiss an OS alert (so it isn't read as a crash) and remember what was tapped, so the
        # caller can record it and feed it to the guide's next strategy.
        dismissed[:] = clear_blocking(driver) if clear_blocking is not None else []
        return driver.query()

    def emit() -> None:
        # Refresh the plan (the live frontier: still-untried operations per screen) before each
        # notification, so a watcher sees what the crawl will try next as it advances.
        screen_map.plan = {fp: [a.describe() for a in acts] for fp, acts in pending.items() if acts}
        if on_event is not None:
            on_event(screen_map)

    # When pruning global controls, the first screen to offer an operation (by replay key) claims
    # and explores it; later screens that offer the same key skip it. A control with a stable id
    # reused across screens — a tab bar, a nav button — collides and is pruned to one exploration;
    # a screen-specific control has a unique key and never collides.
    claimed: dict[str, str] = {}

    def discover(
        fp: Fingerprint, elements: list[base.Element], context: GuideContext
    ) -> list[Action]:
        # Driver is on this screen; ask the guide for its actions (once, given how we got here),
        # record the node, and let the on_node hook capture its screenshot — all before reset.
        actions = guide(driver, elements, context)
        node = _node_of(fp, elements, actions)
        screen_map.nodes[fp.value] = node
        if on_node is not None:
            on_node(node)
        if not prune_global:
            return actions
        kept: list[Action] = []
        for a in actions:
            owner = claimed.get(a.key)
            if owner is not None and owner != fp.value:
                path = (*path_to.get(fp.value, []), a)  # replay to src, then the pruned op
                screen_map.pruned.append(Pruned(fp.value, a.describe(), a.key, owner, path))
            else:
                claimed.setdefault(a.key, fp.value)
                kept.append(a)
        return kept

    # A known replayable path to each discovered screen (from a clean reset), and the still-untried
    # actions per screen. The strategy is a *forward walk*: keep acting on the screen the driver is
    # already on until it has no untried action left, and only reset + replay to reach another
    # screen when the current one is exhausted — so we don't pay a reset/replay for every action.
    path_to: dict[str, list[Action]] = {}
    pending: dict[str, list[Action]] = {}

    reset(driver)
    start = observe()
    if dismissed:
        screen_map.alerts.append(Alert((), tuple(dismissed)))
    if seed_path is not None:
        # Resume: walk back to the pruned branch's screen, then explore onward from it.
        if not _replay(driver, seed_path, settle, clear_blocking):
            screen_map.stop_reason = "completed"
            emit()
            return screen_map
        landed = observe()
        start_fp = fingerprint(landed)
        path_to[start_fp.value] = list(seed_path)
        if start_fp.value not in screen_map.nodes:
            discover(start_fp, landed, GuideContext(tuple(dismissed)))
        pending[start_fp.value] = list(seed_ops or [])
    else:
        start_fp = fingerprint(start)
        path_to[start_fp.value] = []
        pending[start_fp.value] = discover(start_fp, start, GuideContext(tuple(dismissed)))
    emit()
    current_fp: str | None = start_fp.value  # the screen the driver is on (None ⇒ needs a reset)
    steps = 0

    while any(pending.values()) and len(screen_map.nodes) < max_screens and steps < max_steps:
        # Continue from the screen we're already on; else backtrack to the cheapest screen with an
        # untried action (shortest known path) and replay to it, dismissing any OS prompt en route.
        if current_fp is not None and pending.get(current_fp):
            src_fp = current_fp
        else:
            src_fp = min(
                (fp for fp, acts in pending.items() if acts),
                key=lambda fp: (len(path_to[fp]), fp),
            )
            reset(driver)
            observe()
            if not _replay(driver, path_to[src_fp], settle, clear_blocking):
                pending[src_fp] = []  # the path no longer resolves — drop this screen
                current_fp = None
                continue
            current_fp = src_fp

        action = pending[src_fp].pop(0)  # deterministic order
        steps += 1
        path = [*path_to[src_fp], action]
        try:
            action.perform(driver)
        except base.SelectorError:
            current_fp = None  # unknown state — force a reset before the next action
            continue
        landed = observe()
        # An OS prompt cleared while landing is recorded against the triggering path, and fed to
        # the destination's guide as context for its next strategy.
        reached = GuideContext(tuple(dismissed))
        if dismissed:
            screen_map.alerts.append(Alert(tuple(a.describe() for a in path), tuple(dismissed)))

        if not is_app_alive(landed):
            screen_map.crashes.append(Crash(tuple(a.describe() for a in path)))
            current_fp = None  # the app collapsed — reset to keep going
            emit()
            continue

        dst_fp = fingerprint(landed)
        screen_map.edges.append(Edge(src_fp, action.describe(), dst_fp.value, tuple(dismissed)))
        if dst_fp.value not in screen_map.nodes:
            path_to[dst_fp.value] = path
            pending[dst_fp.value] = discover(dst_fp, landed, reached)
        current_fp = dst_fp.value  # the driver is on dst now — keep walking forward from here
        emit()

    # Why we stopped: no screen has an untried action (everything reachable in the model was
    # explored), or a budget was hit and untried actions remain.
    if not any(pending.values()):
        screen_map.stop_reason = "completed"
    elif len(screen_map.nodes) >= max_screens:
        screen_map.stop_reason = "max_screens"
    else:
        screen_map.stop_reason = "max_steps"
    emit()
    return screen_map


def action_to_dict(a: Action) -> dict[str, object]:
    """A JSON-friendly dict of an Action, omitting empty fields — so a replayable path can be
    persisted (in `pruned`) and reconstructed for a resume."""
    d: dict[str, object] = {"kind": a.kind}
    if a.target:
        d["target"] = a.target
    if a.label is not None:
        d["label"] = a.label
    if a.index is not None:
        d["index"] = a.index
    if a.value is not None:
        d["value"] = a.value
    if a.fields:
        d["fields"] = [list(f) for f in a.fields]
    if a.point is not None:
        d["point"] = list(a.point)
    return d


def action_from_dict(d: dict[str, Any]) -> Action:
    """Rebuild an Action from `action_to_dict` (tolerant of missing keys)."""
    point = d.get("point")
    return Action(
        kind=str(d.get("kind") or "tap"),
        target=str(d.get("target") or ""),
        label=d.get("label"),
        index=d.get("index"),
        value=d.get("value"),
        fields=tuple((str(f[0]), str(f[1])) for f in (d.get("fields") or [])),
        point=(float(point[0]), float(point[1])) if point else None,
    )


def screenmap_from_dict(data: dict[str, Any]) -> ScreenMap:
    """Rebuild a ScreenMap from `screenmap_dict` output — so a saved map can be loaded as the base
    for a resume (continue exploring a pruned branch and append to it)."""
    nodes: dict[str, Node] = {}
    for n in data.get("nodes") or []:
        targets = tuple(
            (desc, (float(r[0]), float(r[1]), float(r[2]), float(r[3])))
            for desc, r in (n.get("targets") or {}).items()
        )
        node = Node(
            fingerprint=str(n["fingerprint"]),
            kind=str(n.get("kind") or "id"),
            ids=tuple(n.get("ids") or []),
            actions=tuple(n.get("actions") or []),
            blocked=tuple(n.get("blocked") or []),
            targets=targets,
        )
        nodes[node.fingerprint] = node
    pruned = [
        Pruned(
            str(p["src"]),
            str(p["action"]),
            str(p["key"]),
            str(p["owner"]),
            tuple(action_from_dict(a) for a in (p.get("path") or [])),
        )
        for p in data.get("pruned") or []
    ]
    return ScreenMap(
        nodes=nodes,
        edges=[
            Edge(str(e["src"]), str(e["action"]), str(e["dst"]), tuple(e.get("alert") or []))
            for e in data.get("edges") or []
        ],
        crashes=[Crash(tuple(c.get("path") or [])) for c in data.get("crashes") or []],
        alerts=[
            Alert(tuple(a.get("path") or []), tuple(a.get("buttons") or []))
            for a in data.get("alerts") or []
        ],
        plan={str(fp): list(ops) for fp, ops in (data.get("plan") or {}).items()},
        pruned=pruned,
        stop_reason=str(data.get("stop_reason") or ""),
    )


def _replay(
    driver: base.Driver,
    path: list[Action],
    settle: Settle | None,
    clear_blocking: ClearBlocking | None,
) -> bool:
    """Re-walk a recorded path from the current (clean) state, dismissing any OS prompt that pops
    between steps (so a path through a system alert replays). Returns False if a step no longer
    resolves — the app changed under us — so the caller skips this frontier entry."""
    for action in path:
        try:
            action.perform(driver)
        except base.SelectorError:
            return False
        if settle is not None:
            settle(driver)
        if clear_blocking is not None:
            clear_blocking(driver)
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
                "targets": {desc: list(rect) for desc, rect in node.targets},
            }
            for node in sorted(screen_map.nodes.values(), key=lambda n: n.fingerprint)
        ],
        "edges": [
            {"src": e.src, "action": e.action, "dst": e.dst, "alert": list(e.alert)}
            for e in screen_map.edges
        ],
        "crashes": [{"path": list(c.path)} for c in screen_map.crashes],
        "alerts": [{"path": list(a.path), "buttons": list(a.buttons)} for a in screen_map.alerts],
        "plan": {fp: list(ops) for fp, ops in sorted(screen_map.plan.items())},
        "pruned": [
            {
                "src": p.src,
                "action": p.action,
                "key": p.key,
                "owner": p.owner,
                "path": [action_to_dict(a) for a in p.path],
            }
            for p in screen_map.pruned
        ],
        "stop_reason": screen_map.stop_reason,
    }

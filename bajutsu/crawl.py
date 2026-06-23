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

The engine scales out across **N booted simulators** (BE-0064): a *coordinator* owns the shared
screen map, frontier and budgets under one lock, while *workers* each drive their own simulator,
taking frontier entries, exploring them, and running the guide on the screen they land on — so the
guide's AI round-trips overlap across devices, the primary speedup. What parallelism relaxes is
only the *exploration order* and the recorded canonical `path_to` (which worker reaches a screen
first is scheduling-dependent); screen identity, transition/crash detection and the map's content
stay pure deterministic functions of the element tree, so the crawl is never a verdict. A single
worker (the default) walks exactly as the serial engine always did.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from bajutsu import env
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

# Consecutive device errors after which a parallel worker retires its simulator, so one wedged
# device can't busy-loop or block the others from finishing (BE-0064 failure isolation).
_MAX_WORKER_DEVICE_ERRORS = 3

Reset = Callable[[base.Driver], None]
Settle = Callable[[base.Driver], None]
# Whether the app is still alive after an action — the crash signal. Default reads the iOS
# accessibility tree (`is_app_alive`); a backend with different crash signals (web: pageerror /
# HTTP status / blank DOM, BE-0066) injects its own. It receives the driver so a backend can read
# its own health state, plus the landed elements. It only observes; it never decides pass/fail.
AliveCheck = Callable[[base.Driver, list[base.Element]], bool]
# Heal a wedged worker lane in place so the crawl keeps going (BE-0077). A backend whose lane
# can be torn down and relaunched cheaply (web: a fresh browser process) injects this; the worker
# calls it on a device fault instead of retiring the lane. iOS leaves it unset (a wedged simulator
# isn't cheaply recoverable mid-crawl), so its workers retire as before. It only heals; it never
# decides pass/fail.
Recover = Callable[[base.Driver], None]
# Builds one extra worker's `(driver, reset)` lane. The engine calls it *inside that worker's own
# thread*, so a thread-affine driver (Playwright's sync API, BE-0077) is created on the very thread
# that drives it; idb is thread-agnostic, so this is also where the run pool builds its lanes.
WorkerFactory = Callable[[], "tuple[base.Driver, Reset]"]


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

# Fires once per newly discovered screen, while the worker's driver is still positioned on it — the
# moment to capture a per-screen artifact (a screenshot). It receives that worker's driver, so a
# parallel crawl screenshots each screen on whichever simulator discovered it. Pure observation,
# like `OnEvent`.
OnNode = Callable[[base.Driver, "Node"], None]


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
    return hashlib.sha1("\n".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()


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
    is_alive: AliveCheck | None = None,
    guide: Guide | None = None,
    prune_global: bool = False,
    base_map: ScreenMap | None = None,
    seed_path: list[Action] | None = None,
    seed_ops: list[Action] | None = None,
    on_event: OnEvent | None = None,
    on_node: OnNode | None = None,
    recover: Recover | None = None,
    extra_workers: Sequence[WorkerFactory] | None = None,
) -> ScreenMap:
    """Crawl by a forward walk, resetting + replaying only to backtrack to an unexplored screen.

    `reset` returns the app to a clean starting state (erase/boot/launch on a real device; in
    tests, restoring the start screen). `settle`, if given, waits for the screen to stabilize
    after an action (a condition wait — never a fixed sleep); it is omitted when the driver is
    synchronous. `clear_blocking`, if given, dismisses anything covering the app (e.g. an OS
    alert) at each observation, so a system prompt isn't mistaken for a crash. `is_alive`, if
    given, replaces the default iOS crash signal (the accessibility-tree check) so another backend
    can supply its own (web: pageerror / HTTP status / blank DOM); it only observes. `guide` proposes
    the actions to try from a screen (default: the deterministic `candidate_actions`; an AI guide
    proposes richer operations) — it only chooses *what to try*, never what happened. `on_event`,
    if given, fires after each new node, edge, or crash so a caller can stream the growing screen
    map. `on_node`, if given, fires once per newly discovered screen while the discovering worker's
    driver is still on it (to capture a screenshot). `recover`, if given, heals a worker whose
    device wedged (web: relaunch its browser process) so the lane keeps crawling instead of
    retiring; only fires in a pool. Stops at `max_screens` distinct screens or `max_steps` actions,
    whichever first.

    `extra_workers` adds workers beyond the primary `(driver, reset)`: each is a *factory* the engine
    calls inside that worker's own thread to build one more `(driver, reset)` lane, which explores the
    *same* shared frontier on its own device/browser so the guide's AI round-trips overlap (BE-0064).
    Building inside the thread is what lets a thread-affine driver (Playwright's sync API, BE-0077) be
    created on the very thread that drives it. The default (none) is a single worker that walks exactly
    as the serial engine always did. A resumed crawl (`seed_path`) is one walk, so `extra_workers` is
    ignored for it.
    """
    guide = guide or _deterministic_guide
    # Default crash signal = the iOS accessibility-tree check; a backend can inject its own.
    alive = is_alive or (lambda _driver, elements: is_app_alive(elements))
    # Resume continues an existing map (`base_map`): replay `seed_path` back to a pruned branch's
    # screen, then explore from it with `seed_ops` as that screen's frontier, appending findings.
    screen_map = base_map if base_map is not None else ScreenMap()
    # The worker pool: the caller-built primary `(driver, reset)` (used on this thread for bootstrap
    # and the in-place walk), plus extra-worker factories the engine builds inside each spawned
    # thread (so a thread-affine driver is created on the thread that drives it). A resume is a
    # single branch walk, so the extras are dropped regardless of how many were offered.
    extra_factories = list(extra_workers or []) if seed_path is None else []
    # One bad device must not be able to sink a multi-device crawl, so a worker isolates device
    # errors only in a pool; a lone worker lets them propagate (the serial engine's behavior).
    isolate = (1 + len(extra_factories)) > 1

    # --- coordinator: the shared map, frontier and budgets, all under one lock ---
    cond = threading.Condition()
    # A known replayable path to each discovered screen (set once at discovery, never mutated), and
    # the still-untried actions per screen. The strategy is a *forward walk*: a worker keeps acting
    # on the screen its driver is already on until that screen has no untried action left, and only
    # resets + replays to reach another screen when the current one is exhausted.
    path_to: dict[str, list[Action]] = {}
    pending: dict[str, list[Action]] = {}
    # When pruning global controls, the first screen to offer an operation (by replay key) claims
    # and explores it; later screens that offer the same key skip it. A control with a stable id
    # reused across screens — a tab bar, a nav button — collides and is pruned to one exploration.
    claimed: dict[str, str] = {}
    discovering: set[str] = (
        set()
    )  # fingerprints a worker is currently discovering (guide in flight)
    steps = 0  # shared action budget counter
    active = (
        0  # workers holding a popped action (mid step) — the crawl is done at 0 with no frontier
    )
    stopped = False  # a budget was hit; no worker takes more work
    failure: list[Exception] = []  # the first unexpected worker error, re-raised after join

    def _observe(d: base.Driver) -> tuple[list[base.Element], list[str]]:
        # Per-worker so concurrent observations don't clobber each other: settle, dismiss anything
        # covering the app (so an OS prompt isn't read as a crash), and return the tree + what was
        # tapped to clear it (recorded against the path and fed to the guide's next strategy).
        if settle is not None:
            settle(d)
        dismissed = clear_blocking(d) if clear_blocking is not None else []
        return d.query(), dismissed

    def _emit() -> None:  # call holding the lock (it reads `pending`)
        # Refresh the plan (the live frontier: still-untried operations per screen) before each
        # notification, so a watcher sees what the crawl will try next as it advances.
        screen_map.plan = {fp: [a.describe() for a in acts] for fp, acts in pending.items() if acts}
        if on_event is not None:
            on_event(screen_map)

    def _claim(fp_value: str, actions: list[Action]) -> list[Action]:
        # Holding the lock. Without pruning, every action is the screen's own to explore. With it,
        # an op already claimed by another screen is recorded as Pruned (with a replay path) instead.
        if not prune_global:
            return list(actions)
        kept: list[Action] = []
        for a in actions:
            owner = claimed.get(a.key)
            if owner is not None and owner != fp_value:
                path = (*path_to.get(fp_value, []), a)  # replay to src, then the pruned op
                screen_map.pruned.append(Pruned(fp_value, a.describe(), a.key, owner, path))
            else:
                claimed.setdefault(a.key, fp_value)
                kept.append(a)
        return kept

    def _finish(reason: str) -> None:  # holding the lock
        # Signal the stop; the single authoritative final `_emit()` runs after join (so it captures
        # any late records a worker added between this signal and its own exit, without each finish
        # re-writing the whole map).
        nonlocal stopped
        if not screen_map.stop_reason:
            screen_map.stop_reason = reason
        stopped = True
        cond.notify_all()

    def _bootstrap() -> str | None:
        # Once, before workers start (so single-threaded — no lock needed): reach the entry screen
        # on the primary device and seed the frontier. Returns the fingerprint the primary worker is
        # left on, or None if a resume's replay no longer resolves (nothing to explore).
        d, rst = driver, reset
        rst(d)
        start, dismissed = _observe(d)
        if dismissed:
            screen_map.alerts.append(Alert((), tuple(dismissed)))
        if seed_path is not None:
            if not _replay(d, seed_path, settle, clear_blocking):
                screen_map.stop_reason = "completed"
                _emit()
                return None
            landed, dismissed = _observe(d)
            start_fp = fingerprint(landed)
            path_to[start_fp.value] = list(seed_path)
            if start_fp.value not in screen_map.nodes:
                # Single-threaded here (workers not yet started), so publishing without the lock is
                # safe; we claim ownership but the resume sets `pending` to its own ops below.
                node, actions = _discover(d, start_fp, landed, GuideContext(tuple(dismissed)))
                _publish(start_fp, node, actions)
            pending[start_fp.value] = list(seed_ops or [])  # explore the resumed branch's op(s)
        else:
            start_fp = fingerprint(start)
            path_to[start_fp.value] = []
            node, actions = _discover(d, start_fp, start, GuideContext(tuple(dismissed)))
            pending[start_fp.value] = _publish(start_fp, node, actions)
        _emit()
        return start_fp.value

    def _discover(
        d: base.Driver, fp: Fingerprint, elements: list[base.Element], context: GuideContext
    ) -> tuple[Node, list[Action]]:
        # The driver is on this screen: ask the guide what to try (the slow AI step), build the
        # node, and screenshot it. None of this touches shared coordinator state, so a worker runs
        # it off-lock — that is what lets the guide's AI round-trips overlap across simulators. The
        # caller then publishes the result under the lock (see `_publish`).
        actions = guide(d, elements, context)
        node = _node_of(fp, elements, actions)
        if on_node is not None:
            on_node(d, node)
        return node, actions

    def _publish(fp: Fingerprint, node: Node, actions: list[Action]) -> list[Action]:
        # Holding the lock: register the node and claim its operations. Every mutation of shared
        # state — `screen_map.nodes`, `screen_map.pruned` (via `_claim`), and the `claimed` table —
        # happens here, so the coordinator lock serializes it against other workers and `on_event`
        # readers. Returns the screen's frontier (its actions not already claimed elsewhere).
        screen_map.nodes[fp.value] = node
        return _claim(fp.value, actions)

    def _worker(d: base.Driver, rst: Reset, current_fp: str | None) -> None:
        nonlocal steps, active
        errors = 0  # consecutive device faults → retire so a wedged device can't busy-loop

        def _give_back(src_fp: str, action: Action) -> bool:
            # Pool failure isolation: a device misbehaved (a broken replay or a device error), so
            # hand the popped action back to the front of its frontier — a healthy worker retries
            # it — and report whether this worker has faulted enough times in a row to retire.
            nonlocal active, errors
            with cond:
                pending[src_fp].insert(0, action)
                active -= 1
                cond.notify_all()
            errors += 1
            return errors >= _MAX_WORKER_DEVICE_ERRORS

        while True:
            with cond:
                while True:
                    if stopped:
                        return
                    if len(screen_map.nodes) >= max_screens:
                        _finish("max_screens")
                        return
                    if steps >= max_steps:
                        _finish("max_steps")
                        return
                    # Continue from the screen this worker is on; else backtrack to the cheapest
                    # frontier entry (shortest known path, then fingerprint) and replay to it.
                    if current_fp is not None and pending.get(current_fp):
                        src_fp, replay_needed = current_fp, False
                    elif candidates := [fp for fp, acts in pending.items() if acts]:
                        src_fp = min(candidates, key=lambda fp: (len(path_to[fp]), fp))
                        replay_needed = True
                    elif active == 0:
                        _finish("completed")  # no frontier and no worker in flight → all explored
                        return
                    else:
                        cond.wait()  # another worker is mid-step; it may add frontier
                        continue
                    action = pending[src_fp].pop(0)  # deterministic order
                    src_path = list(path_to[src_fp])
                    steps += 1
                    active += 1
                    break

            # --- device work, off-lock: this is what overlaps across simulators ---
            path = [*src_path, action]
            try:
                if replay_needed:
                    rst(d)
                    _observe(d)
                    if not _replay(d, src_path, settle, clear_blocking):
                        if isolate:  # may be this device misbehaving — let a healthy worker retry
                            current_fp = None
                            if _give_back(src_fp, action):
                                return
                            continue
                        with cond:  # the path no longer resolves — drop this screen
                            pending[src_fp] = []
                            active -= 1
                            cond.notify_all()
                        current_fp = None
                        continue
                action.perform(d)
                landed, dismissed = _observe(d)
            except base.SelectorError:
                with cond:  # the selector no longer resolves — drop this action, reset, move on
                    active -= 1
                    cond.notify_all()
                current_fp = None
                continue
            except env.DeviceError:
                if not isolate:
                    raise  # a lone worker surfaces the device failure, as the serial engine did
                current_fp = None
                retire = _give_back(src_fp, action)
                if recover is not None and not retire:
                    # The lane can be healed in place (web: relaunch its browser process), so do
                    # that and keep crawling rather than abandoning the worker (BE-0077). A clean
                    # step resets the fault counter, so only persistent wedging (MAX faults in a
                    # row) still retires. `recover(d)` runs outside any inner try on purpose: if even
                    # the relaunch fails, that real environment fault propagates (via `_run`, below)
                    # rather than being swallowed — a browser that can't relaunch isn't a transient
                    # wedge. Don't wrap this call in a try.
                    recover(d)
                elif retire:
                    return  # retire the wedged device so it can't keep stealing the frontier
                continue
            errors = 0

            # Pure deterministic reads, off-lock: the crash signal and the destination's identity.
            reached = GuideContext(tuple(dismissed))
            crashed = not alive(d, landed)
            dst_fp = fingerprint(landed)
            is_new = False
            with cond:
                if dismissed:
                    screen_map.alerts.append(
                        Alert(tuple(a.describe() for a in path), tuple(dismissed))
                    )
                if crashed:
                    screen_map.crashes.append(Crash(tuple(a.describe() for a in path)))
                    active -= 1
                    _emit()
                    cond.notify_all()
                    current_fp = None  # the app collapsed — reset to keep going
                    continue
                screen_map.edges.append(
                    Edge(src_fp, action.describe(), dst_fp.value, tuple(dismissed))
                )
                if dst_fp.value not in screen_map.nodes and dst_fp.value not in discovering:
                    discovering.add(dst_fp.value)  # reserve so two workers don't double-discover
                    path_to[dst_fp.value] = path
                    is_new = True
                else:
                    active -= 1  # a known/in-flight screen: just the edge, this step is done
                    _emit()
                    cond.notify_all()
            current_fp = dst_fp.value  # the driver is on dst now — keep walking forward from here
            if not is_new:
                continue
            # Discover the new screen: run the guide on THIS worker's driver, off-lock, so AI calls
            # on different simulators overlap. Then publish the node + frontier under the lock, so
            # every shared-state mutation stays serialized by the coordinator.
            node, actions = _discover(d, dst_fp, landed, reached)
            with cond:
                pending[dst_fp.value] = _publish(dst_fp, node, actions)
                discovering.discard(dst_fp.value)
                active -= 1
                _emit()
                cond.notify_all()

    def _run(d: base.Driver, rst: Reset, current_fp: str | None) -> None:
        # Surface an unexpected worker error after join (a bare thread would otherwise swallow it),
        # while device-error isolation is handled inside `_worker`.
        try:
            _worker(d, rst, current_fp)
        except Exception as exc:  # re-raised on the main thread after join
            with cond:
                failure.append(exc)
                _finish(screen_map.stop_reason or "completed")

    start_fp = _bootstrap()
    if start_fp is None:
        return screen_map  # a resume with nothing to replay (stop_reason set in bootstrap)

    def _run_extra(factory: WorkerFactory) -> None:
        # Build this lane's driver on *this* thread (the Playwright sync API is bound to its creating
        # thread — BE-0077), then walk. A lane that can't even start is surfaced after join, like any
        # other worker fault, rather than silently dropping a worker.
        try:
            d, rst = factory()
        except Exception as exc:
            with cond:
                failure.append(exc)
                _finish(screen_map.stop_reason or "completed")
            return
        _run(d, rst, None)

    # The primary worker is left on the entry screen; extras start cold and reset to a frontier.
    threads = [threading.Thread(target=_run_extra, args=(f,), daemon=True) for f in extra_factories]
    for t in threads:
        t.start()
    _run(driver, reset, start_fp)
    for t in threads:
        t.join()
    if failure:
        raise failure[0]

    # Safety net for the stop reason (a normal finish already set it under the lock), plus a final
    # event so the returned map and the last streamed snapshot match — including any late records a
    # worker added after the stop was signalled.
    if not screen_map.stop_reason:
        screen_map.stop_reason = (
            "completed"
            if not any(pending.values())
            else "max_screens"
            if len(screen_map.nodes) >= max_screens
            else "max_steps"
        )
    _emit()
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

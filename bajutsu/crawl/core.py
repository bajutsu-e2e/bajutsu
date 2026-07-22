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

from bajutsu import device_errors
from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements, shows_app_ui

# Controls a tap drives forward: navigation / activation, toggling a switch, or switching tabs.
TAP_TRAITS = frozenset({"button", "link", "switch", "tab"})
# Text inputs the crawl fills to satisfy a precondition (e.g. enabling a disabled submit button).
INPUT_TRAITS = frozenset({"textField", "searchField", "secureTextField"})
# Any interactive control — used by the structural fingerprint and blocked-control detection.
ACTIONABLE_TRAITS = TAP_TRAITS | INPUT_TRAITS
# Interactive-*state* traits (as opposed to a control's kind). `screen_identity` drops these from
# its structural signature so a control merely enabling/deselecting mid-batch isn't read as a screen
# transition (BE-0178); `fingerprint` keeps them, since the crawl explores distinct control states.
_STATE_TRAITS = frozenset({base.Trait.NOT_ENABLED, base.Trait.SELECTED})

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
# that drives it; the iOS backend is thread-agnostic, so this is also where the run pool builds its lanes.
WorkerFactory = Callable[[], "tuple[base.Driver, Reset]"]


@dataclass(frozen=True)
class Fingerprint:
    """A screen's identity.

    `kind` is "id" (stable, identifier-derived) or "structural" (the less-stable fallback for
    screens with too few accessibility identifiers).
    """

    value: str
    kind: str


@dataclass(frozen=True)
class Action:
    """A replayable action against a screen.

    `kind` is "tap", "type" (text input), "fill" (enter several fields in one step, to cross a
    precondition that needs more than one field), or "tap_point" (tap a normalized [0,1]
    coordinate — for a control the accessibility tree can't address, e.g. a custom tab bar a
    vision guide located). The element is named by `target` (its accessibility identifier —
    stable, preferred) or, for an id-less element, by `label` (+ `index` to disambiguate
    duplicates); a "type" carries the text in `value`, a "fill" its (id, value) pairs in `fields`,
    a "tap_point" its (x, y) in `point` (`label` optional, for logging). All fields are hashable so
    an Action can key the frontier / tried set.
    """

    kind: str
    target: str = ""
    label: str | None = None
    index: int | None = None
    value: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    point: tuple[float, float] | None = None

    @property
    def key(self) -> str:
        """Stable identity for de-duplication and the frontier.

        The id, the label[#index], the fill's field set, or the normalized coordinate.
        """
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
        """Execute against the live screen.

        A type action focuses the field (tap) then enters its value; a fill does that for each of
        its fields in order; a tap_point taps a coordinate (the normalized point scaled to the live
        screen size); a tap just taps. Replayable because every selector is id- or label-based and
        every coordinate is normalized to the screen.
        """
        if self.kind == "fill":
            for fid, val in self.fields:
                driver.tap({"id": fid})
                driver.type_text(val)
            return
        if self.kind == "tap_point" and self.point is not None:
            w, h = screen_size_from_elements(driver.query())
            driver.tap_point((self.point[0] * w, self.point[1] * h))
            return
        driver.tap(self.as_selector())
        if self.kind == "type":
            driver.type_text(self.value or "")


@dataclass(frozen=True)
class Node:
    """A discovered screen.

    Its fingerprint, the identifiers present, the candidate action keys leaving it, `blocked` —
    actionable controls present but disabled (known but un-pressable until a precondition is met) —
    and `targets`: per candidate action, the on-screen rectangle it taps, normalized to [0,1] of
    the screen and keyed by the action's description, so the web UI can highlight on the screenshot
    where a transition's tap lands.
    """

    fingerprint: str
    kind: str
    ids: tuple[str, ...]
    actions: tuple[str, ...]
    blocked: tuple[str, ...] = ()
    targets: tuple[tuple[str, tuple[float, float, float, float]], ...] = ()


@dataclass(frozen=True)
class Edge:
    """A transition: taking `action` from screen `src` landed on screen `dst`.

    `alert` holds the OS-prompt button(s) the guard dismissed during this transition (empty when
    none) — so the graph can show that the step required tapping through a system alert.
    """

    src: str
    action: str
    dst: str
    alert: tuple[str, ...] = ()


@dataclass(frozen=True)
class Crash:
    """A path whose last action collapsed the app UI.

    `path` holds the human-readable action descriptions (for the report); `actions` the structured,
    replayable sequence the same path is built from, so a deterministic repro scenario can be
    emitted from it (BE-0038). `actions` is empty for a map saved before crashes carried it.
    """

    path: tuple[str, ...]
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class Alert:
    """An OS prompt that appeared mid-crawl and was dismissed by the alert guard.

    `path` is the action sequence that triggered it, `buttons` the dismiss button(s) tapped to
    clear it.
    """

    path: tuple[str, ...]
    buttons: tuple[str, ...]


@dataclass(frozen=True)
class Pruned:
    """A candidate operation skipped because the same operation was already claimed by another screen.

    A *global* control (e.g. a tab switch) the crawl explores once instead of from every screen
    that shows it. `src` is the screen where it was skipped, `action` its description, `key` its
    replay identity, `owner` the screen that did explore it, and `path` the replayable action
    sequence to reach `src` and perform the op (so a resume can re-walk to here). The WebUI shows
    these struck through, and a viewer can tap one to resume exploring that branch from `src`.
    """

    src: str
    action: str
    key: str
    owner: str
    path: tuple[Action, ...] = ()


@dataclass
class _Work:
    """One reserved unit of frontier work, handed from `_select_next_work` to a worker.

    The popped `action`, the screen `src_fp` it came from and the `src_path` to replay to reach it,
    and whether the worker must reset+replay first (`replay_needed`) or is already standing on
    `src_fp`. Not frozen: `src_path` is a mutable list (it feeds `_replay`, typed `list[Action]`),
    so `frozen=True` would be only a shallow, misleading guarantee. It is created and consumed
    within the coordinator, never hashed or shared, so plain mutability is fine.
    """

    src_fp: str
    action: Action
    src_path: list[Action]
    replay_needed: bool


@dataclass
class ScreenMap:
    """The crawl's accumulated model: the discovered screens, transitions, crashes, alerts, the live exploration plan, and why it stopped."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    crashes: list[Crash] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    # The exploration plan: still-untried operations per screen fingerprint (what the crawl will
    # try next), refreshed as it advances so a reader can visualize the frontier live.
    plan: dict[str, list[str]] = field(default_factory=dict)
    # Operations pruned as duplicate global controls (explored once from their owner screen).
    pruned: list[Pruned] = field(default_factory=list)
    # The canonical replayable action path from the entry screen to each discovered screen, keyed by
    # fingerprint (empty for the entry screen itself). This is what turns a discovered screen into a
    # committable candidate flow scenario (`flows.py`, BE-0038).
    paths: dict[str, tuple[Action, ...]] = field(default_factory=dict)
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


def _id_of(element: base.Element) -> str | None:
    return element.get("identifier")


def _traits(element: base.Element) -> set[str]:
    return set(element.get("traits") or [])


def _is_enabled(element: base.Element) -> bool:
    """Whether the control is interactive (not flagged `notEnabled`); a disabled one is skipped and reported as blocked instead."""
    return base.Trait.NOT_ENABLED not in _traits(element)


def _input_value(element: base.Element) -> str:
    """A deterministic placeholder to type into a field (the `--guide off` path), good enough to clear "must be non-empty" preconditions but not validation-gated ones."""
    hint = f"{_id_of(element) or ''} {element.get('label') or ''}".lower()
    if "mail" in hint:
        return "test@example.com"
    if "secureTextField" in _traits(element):
        return "Test1234!"
    return "test"


def _fingerprint_token(element: base.Element) -> str:
    """An id-bearing element's contribution to the screen fingerprint: its id plus a marker for differing interactive state, so the crawl explores distinct control-state combinations."""
    ident = _id_of(element) or ""
    traits = _traits(element)
    # (present?, marker) in a fixed order — the markers append as `!`, `=`, `+`, so the hash stays
    # stable. An enabled, empty, unselected element matches none and contributes just its id.
    markers = (
        (base.Trait.NOT_ENABLED in traits, "!"),
        (bool(INPUT_TRAITS & traits and (element.get("value") or "")), "="),
        (base.Trait.SELECTED in traits, "+"),
    )
    return ident + "".join(marker for present, marker in markers if present)


def _reduce(
    elements: list[base.Element],
    id_token: Callable[[base.Element], str],
    *,
    stateless_structure: bool = False,
) -> tuple[str, str]:
    """Reduce a screen to `(hash, kind)` via id tokens or a structural fallback.

    The sorted id-derived tokens when the screen is instrumented (`kind="id"`), else a structural
    traits+frame hash (`kind="structural"`, the less-stable fallback for too few identifiers).
    `id_token` maps each id-bearing element to its contribution, so the caller chooses whether that
    token carries per-element state. `stateless_structure` additionally drops `_STATE_TRAITS` from
    the structural path's traits, so an id-poor screen's signature ignores enabled/selected changes
    too (screen_identity needs this; fingerprint keeps state on both paths).
    """
    if len({i for el in elements if (i := _id_of(el))}) >= _MIN_IDS_FOR_ID_FINGERPRINT:
        return _hash(sorted({id_token(el) for el in elements if _id_of(el)})), "id"

    def _struct(el: base.Element) -> str:
        traits = el.get("traits") or []
        if stateless_structure:
            traits = [t for t in traits if t not in _STATE_TRAITS]
        return f"{','.join(traits)}@{_frame_bucket(el)}"

    structure = sorted(_struct(el) for el in elements if ACTIONABLE_TRAITS & _traits(el))
    return _hash(structure), "structural"


def fingerprint(elements: list[base.Element]) -> Fingerprint:
    """Reduce a screen to a stable identity.

    Primary: the sorted set of accessibility identifiers (each tagged with its disabled/filled
    state — see `_fingerprint_token`), hashed — non-localized and data-independent, so it is
    stable across locales and minor content changes. Fallback (for screens with too few
    identifiers): a structural hash over the actionable elements' `(traits, frame-bucket)`, which
    is less stable and flagged as such.
    """
    return Fingerprint(*_reduce(elements, _fingerprint_token))


def screen_identity(elements: list[base.Element]) -> str:
    """A transition signature for BE-0178's intra-screen batch-abort check.

    Shares `fingerprint`'s id-or-structural reduction but keys the id path on the bare identifier and
    strips interactive-state traits from the structural path, deliberately omitting per-element
    *state* (a field's fill, a control's enabled/selected flags) on both. The record loop compares it
    after each batched step to abort on a genuine transition (elements appearing/disappearing, a
    navigation), not on a field the batch itself just filled or a Submit button it just enabled:
    `fingerprint` folds that state into its tokens, so using it here would abort the very form-fill
    batching the feature exists to enable. Determinism is unchanged — a pure comparison, no LLM.
    """
    value, kind = _reduce(elements, lambda el: _id_of(el) or "", stateless_structure=True)
    # Prefix with the reduction kind so an id-path signature can never accidentally equal a
    # structural-path one — crossing the id-count threshold is itself a transition worth aborting on.
    return f"{kind}:{value}"


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
    """Ids of actionable controls present but disabled (`notEnabled`).

    Known yet un-pressable until a precondition is met. Reported on each node so the screen map can
    flag the gap.
    """
    return sorted(
        {
            i
            for el in elements
            if (i := _id_of(el)) and not _is_enabled(el) and ACTIONABLE_TRAITS & _traits(el)
        }
    )


def is_app_alive(elements: list[base.Element]) -> bool:
    """Whether the app's own UI is showing (not collapsed under a system overlay or crashed).

    Reuses record's public check so "app UI vs. collapsed tree" has a single definition.
    """
    return shows_app_ui(elements)


@dataclass(frozen=True)
class GuideContext:
    """Side information for the guide about how this screen was reached.

    Currently the OS-alert button(s) just dismissed to get here, so an AI guide can factor them
    into its next moves.
    """

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
    """The point-space rectangle the action taps (target frame, or a fill's bounding box), or None when the element can't be located."""
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
    """Per action, the [0,1]-normalized rectangle it taps keyed by its description, so the web UI can highlight where a transition's tap lands on the screenshot."""
    # Single pass over elements for both bounds (each frame is read once, not twice).
    frames = [f for el in elements if (f := el.get("frame"))]
    w = max((f[2] for f in frames), default=0.0)
    h = max((f[3] for f in frames), default=0.0)
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


class _Coordinator:
    """The crawl's shared concurrent state behind one lock.

    Owns the screen map, the frontier (`path_to` shortest known paths + `pending` untried actions),
    the global-control `claimed` table, the in-flight `discovering` set, and the step/active/stopped
    budgets. Every mutation of that state goes through a method here, so the whole lock discipline
    lives in one reviewable place and the device-walk (`crawl`'s `_worker`) reads top to bottom with
    no `with cond:` blocks interleaved through it. Workers call these methods off their own driver
    threads; `path_to` / `pending` / `failure` / `screen_map` are read single-threaded by bootstrap
    and after join.
    """

    def __init__(
        self,
        screen_map: ScreenMap,
        *,
        max_screens: int,
        max_steps: int,
        prune_global: bool,
        on_event: OnEvent | None,
    ) -> None:
        self._cond = threading.Condition()
        self._sm = screen_map
        self._max_screens = max_screens
        self._max_steps = max_steps
        self._prune_global = prune_global
        self._on_event = on_event
        # A known replayable path to each discovered screen (set once at discovery, never mutated),
        # and the still-untried actions per screen. The strategy is a *forward walk*: a worker keeps
        # acting on the screen its driver is on until it has no untried action left, resetting +
        # replaying only to reach another screen. Read single-threaded by bootstrap / after join.
        self.path_to: dict[str, list[Action]] = {}
        self.pending: dict[str, list[Action]] = {}
        # When pruning global controls, the first screen to offer an operation (by replay key) claims
        # and explores it; later screens offering the same key skip it (a tab bar / nav button reused
        # across screens collides and is pruned to one exploration).
        self._claimed: dict[str, str] = {}
        # Fingerprints a worker is currently discovering (guide in flight), so two workers don't
        # double-discover the same new screen.
        self._discovering: set[str] = set()
        self._steps = 0  # shared action budget counter
        self._active = 0  # workers holding a popped action (mid step) — done at 0 with no frontier
        self._stopped = False  # a budget was hit; no worker takes more work
        self.failure: list[
            Exception
        ] = []  # the first unexpected worker error, re-raised after join

    @property
    def screen_map(self) -> ScreenMap:
        return self._sm

    def _emit(self) -> None:  # holding the lock (it reads `pending`)
        # Refresh the plan (the live frontier: still-untried operations per screen) before each
        # notification, so a watcher sees what the crawl will try next as it advances.
        self._sm.plan = {
            fp: [a.describe() for a in acts] for fp, acts in self.pending.items() if acts
        }
        if self._on_event is not None:
            self._on_event(self._sm)

    def emit(self) -> None:
        """The authoritative final/bootstrap notification (acquires the lock)."""
        with self._cond:
            self._emit()

    def _finish(self, reason: str) -> None:  # holding the lock
        # Signal the stop; the single authoritative final `emit()` runs after join (so it captures
        # any late records a worker added between this signal and its own exit).
        if not self._sm.stop_reason:
            self._sm.stop_reason = reason
        self._stopped = True
        self._cond.notify_all()

    def _claim(self, fp_value: str, actions: list[Action]) -> list[Action]:
        # Holding the lock. Without pruning, every action is the screen's own to explore. With it, an
        # op already claimed by another screen is recorded as Pruned (with a replay path) instead.
        if not self._prune_global:
            return list(actions)
        kept: list[Action] = []
        for a in actions:
            owner = self._claimed.get(a.key)
            if owner is not None and owner != fp_value:
                path = (*self.path_to.get(fp_value, []), a)  # replay to src, then the pruned op
                self._sm.pruned.append(Pruned(fp_value, a.describe(), a.key, owner, path))
            else:
                self._claimed.setdefault(a.key, fp_value)
                kept.append(a)
        return kept

    def _publish(self, node: Node, actions: list[Action]) -> list[Action]:
        # Holding the lock: register the node (keyed by its fingerprint) and claim its operations.
        # Returns the screen's frontier (its actions not already claimed elsewhere).
        self._sm.nodes[node.fingerprint] = node
        # The path to reach this screen is already known (set before publish: [] for the entry, the
        # discovering edge's path otherwise) — persist it so a discovered screen carries a
        # committable candidate flow (BE-0038).
        self._sm.paths[node.fingerprint] = tuple(self.path_to.get(node.fingerprint, ()))
        return self._claim(node.fingerprint, actions)

    def publish(self, node: Node, actions: list[Action]) -> list[Action]:
        """Register a node and claim its operations; return its frontier (used by bootstrap)."""
        with self._cond:
            return self._publish(node, actions)

    def select_next_work(self, current_fp: str | None) -> _Work | None:
        # Pick (and reserve) the next frontier entry to explore, or return None when the worker
        # should retire — a stop was signalled, a budget is spent, or the frontier is fully drained
        # with no worker in flight. Continue from the screen the worker is on; else backtrack to the
        # cheapest entry (shortest known path, then fingerprint) and replay to it. Reserving bumps
        # steps/active under the lock, so two workers never pop the same action.
        with self._cond:
            while True:
                if self._stopped:
                    return None
                if len(self._sm.nodes) >= self._max_screens:
                    self._finish("max_screens")
                    return None
                if self._steps >= self._max_steps:
                    self._finish("max_steps")
                    return None
                if current_fp is not None and self.pending.get(current_fp):
                    src_fp, replay_needed = current_fp, False
                elif candidates := [fp for fp, acts in self.pending.items() if acts]:
                    src_fp = min(candidates, key=lambda fp: (len(self.path_to[fp]), fp))
                    replay_needed = True
                elif self._active == 0:
                    self._finish("completed")  # no frontier and no worker in flight → all explored
                    return None
                else:
                    self._cond.wait()  # another worker is mid-step; it may add frontier
                    continue
                action = self.pending[src_fp].pop(0)  # deterministic order
                src_path = list(self.path_to[src_fp])
                self._steps += 1
                self._active += 1
                return _Work(src_fp, action, src_path, replay_needed)

    def record_alert(self, path: list[Action], dismissed: list[str]) -> None:
        """Record an OS prompt the guard dismissed mid-step (no budget change)."""
        with self._cond:
            self._sm.alerts.append(Alert(tuple(a.describe() for a in path), tuple(dismissed)))

    def record_crash(self, path: list[Action]) -> None:
        """Record a crash (with its replayable action path), release the reservation, notify."""
        with self._cond:
            self._sm.crashes.append(Crash(tuple(a.describe() for a in path), tuple(path)))
            self._active -= 1
            self._emit()
            self._cond.notify_all()

    def record_edge(
        self,
        src_fp: str,
        action: Action,
        dst_fp: Fingerprint,
        dismissed: list[str],
        path: list[Action],
    ) -> bool:
        """Record a transition; reserve a newly seen destination for THIS worker to discover.

        Returns True when the destination is new (this worker holds the reservation and must call
        `finish_discovery`); False for a known/in-flight screen (the step is done, reservation
        released).
        """
        with self._cond:
            self._sm.edges.append(Edge(src_fp, action.describe(), dst_fp.value, tuple(dismissed)))
            if dst_fp.value not in self._sm.nodes and dst_fp.value not in self._discovering:
                self._discovering.add(dst_fp.value)  # reserve so two workers don't double-discover
                self.path_to[dst_fp.value] = path
                return True
            self._active -= 1  # a known/in-flight screen: just the edge, this step is done
            self._emit()
            self._cond.notify_all()
            return False

    def finish_discovery(self, node: Node, actions: list[Action]) -> None:
        """Publish a freshly discovered screen's node + frontier, release the reservation, notify."""
        with self._cond:
            self.pending[node.fingerprint] = self._publish(node, actions)
            self._discovering.discard(node.fingerprint)
            self._active -= 1
            self._emit()
            self._cond.notify_all()

    def give_back(self, src_fp: str, action: Action) -> None:
        # Pool failure isolation: a device misbehaved, so hand the popped action back to the front of
        # its frontier (a healthy worker retries it) and release the reservation.
        with self._cond:
            self.pending[src_fp].insert(0, action)
            self._active -= 1
            self._cond.notify_all()

    def drop_screen(self, src_fp: str) -> None:
        # A replay path no longer resolves (lone worker): drop this screen's frontier and release.
        with self._cond:
            self.pending[src_fp] = []
            self._active -= 1
            self._cond.notify_all()

    def cancel_action(self) -> None:
        # A selector no longer resolves: drop this action and release the reservation.
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def note_failure(self, exc: Exception) -> None:
        """Record an unexpected worker error (surfaced after join) and stop the crawl."""
        with self._cond:
            self.failure.append(exc)
            self._finish(self._sm.stop_reason or "completed")


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

    `base_map` warm-starts from an existing map in two ways. With `seed_path`/`seed_ops` it is a
    *single-branch resume*: replay `seed_path` back to one pruned branch's screen, then explore it
    with `seed_ops` as that screen's frontier (one walk, so `extra_workers` is dropped). Supplied
    *without* `seed_path`/`seed_ops` it is a *full-frontier continuation* (BE-0181): every screen the
    prior run left with untried operations is re-seeded from `paths` + `plan` and explored again, so
    a crawl that stopped on a budget can be pushed deeper without re-walking from the entry screen.

    `extra_workers` adds workers beyond the primary `(driver, reset)`: each is a *factory* the engine
    calls inside that worker's own thread to build one more `(driver, reset)` lane, which explores the
    *same* shared frontier on its own device/browser so the guide's AI round-trips overlap (BE-0064).
    Building inside the thread is what lets a thread-affine driver (Playwright's sync API, BE-0077) be
    created on the very thread that drives it. The default (none) is a single worker that walks exactly
    as the serial engine always did. A single-branch resume (`seed_path`) is one walk, so
    `extra_workers` is ignored for it; a full-frontier continuation keeps the pool.
    """
    guide = guide or _deterministic_guide
    # Default crash signal = the iOS accessibility-tree check; a backend can inject its own.
    alive = is_alive or (lambda _driver, elements: is_app_alive(elements))
    # Warm-start from an existing map (`base_map`): a single-branch resume (with `seed_path`/
    # `seed_ops`) replays back to one pruned branch's screen and explores it; a full-frontier
    # continuation (base_map, no `seed_path`) re-seeds every screen that still has untried ops.
    screen_map = base_map if base_map is not None else ScreenMap()
    # A full-frontier continuation warm-starts from the map without a seed path.
    continuing = base_map is not None and seed_path is None
    # The worker pool: the caller-built primary `(driver, reset)` (used on this thread for bootstrap
    # and the in-place walk), plus extra-worker factories the engine builds inside each spawned
    # thread (so a thread-affine driver is created on the thread that drives it). A resume is a
    # single branch walk, so the extras are dropped regardless of how many were offered.
    extra_factories = list(extra_workers or []) if seed_path is None else []
    # One bad device must not be able to sink a multi-device crawl, so a worker isolates device
    # errors only in a pool; a lone worker lets them propagate (the serial engine's behavior).
    isolate = (1 + len(extra_factories)) > 1

    # The shared map, frontier and budgets live behind one lock in the coordinator; the functions
    # below are the off-lock device walk that calls into it for every shared-state transition.
    coord = _Coordinator(
        screen_map,
        max_screens=max_screens,
        max_steps=max_steps,
        prune_global=prune_global,
        on_event=on_event,
    )

    def _observe(d: base.Driver) -> tuple[list[base.Element], list[str]]:
        # Per-worker so concurrent observations don't clobber each other: settle, dismiss anything
        # covering the app (so an OS prompt isn't read as a crash), and return the tree + what was
        # tapped to clear it (recorded against the path and fed to the guide's next strategy).
        if settle is not None:
            settle(d)
        dismissed = clear_blocking(d) if clear_blocking is not None else []
        return d.query(), dismissed

    def _reconstruct_frontier(d: base.Driver, rst: Reset) -> None:
        # Full-frontier continuation (BE-0181): the map's nodes are already known, so instead of
        # discovering the entry screen, re-seed the frontier for every screen that still has untried
        # operations. `paths[fp]` + `plan[fp]` hold everything needed with no new persisted state —
        # replay the recorded path from a clean reset, re-derive the deterministic candidates, and
        # keep only the ones the plan still lists (the exact set the prior run had not yet tried).
        # Screen identity and candidate order are pure functions of the element tree, so this
        # reproduces what the first run would have tried next. AI-only ops from an AI-guided run
        # aren't reconstructed here on purpose — reconstruction stays deterministic (see BE-0181
        # "Alternatives considered": no materialized-action schema, one re-derived query instead).
        # The primary worker starts cold afterward (backtracking like any worker), so the driver's
        # position at the end of this loop doesn't matter — it never assumes a warm screen.
        prior_reason = screen_map.stop_reason
        had_frontier = any(screen_map.plan.values())  # the loaded map recorded untried operations
        for fp in sorted(screen_map.plan):
            remaining = set(
                screen_map.plan[fp]
            )  # `fp` is a key of `plan`; empty list → skipped below
            if not remaining:
                continue
            path = list(screen_map.paths.get(fp, ()))
            rst(d)
            _observe(d)
            if not _replay(d, path, settle, clear_blocking):
                continue  # the recorded path no longer resolves — skip this screen's frontier
            landed, dismissed = _observe(d)
            if fingerprint(landed).value != fp:
                # The path still replays but no longer lands on `fp` (the app changed under us).
                # Screen identity is a pure function of the element tree, so a different fingerprint
                # means a different screen; seeding `pending[fp]`/`path_to[fp]` here would later
                # misattribute this walk's edges to the wrong source screen — skip it.
                continue
            if dismissed:
                coord.record_alert(path, dismissed)
            ops = [a for a in candidate_actions(landed) if a.describe() in remaining]
            if ops:
                coord.path_to[fp] = path
                coord.pending[fp] = ops
        if any(coord.pending.values()):
            # Something to explore: re-decide the stop reason from this continuation's run, and emit
            # so the live plan reflects the reconstructed frontier.
            screen_map.stop_reason = ""
            coord.emit()
        elif had_frontier:
            # The map recorded untried operations but none could be re-seeded — every recorded path
            # is stale (no longer resolves, or lands on a different screen) or the remaining ops are
            # not deterministically reconstructable (AI-only type/fill/tap_point, which
            # `candidate_actions` never re-derives). Don't emit — that would overwrite the persisted
            # `plan` with the empty frontier and destroy the recorded work — and don't claim
            # "completed": keep the prior budget stop so the frontier survives for a retry. Fall back
            # to "max_steps" only if the loaded map somehow carried no reason.
            screen_map.stop_reason = prior_reason or "max_steps"
        else:
            # The loaded map genuinely had no untried operations (every plan entry was empty): the
            # prior run was already complete. Record it and emit the (empty) frontier.
            screen_map.stop_reason = "completed"
            coord.emit()

    def _bootstrap() -> str | None:
        # Once, before workers start (so single-threaded): reach the entry screen on the primary
        # device and seed the frontier. Returns the fingerprint the primary worker is left on, or
        # None if a resume's replay no longer resolves (nothing to explore) — or, for a full-frontier
        # continuation, always None (the primary starts cold and backtracks to the reconstructed
        # frontier like any worker).
        d, rst = driver, reset
        if continuing:
            _reconstruct_frontier(d, rst)
            return None
        rst(d)
        start, dismissed = _observe(d)
        if dismissed:
            coord.record_alert([], dismissed)
        if seed_path is not None:
            if not _replay(d, seed_path, settle, clear_blocking):
                coord.screen_map.stop_reason = "completed"
                coord.emit()
                return None
            landed, dismissed = _observe(d)
            start_fp = fingerprint(landed)
            coord.path_to[start_fp.value] = list(seed_path)
            if start_fp.value not in coord.screen_map.nodes:
                node, actions = _discover(d, start_fp, landed, GuideContext(tuple(dismissed)))
                coord.publish(node, actions)  # claim ownership; the resume sets pending below
            coord.pending[start_fp.value] = list(seed_ops or [])  # explore the resumed branch's op
        else:
            start_fp = fingerprint(start)
            coord.path_to[start_fp.value] = []
            node, actions = _discover(d, start_fp, start, GuideContext(tuple(dismissed)))
            coord.pending[start_fp.value] = coord.publish(node, actions)
        coord.emit()
        return start_fp.value

    def _discover(
        d: base.Driver, fp: Fingerprint, elements: list[base.Element], context: GuideContext
    ) -> tuple[Node, list[Action]]:
        # The driver is on this screen: ask the guide what to try (the slow AI step), build the
        # node, and screenshot it. None of this touches shared coordinator state, so a worker runs
        # it off-lock — that is what lets the guide's AI round-trips overlap across simulators. The
        # caller then publishes the result through the coordinator.
        actions = guide(d, elements, context)
        node = _node_of(fp, elements, actions)
        if on_node is not None:
            on_node(d, node)
        return node, actions

    def _worker(d: base.Driver, rst: Reset, current_fp: str | None) -> None:
        errors = 0  # consecutive device faults → retire so a wedged device can't busy-loop

        def _give_back(src_fp: str, action: Action) -> bool:
            # Pool failure isolation: a device misbehaved (a broken replay or a device error), so
            # hand the popped action back to the front of its frontier — a healthy worker retries
            # it — and report whether this worker has faulted enough times in a row to retire.
            nonlocal errors
            coord.give_back(src_fp, action)
            errors += 1
            return errors >= _MAX_WORKER_DEVICE_ERRORS

        while True:
            work = coord.select_next_work(current_fp)
            if work is None:
                return
            src_fp, action, src_path, replay_needed = (
                work.src_fp,
                work.action,
                work.src_path,
                work.replay_needed,
            )

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
                        coord.drop_screen(src_fp)  # the path no longer resolves — drop this screen
                        current_fp = None
                        continue
                action.perform(d)
                landed, dismissed = _observe(d)
            except base.SelectorError:
                coord.cancel_action()  # the selector no longer resolves — drop it, reset, move on
                current_fp = None
                continue
            except device_errors.DeviceError:
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
            if dismissed:
                coord.record_alert(path, dismissed)
            if crashed:
                coord.record_crash(path)
                current_fp = None  # the app collapsed — reset to keep going
                continue
            is_new = coord.record_edge(src_fp, action, dst_fp, dismissed, path)
            current_fp = dst_fp.value  # the driver is on dst now — keep walking forward from here
            if not is_new:
                continue
            # Discover the new screen: run the guide on THIS worker's driver, off-lock, so AI calls
            # on different simulators overlap. Then publish the node + frontier through the
            # coordinator, so every shared-state mutation stays serialized by its lock.
            node, actions = _discover(d, dst_fp, landed, reached)
            coord.finish_discovery(node, actions)

    def _run(d: base.Driver, rst: Reset, current_fp: str | None) -> None:
        # Surface an unexpected worker error after join (a bare thread would otherwise swallow it),
        # while device-error isolation is handled inside `_worker`.
        try:
            _worker(d, rst, current_fp)
        except Exception as exc:  # re-raised on the main thread after join
            coord.note_failure(exc)

    start_fp = _bootstrap()
    if start_fp is None and not any(coord.pending.values()):
        # Nothing to explore: a single-branch resume whose replay no longer resolves, or a
        # continuation whose frontier reconstructed empty (stop_reason set in bootstrap). A
        # continuation with a live frontier keeps going with the primary starting cold (start_fp None).
        return screen_map

    def _run_extra(factory: WorkerFactory) -> None:
        # Build this lane's driver on *this* thread (the Playwright sync API is bound to its creating
        # thread — BE-0077), then walk. A lane that can't even start is surfaced after join, like any
        # other worker fault, rather than silently dropping a worker.
        try:
            d, rst = factory()
        except Exception as exc:
            coord.note_failure(exc)
            return
        _run(d, rst, None)

    # The primary worker is left on the entry screen; extras start cold and reset to a frontier.
    threads = [threading.Thread(target=_run_extra, args=(f,), daemon=True) for f in extra_factories]
    for t in threads:
        t.start()
    _run(driver, reset, start_fp)
    for t in threads:
        t.join()
    if coord.failure:
        raise coord.failure[0]

    # Safety net for the stop reason (a normal finish already set it under the lock), plus a final
    # event so the returned map and the last streamed snapshot match — including any late records a
    # worker added after the stop was signalled.
    if not screen_map.stop_reason:
        screen_map.stop_reason = (
            "completed"
            if not any(coord.pending.values())
            else "max_screens"
            if len(screen_map.nodes) >= max_screens
            else "max_steps"
        )
    coord.emit()
    return screen_map


def _replay(
    driver: base.Driver,
    path: list[Action],
    settle: Settle | None,
    clear_blocking: ClearBlocking | None,
) -> bool:
    """Re-walk a recorded path from the current clean state (dismissing OS prompts), returning False if a step no longer resolves so the caller skips this frontier entry."""
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

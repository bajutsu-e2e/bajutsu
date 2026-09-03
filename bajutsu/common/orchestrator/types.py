"""Shared types for the orchestrator: protocols, result dataclasses, and injected callables.

These carry no run logic, so they can be imported by every other orchestrator module (and by
the runner) without a cycle.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from bajutsu.common.assertions import AssertionResult
from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import Actuation, ActuationReporter, Drained
from bajutsu.common.evidence import Artifact
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.mailbox import MailboxMessage
from bajutsu.common.scenario import Relaunch

# Returns the network exchanges observed so far (for `request` assertions / waits).
NetworkSource = Callable[[], list[NetworkExchange]]
# Performs an in-scenario app relaunch (terminate + launch). Injected by the runner so the
# orchestrator stays backend-agnostic; None means relaunch is unavailable (e.g. fake driver).
RelaunchFn = Callable[[Relaunch], None]
# Receives a human-readable progress line (e.g. "step 2/5: tap home.title") as the run advances.
# Injected from the CLI (`--progress`) so the web UI can stream per-scenario/step progress; None
# (the default everywhere) keeps the pipeline silent.
ProgressFn = Callable[[str], None]
# Reads the wall clock (epoch seconds), stamped once per scenario so every recorded timestamp is an
# absolute instant that still means something after the process exits (BE-0348). Deliberately not a
# method on `Clock`: a wall clock can jump backward on an NTP correction, so nothing that decides
# whether a wait timed out may read it, and keeping it a separate injected callable also spares every
# clock double in the suite a method none of them need. Injectable so a test can hold it fixed.
WallClock = Callable[[], float]


class MailboxReader(Protocol):
    """Fetches the current inbox for the `email` step (BE-0046). Injected by the runner, built from
    `targets.<name>.mailbox`; None means no mailbox is configured (or the fake driver), in which case
    an `email` step fails cleanly. `fetch` may raise `base.SelectorError` on an unreachable / non-2xx
    endpoint — a clean step failure, never a silent wrong value.

    `timeout` (seconds) bounds a single fetch, so one slow request can't overrun the step's own
    `email.timeout`; the handler passes the time remaining in the poll."""

    def fetch(self, timeout: float) -> list[MailboxMessage]: ...


class DeviceControl(Protocol):
    """Device-environment operations a step may trigger (simctl on iOS, adb on Android). Injected by
    the runner so the orchestrator stays backend-agnostic; None means unavailable (the fake driver,
    or parallel runs which don't pin a single device). A backend that backs only part of the family
    (the Android emulator) raises UnsupportedAction for the rest, guarded up front by preflight."""

    def set_location(self, lat: float, lon: float) -> None: ...
    def push(self, payload: dict[str, object]) -> None: ...
    def clear_keychain(self) -> None: ...
    def clear_clipboard(self) -> None: ...
    def set_clipboard(self, text: str) -> None: ...
    def get_clipboard(self) -> str: ...
    def home(self) -> None: ...
    def foreground(self) -> None: ...
    def override_status_bar(self, **kwargs: str | int) -> None: ...
    def clear_status_bar(self) -> None: ...


@dataclass
class SelectionState:
    """Whether a text selection is currently live, tracked across a run's steps (BE-0265).

    `copy` acts on the selection a prior `select` established; no backend exposes selection as
    queryable state, so this is kept Bajutsu-side and uniform across backends: `select` establishes
    it, `copy` reads it without clearing (one selection can be copied more than once), and every
    other *action* invalidates it. `wait` / `assert` are conditions handled in the run loop, never
    routed through the action dispatcher, so they leave a standing selection intact — a `select`,
    then a `wait` for a menu, then `copy` is a valid sequence. A `copy` with no live selection fails
    the step rather than silently copying nothing.

    The transitions live on this type (not in the caller) so the contract stays in one place.
    """

    active: bool = False

    def establish(self) -> None:
        self.active = True

    def invalidate(self) -> None:
        self.active = False


def _no_network() -> list[NetworkExchange]:
    return []


class Clock(Protocol):
    """Time and sleep (swappable in tests to make waits deterministic)."""

    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class AlertEvent:
    """A system prompt the guard dismissed so a blocked step/expect could proceed.

    Recorded on the outcome (StepOutcome.alerts / RunResult.expect_alerts) and surfaced in
    the report, so a step that only passed on a retry isn't shown as if nothing had blocked
    it. `label` is the button the guard tapped (e.g. "Not Now"); empty when the locator
    named none."""

    label: str = ""


@dataclass
class StepOutcome:
    index: int
    action: str
    ok: bool = True
    reason: str = ""
    duration_s: float = 0.0
    # The absolute wall-clock instant (epoch seconds) the step began, derived from the scenario's
    # anchor pair rather than measured with its own clock read (BE-0348). No video correction is
    # applied here: a report subtracts `RunResult.video_anchor_s` at render time to get the seconds
    # to seek the recording to, so the correction can be recomputed from a manifest read back long
    # after the run instead of being baked in irreversibly.
    started_at: float = 0.0
    assertion_results: list[AssertionResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    # System prompts the guard cleared before this step succeeded (usually 0 or 1).
    alerts: list[AlertEvent] = field(default_factory=list)
    # What the driver actually did to the screen during this step, in order: the coordinate a tap
    # injected, the endpoints a swipe travelled, the channel that carried each gesture. Drained from
    # the driver once per step, so a step that ran its body twice (an alert the guard dismissed, then a
    # retry) carries both attempts. Evidence only — nothing on the verdict path reads it.
    actuations: list[Actuation] = field(default_factory=list)
    # How many of this step's actuations are missing from the list above: the driver's bounded log
    # discarding the oldest to make room for later ones (a pathological step, e.g. a `maxScrolls` in
    # the hundreds), a damaged record the report loader had to drop on a later read, or both. Recorded
    # rather than left implicit either way, so the list is never read as more complete than it is.
    dropped_actuations: int = 0
    # The value a `generate` step produced (BE-0377), so a later failure shows which value this run
    # actually used. Evidence only — nothing on the verdict path reads it. None for every other
    # action, and for a `generate` step that failed before it wrote its var.
    generated: str | None = None


@dataclass
class SkippedCapture:
    """An evidence kind that was requested but no backend could supply (BE-0020).

    Recorded per scenario so a gap is disclosed in the manifest/report rather than left silently
    empty — graceful degradation, never a run failure.
    """

    kind: str  # the evidence kind, e.g. "network"
    reason: str  # why it was skipped, e.g. "no same-platform backend provides network"


@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult] = field(default_factory=list)
    failure: str | None = None
    # Scenario-level artifacts (the always-on screen recording, etc.).
    artifacts: list[Artifact] = field(default_factory=list)
    # Which backend (actuator) drove this scenario: "xcuitest" / "fake".
    backend: str = ""
    # The web rendering engine this result was produced on — "chromium" / "firefox" / "webkit"
    # — set only on a `--browsers` cross-engine run (BE-0076). Empty for iOS and any single-engine
    # run, so `backend` stays the actuator and `engine` carries the rendering-engine axis.
    engine: str = ""
    # The scenario's evidence-dir slug under the run dir (`NN-slug`), stamped by the runner that
    # named the dir, so anything cross-linking to that evidence reads the authoritative value
    # instead of re-deriving it (BE-0076). Empty when no evidence dir was written (e.g. tests).
    sid: str = ""
    # The simulator udid this scenario ran on — shows how a parallel pool split the work.
    device: str = ""
    # The simulator's device model / OS runtime (e.g. "iPhone 15" / "iOS 17.2"), for the
    # report's Environment tab; empty when not resolvable (e.g. the fake driver).
    device_name: str = ""
    device_runtime: str = ""
    # Wall-clock the scenario took end to end (steps + verification), for the report.
    duration_s: float = 0.0
    # The absolute wall-clock instant (epoch seconds) the scenario's video started, corrected by
    # `video_start_offset`. A report subtracts it from a step's or an exchange's `started_at` to get
    # the seconds to seek the recording to (BE-0348); persisted, unlike the monotonic instant this
    # used to be, so that derivation survives the run that produced it.
    video_anchor_s: float = 0.0
    # Added to a raw `time.monotonic()` instant from this run to get its wall-clock epoch
    # (`scenario_wall_start - scenario_start`). The network collector stamps monotonic receive times,
    # so `pipeline.py` converts them through this rather than sampling its own wall/monotonic pair at
    # write time — a second pair would drift from every other timestamp on an NTP correction.
    wall_offset_s: float = 0.0
    # System prompts the guard cleared before the scenario-level `expect` re-checked.
    expect_alerts: list[AlertEvent] = field(default_factory=list)
    # Actuations the guard performed for that same expect-phase retry — the one place a gesture happens
    # with no step to attribute it to, so it is recorded here beside `expect_alerts` rather than left in
    # the driver's log and silently discarded.
    expect_actuations: list[Actuation] = field(default_factory=list)
    # A damaged `expect_actuations` record the report loader had to drop — `dropped_actuations`'
    # scenario-level counterpart, since this list has no step of its own to carry the disclosure.
    dropped_expect_actuations: int = 0
    # Evidence kinds the run couldn't supply (no eligible backend) — disclosed, not silent (BE-0020).
    skipped_captures: list[SkippedCapture] = field(default_factory=list)
    # The lifecycle phases' own step outcomes (BE-0392), each numbered from zero and kept beside
    # `steps` rather than folded into it — the separation `expect_results` already gets, and what
    # lets a report show setup and teardown as their own blocks instead of merging them into the
    # scenario's numbered sequence the way a `preconditions.setup` prelude does.
    before_outcomes: list[StepOutcome] = field(default_factory=list)
    after_outcomes: list[StepOutcome] = field(default_factory=list)
    # The verdict the `after` phase dispatched on — "success" / "error", or "" when the scenario
    # declared no `after` rules. Recorded rather than re-derived from `failure`, which by then may
    # also carry a cleanup step's own reason: without it a report cannot tell which rules ran, and
    # so cannot line an outcome up with the rule that produced it.
    after_verdict: str = ""


# on_blocked(driver) -> the AlertEvent it dismissed if it cleared a blocking condition
# (e.g. a system alert), so the step/expect is worth retrying; else None. `record` / `crawl` pass the
# vision guard's `SystemAlertGuard.dismiss`; `run` passes `AlertGuardConfig` (below), whose every
# path is deterministic (BE-0402).
BlockedHandler = Callable[[base.Driver], "AlertEvent | None"]

# The reactive guard's default native presence-query cadence (seconds), overridable per scenario /
# target / flag via `systemAlertHandling.pollInterval` (BE-0315, riding the BE-0177 precedence).
DEFAULT_ALERT_POLL_INTERVAL = 1.0

# The timeout the reactive guard passes `handle_system_alert` for its tap (BE-0315): 0 means "query
# SpringBoard once and tap if the button is present, else fail fast" — the guard has already observed
# the alert via `system_alert_labels`, so it never waits for one to appear (that is the proactive
# `handleSystemAlert` step's job), and a vanish-between-query-and-tap race fails fast rather than
# blocking the mid-wait poll.
_NATIVE_TAP_TIMEOUT = 0.0

# Default dismissive button labels the native path taps when a scenario names none (BE-0315), in
# preference order: least-destructive first — the notification prompt's "Don't Allow" (straight and
# curly apostrophe, since iOS renders U+2019), App Tracking Transparency's "Ask App Not to Track",
# then generic dismissive labels. A prompt whose dismissive button is none of these resolves to no
# candidate, and `run` then leaves it alone and names it in the blocked step's own failure reason
# (BE-0402). Keep this in step with the vision locator's own dismissive-button policy prose
# (`agents/alerts.py` `LOCATOR_SYSTEM`), which `record` / `crawl` still run, so the two paths agree.
DEFAULT_DISMISSIVE_LABELS: tuple[str, ...] = (
    "Don't Allow",
    "Don’t Allow",
    "Ask App Not to Track",
    "Not Now",
    "No Thanks",
    "Cancel",
    "Close",
    "Dismiss",
)

# What a native probe found: "incapable" (backend has no native path), "absent" (no alert — a
# deterministic fact), "dismissed" (a policy-named button was tapped), "unhandled" (an alert is up
# but no candidate label resolves, so nothing clears it and the caller reports it instead),
# "reserved" (an alert is up and a waiting `handleSystemAlert` step named it, so this probe leaves
# it for the step's own tap — BE-0406).
NativeAlertState = Literal["incapable", "absent", "dismissed", "unhandled", "reserved"]

# The notes a blocked step or wait appends to its own failure reason when the guard saw something it
# could not clear (BE-0402). Without it, a `tap` or `wait` stuck behind an
# unanticipated prompt reads as a bare "element not found" — the reading BE-0402 exists to remove.
_UNHANDLED_ALERT_NOTE = "an unhandled system alert is blocking the screen"
_BLOCKED_SCREEN_NOTE = "the screen appears blocked, possibly by a system alert or another overlay outside the app's view"
_UNCLEARED_PROMPT_NOTE = "a system prompt the guard could not clear is still up"


def alert_block_note(buttons: Sequence[str]) -> str:
    """What the guard saw blocking the screen, for a failure reason to name (BE-0402).

    *buttons* are the labels a native probe read off an alert no rule or candidate label names —
    `probe_native`'s `"unhandled"` answer, and only that. Empty means the block was inferred from
    the collapsed-tree proxy rather than enumerated — a surface `springboard.alerts` cannot see, or
    a backend with no native query at all — so the note hedges rather than naming buttons nobody
    read. A prompt the policy *did* name and the in-tree dismiss failed to clear is a different
    story, and gets `uncleared_prompt_note` below instead.
    """
    if buttons:
        return f"{_UNHANDLED_ALERT_NOTE} (buttons: {', '.join(buttons)})"
    return _BLOCKED_SCREEN_NOTE


def uncleared_prompt_note(label: str) -> str:
    """The in-tree dismiss's own give-up: a prompt it named and could not clear (BE-0402).

    Deliberately not `alert_block_note`: "unhandled" would tell the author no candidate label
    resolved, when their label resolved and only the tap failed — it did not take, or never became
    deliverable — sending them to add a label they already wrote instead of to the stuck prompt.
    """
    return f"{_UNCLEARED_PROMPT_NOTE} (button: {label})"


def pick_alert_label(candidates: Sequence[str], buttons: Sequence[str]) -> str | None:
    """The first candidate label present on the alert exactly once, or None (BE-0315).

    Exactly once — not merely present — so an alert with two identically labeled buttons never
    resolves to "whichever matched first" (determinism first, mirroring `resolve_unique`). None means
    no candidate resolves uniquely, so the caller reports the alert rather than tapping one of them.
    """
    present = list(buttons)
    for label in candidates:
        if present.count(label) == 1:
            return label
    return None


def _alert_button(label: str) -> base.Element:
    """One alert button as an element, so a selector can be matched against a bare label list.

    The native presence query reports labels, not elements; a button on it carries no identifier
    (SpringBoard names them by visible text alone) and no frame this side ever reads.
    """
    return {
        "identifier": None,
        "label": label,
        "traits": [base.Trait.BUTTON],
        "value": None,
        "frame": (0.0, 0.0, 0.0, 0.0),
        "nativeZ": None,
    }


def selector_names_button(sel: base.Selector, buttons: Sequence[str]) -> bool:
    """Whether a waiting `handleSystemAlert` step's selector names a button this alert offers.

    The reservation the reactive guard honors (BE-0406): a scenario may hold a `rules` entry for the
    very prompt a step is placed to answer, and with the opposite choice, so whichever party read
    the alert first would decide it. Matched through `base.matches` rather than a private label
    comparison, so `label` / `labelMatches` / `value` / `traits` mean here what they mean in every
    other selector. An `id` selector reserves nothing, which is the honest answer: no button on
    this surface carries one.

    `base.matches` ignores `index` and `within` by contract, so a selector carrying either reserves
    an alert its own `resolve_unique` may then reject. Erring that way is deliberate: the cost is
    that the step spends its timeout on an alert it could not have tapped anyway, where the
    opposite error would let the guard answer, with the opposite choice, the prompt the step exists
    to decide.
    """
    return any(base.matches(_alert_button(label), sel) for label in buttons)


@dataclass(frozen=True)
class ResolvedAlertRule:
    """One `systemAlertHandling.rules` entry with its prompt's labels resolved for a locale.

    `identifying_labels` is the prompt's full label pair (grant and deny), resolved from
    `bajutsu.common.scenario.system_alerts` for the run's locale — matching requires both, since a single
    shared label (e.g. "Allow") cannot by itself tell two covered prompts apart. `tap_label` is the
    label the rule's `choice` names.
    """

    identifying_labels: frozenset[str]
    tap_label: str


def match_alert_rule(rules: Sequence[ResolvedAlertRule], buttons: Sequence[str]) -> str | None:
    """The tap label of the first rule whose prompt is uniquely identified on `buttons`.

    A rule matches when each of its prompt's two labels is present on the alert exactly once — the
    full pair, not only the label it taps, since a single shared label cannot by itself distinguish
    one covered prompt from another. None means no rule's prompt is identified, so the caller falls
    through to the ordered `labels` candidates.
    """
    present = list(buttons)
    for rule in rules:
        if all(present.count(label) == 1 for label in rule.identifying_labels):
            return rule.tap_label
    return None


@dataclass
class AlertGuardConfig:
    """The reactive system-alert guard's per-scenario configuration and dismiss entry point (BE-0315).

    Callable as the `BlockedHandler` it replaces — `guard(driver)` clears a blocking system alert
    through the deterministic native path (BE-0316's SpringBoard query + `handle_system_alert`) on a
    backend advertising `HANDLE_SYSTEM_ALERT`, or through the in-tree dismiss for an app-owned prompt
    that query cannot see. Every path here is deterministic: BE-0402 removed the AI-vision fallback
    from `run`, so where neither path can act the guard does nothing and records `blocked_note` for
    the blocked step to report. `rules` are checked first — each answers one named prompt regardless
    of which label it shares with another; `labels` are the ordered candidate button labels the
    native path falls back to for whatever no rule identifies (empty → the built-in dismissive
    default); `poll_interval` is the native presence-query cadence the mid-wait gate polls on,
    decoupled from the wait's own condition poll.
    """

    labels: list[str] = field(default_factory=list)
    rules: list[ResolvedAlertRule] = field(default_factory=list)
    poll_interval: float = DEFAULT_ALERT_POLL_INTERVAL
    # What the most recent `__call__` saw blocking the screen and could not clear, for the end-of-step
    # and `expect` retry to append to the step's own failure reason (BE-0402). Rewritten on every
    # call, never accumulated: it states what the last probe saw, not that a block was ever seen.
    # Safe to hold here because `_guard_for` builds one config per scenario and a scenario's steps run
    # in sequence, so no note crosses a scenario or a worker boundary.
    blocked_note: str = field(default="", init=False)

    def probe_native(
        self, driver: base.Driver, reserved: base.Selector | None = None
    ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
        """Query and, where possible, clear a system alert natively; report what happened.

        Reads BE-0316's SpringBoard query (`system_alert_labels`) to learn the alert's buttons, picks
        a policy-named one — a `rules` match first, then the ordered `labels` candidates —
        and taps it through BE-0316's `handle_system_alert`. The returned `AlertEvent` is set only for
        `"dismissed"`. `"absent"` is a deterministic no-*SpringBoard*-alert fact — but the native query
        only sees `springboard.alerts`, so a non-enumerable surface (an action sheet, a WKWebView
        dialog) reads as `"absent"` too, and only the mid-wait gate's debounced collapsed-tree proxy
        can notice it. `"unhandled"` means an alert is up but no rule or candidate label resolves, so
        nothing here can clear it.

        The third member carries the buttons this query actually read, empty unless an alert was
        seen. `"unhandled"` is the state that needs them: BE-0402 left that alert on screen, so the
        labels are all a blocked step or wait has to name what stopped it, and they would otherwise
        be discarded here. Returned rather than re-queried at that moment, since a second
        cross-process query costs another round trip on the runner's single main thread and reopens
        the time-of-check/time-of-use window the dismiss-race branches below exist to close.

        Args:
            reserved: A waiting `handleSystemAlert` step's own selector, when one is running
                (BE-0406). An alert it names is left untouched — see `selector_names_button`.
        """
        if base.Capability.HANDLE_SYSTEM_ALERT not in driver.capabilities():
            return "incapable", None, []
        buttons = driver.system_alert_labels()
        if not buttons:
            return "absent", None, []
        if reserved is not None and selector_names_button(reserved, buttons):
            # The step is waiting on this very alert and taps it on its own next read. Not
            # "absent": an alert *is* up, and "absent" is the one answer licensing an in-tree tap.
            return "reserved", None, list(buttons)
        label = match_alert_rule(self.rules, buttons) or pick_alert_label(
            self.labels or DEFAULT_DISMISSIVE_LABELS, buttons
        )
        if label is None:
            return "unhandled", None, list(buttons)
        try:
            driver.handle_system_alert({"label": label}, _NATIVE_TAP_TIMEOUT)
        except base.ElementNotFound:
            # A time-of-check/time-of-use race: the alert vanished between the presence query and the
            # tap. It is no longer blocking, so treat it as absent rather than failing the step on a
            # benign, self-resolved race — a genuine channel error still propagates.
            return "absent", None, []
        except base.AmbiguousSelector:
            # The other half of that race, and *not* the same answer: the alert is still up, now
            # offering the label twice. Reporting "absent" would say no system alert is showing,
            # which is the one thing licensing an in-tree tap (`_observe_native`'s `probed_absent`) —
            # and that tap, made under a live alert, is what XCUITest answers with its own default
            # button. "unhandled" is what this already is by definition: an alert is up but no
            # candidate resolves, so it licenses nothing and is reported instead.
            return "unhandled", None, list(buttons)
        return "dismissed", AlertEvent(label=label), list(buttons)

    def dismiss_from_tree_once(self, driver: base.Driver) -> AlertEvent | None:
        """Tap a scenario-named dismiss button visible in the driver's own tree, once.

        The one-shot twin of `_AlertGuardGate._dismiss_from_tree` (waits.py), for the end-of-step and
        `expect` retry. It exists for the same prompt that motivated the mid-wait one: iOS raises its
        "Save Password" alert *inside the app's process*, so `springboard.alerts` never sees it and
        only a tap in the tree can clear it — and measured, such an alert can arrive after a
        scenario's last wait has already returned, where only this path is left to meet it.

        One-shot, so it carries none of the mid-wait version's per-showing bookkeeping (retap delay,
        tap ceiling, decline bound): the caller runs it once per failed step, not per poll, so there
        is no stream to pace. It matches the same narrow surface — an identifier-less labelled button
        whose label the scenario's own `labels` named, resolving uniquely — so it stays off the
        default dismissive vocabulary a real screen can legitimately show.

        Returns the `AlertEvent` for the button it tapped, or None when nothing matched, the match was
        ambiguous, or the tap lost a race with the prompt closing itself.
        """
        if not self.labels:
            return None
        elements = driver.query()
        buttons = [
            el["label"]
            for el in elements
            if el["label"] and not el["identifier"] and base.Trait.BUTTON in el["traits"]
        ]
        label = pick_alert_label(self.labels, buttons)
        if label is None:
            return None
        # The same uniqueness pre-check the mid-wait path applies: a bare `{"label": label}` selector
        # ignores traits, so an identified app button of the same name would make the tap ambiguous.
        if (
            sum(1 for el in elements if el["label"] == label and base.Trait.BUTTON in el["traits"])
            != 1
        ):
            return None
        try:
            driver.tap({"label": label, "traits": [base.Trait.BUTTON]})
        except (base.ElementNotFound, base.AmbiguousSelector, base.ElementNotTappable):
            # The prompt closed itself, or another button of that name appeared, or it is not yet
            # reachable. All three are benign here: this is one opportunistic attempt on a step that
            # has already failed, and the step's own outcome still decides the verdict.
            return None
        return AlertEvent(label=label)

    def __call__(self, driver: base.Driver) -> AlertEvent | None:
        """The end-of-step / expect retry: a one-shot dismiss, or None with `blocked_note` set."""
        state, event, buttons = self.probe_native(driver)
        if state == "dismissed":
            self.blocked_note = ""
            return event
        if state == "absent":
            # No *SpringBoard* alert, which is both the licence to tap an app element (XCUITest
            # answers an interrupting out-of-process alert before synthesizing any interaction) and
            # the case where an app-owned prompt is the remaining explanation for the failed step.
            tree_event = self.dismiss_from_tree_once(driver)
            if tree_event is not None:
                self.blocked_note = ""
                return tree_event
        # "unhandled": an alert is up that no rule or candidate label names. BE-0402 leaves it alone
        # rather than asking a model where to tap, so the step keeps failing — but on its own timeout
        # with the alert named, not as an unexplained missing element. "incapable" and a bare
        # "absent" clear the note instead: neither is evidence of anything blocking the screen.
        self.blocked_note = alert_block_note(buttons) if state == "unhandled" else ""
        return None


def push_interruption_policy(driver: base.Driver, guard: AlertGuardConfig | None) -> None:
    """Hand the backend the buttons it may press on an alert that interrupts its own interactions.

    XCUITest resolves such an alert *before* it synthesizes the interaction, and with nothing
    installed answers with the alert's own default button — granting a permission the scenario may
    have refused, with nothing in the report. Pushing the guard's already-resolved labels keeps that
    decision here: the backend applies `rules` then `candidates` by the same discipline
    `probe_native` does, and answers nothing else.

    An absent guard (`systemAlertHandling: false`) pushes an empty policy rather than skipping the
    call, so a scenario that switched the guard off does not inherit the previous scenario's policy
    from the resident runner. A backend that does not implement `InterruptionPolicyTarget` is simply
    never asked.
    """
    if not isinstance(driver, base.InterruptionPolicyTarget):
        return
    rules: list[tuple[frozenset[str], str]] = []
    candidates: list[str] = []
    if guard is not None:
        rules = [(rule.identifying_labels, rule.tap_label) for rule in guard.rules]
        candidates = list(guard.labels or DEFAULT_DISMISSIVE_LABELS)
    driver.set_interruption_policy(rules, candidates)


def drain_interruptions(driver: base.Driver) -> list[AlertEvent]:
    """The prompts the backend answered at interruption time since the last drain.

    Reported as ordinary `AlertEvent`s so a dismissal that happened inside the backend's own
    interruption handling is not missing from the run's report — the silence this mechanism exists
    to end. A backend without the opt-in contributes nothing.
    """
    if not isinstance(driver, base.InterruptionPolicyTarget):
        return []
    return [AlertEvent(label=label) for label in driver.drain_interruptions()]


def drain_actuations(driver: base.Driver) -> Drained:
    """The actuations `driver` has performed since the last drain, or an empty drain if it reports none.

    The one place the `ActuationReporter` opt-in is read, so a backend that does not implement it
    simply contributes nothing rather than needing a stub.
    """
    if isinstance(driver, ActuationReporter):
        return driver.drain_actuations()
    return Drained(records=[], dropped=0)


def scenario_slug(name: str) -> str:
    """A filesystem-safe id derived from a scenario name (for its evidence dir)."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()
    return slug or "scenario"

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

from bajutsu.assertions import AssertionResult
from bajutsu.drivers import base
from bajutsu.evidence import Artifact
from bajutsu.evidence.network import NetworkExchange
from bajutsu.mailbox import MailboxMessage
from bajutsu.scenario import Relaunch

# Returns the network exchanges observed so far (for `request` assertions / waits).
NetworkSource = Callable[[], list[NetworkExchange]]
# Performs an in-scenario app relaunch (terminate + launch). Injected by the runner so the
# orchestrator stays backend-agnostic; None means relaunch is unavailable (e.g. fake driver).
RelaunchFn = Callable[[Relaunch], None]
# Receives a human-readable progress line (e.g. "step 2/5: tap home.title") as the run advances.
# Injected from the CLI (`--progress`) so the web UI can stream per-scenario/step progress; None
# (the default everywhere) keeps the pipeline silent.
ProgressFn = Callable[[str], None]


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
    started_at: float = 0.0  # offset (s) from the scenario video's start, for video sync
    assertion_results: list[AssertionResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    # System prompts the guard cleared before this step succeeded (usually 0 or 1).
    alerts: list[AlertEvent] = field(default_factory=list)


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
    # System prompts the guard cleared before the scenario-level `expect` re-checked.
    expect_alerts: list[AlertEvent] = field(default_factory=list)
    # Evidence kinds the run couldn't supply (no eligible backend) — disclosed, not silent (BE-0020).
    skipped_captures: list[SkippedCapture] = field(default_factory=list)


# on_blocked(driver) -> the AlertEvent it dismissed if it cleared a blocking condition
# (e.g. a system alert), so the step/expect is worth retrying; else None. The vision guard's
# `SystemAlertGuard.dismiss` is one; `AlertGuardConfig` (below) is another, layering the native path
# (BE-0316's `handle_system_alert`) over it.
BlockedHandler = Callable[[base.Driver], "AlertEvent | None"]

# The reactive guard's default native presence-query cadence (seconds), overridable per scenario /
# target / flag via `dismissAlerts.pollInterval` (BE-0315, riding the BE-0177 precedence).
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
# candidate and falls through to the vision guard. Keep this in step with the vision locator's own
# dismissive-button policy prose (`agents/alerts.py` `LOCATOR_SYSTEM`) so the two paths agree.
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
# but no candidate label resolves, so the caller falls back to vision).
NativeAlertState = Literal["incapable", "absent", "dismissed", "unhandled"]


def pick_alert_label(candidates: Sequence[str], buttons: Sequence[str]) -> str | None:
    """The first candidate label present on the alert exactly once, or None (BE-0315).

    Exactly once — not merely present — so an alert with two identically labeled buttons never
    resolves to "whichever matched first" (determinism first, mirroring `resolve_unique`). None means
    no candidate resolves uniquely, so the caller falls back to the vision guard.
    """
    present = list(buttons)
    for label in candidates:
        if present.count(label) == 1:
            return label
    return None


@dataclass
class AlertGuardConfig:
    """The reactive system-alert guard's per-scenario configuration and dismiss entry point (BE-0315).

    Callable as the `BlockedHandler` it replaces — `guard(driver)` clears a blocking system alert,
    preferring the deterministic native path (BE-0316's SpringBoard query + `handle_system_alert`)
    when the backend advertises `HANDLE_SYSTEM_ALERT`, and falling back to the injected `vision` guard
    otherwise. `labels` are the ordered candidate button labels the native path resolves against
    (empty → the built-in dismissive default); `poll_interval` is the native presence-query cadence
    the mid-wait gate polls on, decoupled from the wait's own condition poll.
    """

    vision: BlockedHandler
    labels: list[str] = field(default_factory=list)
    poll_interval: float = DEFAULT_ALERT_POLL_INTERVAL

    def probe_native(self, driver: base.Driver) -> tuple[NativeAlertState, AlertEvent | None]:
        """Query and, where possible, clear a system alert natively; report what happened.

        Reads BE-0316's SpringBoard query (`system_alert_labels`) to learn the alert's buttons, picks
        a policy-named one, and taps it through BE-0316's `handle_system_alert`. The returned
        `AlertEvent` is set only for `"dismissed"`. `"absent"` is a deterministic no-alert fact
        (unlike the collapsed-tree proxy), so the gate can suppress a vision false positive on it;
        `"unhandled"` means an alert is up but no candidate label resolves, so the gate falls back to
        the vision guard.
        """
        if base.Capability.HANDLE_SYSTEM_ALERT not in driver.capabilities():
            return "incapable", None
        buttons = driver.system_alert_labels()
        if not buttons:
            return "absent", None
        label = pick_alert_label(self.labels or DEFAULT_DISMISSIVE_LABELS, buttons)
        if label is None:
            return "unhandled", None
        try:
            driver.handle_system_alert({"label": label}, _NATIVE_TAP_TIMEOUT)
        except base.ElementNotFound:
            # A time-of-check/time-of-use race: the alert changed or vanished between the presence
            # query and the tap. It is no longer blocking, so treat it as absent rather than failing
            # the step on a benign, self-resolved race — a genuine channel error still propagates.
            return "absent", None
        return "dismissed", AlertEvent(label=label)

    def __call__(self, driver: base.Driver) -> AlertEvent | None:
        # The end-of-step / expect retry: a one-shot dismiss. Here "absent" falls through to vision
        # (a sheet the native query cannot enumerate may still be up). The mid-wait gate reacts to
        # "absent" differently — it suppresses vision, since a definitive no-alert beats the
        # collapsed-tree proxy's false positive — so that half of the policy lives in
        # `_AlertGuardGate._observe_native` (waits.py), not here.
        state, event = self.probe_native(driver)
        if state == "dismissed":
            return event
        # incapable / absent / unhandled: let the vision guard try — it may see a surface the native
        # `springboard.alerts` query cannot enumerate (e.g. an action sheet), and no-ops without a
        # credential. It stays off the pass/fail verdict either way (prime directive 1).
        return self.vision(driver)


def scenario_slug(name: str) -> str:
    """A filesystem-safe id derived from a scenario name (for its evidence dir)."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()
    return slug or "scenario"

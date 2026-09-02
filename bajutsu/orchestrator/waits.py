"""Condition waits: poll the screen (or the observed network) until satisfied, never a fixed
sleep — this is what keeps the run deterministic without `sleep`."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from bajutsu.cancellation import CancelSource, RunCancelled, not_cancelled
from bajutsu.common import assertions
from bajutsu.common.scenario import Gone, Wait, WaitRequest
from bajutsu.drivers import base
from bajutsu.elements import shows_app_ui
from bajutsu.evidence.network import TransitionSource, _no_transitions
from bajutsu.orchestrator.types import (
    AlertEvent,
    AlertGuardConfig,
    Clock,
    NetworkSource,
    _no_network,
    alert_block_note,
    pick_alert_label,
    uncleared_prompt_note,
)

_logger = logging.getLogger(__name__)

_POLL = 0.05
_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled" (tree-diff fallback)
# Quiescence window for the signal-based settle path (BE-0310): once no further screen-transition
# has been reported for this long, the last transition is taken as finished. Short by design —
# `viewDidAppear` already fires *after* the appearance transition completes, so this only smooths
# over a chained transition posting more than one report in quick succession, not a whole
# animation's duration.
_TRANSITION_QUIESCENCE = 0.3

# Min seconds between live "still waiting …" lines. Waits poll every _POLL (50ms); a per-poll
# progress line would flood the run log, so the heartbeat below throttles it to a readable cadence.
# 5s keeps the countdown legible rather than a per-second scroll.
_TICK_INTERVAL = 5.0

# Emits a run-log line while a wait is pending; the float is the seconds left before timeout. Bound
# by the caller (the run loop) to prefix the scenario/step and format the condition.
WaitTick = Callable[[float], None]


def describe_wait(w: Wait) -> str:
    """A human-readable description of what a wait is blocked on, for live progress.

    Renders the condition as `key=value` — `for id='home.title'`, `until gone id='spinner'`,
    `until request GET /login`, `until settled` / `until screenChanged` — reusing the assertion
    report's `sel_str` so a pending line and an assertion detail render a selector the same way.
    (`_wait`'s timeout reason prints the raw selector dict, so it is not byte-identical to this.)
    """
    if w.for_ is not None:
        return f"for {assertions.sel_str(w.for_)}"
    if isinstance(w.until, Gone):
        return f"until gone {assertions.sel_str(w.until.gone)}"
    if isinstance(w.until, WaitRequest):
        return f"until request {assertions.request_label(w.until.request)}"
    return f"until {w.until}"  # "settled" | "screenChanged"


@dataclass
class _Heartbeat:
    """Throttled emitter of "still waiting …" lines so a pending wait shows what it awaits.

    Purely a display aid, fed the poll clock via `tick`: it never reads the tree or influences the
    wait's pass/fail (prime directive 1). The first `tick` always fires, so even a wait that resolves
    on its first poll surfaces its condition once; later ticks are spaced by `_TICK_INTERVAL`.
    """

    emit: WaitTick
    deadline: float
    _next: float = 0.0  # clock time of the next allowed emit; 0 => fire on the first tick

    def tick(self, now: float) -> None:
        if now >= self._next:
            self._next = now + _TICK_INTERVAL
            self.emit(max(0.0, self.deadline - now))


# Mid-wait system-alert guard (BE-0269). A SpringBoard-level prompt collapses the iOS app-scoped tree
# to bare content (`not shows_app_ui`); rather than let a wait burn its whole timeout before the
# end-of-step guard looks, watch the already-fetched poll tree and ask the guard to clear it early.
# A hair above _SETTLE_POLLS, so a transient collapsed frame does not read as a blocked screen: since
# BE-0402 a trip costs no model call, only the note a timeout would then carry, and a note naming a
# block that was never there is the one wrong answer this path can still give.
_GUARD_DEBOUNCE_POLLS = 3  # consecutive collapsed polls before recording the note
# Seconds of consecutive `ElementNotTappable` declines `_dismiss_from_tree` tolerates for one
# showing of a label before it stops attempting the tap: unlike `ElementNotFound` (the button left
# the tree, so the next poll can't re-match it) and `AmbiguousSelector` (guarded by the uniqueness
# pre-check above it), a genuinely stuck obstruction — a scrim that never lifts, an `elevation`
# false positive — has neither property, so without its own bound this decline would re-issue a real
# actuation attempt for the rest of the wait. Clock-based, for the reason `_TREE_RETAP_DELAY` records
# for itself: what is being waited out is a presentation animation measured in seconds (a UIKit sheet
# ~0.35-0.5s, an Android dialog enter ~0.25s+). A poll count cannot express that here anyway, because
# `_dismiss_from_tree` is paced by `guard.poll_interval` rather than `_POLL` (see `_observe_native`)
# and that interval is configurable per scenario, target, and flag (BE-0177). Twice the default
# `poll_interval` rather than the animation's own horizon, because the give-up is checked *before*
# the tap: at one interval the very first attempt would exhaust the budget, leaving a transient scrim
# no retry at all. That is also why the horizon is *derived* from the interval rather than fixed. A
# scenario may tune `pollInterval` upwards (the save-password one sets 5), and a fixed 2s would
# then put the second pass past the horizon before it ever ran — reinstating the zero-retry case
# this value exists to avoid. The floor keeps the animation horizon intact at short intervals.
_TREE_DISMISS_DECLINE_GIVEUP_FLOOR = 2.0


def _decline_giveup(poll_interval: float) -> float:
    """Seconds `_dismiss_from_tree` tolerates `ElementNotTappable` on one showing of a label.

    Twice the cadence the path is paced at, floored at the animation horizon: the bound is
    checked before the tap, so anything under two intervals spends itself on the first attempt.
    """
    return max(_TREE_DISMISS_DECLINE_GIVEUP_FLOOR, 2 * poll_interval)


# Min seconds before `_dismiss_from_tree` re-taps a label its own tap left still showing. A tap the
# runner accepts does not always land — measured on iOS, testmanagerd confirmed `touch down`/`touch
# up` at the target's centre with `TouchEventsCompleted` while the app never acted on it — and a
# prompt that stays up is indistinguishable, at the poll that follows, from one merely fading out.
# So wait past any real dismiss animation (the same ~1s horizon as `_GUARD_COOLDOWN`) before
# concluding the tap did not land: re-tapping inside that window would land on whatever is under a
# vanishing sheet. Clock-based like `_GUARD_COOLDOWN` and like the decline give-up above, because
# what is being waited out is an animation measured in seconds — on a backend whose `query()` costs
# 100-300ms, a poll count would stretch this to several seconds of dead wait.
_TREE_RETAP_DELAY = 1.0
# Taps `_dismiss_from_tree` spends on one showing of a label that never clears: a prompt still up
# after this many is not one more tap will fix, so it degrades to the wait's own timeout rather than
# actuating the device for the rest of it.
_TREE_DISMISS_MAX_TAPS = 3


def _tree_signature(elements: list[base.Element]) -> tuple[tuple[str | None, str | None], ...]:
    """A cheap identity for one poll's screen, used to tell a tap that did nothing from one that did.

    `_dismiss_from_tree` matches identifier-less buttons, and `shows_app_ui`'s docstring records that
    a whole app can legitimately have none (the label/coordinate-driven `-noax` shape). So a label
    still matching after a tap is *not* by itself evidence the prompt is still up — an app-authored
    button carrying the same label, revealed once the sheet closed, matches just as well, and
    re-tapping that navigates the app under test and fails the step for an unrelated reason.

    A tap the app never acted on leaves the screen byte-identical; a tap that dismissed a sheet does
    not. Comparing this signature is what makes "the tap did not land" a measured claim rather than
    an assumption. Labels and identifiers rather than frames, so an animation settling a few pixels
    does not read as a changed screen.
    """
    return tuple((el["label"], el["identifier"]) for el in elements)


@dataclass
class WaitTrace:
    """Poll-by-poll record of a `for` wait, filled in place so a timeout is diagnosable (BE-0231).

    On a first-wait timeout these fields separate the candidate causes: a tree that never became
    non-empty (`first_nonempty_s is None`) points at "nothing rendered / transient-empty"; a
    non-empty tree with `elements_at_timeout` content but a still-unmet target points at "the awaited
    element didn't render / readyWhen mismatch"; a large `first_nonempty_s` points at a slow
    cold-boot render. Pure diagnosis — it never enters a verdict (prime directive 1).
    """

    target: str = ""
    timeout_s: float = 0.0
    polls: int = 0
    first_nonempty_s: float | None = None
    elements_at_timeout: int = 0


# A lane may raise the floor under a wait's ceiling: a condition wait returns the instant it is
# satisfied, so a larger ceiling never slows a fast backend — it only gives a slow environment
# (e.g. the CI x86_64 software-rendered emulator) time to draw before the step is failed. Set by
# the Android e2e lane so the shared scenarios' `timeout: 5` need not be retuned per backend.
_FLOOR_ENV = "BAJUTSU_MIN_WAIT_TIMEOUT"


def _timeout_floor() -> float:
    raw = os.environ.get(_FLOOR_ENV)
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        raise ValueError(f"{_FLOOR_ENV}={raw!r} is not a valid float") from None


def _effective_timeout(w: Wait) -> float:
    return max(w.timeout, _timeout_floor())


def _exists(elements: list[base.Element], sel: base.Selector) -> bool:
    return len(base.find_all(elements, sel)) >= 1


def _with_block_note(reason: str, gate: _AlertGuardGate | None) -> str:
    """The wait's timeout reason, plus what the guard last saw blocking the screen (BE-0402).

    Read only at the moment a timeout is reported, and only from the gate's latest observation, so a
    prompt that appeared and resolved mid-wait leaves nothing behind. Without it a `wait` stuck
    behind a prompt no rule or candidate label names reports only the element that never appeared.
    """
    if gate is None or not gate.blocked_note:
        return reason
    return f"{reason} \u2014 {gate.blocked_note}"


def _adaptive_sleep(clock: Clock, before: float) -> None:
    """Sleep only the remainder of _POLL after subtracting time already spent (e.g. in query).

    When `driver.query()` is backed by a subprocess (a device-tree dump ≈ 100-300ms or more), the call
    itself already provides sufficient delay and an additional fixed sleep is wasteful."""
    elapsed = clock.now() - before
    remaining = _POLL - elapsed
    if remaining > 0:
        clock.sleep(remaining)


@dataclass
class _AlertGuardGate:
    """Fires the system-alert guard mid-wait (BE-0269; native path BE-0315).

    Fed each poll's already-fetched tree via `observe`. It is the deterministic trigger only — it
    decides *when* to act, never the wait's pass/fail (prime directive 1).

    On a backend advertising `HANDLE_SYSTEM_ALERT` it prefers the native path (BE-0315): it reads
    BE-0316's SpringBoard query (`system_alert_labels`) on its own wall-clock interval
    (`guard.poll_interval`, decoupled from the wait's `_POLL`) and taps a policy-named button the
    moment a poll finds one — no debounce, cooldown, or attempt ceiling, because a native query
    reports a fact (not the collapsed-tree proxy's correlation, so no transient-frame false positive)
    and the fixed interval already rate-limits the cross-process query. This resolves the tension
    BE-0316 recorded for keeping the guard reactive — a native query is not a model call, so "a
    passing scenario never calls the model" still holds.

    Where the backend lacks the capability — or an alert is up but no policy label resolves, or the
    native query reports no SpringBoard alert yet a non-SpringBoard surface (an action sheet, a
    WKWebView JS dialog) it cannot enumerate is blocking and the scenario's own `labels` do not
    name its button (the one such surface `_dismiss_from_tree` below still taps) — nothing here can
    clear it. BE-0402 removed the AI-vision fallback that used to answer those cases from `run`, so
    the gate records what it saw in `blocked_note` and the wait polls on to its own deadline, where
    `_wait` appends the note to the timeout it reports. The note is the gate's *latest* observation,
    cleared the moment a poll shows an unblocked screen, so a block that resolved itself never
    reaches a later timeout message.
    """

    driver: base.Driver
    clock: Clock
    guard: AlertGuardConfig
    alerts: list[AlertEvent]
    _native: bool = field(init=False)
    _last_native: float | None = None
    _collapsed_polls: int = 0
    # Whether the most recent native probe found an alert it could not name. The native query runs
    # once per `poll_interval` while the collapsed-tree proxy below samples every `_POLL`, so without
    # this the proxy would overwrite the probe's own button-naming note with its hedged one on every
    # tick in between — reporting less than the guard actually knows.
    _native_unhandled: bool = False
    # What this gate last saw blocking the screen and could not clear (BE-0402), for `_wait` to
    # append to a timeout it is about to report. Empty whenever the latest poll showed no block.
    blocked_note: str = ""
    _tree_dismiss_pending: str | None = None
    _tree_tapped_at: float | None = None
    _tree_signature: tuple[tuple[str | None, str | None], ...] | None = None
    _tree_event: AlertEvent | None = None
    _tree_taps: int = 0
    _tree_gave_up: bool = False
    _tree_not_tappable_label: str | None = None
    _tree_not_tappable_since: float | None = None

    def __post_init__(self) -> None:
        self._native = base.Capability.HANDLE_SYSTEM_ALERT in self.driver.capabilities()

    def observe(self, elements: list[base.Element]) -> None:
        """Inspect one poll; clear a blocking system prompt if warranted, else note what blocks it."""
        if self._native:
            self._observe_native(elements)
        else:
            self._observe_collapsed(elements)

    def _observe_native(self, elements: list[base.Element]) -> None:
        # Rate-limit only the cross-process native query to `poll_interval`, not the whole gate: a
        # per-`_POLL` SpringBoard query would roughly double the single-main-thread runner's load
        # (BE-0315). `_last_native` starts None so the first poll probes at once.
        now = self.clock.now()
        # Whether *this* poll asked SpringBoard and was told no alert is up. `_dismiss_from_tree`
        # below is gated on it, so a fresh negative answer — not a remembered one — is what licenses
        # an in-tree tap.
        probed_absent = False
        if self._last_native is None or now - self._last_native >= self.guard.poll_interval:
            self._last_native = now
            state, event, buttons = self.guard.probe_native(self.driver)
            probed_absent = state == "absent"
            self._native_unhandled = state == "unhandled"
            if state != "unhandled" and not self._tree_gave_up:
                # Nothing the native query names is blocking, so any note it left is stale. The proxy
                # below may still set its hedged one for a surface the query cannot enumerate. An
                # in-tree give-up standing is the exception: `springboard.alerts` never saw that
                # prompt in the first place, so "absent" is not evidence it went away.
                self.blocked_note = ""
            if state == "dismissed" and event is not None:
                # A SpringBoard alert was up and tapped natively — no model. Clear the proxy debounce
                # so a later collapse starts fresh.
                self.alerts.append(event)
                self._collapsed_polls = 0
                return
            if state == "unhandled":
                # An alert is up but no policy label resolves — an unknown button, or a query that
                # could not name it. Nothing here can clear it (BE-0402 removed the vision fallback),
                # so record the buttons the probe read and let the wait run to its own deadline: a
                # timeout naming the alert beats a guessed tap (prime directive 2).
                self._collapsed_polls = 0
                self.blocked_note = alert_block_note(buttons)
                return
            # "absent": no *SpringBoard* alert — fall through to the collapsed-tree proxy below.
        if self.guard.labels and probed_absent:
            # Only once the scenario has named its own candidate labels: an author who configured
            # `systemAlertHandling.labels` has opted into exactly the narrow surface
            # `_dismiss_from_tree` matches against, so the fast in-tree path is safe to try here. It
            # stays off the *default* dismissive labels (`Cancel`, `Close`, …) and off every
            # non-native backend, where those are ordinary English UI vocabulary a real screen can
            # legitimately show (see `_dismiss_from_tree`'s docstring).
            #
            # And only on a poll whose own native probe just reported no SpringBoard alert. This tap
            # goes through `Driver.tap`, which resolves an element, and XCUITest answers whatever
            # out-of-process alert is interrupting *before* it synthesizes such an interaction. The
            # app's own tree cannot see that alert, so an ungated in-tree tap acts blind: at `_POLL`
            # it fired ~20x per `poll_interval`, ~19 of them with no idea whether a prompt was up.
            # Pairing the tap with a same-poll negative answer paces it to `poll_interval` and makes
            # the order deterministic — the SpringBoard alert is cleared natively by the scenario's
            # policy first, and only then is an app-attached sheet cleared from the tree.
            event = self._dismiss_from_tree(elements)
            if event is not None:
                self.alerts.append(event)
                self._collapsed_polls = 0
                self.blocked_note = ""
                return
        if self._native_unhandled:
            # The last probe named an alert nothing will clear, and the proxy can only say less about
            # the same block. Keep the specific note until a probe reports the screen unblocked.
            return
        if self._tree_gave_up:
            # The in-tree path spent its tap budget on a prompt still showing, and said so in the
            # note. Keep it: an app-attached sheet does *not* collapse the tree, so the proxy below
            # would read the screen as unblocked and erase the one disclosure the eventual timeout
            # has (BE-0402). It lifts on its own once the label stops matching the tree.
            return
        # Every `_POLL`, whether or not the native query ran this tick, drive the debounced collapsed-
        # tree proxy: `system_alert_labels()` only sees `springboard.alerts`, so an action sheet or a
        # WKWebView JS dialog reads as absent yet still collapses the tree, and only this proxy
        # notices those (BE-0269). Sampling every `_POLL` (not once per `poll_interval`) keeps its
        # latency at ~`_GUARD_DEBOUNCE_POLLS * _POLL`; the debounce filters transient frames.
        self._observe_collapsed(elements)

    def _observe_collapsed(self, elements: list[base.Element]) -> None:
        """The collapsed-tree proxy: for a backend without the native capability, and for a native
        backend's `"absent"` polls, where a non-SpringBoard surface the native query cannot enumerate
        may still be blocking.

        It reads a correlation, not a fact — no query named a button here — so past the debounce it
        records the hedged, label-less note rather than one claiming a system alert (BE-0402).
        """
        if shows_app_ui(elements):
            self._collapsed_polls = 0
            self.blocked_note = ""
            return
        self._collapsed_polls += 1
        if self._collapsed_polls < _GUARD_DEBOUNCE_POLLS:
            return
        self.blocked_note = alert_block_note([])

    def _dismiss_from_tree(self, elements: list[base.Element]) -> AlertEvent | None:
        """Tap a scenario-named dismiss button already visible in this poll's own tree — no model call.

        Covers a system-owned prompt the native query cannot enumerate (BE-0315's `probe_native`
        reads only `springboard.alerts`) yet that still surfaces its buttons in the normalized tree
        the wait already fetched — an app-attached sheet such as iOS's Save Password prompt, whose
        `label`ed buttons appear right in the poll's own `elements`.

        Only ever called (see `_observe_native`) when `self.guard.labels` is non-empty and the
        backend is native-capable: `identifier is None` is not by itself a reliable "system-owned"
        signal (a backend or an unlabeled-by-design app screen can carry legitimate identifier-less
        buttons, per `shows_app_ui`'s own docstring), so this stays off the generic
        `DEFAULT_DISMISSIVE_LABELS` — ordinary English UI vocabulary ("Cancel", "Close") a real
        screen can legitimately show — and acts only on the scenario author's own explicit
        `systemAlertHandling.labels`, the narrow surface this path exists to speed up.

        Paces its taps on a label rather than tapping every match: unlike the native probe
        (rate-limited to `poll_interval`) and the collapsed-tree proxy (debounced),
        this runs every `_POLL`, so without its own guard a dismiss animation that keeps the button in
        the tree for a few frames — or a target screen that renders a poll or two later — would
        re-match and re-tap it on every one of those polls, over-counting one dismissal into several
        `AlertEvent`s and actuating the app repeatedly. `_tree_dismiss_pending` remembers the label
        just tapped and skips re-tapping it while it is still the poll's match; the tree ceasing to
        match (dismissed, or a different label) re-arms it.

        A label still matching `_TREE_RETAP_DELAY` after its own tap is the one case that must not be
        left there, and the reason the skip above is a delay rather than a once-per-showing rule: a
        tap the runner accepts does not always land (measured on iOS — `TouchEventsCompleted`
        confirmed, app unmoved), and a prompt that stays up is indistinguishable at the next poll from
        one merely fading out. Past that delay the animation is over, so the tap plainly did not land
        and is retried, `_TREE_DISMISS_MAX_TAPS` per showing. Only the first tap of a showing reports
        an `AlertEvent`, so a retry does not inflate one dismissal into several — and if the ceiling
        is reached, `_withdraw_tree_event` takes that event back, since the prompt is then known never
        to have cleared.

        A not-yet-reachable button (`ElementNotTappable`) gets a per-showing bound the two decline
        branches below it do not need, `_decline_giveup` long: unlike a vanished button
        or a transient ambiguity, an obstruction can be permanent (a scrim that never lifts, an
        `elevation` false positive in `topmost_at_point`), and the button staying in the tree means
        nothing here re-arms `_tree_dismiss_pending` to stop the retries on its own — so a stuck
        obstruction still degrades to the wait's own timeout instead of hammering the device for
        its entire remainder.
        """
        candidates = self.guard.labels
        buttons = [
            el["label"]
            for el in elements
            if el["label"] and not el["identifier"] and base.Trait.BUTTON in el["traits"]
        ]
        label = pick_alert_label(candidates, buttons)
        if label is None:
            # The tree stopped matching: the showing ended, so its recorded event stands as the real
            # dismissal it was — only the reference is dropped, so a later give-up cannot withdraw it.
            self._tree_dismiss_pending = None
            self._tree_tapped_at = None
            self._tree_signature = None
            self._tree_event = None
            self._tree_taps = 0
            self._tree_gave_up = False
            self._tree_not_tappable_label = None
            self._tree_not_tappable_since = None
            return None
        if label == self._tree_dismiss_pending:
            # This label's own tap left it showing. Inside `_TREE_RETAP_DELAY` that is the dismiss
            # animation, so wait rather than tap what is under a vanishing sheet; past it the tap did
            # not land, so retry — up to `_TREE_DISMISS_MAX_TAPS`, after which the wait's own timeout
            # takes over. Without the retry, one unacted-on tap disarmed this path for the whole
            # remaining wait and the prompt simply stayed up.
            assert self._tree_tapped_at is not None  # set with `_tree_dismiss_pending`, never apart
            if self.clock.now() - self._tree_tapped_at < _TREE_RETAP_DELAY:
                return None
            if _tree_signature(elements) != self._tree_signature:
                # The screen moved, so the tap *did* land. This label still matching is then a
                # different element — most likely an app-authored button of the same name the sheet
                # was covering — and re-tapping it would actuate the app, not a prompt. Decline for
                # the rest of this showing, the same conservative answer the once-per-showing rule
                # gave before the retry existed, and keep the recorded dismissal: it was real.
                return None
            if self._tree_taps >= _TREE_DISMISS_MAX_TAPS:
                # Loudly, once: the wait is about to spend its whole budget on a prompt this path
                # could not clear, and a silent give-up would leave the eventual timeout looking like
                # the awaited element simply never rendered. The note carries the same disclosure
                # onto the failure itself (BE-0402) — an app-attached sheet does not collapse the
                # tree, so the proxy below would otherwise report nothing at all about it. It is
                # `uncleared_prompt_note`, not the "unhandled" one: this label *did* resolve, so
                # reporting it as unnamed would send the author to add a label they already wrote.
                self.blocked_note = uncleared_prompt_note(label)
                if not self._tree_gave_up:
                    self._tree_gave_up = True
                    self._withdraw_tree_event()
                    _logger.warning(
                        "in-tree alert dismiss gave up after %d taps on %r; the prompt is still "
                        "showing — falling back to the wait's own timeout",
                        _TREE_DISMISS_MAX_TAPS,
                        label,
                    )
                return None
        else:
            # A different label: its own showing, with its own tap budget and give-up disclosure. The
            # previous showing's pending state goes with it, so `first_tap` below is decided against
            # this showing rather than against a label left pending that was never re-tapped — which
            # would silently drop a genuine second dismissal of that label from `alerts`. The event
            # the previous showing already recorded stays: it was a real dismissal, and this different
            # label is often exactly what it revealed.
            self._tree_dismiss_pending = None
            self._tree_tapped_at = None
            self._tree_signature = None
            self._tree_event = None
            self._tree_taps = 0
            self._tree_gave_up = False
        if label != self._tree_not_tappable_label:
            self._tree_not_tappable_label = label
            self._tree_not_tappable_since = None
        elif (
            self._tree_not_tappable_since is not None
            and self.clock.now() - self._tree_not_tappable_since
            >= _decline_giveup(self.guard.poll_interval)
        ):
            # Gave up on this showing; the wait's own timeout takes over — but says what it gave up
            # on, like the tap-budget branch below. Latched on `_tree_gave_up` for the same reason:
            # a permanently obstructed sheet keeps its own labelled buttons in the tree, so the
            # collapsed-tree proxy reads the screen as unblocked and would erase the note (BE-0402).
            self._tree_gave_up = True
            self.blocked_note = uncleared_prompt_note(label)
            return None
        # Scope the tap to `traits: [BUTTON]`, the same constraint `buttons` above already applied
        # when resolving `label` — matching a bare `{"label": label}` selector against `matches()`
        # (base.py) ignores `traits` entirely, so a non-button element sharing the exact text (a
        # static caption, a header drawn next to the sheet) would otherwise make the tap ambiguous
        # despite the intended button being uniquely named. Pre-checking uniqueness over this same
        # button-scoped count before tapping keeps a *persistent* same-label app button (identified,
        # so excluded from `buttons` above, but still a button) a cheap in-memory decline: that
        # `except AmbiguousSelector` branch below never arms `_tree_dismiss_pending`, so reaching it
        # every poll would re-issue the on-device tap ~20x/s for the rest of the wait.
        if (
            sum(1 for el in elements if el["label"] == label and base.Trait.BUTTON in el["traits"])
            != 1
        ):
            return None
        try:
            self.driver.tap({"label": label, "traits": [base.Trait.BUTTON]})
        except base.ElementNotFound:
            # The button vanished between this poll's query and the tap — a benign self-resolved
            # race, same treatment as `probe_native`'s TOCTOU branch.
            return None
        except base.AmbiguousSelector:
            # A rare query-vs-tap race: another button carrying the same label appeared between this
            # poll's tree read (checked unique above) and the tap. Declines rather than risk tapping
            # the wrong one.
            return None
        except base.ElementNotTappable:
            # Not yet reachable — a scrim the sheet draws over its own button before finishing its
            # presentation animation. The next poll's tree read tries again, up to the bound above:
            # the same benign self-resolved race as the two branches above, not a reason to fail the
            # wait, but not assumed to always self-resolve either.
            if self._tree_not_tappable_since is None:
                self._tree_not_tappable_since = self.clock.now()
            return None
        # Only the first tap of a showing reports an `AlertEvent`: a retry is the same prompt being
        # cleared again, not a second one, so counting each would inflate one dismissal into several
        # in the report. The retry's actuation is still recorded in the driver's own log.
        first_tap = label != self._tree_dismiss_pending
        self._tree_dismiss_pending = label
        self._tree_tapped_at = self.clock.now()
        self._tree_signature = _tree_signature(elements)
        self._tree_taps += 1
        self._tree_not_tappable_label = None
        self._tree_not_tappable_since = None
        if not first_tap:
            return None
        # Held by identity so the give-up path can withdraw this exact event — two showings of the
        # same label compare equal, and withdrawing the wrong one would misreport a real dismissal.
        self._tree_event = AlertEvent(label=label)
        return self._tree_event

    def _withdraw_tree_event(self) -> None:
        """Un-record this showing's dismissal once the prompt is known to still be up.

        The `AlertEvent` is recorded on the tap, which is the only moment it *can* be — nothing at
        that point distinguishes a tap that lands from one the app never acts on. Reaching the tap
        ceiling is where that becomes knowable, and `AlertEvent`'s own contract is a prompt the guard
        *dismissed*, so leaving it would make the report contradict the warning beside it: the step
        times out with the sheet still up while the report says it was cleared. Removed by identity,
        not equality, so an earlier showing of the same label keeps its own genuine record.
        """
        event = self._tree_event
        self._tree_event = None
        if event is None:
            return
        for i, recorded in enumerate(self.alerts):
            if recorded is event:
                del self.alerts[i]
                return


# Genuinely long: the wait state machine on the deterministic run path. Splitting it carries real
# behavioral risk, so it belongs to BE-0386's ratchet steps rather than the PR that sets the
# ceiling.
def _wait(  # noqa: C901, PLR0912
    driver: base.Driver,
    w: Wait,
    clock: Clock,
    network: NetworkSource = _no_network,
    *,
    trace: WaitTrace | None = None,
    alert_guard: AlertGuardConfig | None = None,
    alerts: list[AlertEvent] | None = None,
    on_tick: WaitTick | None = None,
    transitions: TransitionSource = _no_transitions,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
    cancelled: CancelSource = not_cancelled,
) -> tuple[bool, str, list[base.Element] | None]:
    """Condition wait. Polls query() (or the observed network) until satisfied instead
    of a fixed sleep.

    When `trace` is given (a `for` wait only), each poll is recorded into it so a timeout can be
    diagnosed from artifacts (BE-0231 Unit 1); it never changes the wait's outcome.

    When `alert_guard` is given, the branches a system alert can *stall* — `for`, `settled`, and
    `screenChanged` (where a collapsed tree keeps the condition unmet and would otherwise burn the
    whole timeout) — drive the guard mid-wait, then resume polling against the *same* `deadline`. On
    an iOS backend the guard queries SpringBoard natively on its own interval (BE-0315, reusing
    BE-0316's primitive); elsewhere it watches the already-fetched tree for the collapsed-tree
    signature of a blocking prompt (BE-0269). The condition check still decides pass/fail; the guard
    only accelerates recovery, and dismissed alerts are appended to `alerts` (the step's outcome
    list) for the report. A block it cannot clear is not acted on at all (BE-0402) — it is appended
    to the timeout this returns, so the failure names the alert instead of only the element that
    never appeared. `gone` is guarded
    too. It was not, on the reasoning that a collapsed tree already satisfies "gone" and returns at
    once — true of a SpringBoard prompt, which covers the app and empties its tree, but only of
    those. A prompt drawn *inside* the app's own process collapses nothing and instead **adds** its
    buttons to the tree, so a `gone` wait on one of them sits unsatisfied for its whole timeout with
    nothing to clear it. iOS's "Save Password" alert is exactly that shape, which is how the gap
    surfaced. `request` polls the network, not the screen, so it is still not guarded.

    When `on_interrupt_poll` is given, it is called with each poll's already-fetched tree — after
    the wait's own condition is checked, so it fires only while the wait is still blocked — so a
    scenario's `interrupts` handlers can clear an interstitial screen mid-wait (BE-0314). Like the
    alert guard, it rides on the poll the wait already performs (zero extra query) and resumes
    against the *same* `deadline`; the `gone`/`request` branches are not hooked (a collapsed tree
    already satisfies `gone`, and `request` polls the network, not the screen). A `True` return ends
    the wait immediately (skipping the `deadline` check) rather than burning the rest of the
    timeout: an interrupt's own recovery `steps` can fail, and that failure is already decided by
    the first poll that hits it, so polling on would only turn a fast, loud failure into a slow one.
    The caller (the run loop) knows the real reason and overrides the placeholder this returns.

    When `on_tick` is given, a throttled "still waiting …" line is emitted while the wait is pending:
    once on entry — so even an instantly-satisfied wait surfaces its condition — then every
    `_TICK_INTERVAL` until it resolves. It is display only and never affects the outcome.

    `transitions` (BE-0310) is the `settled` branch's read-only screen-transition signal; the
    default reports none, so `settled` keeps its unchanged tree-diff behavior unless a caller passes
    a real source.

    `cancelled` (BE-0370) is consulted once per poll, right where the deadline is, so a wait blocked
    on a condition notices a cancelled run within one polling tick instead of burning the rest of
    its timeout. It raises `RunCancelled` rather than returning a verdict: the condition is neither
    satisfied nor timed out, and the scenario is over either way. The condition check comes first, so
    a wait already satisfied on that poll still passes.

    Returns `(ok, reason, tree)` where `tree` is the last screen the wait queried — the settled
    device state, since nothing actuates in a wait. The caller reuses it as the step's `after`
    snapshot instead of re-querying (BE-0259). It is `None` for the `request` variant, which polls
    the observed network rather than the tree, so there is no screen read to hand back.
    """
    timeout = _effective_timeout(w)
    start = clock.now()
    deadline = start + timeout
    # Give the gate a real list to record into even when the caller passed none (e.g. a direct
    # _wait() unit test), so the record-the-event path has no None branch. `is not None`, not
    # `or []`: an empty list the caller *did* pass is falsy but must still be the one appended to.
    gate = (
        _AlertGuardGate(
            driver=driver,
            clock=clock,
            guard=alert_guard,
            alerts=alerts if alerts is not None else [],
        )
        if alert_guard is not None
        else None
    )
    hb = _Heartbeat(on_tick, deadline) if on_tick is not None else None
    if hb is not None:
        # Fire once up front so the awaited condition is shown even for a wait that resolves on its
        # first poll (the common fast case), before any per-loop tick has had a chance to run.
        hb.tick(start)
    if w.for_ is not None:
        target = w.for_.as_selector()
        if trace is not None:
            trace.target = str(target)
            trace.timeout_s = timeout
        while True:
            t0 = clock.now()
            elements = driver.query()
            if trace is not None:
                trace.polls += 1
                if elements and trace.first_nonempty_s is None:
                    trace.first_nonempty_s = t0 - start
            if _exists(elements, target):
                return True, "", elements
            if gate is not None:
                gate.observe(elements)
            if on_interrupt_poll is not None and on_interrupt_poll(elements):
                return False, "interrupt recovery failed", elements
            if cancelled():
                raise RunCancelled
            if clock.now() >= deadline:
                if trace is not None:
                    trace.elements_at_timeout = len(elements)
                return (
                    False,
                    _with_block_note(f"wait timeout: for {target} ({timeout}s)", gate),
                    elements,
                )
            if hb is not None:
                hb.tick(clock.now())
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, Gone):
        target = w.until.gone.as_selector()
        while True:
            t0 = clock.now()
            elements = driver.query()
            if not _exists(elements, target):
                return True, "", elements
            # Guarded like `for` (see the docstring): a prompt the app draws in its own process does
            # not collapse the tree, it adds to it, so "gone" stays false until something clears the
            # prompt — and only the guard will. Observed after the condition, so a wait already
            # satisfied never actuates.
            if gate is not None:
                gate.observe(elements)
            if cancelled():
                raise RunCancelled
            if clock.now() >= deadline:
                return (
                    False,
                    _with_block_note(f"wait timeout: gone {target} ({timeout}s)", gate),
                    elements,
                )
            if hb is not None:
                hb.tick(clock.now())
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, WaitRequest):
        req = w.until.request
        need = req.count if req.count is not None else 1
        while True:
            t0 = clock.now()
            if assertions.count_matching(network(), req) >= need:
                return True, "", None
            if cancelled():
                raise RunCancelled
            if clock.now() >= deadline:
                label = assertions.request_label(req)
                return False, f"wait timeout: request {label} ({timeout}s)", None
            if hb is not None:
                hb.tick(clock.now())
            _adaptive_sleep(clock, t0)
    if w.until == "settled":
        return _wait_settled(
            driver, deadline, clock, gate, hb, transitions, on_interrupt_poll, start, cancelled
        )
    # until == "screenChanged"
    before = driver.query()
    if gate is not None:
        gate.observe(before)
    while True:
        t0 = clock.now()
        current = driver.query()
        if current != before:
            return True, "", current
        if gate is not None:
            gate.observe(current)
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        if cancelled():
            raise RunCancelled
        if clock.now() >= deadline:
            return (
                False,
                _with_block_note(f"wait timeout: screenChanged ({timeout}s)", gate),
                current,
            )
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)


def _wait_settled(
    driver: base.Driver,
    deadline: float,
    clock: Clock,
    gate: _AlertGuardGate | None = None,
    hb: _Heartbeat | None = None,
    transitions: TransitionSource = _no_transitions,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
    start: float = 0.0,
    cancelled: CancelSource = not_cancelled,
) -> tuple[bool, str, list[base.Element]]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    When `transitions` has reported a screen-transition event *since this wait began*
    (`events[-1][1] >= start`), settled is a positive signal — no further transition reported for
    `_TRANSITION_QUIESCENCE` — rather than an inference from tree reads; see
    `_wait_settled_by_signal`. This is re-checked on every poll, not only at entry: `viewDidAppear`'s
    report is POSTed fire-and-forget *after* the appearance animation, so for the canonical
    tap → navigate → `settled` step it lands a few hundred ms into the wait, not before it — the wait
    switches onto the signal path the instant that report arrives, mirroring the readiness gate's
    per-tick re-read. The since-start guard mirrors the one the readiness gate applies to the same
    signal (BE-0310): a transition left over from a *prior* step (the collector is scenario-scoped,
    not per-wait) predates `start`, so it is ignored rather than settling this wait instantly and
    missing the current step's own transition. Until a since-start transition is observed (the app
    doesn't link the observer, or its report is still in flight), this runs the original tree-diff
    behavior, which waits the animation out: a blank/collapsed tree (e.g. a screen mid-render, or one
    covered by a system alert) is never treated as settled, and settled is two consecutive unchanged
    polls with an identified element. Both paths are best-effort: timing out
    just proceeds with the current screen — a settle is a stabilization hint, not a correctness
    assertion, so it never fails the step. When `gate` is given, a screen that stays collapsed (a
    system alert) is cleared mid-settle rather than burning the whole timeout (BE-0269). When `hb` is
    given, it emits the throttled "still waiting …" progress line while settling. Returns the last
    queried tree so the caller can reuse it as the step's `after` snapshot (BE-0259).

    A `True` from `on_interrupt_poll` ends the settle immediately (BE-0314) — a failed interrupt
    recovery is a decided outcome the caller (the run loop) fails the step on, so polling toward
    settled would only delay a failure that best-effort settling would otherwise mask. `cancelled`
    raises `RunCancelled` out of the settle the same way it does out of every other wait branch
    (BE-0370): settling is best-effort, but a cancelled run has nothing left to settle *for*.
    """
    previous = driver.query()
    if gate is not None:
        gate.observe(previous)
    stable = 0
    while stable < _SETTLE_POLLS:
        # A qualifying transition can land mid-wait, not only before it: `viewDidAppear`'s
        # fire-and-forget report arrives *after* the appearance animation, so for the canonical
        # tap → navigate → `settled` step it lands a few hundred ms into this wait rather than at
        # entry. Re-consult every poll — like the readiness gate — and switch to the signal path the
        # instant a since-start transition appears; until then the tree-diff loop below waits the
        # animation out. A left-over transition from a prior step predates `start`, so it is ignored.
        events = transitions()
        if events and events[-1][1] >= start:
            return _wait_settled_by_signal(
                driver,
                deadline,
                clock,
                gate,
                hb,
                transitions,
                events[-1][1],
                on_interrupt_poll,
                cancelled,
            )
        if clock.now() >= deadline:
            return True, "", previous
        # After the deadline return, not before it: a settle never fails a step, so that return is a
        # *pass*, and checking first would turn a settle that had already finished into a cancelled
        # failure — the retroactive verdict change every other branch's ordering rules out.
        if cancelled():
            raise RunCancelled
        t0 = clock.now()
        current = driver.query()
        if gate is not None:
            gate.observe(current)
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)
    return True, "", previous


def _wait_settled_by_signal(
    driver: base.Driver,
    deadline: float,
    clock: Clock,
    gate: _AlertGuardGate | None,
    hb: _Heartbeat | None,
    transitions: TransitionSource,
    last: float,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
    cancelled: CancelSource = not_cancelled,
) -> tuple[bool, str, list[base.Element]]:
    """The signal-based settle path (BE-0310): quiescence since the last observed transition.

    "No further screen-change transition reported for `_TRANSITION_QUIESCENCE`" is a positive "the
    last transition has finished and no new one started," not "two reads happened to match" — the
    window restarts each time a fresh transition is observed. `last` is the most recent transition's
    receive time, already fetched by the caller (`_wait_settled`) to confirm at least one had been
    reported. A collector only ever appends in receive order, so it stays non-empty and its final
    element is always the newest — later reads take `transitions()[-1][1]` rather than scanning for
    a max.

    A `True` from `on_interrupt_poll` ends the settle immediately (BE-0314), same as the tree-diff
    fallback above — the signal path is still a settle loop over `driver.query()`, so a scenario's
    `interrupts` handlers apply here too, not only when no transition signal is available.
    """
    # Diagnostic only (BE-0310 Unit 5): confirms the signal path actually decided settled on a real
    # device, so on-device verification needs no extra instrumentation to observe it.
    _logger.debug(
        "settled via the screen-transition signal (quiescence=%ss)", _TRANSITION_QUIESCENCE
    )
    current = driver.query()
    if gate is not None:
        gate.observe(current)
    while clock.now() - last < _TRANSITION_QUIESCENCE:
        if clock.now() >= deadline:
            return True, "", current
        # Below the deadline return for the same reason as the tree-diff path above: that return is a
        # pass, and a settle already finished must not become a cancelled failure.
        if cancelled():
            raise RunCancelled
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        t0 = clock.now()
        last = transitions()[-1][1]
        current = driver.query()
        if gate is not None:
            gate.observe(current)
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)
    return True, "", current

"""Fire the system-alert guard mid-wait, without spending the wait's own budget (BE-0269)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import shows_app_ui, tree_signature
from bajutsu.common.orchestrator.types import (
    AlertEvent,
    AlertGuardConfig,
    Clock,
    alert_block_note,
    identified_alert_rules,
    match_alert_rule,
    subtract_labels,
    uncleared_prompt_note,
)

from ._shared import _logger

# Mid-wait system-alert guard (BE-0269). A SpringBoard-level prompt collapses the iOS app-scoped tree
# to bare content (`not shows_app_ui`); rather than let a wait burn its whole timeout before the
# end-of-step guard looks, watch the already-fetched poll tree and ask the guard to clear it early.
# A hair above _SETTLE_POLLS, so a transient collapsed frame does not read as a blocked screen: since
# BE-0402 a trip costs no model call, only the note a timeout would then carry, and a note naming a
# block that was never there is the one wrong answer this path can still give.
_GUARD_DEBOUNCE_POLLS = 3  # consecutive collapsed polls before recording the note


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
    WKWebView JS dialog) it cannot enumerate is blocking and no in-tree-capable rule of the
    scenario's identifies it (the one such surface `_dismiss_from_tree` below still taps) — nothing here can
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
    # The selector a `handleSystemAlert` step running this gate is itself waiting on (BE-0406), so
    # the guard leaves that step's own prompt alone. None for a `wait` step, which names no prompt.
    reserved: base.Selector | None = None
    _native: bool = field(init=False)
    _last_native: float | None = None
    _collapsed_polls: int = 0
    # Whether the most recent native probe found an alert it could not name. The native query runs
    # once per `poll_interval` while the collapsed-tree proxy below samples every `_POLL`, so without
    # this the proxy would overwrite the probe's own button-naming note with its hedged one on every
    # tick in between — reporting less than the guard actually knows.
    _native_unhandled: bool = False
    # Whether the most recent native probe found the alert the waiting step itself named. Latched
    # for the same reason as `_native_unhandled` above: the probe runs once per `poll_interval`
    # while the collapsed-tree proxy samples every `_POLL`, and that alert covers the app, so
    # without this the proxy would spend the polls in between recording a hedged "something is
    # blocking the screen" note against a prompt the step is about to answer.
    _native_reserved: bool = False
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
            state, event, buttons = self.guard.probe_native(self.driver, self.reserved)
            # `dismissed` is never passed here, unlike `AlertGuardConfig.__call__`'s own loop
            # (BE-0418) -- this poll never accumulates state across calls, so `_resolve_alert_rule`'s
            # subset-based retry (the only path that can return `None`) never runs, and
            # `probe_native` can never report "already_dismissed" from this call site. Asserted
            # rather than left to fall through: every branch below still tests the five states that
            # predate BE-0418's sixth by equality, not a `match` or `assert_never`, so a later change
            # threading real `dismissed` state through this poll -- this gate faces the identical
            # lingering-fade race `__call__` already handles -- would otherwise have the new state
            # silently read as "nothing is blocking" here (review finding).
            assert state != "already_dismissed"
            # Only a genuinely empty read licenses the in-tree tap below: since BE-0418 the
            # time-of-check/time-of-use race answers "absent" over a *non-empty* read, and a live
            # SpringBoard alert is what XCUITest answers with its own default button before
            # synthesizing any interaction (BE-0399) — the same gate `AlertGuardConfig.__call__`
            # applies with its own `if not buttons` before reaching `dismiss_from_tree_once`.
            probed_absent = state == "absent" and not buttons
            # A raced `"absent"` over a non-empty read is no more evidence the *other* buttons that
            # read enumerated went away than it is licence to tap the tree, so it must not drop this
            # latch either: once this goes False the collapsed-tree proxy below erases the note on
            # the very next `_POLL` tick, long before the next native probe is due (BE-0418).
            self._native_unhandled = state == "unhandled" or (
                state == "absent" and bool(buttons) and self._native_unhandled
            )
            self._native_reserved = state == "reserved"
            # A race-`"absent"` over a non-empty read is no more evidence the surface is clear than
            # it is licence to tap the tree, so it must not erase a note either (BE-0418): the one
            # alert this round tried to tap raced away, which says nothing about a *different*
            # button the same read enumerated, e.g. one an earlier round already named "unhandled".
            raced = state == "absent" and bool(buttons)
            if state != "unhandled" and not raced and not self._tree_gave_up:
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
                # `probe_native` reaches "unhandled" two ways: a genuinely unidentified alert, and
                # a matched rule whose tap found the label twice (`AmbiguousSelector`, "the other
                # half of that race"). Only the first is a prompt no rule identifies. Re-resolving
                # tells them apart: a rule did identify the second, and only the tap failed to take,
                # which is `uncleared_prompt_note`'s own case, not the hedged "unhandled" form
                # (BE-0418 review finding; see `uncleared_prompt_note`'s docstring).
                #
                # But `buttons` here is the whole SpringBoard enumeration, not the ambiguous rule's
                # own shape, and `AmbiguousSelector` fires only after a rule already matched -- so a
                # co-present button no rule identifies can sit alongside it in the same read. Every
                # shape a rule identifies, not only the ambiguous one's own, and the same
                # `identified_alert_rules` / `subtract_labels` pair the `raced` branch below shares
                # with `_leftover_note` (`types/_functions.py`): a button nothing accounts for still
                # outranks the ambiguous rule's own diagnosis, since something else is demonstrably
                # unhandled either way (BE-0418 review finding).
                self._collapsed_polls = 0
                if not self._tree_gave_up:
                    # The same exception the clear-guard above and the `raced` branch below both
                    # make: an in-tree give-up names a prompt a rule *did* identify and a tap
                    # failed to clear, and nothing re-arms that note once a live SpringBoard alert
                    # stops `probed_absent` from holding (BE-0418 review finding).
                    identified = identified_alert_rules(self.guard.native_rules, buttons)
                    leftover = subtract_labels(
                        buttons, (rule.identifying_labels for rule in identified)
                    )
                    self.blocked_note = (
                        alert_block_note(leftover)
                        if leftover or not identified
                        else uncleared_prompt_note(identified[0].tap_label)
                    )
                return
            if raced:
                # The same deference "unhandled" gets, just above: a live, enumerated surface is not
                # something the collapsed-tree proxy below can say more about, and letting this poll
                # fall into it would replace the note this branch's own clear-guard preserved with the
                # proxy's hedged one — or erase it outright, since the app tree an out-of-process
                # SpringBoard alert covers still `shows_app_ui` (BE-0418 review finding).
                #
                # Preserving an existing note is not the same as producing one: a co-present button
                # no rule identifies, enumerated by this very read alongside the raced rule's own
                # shape, would otherwise go unreported for a whole `poll_interval` (BE-0418 review
                # finding).
                #
                # Every shape a rule identifies on this read, not only the one that raced: a
                # second, *declared* prompt co-present with it is one the next probe answers, so
                # naming it here would report an alert a rule does identify as unhandled. Shared
                # with `matching_alert_rule` (`types/_functions.py`) via `identified_alert_rules`,
                # rather than a hand-rolled copy of its accept test, so this can never credit a
                # shape a probe itself would refuse -- or refuse one a probe would credit (BE-0418
                # review finding).
                # Shares `subtract_labels` with `AlertGuardConfig.__call__`'s own `_leftover_note`
                # (`types/_functions.py`) rather than a second, hand-rolled removal loop -- the
                # credit test above admits a shape only when each of its labels appears exactly
                # once, so multiplicity is defensive rather than load-bearing here, but the
                # subtraction itself stays a single, shared spelling (BE-0418 review finding).
                leftover = subtract_labels(
                    buttons,
                    (
                        rule.identifying_labels
                        for rule in identified_alert_rules(self.guard.native_rules, buttons)
                    ),
                )
                if leftover:
                    self._native_unhandled = True
                    if not self._tree_gave_up:
                        # The same exception the clear-guard above and the `elif` below both make:
                        # an in-tree give-up names a prompt a rule *did* identify and a tap failed
                        # to clear, and the hedged "unhandled" note would tell the author the
                        # opposite (`uncleared_prompt_note`'s own docstring, BE-0418 review finding).
                        self.blocked_note = alert_block_note(leftover)
                elif self._native_unhandled and not self._tree_gave_up:
                    # Nothing but the raced rule's own shape is on the surface, and this read is the
                    # whole SpringBoard enumeration -- so an earlier probe's "unhandled" note names a
                    # button this very read proves gone. The proxy's hedged note, for a surface the
                    # query cannot enumerate, is a different story and is preserved above (BE-0418
                    # review finding).
                    self._native_unhandled = False
                    self.blocked_note = ""
                self._collapsed_polls = 0
                return
            # Only a genuinely empty "absent" falls through to the in-tree dismiss below; "reserved"
            # falls through to the collapsed-tree proxy, but its own latch stops it short of it.
        if self.guard.tree_rules and probed_absent:
            # Only once the scenario holds a rule for a prompt this path can actually reach: an
            # author who declared one has named the alert they expect, which is what makes the fast
            # in-tree path safe to try here. A rule for a SpringBoard-only prompt arms nothing, so a
            # scenario declaring `notifications` alone never gets an application screen's own
            # identifier-less "Allow" tapped (see `_dismiss_from_tree`'s docstring).
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
        if self._native_reserved:
            # The last probe found the alert the `handleSystemAlert` step driving this gate is
            # itself waiting on, and that step taps it on its own next read (BE-0406). Nothing here
            # acts, and the proxy below must not run either: the alert covers the app, so the proxy
            # would record a block against a prompt that is about to be answered.
            self._collapsed_polls = 0
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

        Only ever called (see `_observe_native`) when `self.guard.tree_rules` is non-empty and the
        backend is native-capable: `identifier is None` is not by itself a reliable "system-owned"
        signal (a backend or an unlabeled-by-design app screen can carry legitimate identifier-less
        buttons, per `shows_app_ui`'s own docstring), so this acts only on an alert one of the
        scenario's own rules identifies by its full label set (BE-0406). Matching a prompt rather
        than a word is what keeps ordinary UI vocabulary — "Cancel", "Close", and, for iOS 26.5's
        in-app save sheet, "Save" — from licensing a tap on a screen no scenario described.

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
        # Imported in the method, not at module load: `_functions` builds this gate, and rule 5
        # breaks the cycle the split creates on this side.
        from ._functions import _decline_giveup

        buttons = [
            el["label"]
            for el in elements
            if el["label"] and not el["identifier"] and base.Trait.BUTTON in el["traits"]
        ]
        # The one shared ordering every in-tree, dedup-aware match reads from (BE-0418 review
        # finding): matching over anything else here would let this gate and `dismiss_from_tree_once`
        # — declared twins over the same screen — pick differently, so which button a scenario gets
        # would depend on whether a `wait` happened to be running when the sheet appeared.
        label = match_alert_rule(self.guard.tree_dedup_rules, buttons)
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
            if tree_signature(elements) != self._tree_signature:
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
        self._tree_signature = tree_signature(elements)
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

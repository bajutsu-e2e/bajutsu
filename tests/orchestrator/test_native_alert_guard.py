"""Tests for the reactive native system-alert guard path (BE-0315).

The reactive guard clears SpringBoard prompts automatically, preferring a deterministic native path
built on BE-0316's primitives — `system_alert_labels()` (a read of BE-0316's `/systemAlert/query`)
to see the alert's buttons, then `handle_system_alert()` to tap a policy-named one — over the vision
fallback. Exercised against `FakeDriver`, which advertises `HANDLE_SYSTEM_ALERT` and can be seeded
with alert buttons, so nothing here needs a Simulator; the on-device confirmation is a separate lane.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar, cast

import pytest
from conftest import guard_rule

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertEvent, AlertGuardConfig
from bajutsu.common.orchestrator.types import (
    ResolvedAlertRule,
    match_alert_rule,
    uncleared_prompt_note,
)
from bajutsu.common.scenario import Wait


class _LogicalClock:
    """A clock whose only motion is `sleep` advancing logical time (no real waiting)."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


class _Incapable:
    """A driver stub without the HANDLE_SYSTEM_ALERT capability (for the incapable branch)."""

    def capabilities(self) -> set[str]:
        return set()


class _NonNativeDriver(FakeDriver):
    """A web/Android-shaped backend stand-in: capable of everything `FakeDriver` is, minus
    `HANDLE_SYSTEM_ALERT` — for asserting the in-tree dismiss path stays native-only."""

    def capabilities(self) -> set[str]:
        return super().capabilities() - {base.Capability.HANDLE_SYSTEM_ALERT}


def _button(label: str) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0, 0, 10, 10),
        "nativeZ": None,
    }


def _fake_with_alert(labels: list[str], react: object = None) -> FakeDriver:
    driver = FakeDriver([], react=react)  # type: ignore[arg-type]
    driver.system_alert_buttons = [_button(label) for label in labels]
    return driver


def _call(
    driver: base.Driver,
    guard: AlertGuardConfig,
    *,
    settle: Callable[[], None] | None = None,
) -> tuple[bool, list[AlertEvent]]:
    """`guard(driver, ...)` (BE-0418) with a fresh `alerts` list and a no-op `settle` by default.

    Nothing here needs the real `settle_after_alert_dismiss` sweep: the fake driver never animates,
    so a no-op stands in unless a test cares whether/when `settle` itself ran.
    """
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle or (lambda: None))
    return cleared, alerts


def _clearing_native(labels_left: list[str]) -> Callable[[FakeDriver, str, object], None]:
    """A `react` hook: dismissing a SpringBoard alert actually clears it, as a real device would.

    `FakeDriver.handle_system_alert` never removes the tapped button on its own — a test opts into
    that, exactly as the mid-wait gate's own tests already do — so without this a BE-0418 loop
    round would re-probe the same still-seeded alert and re-dismiss it.
    """

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button(label) for label in labels_left]

    return react


def _clearing_tree_tap(label: str) -> Callable[[FakeDriver, str, object], None]:
    """A `react` hook: a landed in-tree tap actually removes the button, as a real dismiss would.

    `FakeDriver.tap` never mutates `screen` on its own, so without this a BE-0418 loop round would
    find the same button still there and tap it a second time.
    """

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == label:
            d.screen = [el for el in d.screen if el["label"] != label]

    return react


# --- match_alert_rule -------------------------------------------------------------------------------

_NOTIF_RULE = ResolvedAlertRule(
    identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
)
_TRACKING_RULE = ResolvedAlertRule(
    identifying_labels=frozenset({"Allow", "Ask App Not to Track"}),
    tap_label="Ask App Not to Track",
)


def test_match_alert_rule_identifies_the_prompt_by_its_full_label_pair() -> None:
    # The tracking prompt's alert carries both of its own labels, so the tracking rule matches even
    # though "Allow" alone is shared with the notifications prompt.
    assert (
        match_alert_rule([_TRACKING_RULE], ["Allow", "Ask App Not to Track"])
        == "Ask App Not to Track"
    )


def test_match_alert_rule_none_when_only_the_shared_label_is_present() -> None:
    # "Allow" alone cannot identify which of two prompts is on screen.
    assert match_alert_rule([_TRACKING_RULE], ["Allow", "Cancel"]) is None


def test_match_alert_rule_returns_the_first_matching_rule_in_order() -> None:
    assert match_alert_rule([_NOTIF_RULE, _TRACKING_RULE], ["Allow", "Don't Allow"]) == "Allow"
    assert (
        match_alert_rule([_NOTIF_RULE, _TRACKING_RULE], ["Allow", "Ask App Not to Track"])
        == "Ask App Not to Track"
    )


def test_match_alert_rule_none_when_no_rules_or_no_match() -> None:
    assert match_alert_rule([], ["Allow", "Don't Allow"]) is None
    assert match_alert_rule([_NOTIF_RULE], ["Weird Button"]) is None
    assert match_alert_rule([_NOTIF_RULE], []) is None  # nothing on screen identifies nothing


def test_match_alert_rule_requires_each_identifying_label_exactly_once() -> None:
    # Two buttons carrying the same label cannot uniquely identify the prompt, so the rule is
    # declined rather than resolved to whichever button matched first (determinism first, mirroring
    # resolve_unique).
    assert match_alert_rule([_NOTIF_RULE], ["Allow", "Allow", "Don't Allow"]) is None


def test_match_alert_rule_skips_an_ambiguous_rule_and_takes_the_next_one() -> None:
    # A duplicated label disqualifies only the rule that names it: the scan continues rather than
    # stopping at the first rule it could not resolve, so a second rule the same alert identifies
    # unambiguously still answers it.
    ambiguous = ResolvedAlertRule(identifying_labels=frozenset({"OK"}), tap_label="OK")
    unambiguous = ResolvedAlertRule(identifying_labels=frozenset({"Cancel"}), tap_label="Cancel")
    assert match_alert_rule([ambiguous, unambiguous], ["OK", "OK", "Cancel"]) == "Cancel"
    assert match_alert_rule([ambiguous], ["OK", "OK"]) is None


def test_match_alert_rule_declines_a_rule_whose_excluded_label_is_on_the_alert() -> None:
    # Two prompts can share their whole identifying pair — iOS 26.5's save sheet and its credit-card
    # sibling are both "Save"/"Not Now" — and only a third button tells them apart. `excluded_labels`
    # is how the save rule declines the card sheet: the pair is present, so without the exclusion it
    # would match and answer a prompt it was never written for (BE-0406).
    save = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        excluded_labels=frozenset({"Never for This Card"}),
    )
    assert match_alert_rule([save], ["Save", "Not Now", "Never for This Card"]) is None
    assert match_alert_rule([save], ["Save", "Not Now"]) == "Not Now"


# --- AlertGuardConfig.probe_native ------------------------------------------------------------------


def test_probe_native_incapable_backend() -> None:
    guard = AlertGuardConfig()
    assert guard.probe_native(_Incapable()) == ("incapable", None, [])  # type: ignore[arg-type]


def test_probe_native_absent_when_no_alert() -> None:
    guard = AlertGuardConfig()
    assert guard.probe_native(FakeDriver([])) == ("absent", None, [])  # capable, no alert seeded


def test_probe_native_dismisses_a_named_button() -> None:
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    state, event, _ = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Allow")
    # It tapped through BE-0316's handle_system_alert with the picked label.
    assert ("handle_system_alert", ({"label": "Allow"}, 0.0)) in driver.actions


def test_probe_native_does_not_fall_back_to_dismissive_defaults() -> None:
    # BE-0406 made `rules` the whole policy, so a scenario declaring none gets no dismissal at all —
    # not even for a button as ordinary as "Don't Allow", which the removed built-in fallback used
    # to tap on generic English vocabulary alone, rather than on a rule naming the prompt.
    guard = AlertGuardConfig()  # no rules → nothing this guard may answer
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    assert guard.probe_native(driver) == ("unhandled", None, ["Don't Allow", "Allow"])
    assert driver.actions == []  # and nothing was tapped on the way to that answer


def test_probe_native_taps_the_choice_of_the_rule_that_identifies_the_prompt() -> None:
    # The decision is the rule's, not the buttons' own wording: the tracking prompt is identified by
    # its full label pair and answered with the choice its rule names, though "Allow" sits beside it.
    guard = AlertGuardConfig(rules=[_TRACKING_RULE])
    driver = _fake_with_alert(["Allow", "Ask App Not to Track"])
    state, event, _ = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Ask App Not to Track")


def test_probe_native_leaves_an_alert_no_rule_identifies_alone() -> None:
    # A configured guard is no licence over every prompt: the notifications alert is not the one this
    # scenario's rule describes, so it is reported rather than answered — neither with that rule's
    # own choice nor with a dismissive-looking button read off the alert (BE-0406).
    guard = AlertGuardConfig(rules=[_TRACKING_RULE])
    driver = _fake_with_alert(
        ["Don't Allow", "Allow"]
    )  # notifications prompt: no rule identifies it
    assert guard.probe_native(driver) == ("unhandled", None, ["Don't Allow", "Allow"])
    assert driver.actions == []


def test_probe_native_never_matches_a_rule_the_surface_cannot_reach() -> None:
    # An in-tree-only rule's identifying labels are ordinary vocabulary a real SpringBoard alert
    # could coincidentally offer — savePassword's 26.5 shape is just "Save" / "Not Now" — so this
    # surface must filter on `native` rather than trust that no SpringBoard alert ever matches.
    # Without the filter this alert would be answered through `handle_system_alert` for a prompt
    # that path can never actually reach (BE-0406 review finding).
    in_tree_only = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    guard = AlertGuardConfig(rules=[in_tree_only])
    driver = _fake_with_alert(["Save", "Not Now"])
    assert guard.probe_native(driver) == ("unhandled", None, ["Save", "Not Now"])
    assert driver.actions == []


def test_probe_native_unhandled_when_no_candidate_resolves() -> None:
    # The buttons come back with the state (BE-0402): nothing will clear this alert, so they are all
    # a blocked step or wait has left to name what stopped it.
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    driver = _fake_with_alert(["Weird Button"])
    assert guard.probe_native(driver) == ("unhandled", None, ["Weird Button"])


def test_probe_native_treats_a_dismiss_race_as_absent() -> None:
    # TOCTOU: the alert vanishes between the presence query and the tap, so handle_system_alert
    # raises ElementNotFound. That is a benign self-resolved race — reported as absent, not a failure.
    # Carries the original non-empty read forward rather than discarding it like a genuine empty
    # enumeration would: this race only proves the one alert the round tried to tap is gone, not
    # that the rest of the surface is (BE-0418 review finding) — `__call__` needs the distinction to
    # avoid retracting an unrelated, still-fading dismissal's own dedup record on this race alone.
    class _RaceDriver(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the alert vanished before the tap")

    driver = _RaceDriver([])
    driver.system_alert_buttons = [_button("Allow")]
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    assert guard.probe_native(driver) == ("absent", None, ["Allow"])


# --- AlertGuardConfig.__call__ (native only since BE-0402) ------------------------------------------


def test_call_returns_the_native_event_and_leaves_no_note() -> None:
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    driver = _fake_with_alert(["Allow"], react=_clearing_native([]))
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert guard.blocked_note == ""  # the screen is unblocked; nothing to report


def test_call_reports_an_unnamed_alert_instead_of_guessing_at_it() -> None:
    # BE-0402: no rule or candidate label names this button, and nothing here will guess where to
    # tap. The step keeps failing — but with the alert named, which is the whole point.
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    cleared, alerts = _call(_fake_with_alert(["Weird Button"]), guard)
    assert not cleared and alerts == []
    assert "unhandled system alert" in guard.blocked_note
    assert "Weird Button" in guard.blocked_note


def test_call_leaves_no_note_on_an_incapable_backend() -> None:
    # No native query ran, so nothing was observed. A note here would claim a block the guard never
    # saw, on every failed step of every non-iOS backend.
    guard = AlertGuardConfig()
    cleared, alerts = _call(cast("base.Driver", _Incapable()), guard)
    assert not cleared and alerts == []
    assert guard.blocked_note == ""


def test_call_clears_a_stale_note_once_the_alert_is_gone() -> None:
    # The note states the *latest* observation, never that a block was ever seen: a scenario runs its
    # steps against one config, so a sticky note would mislabel every later failure in it.
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    cleared, _alerts = _call(_fake_with_alert(["Weird Button"]), guard)
    assert not cleared
    assert guard.blocked_note
    cleared, _alerts = _call(FakeDriver([]), guard)
    assert not cleared
    assert guard.blocked_note == ""


# --- the mid-wait gate on the native path -----------------------------------------------------------


def _for_wait(target_id: str, timeout: float) -> Wait:

    return Wait.model_validate({"for": {"id": target_id}, "timeout": timeout})


def test_gate_dismisses_natively_mid_wait_and_records_the_alert() -> None:
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []  # the alert cleared
            d.screen = [target]  # and the awaited element is revealed

    driver = _fake_with_alert(["Allow"], react=react)
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    alerts: list[AlertEvent] = []
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert alerts == [AlertEvent(label="Allow")]


def test_gate_absent_native_alert_debounces_a_transient_collapse() -> None:
    # "absent" means the native query saw no *SpringBoard* alert. A single transient collapsed frame
    # under it must not fire the vision path — the debounce filters that false positive (BE-0315 /
    # BE-0269). A *persistent* non-SpringBoard collapse is the separate case the next test covers.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"

    class _OneFrameCollapse(FakeDriver):
        def __init__(self) -> None:
            super().__init__([])  # capable; no alert seeded, so the native probe returns "absent"
            self._polls = 0

        def query(self) -> list[base.Element]:
            self._polls += 1
            return [] if self._polls == 1 else [target]  # one collapsed frame, then app UI

    # poll_interval=1.0 rate-limits only the native query; the collapsed-tree debounce still samples
    # every _POLL, so one transient frame that clears before the debounce is filtered regardless.
    guard = AlertGuardConfig(poll_interval=1.0)
    ok, reason, _tree = _wait(
        _OneFrameCollapse(), _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert ok and reason == ""  # revealed after one transient collapse; vision never called


def test_gate_absent_native_alert_reports_a_persistent_collapse_it_cannot_name() -> None:
    # "absent" only rules out a *SpringBoard* alert: an action sheet or a WKWebView JS dialog the
    # native query cannot enumerate reads as absent too while still collapsing the tree. Nothing can
    # clear that since BE-0402, so the wait runs to its own timeout — but the collapsed-tree proxy's
    # observation rides along on the failure, hedged, because no query ever named a button here.
    from bajutsu.common.orchestrator.waits import _wait

    driver = FakeDriver(
        []
    )  # capable, collapsed, no SpringBoard alert seeded → probe returns "absent"
    # poll_interval=1.0 (the realistic default) but a tight 2s budget: the collapsed-tree proxy must
    # sample every _POLL, not once per interval, or its observation would first land at ~3x
    # poll_interval and miss this budget entirely (the BE-0269 latency PR #1330 locked in).
    guard = AlertGuardConfig(poll_interval=1.0)
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 2.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert "wait timeout: for" in reason
    assert "the screen appears blocked" in reason  # hedged: no button was ever enumerated
    assert "buttons:" not in reason


def test_gate_polls_the_native_query_on_its_own_interval_not_every_tick() -> None:
    # The native query is rate-limited to one per poll_interval, decoupled from the 50ms condition
    # poll, so it does not roughly double the single-threaded runner's load (BE-0315).
    from bajutsu.common.orchestrator.waits import _wait

    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def system_alert_labels(self) -> list[str]:
            probes["n"] += 1
            return []  # never an alert, so the wait runs to its full budget

    # App UI is visible (a non-collapsed tree), so the collapsed-tree vision fallback never enters —
    # this isolates the native probe's cadence from the "absent + collapsed" fallback path.
    app_ui = _button("home")
    guard = AlertGuardConfig(poll_interval=1.0)
    ok, _reason, _tree = _wait(
        _CountingProbe([app_ui]),
        _for_wait("never", 2.0),
        _LogicalClock(),
        alert_guard=guard,
        alerts=[],
    )
    assert not ok
    # ~40 condition polls over the 2s budget, but the native query fires about once per second:
    # a two-sided bound proves it re-fires on the interval (not just once) yet not every tick.
    assert 2 <= probes["n"] <= 4


def test_gate_dismisses_an_app_attached_sheet_from_the_tree_without_vision() -> None:
    # A system-owned prompt that is never SpringBoard-enumerable (e.g. iOS's Save Password sheet,
    # attached to the app's own accessibility tree) reads as "absent" on every native probe forever,
    # yet its own labeled, identifier-less buttons keep `shows_app_ui` from seeing a collapsed tree
    # either — both mid-wait detectors used to sit idle for the whole timeout on exactly this shape.
    # The in-tree label match (BE-0316's gap) must clear it within a poll or two, no vision call.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button(
        "今はしない"
    )  # identifier-less, like a real system-owned sheet's button

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "今はしない", "traits": ["button"]}:
            d.screen = [target]  # dismissing the sheet reveals the awaited element

    driver = FakeDriver([prompt_button], react=react)  # capable; no SpringBoard alert seeded
    guard = AlertGuardConfig(
        rules=[guard_rule("今はしない"), guard_rule("Not Now")], poll_interval=1.0
    )
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), clock, alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert clock.now() < 1.0  # cleared at tree-poll latency, not anywhere near the 30s timeout
    assert alerts == [AlertEvent(label="今はしない")]


def test_dismiss_from_tree_taps_a_showing_at_most_once() -> None:
    # A real dismiss isn't instant: a fading-out button can linger in the tree for a poll or two
    # after the tap before the screen actually updates. Without its own guard, `_dismiss_from_tree`
    # runs every `_POLL` with no cooldown, so it would re-match and re-tap the same button on every
    # one of those polls — over-counting one dismissal into several `AlertEvent`s and actuating the
    # app repeatedly (a second tap can land on whatever is underneath a fading sheet).
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    class _LingeringDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self._polls_since_tap: int | None = None

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            self._polls_since_tap = 0

        def query(self) -> list[base.Element]:
            if self._polls_since_tap is not None:
                self._polls_since_tap += 1
                if self._polls_since_tap >= 3:  # the animation finishes; the screen updates
                    return [target]
            return list(self.screen)

    driver = _LingeringDismiss()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 2.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    tap_sel = {"label": "今はしない", "traits": ["button"]}
    assert driver.actions.count(("tap", tap_sel)) == 1  # tapped exactly once
    assert alerts == [AlertEvent(label="今はしない")]  # exactly one dismissal recorded, not several


def test_dismiss_from_tree_retries_a_delivered_tap_that_did_not_clear_the_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The counterpart to the lingering-fade case above, and the one it cannot be told apart from at
    # the first poll: a tap the runner *accepts* can still leave the prompt up. Measured on iOS —
    # testmanagerd logged `touch down`/`touch up` at the target's exact centre and
    # `confirmed by TouchEventsCompleted`, yet the app never acted on the touch (PR #1686). Where the
    # fade clears the label within a poll or two, this never does, so the two differ only in what the
    # tree does *after* the tap.
    #
    # `_tree_dismiss_pending` arms on a tap that merely returned without raising, and re-arms only
    # once the tree stops matching that label — which an unacted-on tap never causes. So one such tap
    # disarms the in-tree path for the whole remaining wait: the sheet stays up, nothing retries, and
    # the step burns its full timeout with a dismissal recorded as if it had worked. Like
    # `ElementNotTappable`, this retries under a bound (`_TREE_DISMISS_MAX_TAPS`) rather than either
    # giving up after one attempt or hammering the device for the rest of the wait.
    from bajutsu.common.orchestrator.waits import _TREE_DISMISS_MAX_TAPS, _wait

    prompt_button = _button("今はしない")

    class _IneffectiveTap(FakeDriver):
        """Accepts the tap as a real runner does, but the prompt it targets never clears."""

        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            super().tap(sel)  # delivered and recorded; `screen` deliberately left unchanged

    driver = _IneffectiveTap()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    # A 30s budget, far longer than `_TREE_DISMISS_MAX_TAPS` taps spaced by `_TREE_RETAP_DELAY`, so
    # what stops the retries here is the tap ceiling rather than the wait running out: no retry at all
    # taps exactly once, an unbounded one ~30 times over this wait, and only the bound gives 3.
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.orchestrator.waits"):
        ok, _reason, _tree = _wait(
            driver, _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=alerts
        )
    assert not ok  # "ready" never appears either way, so the wait still times out on its own
    assert driver.tap_calls == _TREE_DISMISS_MAX_TAPS
    # The dismissal is recorded on the tap — the only moment it can be, since nothing there tells a
    # tap that lands from one the app never acts on — then withdrawn once the ceiling proves the
    # prompt never cleared. `AlertEvent` means a prompt the guard *dismissed*, so leaving it would
    # make the report contradict the warning below: timed out with the sheet still up, reported as
    # cleared. The retried actuations stay visible in the driver's own log (`tap_calls` above).
    assert alerts == []
    # Disclosed once, not silently: the wait is about to burn its whole budget, and without this the
    # timeout would read as "the awaited element never rendered" rather than "the prompt never left".
    gave_up = [r for r in caplog.records if "in-tree alert dismiss gave up" in r.message]
    assert len(gave_up) == 1
    # And the same disclosure rides the failure, not only the log (BE-0402). This is the one case the
    # collapsed-tree proxy cannot cover for: an app-attached sheet keeps the tree looking like
    # ordinary app UI, so without the note here the timeout would say nothing about the prompt at all.
    # Not the "unhandled" wording: this label *did* resolve and was tapped, and telling the author no
    # candidate was named would send them to add a label they already wrote.
    assert "a system prompt the guard could not clear is still up (button: 今はしない)" in _reason
    assert "unhandled system alert" not in _reason


def test_dismiss_from_tree_retry_that_lands_clears_the_prompt_and_passes_the_wait() -> None:
    # The payoff the retry exists for, which the never-clears test above cannot show: the second tap
    # *does* land, so the wait passes at retry latency instead of burning its timeout. Without this,
    # a change that left the retry inert — an off-by-one in the ceiling, a delay that never elapses
    # under the real clock — would keep every other in-tree test green while the sheet stays up on
    # device, since they all either tap once successfully or never clear at all.
    from bajutsu.common.orchestrator.waits import _TREE_RETAP_DELAY, _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    class _SecondTapLands(FakeDriver):
        """The first tap is accepted but ignored by the app; the second actually dismisses."""

        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            super().tap(sel)
            if self.tap_calls >= 2:
                self.screen = [target]  # this one lands

    driver = _SecondTapLands()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), clock, alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert driver.tap_calls == 2  # the retry is what cleared it
    # Cleared one retry in, nowhere near the 30s budget — the recovery came from the retry, not from
    # the wait outlasting the prompt.
    assert clock.now() < _TREE_RETAP_DELAY * 2
    assert alerts == [AlertEvent(label="今はしない")]  # one prompt, one dismissal


def test_dismiss_from_tree_does_not_retry_when_the_tap_moved_the_screen() -> None:
    # The retry must not fire when the tap plainly *did* land. `_dismiss_from_tree` matches
    # identifier-less buttons, and a whole app can legitimately have none (`shows_app_ui`'s `-noax`
    # shape), so a dismissed sheet can reveal an app-authored button carrying the very same label.
    # Re-tapping that would actuate the app under test — navigating it mid-wait and failing the step
    # for an unrelated reason — and would end in a warning claiming a prompt is up when none is.
    # The screen having changed since the tap is what separates the two cases.
    from bajutsu.common.orchestrator.waits import _wait

    prompt_button = _button("今はしない")
    app_button = _button("今はしない")  # identifier-less, app-authored, same label
    content = _button("Content")
    content["identifier"] = "home.content"

    class _RevealsSameLabelAppButton(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            super().tap(sel)
            self.screen = [app_button, content]  # sheet gone; the app's own button still matches

    driver = _RevealsSameLabelAppButton()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 5.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert not ok  # "ready" never appears; the wait times out on its own
    assert driver.tap_calls == 1  # the app's button was never tapped, despite matching every poll
    assert alerts == [AlertEvent(label="今はしない")]  # the real dismissal stands


def test_dismiss_from_tree_records_a_second_showing_after_an_untapped_other_label() -> None:
    # A second label can appear and go without ever being tapped — here it collides with an
    # identically labelled in-app button, so the uniqueness pre-check declines before any tap. That
    # showing must not leave the *first* label pending: if it did, the first label reappearing would
    # compare equal to the stale pending, `first_tap` would be False, and a genuine second dismissal
    # would be tapped but never recorded — under-reporting the run's prompts.
    from bajutsu.common.orchestrator.waits import _POLL, _wait

    target = _button("R")
    target["identifier"] = "ready"
    save = _button("今はしない")
    other = _button("あとで")  # system-owned, but collides with the app button below
    app_other = _button("あとで")
    app_other["identifier"] = "screen.home.button.later"

    class _TwoShowings(FakeDriver):
        """`今はしない` → an untappable `あとで` → `今はしない` again, then the awaited screen."""

        def __init__(self) -> None:
            super().__init__([save])
            self.taps: list[str] = []
            self.other_polls = 0

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            label = str(sel["label"])
            self.taps.append(label)
            # Each dismissal of the save sheet reveals the next screen in the sequence.
            self.screen = [other, app_other] if len(self.taps) == 1 else [target]

        def query(self) -> list[base.Element]:
            # The colliding pair clears on its own, putting the save sheet back up — a second,
            # genuine showing that was never tapped in between. It has to stay up for longer than
            # one `poll_interval` to be part of the story: the in-tree path is paced by that
            # interval, so a showing that came and went inside one would simply never be observed.
            if self.screen and self.screen[0] is other:
                self.other_polls += 1
                if self.other_polls > int(1.0 / _POLL) + 1:
                    self.screen = [save]
                return [other, app_other]
            return list(self.screen)

    driver = _TwoShowings()
    guard = AlertGuardConfig(
        rules=[guard_rule("今はしない"), guard_rule("あとで")], poll_interval=1.0
    )
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 5.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert driver.taps == ["今はしない", "今はしない"]  # the ambiguous `あとで` was never tapped
    # Both showings recorded — the second is a distinct prompt, not a retry of the first.
    assert alerts == [AlertEvent(label="今はしない"), AlertEvent(label="今はしない")]


def test_dismiss_from_tree_declines_on_not_yet_tappable_then_dismisses() -> None:
    # A sheet's own scrim can still cover its button while the presentation animation finishes, the
    # platform's hit-test (`isHittable` / `topmost_at_point`) reading the button as unreachable until
    # it settles. `ElementNotTappable` here is the same benign, self-resolved race `ElementNotFound`
    # and `AmbiguousSelector` already forgive — not a reason to fail the wait. One decline is what a
    # real presentation animation now costs: the in-tree path runs once per `poll_interval`, so the
    # retry arrives a full second later, well past a UIKit sheet's ~0.35-0.5s or an Android dialog's
    # ~0.25s+. `_TREE_DISMISS_DECLINE_GIVEUP` sits at twice that interval precisely so this retry
    # happens at all — a give-up horizon equal to the interval would spend itself on the first
    # attempt and leave the prompt up.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    class _NotYetTappableDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls < 2:  # the scrim is still animating away
                raise base.ElementNotTappable("covered by the sheet's own scrim")
            super().tap(sel)
            self.screen = [target]  # the animation finishes; the screen updates

    driver = _NotYetTappableDismiss()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 3.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert driver.tap_calls == 2  # declined once, then dismissed on the next interval
    assert alerts == [AlertEvent(label="今はしない")]


def test_dismiss_from_tree_stops_retrying_a_permanently_covered_button() -> None:
    # Unlike the transient scrim above, a genuinely stuck obstruction (a scrim that never lifts, an
    # `elevation` false positive) must not re-issue a real actuation attempt for the rest of the
    # wait: `_decline_giveup` bounds how long this label's tap keeps being retried before the wait
    # falls back to its own timeout, the same shape the vision guard's attempt ceiling already uses
    # for a persistent false positive. Bounding it in seconds rather than polls is what keeps the
    # bound meaningful at any `poll_interval`, and deriving it *from* that interval is what keeps it
    # meaningful at a long one: the bound is checked before the tap, so a fixed horizon shorter than
    # two intervals would spend itself on the first attempt and never retry at all.
    from bajutsu.common.orchestrator.waits import _decline_giveup, _wait

    prompt_button = _button("今はしない")

    class _PermanentlyCoveredDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            raise base.ElementNotTappable("covered by a scrim that never lifts")

    driver = _PermanentlyCoveredDismiss()
    guard = AlertGuardConfig(rules=[guard_rule("今はしない")], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 3.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # "ready" never appears; the wait times out on its own deadline
    # Attempted at t=0 and t=1.0 (one `poll_interval` apart), then declined outright at t=2.0 once
    # the give-up horizon is reached — bounded, not one attempt per poll for the whole 3s wait.
    assert driver.tap_calls == 2
    assert _decline_giveup(1.0) == 2.0  # the horizon the count above is derived from
    # And the give-up says what it gave up on (BE-0402), like the tap-budget one: an obstructed sheet
    # keeps its own labelled buttons in the tree, so nothing else on this path would report it — and
    # in the same "could not clear" wording, since the label resolved here too.
    assert "a system prompt the guard could not clear is still up (button: 今はしない)" in _reason
    assert "unhandled system alert" not in _reason


def test_dismiss_from_tree_declines_on_an_in_app_label_collision() -> None:
    # A system-owned identifier-less button and an app-authored one share a rule's label:
    # `match_alert_rule` resolves uniquely over the identifier-less subset, but the whole-tree tap
    # sees both and must decline rather than tap the wrong one (determinism first). A *persistent*
    # collision (unlike a vanish race) must decline before ever attempting the tap: the collision
    # never clears, so `_tree_dismiss_pending` (only armed on a successful tap) never guards it, and
    # a decline reached only via `except AmbiguousSelector` would re-issue the on-device tap every
    # `_POLL` for the rest of the wait — count `tap()` calls directly, not `driver.actions`, since a
    # raised `AmbiguousSelector` never reaches `_record` either way.
    from bajutsu.common.orchestrator.waits import _wait

    prompt_button = _button("Not Now")  # identifier-less, system-owned
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"

    class _CountingTapDriver(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button, app_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            super().tap(sel)

    driver = _CountingTapDriver()  # native-capable; no SpringBoard alert
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert driver.actions == []  # ambiguous → declined, nothing tapped
    assert driver.tap_calls == 0  # declined before attempting the tap, not caught after


def test_dismiss_from_tree_dismisses_despite_a_non_button_label_collision() -> None:
    # A bare `{"label": label}` selector resolves via `matches()` (base.py), which matches on
    # `label` alone and ignores `traits` — so a non-button element sharing the exact text (a static
    # caption drawn next to the sheet, a header) would make a trait-unscoped tap ambiguous despite
    # the intended button being uniquely named among buttons. Scoping both the pre-check and the tap
    # itself to `traits: [BUTTON]` (mirroring the `buttons` filter `label` was resolved against)
    # means a same-labeled caption never blocks dismissal at all.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("Not Now")  # identifier-less, system-owned
    caption = _button("Not Now")
    caption["traits"] = ["staticText"]  # not a button; must not block the tap below

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Not Now", "traits": ["button"]}:
            d.screen = [target]

    driver = FakeDriver(
        [prompt_button, caption], react=react
    )  # native-capable; no SpringBoard alert
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert alerts == [AlertEvent(label="Not Now")]


def test_dismiss_from_tree_never_matches_an_in_app_button_carrying_an_identifier() -> None:
    # An app screen with its own button that happens to share a policy label (e.g. a real in-app
    # "Not Now") must never be tapped by the guard — only a system-owned, identifier-less button can
    # match, same restriction `system_alert_labels()` already assumes for a genuine SpringBoard alert.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"  # an app-authored button, not a prompt

    driver = FakeDriver([app_button])  # capable; no SpringBoard alert seeded; never reveals "ready"
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # the in-app button was never tapped, so "ready" never appears
    assert driver.actions == []  # confirms no tap was issued against it


def test_dismiss_from_tree_never_fires_on_a_non_native_backend() -> None:
    # The in-tree match is native-only (the app-attached-sheet case it targets is an iOS one): a
    # web/Android-shaped backend must keep its pre-existing behavior untouched — only the
    # collapsed-tree + vision path (BE-0269) can act there, never the fast in-tree tap.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "今はしない"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = _NonNativeDriver([prompt_button], react=react)
    guard = AlertGuardConfig(
        rules=[guard_rule("今はしない"), guard_rule("Not Now")], poll_interval=1.0
    )
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # never tapped, so "ready" never appears; the wait runs to its full budget
    assert driver.actions == []


def test_dismiss_from_tree_never_fires_on_generic_dismissive_vocabulary_alone() -> None:
    # "Cancel" is generic English UI vocabulary a real app screen can legitimately show, and since
    # BE-0406 no built-in list of such words is a policy this guard resolves from at all. The fast
    # in-tree path must stay off unless the scenario declared a rule of its own; a ruleless guard
    # falls back to the collapsed-tree report, same as before this path existed.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    cancel_button = _button("Cancel")  # identifier-less; no rule names it

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Cancel"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = FakeDriver([cancel_button], react=react)  # native-capable; no rules configured
    guard = AlertGuardConfig(poll_interval=1.0)  # rules=[] (default)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert driver.actions == []


def test_dismiss_from_tree_never_fires_on_a_springboard_only_rule() -> None:
    # A rule's `AlertSurfaces` record says where its prompt can appear, and only an `in_tree` one
    # arms the tree match (BE-0406). A scenario declaring, say, the notifications prompt alone — pure
    # SpringBoard — must not thereby license a tap on any identifier-less button of that name in the
    # app's own tree: `tree_rules` is empty, so the path never arms.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("Not Now")  # identifier-less, and named by the rule below

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Not Now", "traits": ["button"]}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = FakeDriver([prompt_button], react=react)  # native-capable; no SpringBoard alert
    guard = AlertGuardConfig(rules=[guard_rule("Not Now", in_tree=False)], poll_interval=1.0)
    assert guard.tree_rules == []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # never tapped, so "ready" never appears
    assert driver.actions == []


def test_dismiss_from_tree_fires_on_the_same_rule_marked_in_tree() -> None:
    # The other half of the pair above, and what makes it an arming test rather than a driver quirk:
    # the identical prompt, tree and wait, with the rule's own `in_tree` flag as the only difference,
    # is cleared within a poll.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("Not Now")

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Not Now", "traits": ["button"]}:
            d.screen = [target]

    driver = FakeDriver([prompt_button], react=react)
    guard = AlertGuardConfig(rules=[guard_rule("Not Now", in_tree=True)], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 5.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert alerts == [AlertEvent(label="Not Now")]


def test_gate_unhandled_native_alert_names_it_in_the_wait_timeout() -> None:
    # An alert is up but no policy label resolves (unknown button). BE-0402 leaves it alone rather
    # than asking a model where to tap — and the wait, which would otherwise report only the element
    # that never appeared, names the alert and the buttons the probe actually read.
    from bajutsu.common.orchestrator.waits import _wait

    driver = _fake_with_alert(["Weird Button"])  # capable; alert stays up (never dismissed)
    guard = AlertGuardConfig(rules=[guard_rule("Allow")], poll_interval=1.0)
    ok, reason, _tree = _wait(
        driver, _for_wait("never", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert reason.startswith("wait timeout: for")
    assert "an unhandled system alert is blocking the screen (buttons: Weird Button)" in reason
    assert driver.actions == []  # and nothing was tapped on the way there


def test_gate_drops_the_note_once_the_prompt_goes_away() -> None:
    # A prompt that appears and resolves itself mid-wait must leave nothing behind: the note states
    # the latest observation, so a later, unrelated timeout never blames an alert long gone. The app
    # is on screen throughout, so the collapsed-tree proxy has nothing of its own to say either.
    from bajutsu.common.orchestrator.waits import _wait

    class _SelfResolving(FakeDriver):
        probes = 0

        def system_alert_labels(self) -> list[str]:
            self.probes += 1
            return ["Weird Button"] if self.probes < 3 else []

    app = _button("Home")
    app["identifier"] = "screen.home"
    driver = _SelfResolving([app])
    guard = AlertGuardConfig(rules=[guard_rule("Allow")], poll_interval=0.05)
    ok, reason, _tree = _wait(
        driver, _for_wait("never", 1.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert reason.startswith("wait timeout: for")
    assert "\u2014" not in reason  # no note appended: the prompt resolved itself mid-wait


# --- the in-tree dismiss is gated on a fresh "no SpringBoard alert" answer ---------------------


def test_dismiss_from_tree_is_withheld_while_a_springboard_alert_is_up() -> None:
    # The regression this locks in. The save-password prompt is an *app-process* sheet, so it shows
    # up in the poll's own tree and `_dismiss_from_tree` is the only path that clears it; the
    # notification request beside it is a SpringBoard alert the app's tree cannot see. `Driver.tap`
    # resolves an element, and XCUITest answers whatever out-of-process alert is interrupting before
    # it synthesizes such an interaction — with its own default handler, which taps the alert's
    # *default* button ("Allow"), the opposite of the guard's least-destructive policy and invisible
    # to the report. So while the native probe says an alert is up, the in-tree tap must not be
    # issued at all: the SpringBoard alert is answered natively first, by the scenario's policy.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    save_sheet = _button("Not Now")  # identifier-less, the app-attached sheet's own button

    class _AlertOverSheet(FakeDriver):
        """A SpringBoard alert stands over an app sheet until the native path taps its button."""

        def __init__(self) -> None:
            super().__init__([save_sheet])
            # Both prompts are up: the sheet in the tree, the permission request in SpringBoard.
            self.system_alert_buttons = [_button("Don't Allow"), _button("Allow")]
            self.tapped: list[str] = []

        def tap(self, sel: base.Selector) -> None:
            self.tapped.append(str(sel["label"]))
            super().tap(sel)
            self.screen = [target]

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            super().handle_system_alert(sel, timeout)
            self.system_alert_buttons = []  # answered by policy; the sheet is now uncovered

    driver = _AlertOverSheet()
    # One `systemAlertHandling.instruction` covers both prompts, as an author would write it: the
    # permission request's refusal first, then the sheet's own dismissal. Each path resolves the one
    # candidate its own surface carries.
    guard = AlertGuardConfig(
        rules=[guard_rule("Don't Allow"), guard_rule("Not Now")], poll_interval=1.0
    )
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 5.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    # The order is the whole point: the SpringBoard alert is refused natively, and only afterwards
    # is the app-attached sheet cleared from the tree. Never the reverse, and never both at once.
    assert alerts == [AlertEvent(label="Don't Allow"), AlertEvent(label="Not Now")]
    assert driver.tapped == ["Not Now"]  # the in-tree tap fired once, after the alert was gone


def test_dismiss_from_tree_is_paced_by_the_native_probe_not_by_the_poll() -> None:
    # The native probe is rate-limited to `poll_interval` (a per-`_POLL` SpringBoard query would
    # roughly double the runner's single-main-thread load, BE-0315), so on every other poll the gate
    # has no current answer about SpringBoard at all. Gating the in-tree tap on a probe that ran
    # *this* poll is what stops those polls tapping blind — and the observable consequence is the
    # spacing: a tap that never clears the prompt repeats once per `poll_interval`, not once per
    # `_TREE_RETAP_DELAY`.
    #
    # The interval has to exceed that delay for the assertion to mean anything. At the default 1.0
    # the two coincide and the retap delay alone would produce the same timings, so this drives a
    # 2.0 interval: ungated, the second tap lands 1.0s after the first.
    from itertools import pairwise

    from bajutsu.common.orchestrator.waits import _wait

    prompt_button = _button("Not Now")

    class _TapNeverLands(FakeDriver):
        """The prompt stays up however often it is tapped — an unactioned tap (measured on iOS)."""

        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_times: list[float] = []

        def tap(self, sel: base.Selector) -> None:
            self.tap_times.append(clock.now())
            super().tap(sel)  # the screen never changes; the prompt stays in the tree

    clock = _LogicalClock()
    driver = _TapNeverLands()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=2.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 10.0), clock, alert_guard=guard, alerts=[]
    )
    assert not ok  # "ready" never appears; the wait times out on its own deadline
    assert len(driver.tap_times) > 1, driver.tap_times
    gaps = [b - a for a, b in pairwise(driver.tap_times)]
    assert all(gap >= 2.0 for gap in gaps), gaps


def test_dismiss_from_tree_still_runs_when_no_springboard_alert_is_up() -> None:
    # The gate withholds the in-tree tap, it does not retire it: on a poll whose own native probe
    # reported no SpringBoard alert, the app-attached sheet is cleared exactly as before.
    from bajutsu.common.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    sheet_button = _button("Not Now")

    class _SheetOnly(FakeDriver):
        def __init__(self) -> None:
            super().__init__([sheet_button])
            self.tapped: list[str] = []

        def tap(self, sel: base.Selector) -> None:
            self.tapped.append(str(sel["label"]))
            super().tap(sel)
            self.screen = [target]

    driver = _SheetOnly()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 5.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert driver.tapped == ["Not Now"]
    assert alerts == [AlertEvent(label="Not Now")]


def test_dismiss_from_tree_waits_out_the_retap_delay_at_a_short_poll_interval() -> None:
    # `_TREE_RETAP_DELAY` still governs the gap between two taps on one showing, independently of the
    # `poll_interval` the gate now paces the in-tree path by. A scenario that tunes `pollInterval`
    # below that delay gets several in-tree passes inside one dismiss animation, and every pass but
    # the first must decline: re-tapping while a sheet is still fading out lands on whatever is
    # underneath it. So the taps are spaced by the delay, not by the interval, and stop at
    # `_TREE_DISMISS_MAX_TAPS` rather than continuing for the rest of the wait.
    from itertools import pairwise

    from bajutsu.common.orchestrator.waits import _TREE_DISMISS_MAX_TAPS, _TREE_RETAP_DELAY, _wait

    prompt_button = _button("Not Now")

    class _TapNeverLands(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_times: list[float] = []

        def tap(self, sel: base.Selector) -> None:
            self.tap_times.append(clock.now())
            super().tap(sel)  # the screen never changes; the prompt stays in the tree

    clock = _LogicalClock()
    driver = _TapNeverLands()
    # A fifth of the retap delay: five in-tree passes fit inside one, so a gap of `poll_interval`
    # between taps would be plainly visible in the timings below.
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=0.2)
    ok, _reason, _tree = _wait(driver, _for_wait("ready", 5.0), clock, alert_guard=guard, alerts=[])
    assert not ok  # "ready" never appears; the wait times out on its own deadline
    assert len(driver.tap_times) == _TREE_DISMISS_MAX_TAPS
    gaps = [b - a for a, b in pairwise(driver.tap_times)]
    assert all(gap >= _TREE_RETAP_DELAY for gap in gaps), gaps


# --- the interruption policy pushed to the backend ---------------------------------------------


def test_push_interruption_policy_hands_the_backend_the_guard_s_own_rules() -> None:
    # The decision stays here: what the backend receives is the scenario's own resolved rules, plus
    # `governs=True`, so the runner reports (rather than guesses at) any alert none of them identify.
    # Without this the backend answers an alert that interrupts one of its own interactions with the
    # alert's *default* button, which is the opposite of the least-destructive policy and reaches no
    # report.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Don't Allow"
    )
    push_interruption_policy(driver, AlertGuardConfig(rules=[rule]))
    assert driver.interruption_policy == ([({"Allow", "Don't Allow"}, "Don't Allow")], True)


def test_push_interruption_policy_still_governs_without_rules() -> None:
    # A scenario that declares no rules of its own still governs — a guard being on at all is what
    # decides whether a declined alert gets reported, independent of whether any rule survived the
    # in-tree-only drop below (BE-0406 Unit 2b): a real declaration filtered down to nothing this
    # surface can act on is not the same as no declaration at all.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, AlertGuardConfig())
    assert driver.interruption_policy == ([], True)


def test_push_interruption_policy_omits_a_rule_the_backend_can_never_meet() -> None:
    # This surface exists for an alert in *another* process interrupting an XCUITest interaction, so
    # a prompt raised into the application's own process never reaches it. Pushing such a rule anyway
    # would not merely be untidy: the Swift side matches by subset, so an in-tree-only shape would
    # re-open on the runner the collision `excluded_labels` closes here (BE-0406).
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    reachable = guard_rule("Don't Allow", identifying=("Allow", "Don't Allow"))
    in_tree_only = guard_rule("Not Now", identifying=("Save", "Not Now"), native=False)
    push_interruption_policy(driver, AlertGuardConfig(rules=[reachable, in_tree_only]))
    assert driver.interruption_policy is not None
    assert driver.interruption_policy[0] == [({"Allow", "Don't Allow"}, "Don't Allow")]


def test_push_interruption_policy_refuses_a_reachable_rule_carrying_an_exclusion_set() -> None:
    # No such shape exists today — every excluded one is in-tree-only, and dropped above — so this
    # fails loudly rather than letting a later addition reach the runner with its exclusion silently
    # discarded, which is exactly the subset-match collision the drop avoids.
    from bajutsu.common.orchestrator import push_interruption_policy

    excluding = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        excluded_labels=frozenset({"Never for This Card"}),
        native=True,
    )
    driver = FakeDriver([])
    with pytest.raises(ValueError, match="exclusion set"):
        push_interruption_policy(driver, AlertGuardConfig(rules=[excluding]))
    assert driver.interruption_policy is None  # refused outright, not half-pushed


def test_push_interruption_policy_clears_it_when_the_scenario_disables_the_guard() -> None:
    # `systemAlertHandling: false` must not inherit the previous scenario's policy from the resident
    # runner, so the push happens with an empty, non-governing policy rather than being skipped. Not
    # governing is what keeps this the one case that still leaves a declined alert unreported —
    # declaring nothing is the author's own choice, not an omission this mechanism exists to catch.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, None)
    assert driver.interruption_policy == ([], False)


def test_drain_interruptions_reports_what_the_backend_answered_as_alert_events() -> None:
    # A prompt answered inside the backend's interruption handling is still a prompt this run
    # dismissed; reporting it is what keeps that dismissal out of the silence the mechanism exists
    # to end.
    from bajutsu.common.orchestrator import drain_interruptions

    driver = FakeDriver([])
    driver.interruptions_to_drain = ["Don't Allow", "Not Now"]
    drained = drain_interruptions(driver)
    assert drained.alerts == [AlertEvent(label="Don't Allow"), AlertEvent(label="Not Now")]
    assert drained.undeclared == []
    again = drain_interruptions(driver)
    assert again.alerts == []  # drained, not repeated onto the next step


def test_drain_interruptions_reports_what_the_backend_declined_as_undeclared_interruptions() -> (
    None
):
    # A declined alert is not a dismissal — nothing answered it on the scenario's behalf — so it is
    # reported through the separate `undeclared` list, buttons intact, for the caller to fail by name
    # rather than let the interruption pass in silence (BE-0406 Unit 2b).
    from bajutsu.common.orchestrator import drain_interruptions
    from bajutsu.common.orchestrator.types import UndeclaredInterruption

    driver = FakeDriver([])
    driver.interruptions_declined_to_drain = [["Save", "Not Now"], ["Allow", "Don't Allow"]]
    drained = drain_interruptions(driver)
    assert drained.alerts == []
    assert drained.undeclared == [
        UndeclaredInterruption(buttons=["Save", "Not Now"]),
        UndeclaredInterruption(buttons=["Allow", "Don't Allow"]),
    ]
    again = drain_interruptions(driver)
    assert again.undeclared == []  # drained, not repeated


def test_a_gone_wait_is_guarded_so_an_in_app_prompt_can_be_cleared() -> None:
    # `gone` went unguarded on the reasoning that a blocking prompt collapses the tree, which already
    # satisfies "gone". That holds for a SpringBoard prompt and only for those: iOS's "Save Password"
    # alert is drawn in the app's own process, so it collapses nothing and *adds* its buttons to the
    # tree. A `gone` wait on one of them then sits unsatisfied for its whole timeout with nothing to
    # clear it — measured on-device before this branch was guarded.
    from bajutsu.common.orchestrator.waits import _wait

    prompt_button = _button("Not Now")

    class _AppOwnedPrompt(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Sign In"), prompt_button])
            self.tapped: list[str] = []

        def tap(self, sel: base.Selector) -> None:
            self.tapped.append(str(sel["label"]))
            super().tap(sel)
            self.screen = [_button("Sign In")]  # the alert closes

    driver = _AppOwnedPrompt()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver,
        Wait.model_validate({"until": {"gone": {"label": "Not Now"}}, "timeout": 10.0}),
        _LogicalClock(),
        alert_guard=guard,
        alerts=alerts,
    )
    assert ok  # the guard cleared it, so "gone" became true well inside the timeout
    assert driver.tapped == ["Not Now"]
    assert alerts == [AlertEvent(label="Not Now")]


def test_the_interruption_policy_is_skipped_on_a_backend_without_the_opt_in() -> None:
    # A narrow opt-in: only XCUITest interposes on an interaction this way, so a backend that does
    # not implement it is never asked and contributes no events — the run is otherwise unchanged.
    from bajutsu.common.orchestrator import drain_interruptions, push_interruption_policy

    class _NoOptIn:
        """A backend stub carrying neither half of the opt-in (a web / Android shape)."""

    driver = cast("base.Driver", _NoOptIn())
    push_interruption_policy(driver, AlertGuardConfig())
    drained = drain_interruptions(driver)
    assert drained.alerts == []
    assert drained.undeclared == []


# --- the end-of-step / expect guard's own in-tree dismissal --------------------------------------


def test_the_end_of_step_guard_clears_an_app_owned_prompt_from_the_tree() -> None:
    # Measured on iOS 26.3/26.4/26.5: the save-password alert can arrive *after* a scenario's last
    # wait has returned, so the only guard left to meet it is the end-of-step / expect one. Its
    # native probe reads `springboard.alerts` and sees nothing there, and the vision fallback no-ops
    # without a credential — so without this the prompt covered the screen and `expect` read a
    # covered tree.
    prompt_button = _button("Not Now")
    driver = FakeDriver([_button("Sign In"), prompt_button], react=_clearing_tree_tap("Not Now"))
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert ("tap", {"label": "Not Now", "traits": [base.Trait.BUTTON]}) in driver.actions


def test_the_end_of_step_guard_clears_a_native_alert_stacked_in_front_of_an_in_tree_one() -> None:
    # BE-0418's own motivating case: iOS commonly queues more than one prompt after a single user
    # action. A single one-shot dismiss used to end the moment the native SpringBoard alert cleared,
    # leaving the app-owned sheet underneath for the step's own retry to fail against a second time,
    # with no note explaining why. The guard now keeps going once the SpringBoard alert is gone.
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == "Not Now":
            d.screen = [el for el in d.screen if el["label"] != "Not Now"]

    driver = _fake_with_alert(["Don't Allow", "Allow"], react=react)
    driver.screen = [_button("Not Now")]
    guard = AlertGuardConfig(rules=[guard_rule("Don't Allow"), guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Don't Allow"), AlertEvent(label="Not Now")]
    # The SpringBoard alert is answered natively before the tree is ever touched: the first action
    # is the native handle, not a tap.
    assert driver.actions[0][0] == "handle_system_alert"
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_leaves_the_tree_alone_while_a_springboard_alert_is_up() -> None:
    # The same licence the mid-wait gate needs: XCUITest answers an interrupting out-of-process alert
    # before it synthesizes any element interaction, so an app tap issued while one is up is not this
    # guard's to make. The native probe reports "unhandled" (no rule identifies the alert) rather
    # than "absent" — the one answer that licenses the tree — so the same-labelled in-tree button
    # underneath is never touched. This call has dismissed nothing of its own, so "unhandled" ends
    # the call on this first round rather than spending the rest of the bound re-reading the same
    # surface (BE-0418).
    driver = _fake_with_alert(["Weird Button"])  # up, and no rule identifies it
    driver.screen = [_button("Not Now")]
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert not any(action[0] == "tap" for action in driver.actions)
    assert "Weird Button" in guard.blocked_note


def test_the_end_of_step_guard_ends_on_the_first_unhandled_round_when_it_dismissed_nothing() -> (
    None
):
    # Continuing past "unhandled" only makes sense when this call has dismissed something of its
    # own -- the recovery is that answered alert's own fade draining to reveal a live one uniquely.
    # With nothing yet dismissed there is no such fade, and continuing risks erasing this round's
    # own diagnosis if the alert clears on its own before the bound is spent (BE-0418 review
    # finding). Ending the call on this first round instead keeps it.
    class _ClearsAfterFirstRead(FakeDriver):
        def __init__(self) -> None:
            super().__init__([])
            self.probes = 0

        def system_alert_labels(self) -> list[str]:
            self.probes += 1
            return ["Weird Button"] if self.probes == 1 else []

    driver = _ClearsAfterFirstRead()
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert "Weird Button" in guard.blocked_note
    assert driver.probes == 1  # never re-probed to see the alert clear itself


def test_the_end_of_step_guard_ends_on_the_first_unhandled_round_ruled_out_by_an_exclusion() -> (
    None
):
    # `rule.identifying_labels <= set(buttons)` is also true, with no fade and no collision, when
    # a rule matched on shape but was ruled out by an *excluded* label being present --
    # `matching_alert_rule` rejects that case just as it rejects a genuine count collision, but the
    # early-break condition above only ever checked the shape, not the exclusion, so this case was
    # read as "recoverable" and given rounds it could never use (review finding). No native rule in
    # today's catalogue declares `excluded_labels`, but a tree one already does (`savePassword`'s
    # 26.5 shape), so this is a matter of when a native one does, not if.
    class _CountingDriver(FakeDriver):
        def __init__(self, buttons: list[str]) -> None:
            super().__init__([])
            self.system_alert_buttons = [_button(label) for label in buttons]
            self.probes = 0

        def system_alert_labels(self) -> list[str]:
            self.probes += 1
            return [b["label"] for b in self.system_alert_buttons if b["label"]]

    driver = _CountingDriver(["A1", "A2", "X"])
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"A1", "A2"}), tap_label="A1", excluded_labels=frozenset({"X"})
    )
    guard = AlertGuardConfig(rules=[rule])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert "A1" in guard.blocked_note or "X" in guard.blocked_note
    assert driver.probes == 1  # never re-probed a screen no later round could read differently


def test_the_end_of_step_guard_still_recovers_a_collision_on_its_very_first_round() -> None:
    # The early-break above is gated on more than `dismissed_native` alone: a rule's own shape
    # being present on the surface (just not uniquely) is itself evidence a later round can read
    # differently, even when nothing has been dismissed yet -- BE-0418's flagship stacked pair can
    # collide on round 0 itself, not only after an earlier dismissal, and must still recover.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    # Both alerts are already up on round 0 -- nothing dismissed yet, but notifications' own shape
    # is present on the surface (just not uniquely), so the round must not end here.
    driver = _fake_with_alert(["Allow", "Don't Allow", "Allow", "Ask App Not to Track"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # notifications' own buttons drop out, leaving tracking uniquely matchable.
            driver.system_alert_buttons = [_button("Allow"), _button("Ask App Not to Track")]
        elif settle_count == 2:
            # tracking's own tap actually lands and the sheet clears.
            driver.system_alert_buttons = []

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]  # tracking, tapped on round 1
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_leaves_a_second_native_alert_unhandled_after_clearing_the_first() -> (
    None
):
    # The stacked case's other half (BE-0418): the loop still cleared something (`cleared` is True),
    # but the second alert is one no rule identifies, so it is left alone and named in `blocked_note`
    # rather than silently dropped — the same fact the failure reason needs even though the call as a
    # whole did clear an alert.
    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button("Weird Button")]

    driver = _fake_with_alert(["Allow"], react=react)
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert "unhandled system alert" in guard.blocked_note
    assert "Weird Button" in guard.blocked_note


def test_the_end_of_step_guard_recovers_the_stacked_case_when_labels_collide_mid_fade() -> None:
    # BE-0418's own flagship stacked pair, with an added wrinkle: `notifications` and `tracking`
    # both tap "Allow", so a round reading the first's still-fading buttons alongside the second's
    # now-live ones fails `matching_alert_rule`'s per-label uniqueness check for either and lands
    # on `"unhandled"` rather than `"already_dismissed"`. That round must settle and try again, the
    # same as an ordinary already_dismissed fade, rather than ending the call and leaving a
    # rule-named alert untapped.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    driver = _fake_with_alert(["Allow", "Don't Allow"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # notifications' own dismiss animation still enumerates its buttons, and tracking has
            # now also queued -- the colliding-label read the "unhandled" fix must see past.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("Allow"),
                _button("Ask App Not to Track"),
            ]
        elif settle_count == 2:
            # notifications' fade has now drained; only tracking remains, uniquely matchable.
            driver.system_alert_buttons = [_button("Allow"), _button("Ask App Not to Track")]

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label="Allow"), AlertEvent(label="Allow")]
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_filters_the_unhandled_note_when_the_collision_never_resolves() -> (
    None
):
    # Direct coverage of the `"unhandled"` branch's own leftover filter surviving to the call's
    # final note: unlike the recovery case above, this collision never drains, so the filtered note
    # from the last round is what `blocked_note` ends up holding. The filter subtracts with
    # multiplicity, not as a set: `notifications`' answered shape accounts for exactly one "Allow"
    # and one "Don't Allow", so the *second* "Allow" -- `tracking`'s own, still genuinely unhandled
    # -- is left behind to name, while "Don't Allow" (fully accounted for, one occurrence only) is
    # not (BE-0418 review finding).
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    driver = _fake_with_alert(["Allow", "Don't Allow"])

    def settle() -> None:
        # The collision persists for the rest of the call: notifications' fade never drains.
        driver.system_alert_buttons = [
            _button("Allow"),
            _button("Don't Allow"),
            _button("Allow"),
            _button("Ask App Not to Track"),
        ]

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]  # only notifications ever tapped
    assert "Ask App Not to Track" in guard.blocked_note
    assert (
        guard.blocked_note.count("Allow") == 1
    )  # tracking's own copy, not notifications' answered one
    assert "Don't Allow" not in guard.blocked_note


def test_the_end_of_step_guard_keeps_a_pending_tree_note_through_a_later_unhandled_round() -> None:
    # The `"unhandled"` branch's own note computation must defer to a pending tree diagnosis the
    # same way `already_dismissed` and the tree-lingering branch already do: a native alert that
    # shows up on a later round, and that no rule identifies, must not overwrite (or clear) a
    # `NotTappable` note an earlier round is still standing by.
    class _StuckTreeThenUnhandledNative(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.probes = 0

        def tap(self, sel: base.Selector) -> None:
            raise base.ElementNotTappable("the scrim never lifts")

        def system_alert_labels(self) -> list[str]:
            self.probes += 1
            return [] if self.probes == 1 else ["Weird Button"]

    driver = _StuckTreeThenUnhandledNative()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now", native=False, in_tree=True)])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert "Not Now" in guard.blocked_note  # the tree diagnosis, not the later native one
    assert "Weird Button" not in guard.blocked_note


def test_the_end_of_step_guard_keeps_a_stuck_tree_note_after_a_different_tree_tap_lands() -> None:
    # The bool `tree_note_pending` used to be, protects a pending `NotTappable` diagnosis from a
    # later *native* dismissal, but a later round that dismisses a different, unrelated in-tree
    # prompt is not evidence the stuck one became tappable — that requires the same label to land
    # (BE-0418 review finding).
    stuck = ResolvedAlertRule(
        identifying_labels=frozenset({"StuckBtn"}), tap_label="StuckBtn", native=False, in_tree=True
    )
    other = ResolvedAlertRule(
        identifying_labels=frozenset({"OtherBtn"}), tap_label="OtherBtn", native=False, in_tree=True
    )

    class _OneButtonNeverLands(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            if isinstance(sel, dict) and sel.get("label") == "StuckBtn":
                raise base.ElementNotTappable("StuckBtn's scrim never lifts")
            super().tap(sel)

    driver = _OneButtonNeverLands([_button("StuckBtn")])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # StuckBtn is still unlanded and lingering; a second, unrelated prompt has now raised.
            driver.screen = [_button("StuckBtn"), _button("OtherBtn")]

    guard = AlertGuardConfig(rules=[other, stuck])  # `other` first, so it matches first once up
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="OtherBtn")]
    # StuckBtn's own diagnosis must survive OtherBtn's unrelated success.
    assert "StuckBtn" in guard.blocked_note


def test_a_note_from_a_cleared_stacked_call_does_not_survive_a_retry_that_passes() -> None:
    # The step-runner's `not ok` conjunct, pinned (BE-0418 review finding): a cleared stacked call's
    # note explains a failure the retry still has, but when the dismiss reveals the step's own
    # target and the retry lands, the step passed — the still-unhandled second alert's note must not
    # tag along onto a step with nothing to explain. Every other stacked-call test either has an
    # empty note or a retry that fails, so without `not ok` this is the one case that would silently
    # start tagging a passing step with a failure diagnosis.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    go = _button("Go")
    go["identifier"] = "go"

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button("Weird Button")]  # a second, unhandled alert
            d.screen = [go]  # ...and the dismiss reveals the step's own target

    driver = _fake_with_alert(["Allow"], react=react)
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    result = run_scenario(
        driver,
        load_scenarios("- name: t\n  steps:\n    - tap: { id: go }\n")[0],
        alert_guard=guard,
    )
    assert result.ok
    # Asserted so the test cannot pass vacuously: `not ok` only suppresses anything while the
    # note itself is non-empty.
    assert "Weird Button" in guard.blocked_note
    assert result.steps[0].reason == ""
    assert result.steps[0].alerts == [AlertEvent(label="Allow")]  # the first alert did clear


def test_a_note_from_a_cleared_stacked_call_still_reaches_the_step_s_own_failure() -> None:
    # The same stacked case, driven through `run_scenario` (BE-0418): `cleared` and a non-empty
    # `blocked_note` are no longer mutually exclusive the way a single-shot dismiss made them, so the
    # step-runner's own note-append must survive the retry it now runs alongside. A version that
    # appends the note before the retry loses it the moment the retry reassigns the failure reason.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button("Weird Button")]

    driver = _fake_with_alert(["Allow"], react=react)  # no "go" element at any point
    result = run_scenario(
        driver,
        load_scenarios("- name: t\n  steps:\n    - tap: { id: go }\n")[0],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    reason = result.steps[0].reason or ""
    assert "an unhandled system alert is blocking the screen (buttons: Weird Button)" in reason
    assert result.steps[0].alerts == [AlertEvent(label="Allow")]  # the first alert did clear


def test_the_end_of_step_guard_retries_a_tap_that_lands_on_a_later_round() -> None:
    # The landing-race gap (BE-0418): a scrim still covers the sheet's button on the first attempt,
    # exactly the "not yet reachable" condition the mid-wait path already retries. The one-shot path
    # now gets the same short, round-bounded retry instead of giving up on the first `ElementNotTappable`.
    prompt_button = _button("Not Now")
    attempts = 0

    class _SlowToLand(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise base.ElementNotTappable("a scrim still covers the button")
            super().tap(sel)
            self.screen = [el for el in self.screen if el["label"] != "Not Now"]

    driver = _SlowToLand([prompt_button])
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert attempts == 2
    assert guard.blocked_note == ""  # the eventual dismiss clears the interim "not tappable" note


def test_the_end_of_step_guard_names_a_permanently_obstructed_prompt() -> None:
    # Out of scope (per the proposal): actually clearing a permanently obstructed prompt is not this
    # change's job. What changes is the diagnosis — the failure names the alert instead of reading as
    # a bare missing element, once the loop's round bound is spent retrying a tap that never lands.
    class _NeverLands(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            raise base.ElementNotTappable("the scrim never lifts")

    driver = _NeverLands()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1

    cleared, alerts = _call(driver, guard, settle=settle)
    assert not cleared and alerts == []
    assert "a system prompt the guard could not clear is still up" in guard.blocked_note
    assert "Not Now" in guard.blocked_note
    # Pins the round bound itself: a widened `_GUARD_CALL_MAX_ROUNDS` would retap (and re-settle)
    # more times here without any other assertion in the suite noticing.
    assert driver.tap_calls == 3
    # And pins the settle on a round that found a button not yet tappable, not only on one that
    # dismissed (BE-0418 Unit 1): without it the next round's retap — and, on the bound-exhausting
    # round, the caller's own read — lands on a scrim still mid-presentation.
    assert settle_calls == 3


def test_the_end_of_step_guard_preserves_an_uncleared_tree_note_past_an_unrelated_native_dismissal() -> (
    None
):
    # The other half of BE-0418's note bookkeeping: a round that clears an unrelated SpringBoard
    # alert must not erase an earlier round's still-open tree diagnosis. Round 1 and 2 both find the
    # same tree button stuck; round 2's own tap incidentally reveals a distinct native alert, which
    # round 3 — the call's last — dismisses. Without gating the reset on whether the tree issue is
    # still open, that unrelated success would silently discard the genuine, unresolved obstruction.
    class _StuckTreePromptRevealsANativeOne(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls == 2:
                self.system_alert_buttons = [_button("Allow")]
            raise base.ElementNotTappable("the scrim never lifts")

    driver = _StuckTreePromptRevealsANativeOne()
    guard = AlertGuardConfig(
        rules=[
            guard_rule("Not Now", native=False, in_tree=True),
            guard_rule("Allow", native=True, in_tree=False),
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert driver.tap_calls == 2
    assert "a system prompt the guard could not clear is still up" in guard.blocked_note
    assert "Not Now" in guard.blocked_note


def test_the_end_of_step_guard_does_not_double_report_an_alert_reappearing_after_a_different_one() -> (
    None
):
    # The native dedup must remember every alert already dismissed this call, not only the round
    # immediately before: round 1 clears alert A, round 2 clears a distinct alert B, and round 3
    # reads A's exact button set again — the first dismissal still fading, not a genuine third alert.
    # Comparing only against the *previous* round's key would miss this A-B-A ordering entirely.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    handled = 0

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        nonlocal handled
        if kind != "handle_system_alert":
            return
        handled += 1
        if handled == 1:
            d.system_alert_buttons = [_button("Allow"), _button("Ask App Not to Track")]
        else:
            d.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]

    driver = _fake_with_alert(["Allow", "Don't Allow"], react=react)
    guard = AlertGuardConfig(rules=[notifications, tracking])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [
        AlertEvent(label="Allow"),
        AlertEvent(label="Allow"),
    ]  # A, then B — not A again
    # Round 2's already_dismissed decline is A's fade reappearing, not three consecutive reads of
    # a tap that never landed — B was tapped in between, so the round-exhaustion diagnosis must not
    # fire here even though the round bound is spent on an already_dismissed round (BE-0418).
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_preserves_an_uncleared_note_through_a_later_empty_round() -> None:
    # A round that matches nothing does not prove the screen actually cleared (BE-0418's own design
    # names this risk: "a round that reads a tree still mid-animation risks matching nothing at
    # all"). Erasing round 1's real diagnosis on that ambiguous evidence would leave the eventual
    # failure reading as a bare missing element again — exactly what this proposal exists to fix.
    # An open `NotTappable` diagnosis must also keep the call retrying through that ambiguous round
    # rather than ending on it (BE-0418 review finding): round 1's empty read is itself only
    # ambiguous, not proof the stuck sheet resolved, so the call still spends round 2 on it — this
    # driver never lets the tap land, so all three rounds are used before the call gives up.
    class _ObstructedThenAmbiguous(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.attempts = 0

        def tap(self, sel: base.Selector) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise base.ElementNotTappable("the scrim never lifts")
            raise base.ElementNotFound("a mid-transition read caught it between frames")

    driver = _ObstructedThenAmbiguous()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert driver.attempts == 3  # every round retried; the stuck diagnosis never let the call give
    # up on round 1's merely-ambiguous read
    assert "a system prompt the guard could not clear is still up" in guard.blocked_note
    assert "Not Now" in guard.blocked_note


def test_the_end_of_step_guard_lands_a_stuck_tap_after_an_ambiguous_middle_round() -> None:
    # The positive twin of the test above: an open `NotTappable` diagnosis must not just survive an
    # ambiguous middle round, it must let the call spend a genuinely useful round after it. Round 0's
    # scrim has not lifted; round 1's read catches the sheet mid-frame and matches nothing at all
    # (no rule, not even an already-excluded one); round 2's scrim has finally lifted. Before the fix,
    # round 1's empty read ended the call outright, so the tap round 2 would have landed never ran.
    class _ScrimLiftsOnTheLastRound(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.query_calls = 0
            self.tap_calls = 0

        def query(self) -> list[base.Element]:
            self.query_calls += 1
            if self.query_calls == 2:  # round 1's own read: the sheet mid-frame, nothing enumerable
                return []
            return super().query()

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls == 1:
                raise base.ElementNotTappable("the scrim has not lifted yet")
            super().tap(sel)

    driver = _ScrimLiftsOnTheLastRound()
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])

    def settle() -> None:
        if driver.tap_calls == 2:  # the landing tap genuinely closes the sheet, once it lands
            driver.screen = []

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert guard.blocked_note == ""  # the stuck diagnosis clears once its own shape finally lands
    assert driver.tap_calls == 2  # round 0's failed attempt, round 2's landing one — round 1 tapped
    # nothing because its own read matched no rule at all


def test_the_end_of_step_guard_keeps_trying_a_stuck_tree_prompt_through_an_unrelated_native_round() -> (
    None
):
    # The `"unhandled"` branch's own settle-skip condition used to break the call without ever
    # consulting `stuck_tree_label`, unlike the tree side's own equivalent check (line 778) -- so an
    # unrelated, transient SpringBoard alert no rule identifies could cut Unit 2's landing-race
    # retry short even with a tree-only policy (`self.native_rules` empty, so the condition's own
    # `any(...)` is vacuously `False`). Round 0's scrim has not lifted; round 1 is an unrelated
    # native alert with nothing else to gain from continuing; round 2's scrim finally lifts
    # (BE-0418 review finding).
    class _StuckThenLandsPastAnUnrelatedNativeRound(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Sheet")])
            self.tree_tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tree_tap_calls += 1
            if self.tree_tap_calls == 1:
                raise base.ElementNotTappable("the scrim has not lifted yet")
            super().tap(sel)

    driver = _StuckThenLandsPastAnUnrelatedNativeRound()
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Sheet"}), tap_label="Sheet", native=False, in_tree=True
    )
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            # An unrelated, transient SpringBoard alert no rule identifies appears for round 1.
            driver.system_alert_buttons = [_button("Weird Button")]
        elif settle_calls == 2:
            # It resolves on its own before round 2's own native probe.
            driver.system_alert_buttons = []
        elif settle_calls == 3:
            # Round 2's own landing tap genuinely closes the sheet, the way a real device's
            # settle_after_alert_dismiss would reflect by the time this call returns.
            driver.screen = []

    guard = AlertGuardConfig(rules=[tree_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Sheet")]
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_never_retaps_a_native_alert_it_already_dismissed() -> None:
    # `settle` is best-effort and bounded: a dismissal whose own animation runs past it can still be
    # up, unchanged, on a later round's read. `probe_native` declines to tap a match already in
    # `dismissed` (BE-0418), so this is not merely deduplicated after the fact — a real device never
    # sees a second tap that could land on nothing (the alert genuinely gone) or on whatever a
    # closing alert has by then revealed underneath it, the same hazard the tree path's `exclude`
    # closes on its own surface.
    driver = _fake_with_alert(["Allow"])  # never cleared: models a fade that outlasts `settle`
    guard = AlertGuardConfig(rules=[guard_rule("Allow")])
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]  # one dismissal, not three
    # The bound is spent with the same alert still up on every round: three consecutive reads is
    # evidence the tap never actually landed, not that its dismiss animation is merely still
    # playing out, so the last round names it rather than clearing the note (BE-0418).
    assert guard.blocked_note == uncleared_prompt_note("Allow")
    assert sum(1 for action in driver.actions if action[0] == "handle_system_alert") == 1
    # Every round still settles, the declined ones included: each one enumerated a live alert
    # mid-fade, and a caller reading the screen the instant this call returns must not read one
    # still animating — the guarantee `__call__`'s own docstring makes for every round.
    assert settle_calls == 3


def test_the_end_of_step_guard_retaps_a_native_alert_that_genuinely_re_raises_after_absent() -> (
    None
):
    # `dismissed_native` exists only to keep a still-fading alert from a second real tap — but an
    # `"absent"` round is a deterministic proof the surface holds nothing at all, fading or
    # otherwise. Carrying the record past that round declines a *later*, genuine re-raise of the
    # same shape as though it were the earlier occurrence's own stale fade, tapping nothing
    # (BE-0418 review finding). Round 0 dismisses "Allow"; round 1 reads "absent" and clears an
    # in-tree sheet instead; round 2's app re-raises the identical "Allow" prompt, which must be
    # tapped again rather than declined.
    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []  # the tapped alert genuinely clears every time

    driver = _fake_with_alert(["Allow"], react=react)
    driver.screen = [_button("Not Now")]
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 2:  # right after round 1's in-tree dismiss settles
            driver.system_alert_buttons = [_button("Allow")]  # the app re-raises the same prompt
            # Round 1's own tap genuinely closed "Not Now", the way a real device's
            # settle_after_alert_dismiss would reflect by the time this call returns.
            driver.screen = []

    guard = AlertGuardConfig(
        rules=[
            guard_rule("Allow", native=True, in_tree=False),
            guard_rule("Not Now", native=False, in_tree=True),
        ]
    )
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [
        AlertEvent(label="Allow"),
        AlertEvent(label="Not Now"),
        AlertEvent(label="Allow"),
    ]  # the re-raised "Allow" tapped a second time, not declined as the first occurrence's fade
    assert sum(1 for action in driver.actions if action[0] == "handle_system_alert") == 2
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_does_not_blame_a_retracted_native_shape_after_a_later_race() -> None:
    # The genuinely-empty-read retraction cleared `dismissed_native` but left `native_dismiss_shape`
    # / `native_dismiss_label` standing, so the bound-exhaustion diagnosis could still fire on a
    # shape this very call already watched go away -- an empty enumeration is equally proof the tap
    # that recorded the shape landed (BE-0418 review finding). Round 0 taps "notifications" cleanly;
    # round 1 reads "absent" and clears an in-tree sheet instead, retracting the record; round 2's
    # app genuinely re-raises the identical prompt, but *this* occurrence races away -- the call
    # must not blame round 0's own, already-cleared shape for it. The race branch's own fallback
    # resolves the rule fresh against this round's `buttons`/`dismissed_native` (BE-0418 review
    # finding) rather than trusting a stale `native_dismiss_shape`, so it correctly names round 2's
    # own re-raised occurrence -- not round 0's, and not silence either.
    class _SucceedsOnceThenRaces(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.tap_calls = 0

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            self.tap_calls += 1
            if self.tap_calls == 1:
                super().handle_system_alert(sel, timeout)
            else:
                raise base.ElementNotFound("the re-raised prompt raced away")

    driver = _SucceedsOnceThenRaces()
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            driver.system_alert_buttons = []  # genuinely gone before round 1's own probe
        elif settle_calls == 2:
            driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]  # re-raised
            # Round 1's own tap genuinely closed "Not Now" -- isolates this test to the native-side
            # retraction under review, not the tree side's own separate post-loop check.
            driver.screen = []

    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            ),
            guard_rule("Not Now", native=False, in_tree=True),
        ]
    )
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label="Allow"), AlertEvent(label="Not Now")]
    assert guard.blocked_note == uncleared_prompt_note("Allow")


def test_the_end_of_step_guard_does_not_retap_a_fading_alert_after_a_toctou_race_clears_a_second() -> (
    None
):
    # The other half of the fix above: `dismissed_native`'s retraction must fire only on a
    # genuinely empty read, not on every `"absent"` answer. `probe_native` also reports `"absent"`
    # after a *non-empty* read, when the alert it tried to tap that round raced away between the
    # query and the tap — that only proves the one alert this round tried is gone, not that the
    # rest of the surface (an earlier round's own still-fading dismissal included) is (BE-0418
    # review finding). Round 0 dismisses `notifications`; its fade outlasts `settle`, and a second,
    # disjoint alert joins it; round 1 tries the second alert, which races away before the tap
    # lands — a live native read, so the tree is left alone that round too (BE-0418 review
    # finding); round 2's own read is genuinely empty, and must still decline `notifications`'
    # lingering fade rather than tapping the device a second real time, while finally reaching the
    # tree.
    handled = 0

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        nonlocal handled
        if kind != "handle_system_alert":
            return
        handled += 1
        if handled == 2:
            raise base.ElementNotFound("the second alert vanished between query and tap")

    driver = _fake_with_alert(["Allow", "Don't Allow"], react=react)
    driver.screen = [_button("Not Now")]
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    second = ResolvedAlertRule(identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK")
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Not Now"}), tap_label="Not Now", native=False, in_tree=True
    )
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            # notifications' own fade outlasts this settle, and a second, disjoint alert joins it.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("OK"),
                _button("Cancel"),
            ]
        elif settle_calls == 2:
            # Both alerts genuinely resolve on their own, so round 2's own native read is empty —
            # the only read that licenses the tree tap now (BE-0418 review finding).
            driver.system_alert_buttons = []

    guard = AlertGuardConfig(rules=[notifications, second, tree_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    # No duplicate "Allow": the TOCTOU-race "absent" must not have retracted notifications' own
    # dismissed-shape record.
    assert alerts == [AlertEvent(label="Allow"), AlertEvent(label="Not Now")]
    assert sum(1 for action in driver.actions if action[0] == "handle_system_alert") == 2


def test_the_end_of_step_guard_names_an_unhandled_button_after_a_toctou_race() -> None:
    # The tree-absent branch's own terminal note-clear used to treat every `"absent"` round as
    # proof the SpringBoard surface was empty, but a time-of-check/time-of-use race also answers
    # `"absent"` after a *non-empty* read (BE-0418 review finding): only the alert this round tried
    # to tap raced away, not the rest of the surface. Clearing the note there drops a co-present,
    # unhandled button the bare `element not found` BE-0402 exists to prevent naming.
    class _RacesAwayOnTap(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAwayOnTap([])
    driver.system_alert_buttons = [_button("OK"), _button("Cancel"), _button("Weird Button")]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"OK", "Cancel"}),
                tap_label="OK",
                native=True,
                in_tree=False,
            )
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert "Weird Button" in guard.blocked_note
    # The raced-away shape is not in dismissed_native (nothing was dismissed), but a rule did
    # identify it -- naming it as unhandled too would be exactly the misdiagnosis this branch
    # exists to avoid (BE-0418 review finding).
    assert "OK" not in guard.blocked_note and "Cancel" not in guard.blocked_note


def test_the_end_of_step_guard_names_a_fourth_queued_prompt_instead_of_going_silent() -> None:
    # The "dismissed" branch's own leftover check credited only `dismissed_native` -- the shapes
    # this call has *tapped* -- not every rule `identified_alert_rules` finds on the same read, so a
    # fourth declared prompt queued behind three the call already dismissed, uniquely identified on
    # the final round's own read but not yet tapped, survived into the leftover and was named an
    # alert no rule identifies (BE-0418 review finding). Unlike a mid-call round, the final round has
    # no successor to self-correct it. Four disjoint native rules; rounds 0-2 dismiss the first
    # three cleanly, and the fourth is still on screen, fully identified, when the bound is spent.
    # Crediting it keeps it out of the generic `alert_block_note` -- it must never be named as an
    # alert nothing identifies -- but the call still never taps it, so the exhaustion fallback
    # (`_raced_exhaustion_note`, BE-0418 review finding) now names it via `uncleared_prompt_note`
    # rather than falling silent, the same bare-`""` misdiagnosis already fixed on the race and
    # `"unhandled"` branches.
    class _DismissesThreeOfFour(FakeDriver):
        _SHAPES: ClassVar[dict[str, set[str]]] = {
            "A1": {"A1", "A2"},
            "B1": {"B1", "B2"},
            "C1": {"C1", "C2"},
        }

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            super().handle_system_alert(sel, timeout)
            label = sel["label"] if isinstance(sel, dict) else None
            shape = self._SHAPES.get(label, set()) if label is not None else set()
            self.system_alert_buttons = [
                b for b in self.system_alert_buttons if b["label"] not in shape
            ]

    driver = _DismissesThreeOfFour([])
    driver.system_alert_buttons = [
        _button(label) for label in ("A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2")
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(identifying_labels=frozenset({"A1", "A2"}), tap_label="A1"),
            ResolvedAlertRule(identifying_labels=frozenset({"B1", "B2"}), tap_label="B1"),
            ResolvedAlertRule(identifying_labels=frozenset({"C1", "C2"}), tap_label="C1"),
            ResolvedAlertRule(identifying_labels=frozenset({"D1", "D2"}), tap_label="D1"),
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [
        AlertEvent(label="A1"),
        AlertEvent(label="B1"),
        AlertEvent(label="C1"),
    ]
    assert guard.blocked_note == uncleared_prompt_note("D1")


def test_the_end_of_step_guard_does_not_call_a_co_present_declared_prompt_unhandled_on_a_race() -> (
    None
):
    # The TOCTOU race branch's own leftover credit used to resolve only the *one* rule that raced
    # (`_resolve_alert_rule`), so a second, disjoint declared prompt co-present on the very same
    # read survived into the leftover and was named as an alert no rule identifies -- exactly the
    # misdiagnosis `uncleared_prompt_note`'s docstring says must not happen, since a rule does
    # identify it and the very next native probe would dismiss it (BE-0418 review finding). Mirrors
    # `test_wait_guard_does_not_call_a_co_present_declared_prompt_unhandled_on_a_race`
    # (`tests/orchestrator/test_waits.py`), the mid-wait gate's own fix for the identical shape.
    # "notifications" races away on every round; "paste" is a second, disjoint prompt fully present
    # on every read but never itself attempted. The leftover fix keeps "paste" out of the generic
    # `alert_block_note` -- it must never be named as an alert nothing identifies -- but the call
    # still never confirms "notifications" cleared in three rounds, so the bound-exhaustion fallback
    # (BE-0418 review finding, separate from the leftover credit above) now names *that* rule rather
    # than going silent.
    class _RacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAway([])
    driver.system_alert_buttons = [
        _button("Allow"),
        _button("Don't Allow"),
        _button("Allow Paste"),
        _button("Don't Allow Paste"),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow Paste", "Don't Allow Paste"}),
                tap_label="Allow Paste",
                native=True,
                in_tree=False,
            ),
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert guard.blocked_note == uncleared_prompt_note("Allow")
    assert "Allow Paste" not in guard.blocked_note and "Don't Allow Paste" not in guard.blocked_note


def test_the_end_of_step_guard_does_not_call_a_raced_ambiguous_alert_unhandled() -> None:
    # `probe_native` reaches "unhandled" two ways: a genuinely unidentified alert, and the other
    # half of the TOCTOU race above -- a matched rule whose tap found the label twice
    # (`AmbiguousSelector`). The "unhandled" branch's own note used to subtract only
    # `dismissed_native`, so a raced-but-matched rule's own labels survived into the leftover and
    # were named as an alert no rule identifies -- exactly what `uncleared_prompt_note`'s docstring
    # says must not happen, since the rule did identify it and only the tap failed (BE-0418 review
    # finding). With nothing else on the surface, the bound-exhaustion fallback must also name that
    # same rule on the final round rather than fall silent: the earlier fix folded the rule's own
    # labels out of the leftover but left the fallback keyed on `native_dismiss_shape` alone, which
    # is `None` here since this call never actually tapped anything -- the note went empty instead
    # of `uncleared_prompt_note`, a silence worse than the "unhandled" framing it replaced (BE-0418
    # review finding).
    class _AmbiguousEveryRound(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.AmbiguousSelector("the alert offers this label twice")

    driver = _AmbiguousEveryRound([])
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            )
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert guard.blocked_note == uncleared_prompt_note("Allow")


def test_the_end_of_step_guard_does_not_call_a_co_present_declared_prompt_unhandled_on_an_ambiguous_match() -> (
    None
):
    # The "unhandled" branch's own leftover credit resolved only `_resolve_alert_rule`'s single
    # match, unlike the race branch beside it (`leftover_dismissed_native`) and the mid-wait gate's
    # own `"unhandled"` branch, both of which credit every rule `identified_alert_rules` finds on
    # the same read (BE-0418 review finding). "notifications" hits `AmbiguousSelector` every round;
    # "paste" is a second, disjoint declared prompt fully present on the same read but never itself
    # attempted -- a single-rule credit still let its labels survive into the leftover and be named
    # an alert no rule identifies.
    class _AmbiguousEveryRound(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.AmbiguousSelector("the alert offers this label twice")

    driver = _AmbiguousEveryRound([])
    driver.system_alert_buttons = [
        _button("Allow"),
        _button("Don't Allow"),
        _button("Allow Paste"),
        _button("Don't Allow Paste"),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow Paste", "Don't Allow Paste"}),
                tap_label="Allow Paste",
                native=True,
                in_tree=False,
            ),
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert guard.blocked_note == uncleared_prompt_note("Allow")
    assert "Allow Paste" not in guard.blocked_note and "Don't Allow Paste" not in guard.blocked_note


def test_the_end_of_step_guard_never_taps_the_tree_while_a_native_alert_races() -> None:
    # `"absent"` stopped meaning "the SpringBoard surface is clear" once the time-of-check/time-of-
    # use race started carrying its own non-empty read forward -- but the tree tap was still
    # licensed by `state == "absent"` alone, so it could fire with a live system alert demonstrably
    # on screen. XCUITest answers an interrupting alert with its own default button before
    # synthesizing any interaction (BE-0399), so a tap issued here would silently override the
    # scenario's own policy with nothing in the report. A tree rule's own button is present and
    # tappable throughout, and is never touched (BE-0418 review finding).
    class _RacesAwayEveryRound(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAwayEveryRound([_button("Sheet")])
    driver.system_alert_buttons = [_button("OK"), _button("Cancel"), _button("Weird Button")]
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Sheet"}), tap_label="Sheet", native=False, in_tree=True
    )
    native_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK", native=True, in_tree=False
    )
    guard = AlertGuardConfig(rules=[tree_rule, native_rule])
    cleared, alerts = _call(driver, guard)
    assert not cleared and alerts == []
    assert not any(action[0] == "tap" for action in driver.actions)  # the tree was never touched
    assert "Weird Button" in guard.blocked_note


def test_the_end_of_step_guard_keeps_a_stuck_tree_diagnosis_through_a_racing_native_round() -> None:
    # The race branch above used to overwrite `note` unconditionally, unlike every other branch in
    # this loop -- so a still-open `NotTappable` diagnosis from an earlier round could be silently
    # erased the moment a *different* round's own native probe raced away (BE-0418 review finding).
    # Round 0's tree tap is blocked by a scrim (`NotTappable`); round 1 (and the final round 2)
    # each match the native rule but race away (`ElementNotFound`), leaving a non-empty read that
    # skips the tree entirely -- the stuck diagnosis must survive both, not vanish under either.
    class _StuckThenNativeRaces(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])  # never removed: the scrim never lifts
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls == 1:
                raise base.ElementNotTappable("the scrim has not lifted yet")
            super().tap(sel)

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _StuckThenNativeRaces()
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            # Round 0's own NotTappable diagnosis settles; a live alert appears for round 1's own
            # native probe to race away on.
            driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]

    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Not Now"}), tap_label="Not Now", native=False, in_tree=True
    )
    native_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}),
        tap_label="Allow",
        native=True,
        in_tree=False,
    )
    guard = AlertGuardConfig(rules=[tree_rule, native_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert not cleared and alerts == []
    assert guard.blocked_note == uncleared_prompt_note("Not Now")


def test_the_end_of_step_guard_names_a_leftover_native_alert_on_a_lingering_tree_round() -> None:
    # The third branch sharing the same flawed premise as the two fixes above: the lingering-fade
    # branch's own exhaustion-note computation used to discard the native leftover unconditionally,
    # on the same stale assumption that "absent" always means the native surface is clear. Round 0
    # taps an in-tree sheet whose fade outlasts `settle`; round 1's native probe races away on a
    # non-empty read, leaving "Weird Button" unaccounted for, while the unchanged tree read lands in
    # the lingering-fade branch below (BE-0418 review finding).
    class _RacesAwayOnTap(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAwayOnTap([_button("Sheet")])
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Sheet"}), tap_label="Sheet", native=False, in_tree=True
    )
    native_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK", native=True, in_tree=False
    )

    def settle() -> None:
        # Round 0's own dismiss seeds the native surface for round 1 onward; "Sheet" is never
        # removed from the tree, modeling its own fade outlasting every settle in this test.
        driver.system_alert_buttons = [
            _button("OK"),
            _button("Cancel"),
            _button("Weird Button"),
        ]

    guard = AlertGuardConfig(rules=[tree_rule, native_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Sheet")]
    assert "Weird Button" in guard.blocked_note
    # The raced-away "OK"/"Cancel" shape must not also be named (BE-0418 review finding).
    assert "OK" not in guard.blocked_note and "Cancel" not in guard.blocked_note


def test_the_end_of_step_guard_reports_a_native_alert_uncleared_after_a_leading_tree_round() -> (
    None
):
    # `native_declines == round_index` measured position in the call, not declines since the
    # dismissal: a round of any other kind before the tap -- an unrelated in-tree prompt tapped
    # cleanly here -- permanently left the decline count behind `round_index`, so a genuinely stuck
    # native alert preceded by one such round was never reported (review finding).
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    other = ResolvedAlertRule(
        identifying_labels=frozenset({"T"}), tap_label="T", native=False, in_tree=True
    )

    driver = FakeDriver([_button("T")])  # round 0: an unrelated in-tree prompt, tapped cleanly
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # The native alert this test cares about only raises after round 0's tree round.
            driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]

    guard = AlertGuardConfig(rules=[rule, other])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="T"), AlertEvent(label="Allow")]
    assert guard.blocked_note == uncleared_prompt_note("Allow")


def test_the_end_of_step_guard_reports_a_second_native_alert_stuck_behind_an_earlier_ones_fade() -> (
    None
):
    # `_bound_exhaustion_note` must anchor on the *shape* of the most recently tapped alert
    # directly, not on whichever shape `matching_alert_rule`'s own plain, dedup-blind pick happens
    # to return: an earlier dismissal's own still-enumerable fade can crowd the recent one out of
    # that pick, which would otherwise lose the diagnosis for the rest of the call once two native
    # shapes have been tapped in it (review finding). Wide (round 0) never actually clears, and
    # narrow -- tapped on round 1 once wide's own fade excludes it -- never clears either;
    # `matching_alert_rule`'s plain pick keeps returning wide (first in `native_rules`, which stays
    # in declaration order -- `_widest_first` is the tree path's own, not this one's) on every later
    # round even though narrow is the shape the check is meant to be reading.
    wide = ResolvedAlertRule(identifying_labels=frozenset({"A1", "A2"}), tap_label="A1")
    narrow = ResolvedAlertRule(identifying_labels=frozenset({"B1"}), tap_label="B1")
    driver = _fake_with_alert(["A1", "A2"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # Round 0 tapped wide, but its fade lingers, and narrow's own alert now joins it.
            driver.system_alert_buttons = [_button("A1"), _button("A2"), _button("B1")]

    guard = AlertGuardConfig(rules=[wide, narrow])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="A1"), AlertEvent(label="B1")]
    assert guard.blocked_note == uncleared_prompt_note("B1")


def test_the_end_of_step_guard_reports_a_native_alert_uncleared_past_an_intervening_collision() -> (
    None
):
    # `_bound_exhaustion_note` must check the final round's own read directly, not a streak counted
    # since the tap: an "unhandled" collision round -- BE-0418's own notifications/tracking pair,
    # sharing the tapped label "Allow" -- sits between the tap and the bound here, and an earlier
    # version of the check never advanced its own count for a round of that kind, permanently losing
    # the diagnosis for the rest of the call once it did (review finding).
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    driver = _fake_with_alert(["Allow", "Don't Allow"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # Round 0 tapped notifications, but its fade lingers, and tracking's own alert joins it:
            # two "Allow" buttons now enumerable together fail the per-label uniqueness check for
            # either, landing round 1 in "unhandled" rather than "already_dismissed".
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("Allow"),
                _button("Ask App Not to Track"),
            ]
        elif settle_count == 2:
            # Tracking's own alert resolves elsewhere (an interruption monitor, say) by round 2, but
            # notifications' own tap still never actually landed.
            driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]  # tracking never actually tapped
    assert guard.blocked_note == uncleared_prompt_note("Allow")


def test_the_end_of_step_guard_reports_an_unhandled_native_alert_uncleared_at_the_bound() -> None:
    # The "unhandled" branch must fall back to `_bound_exhaustion_note` exactly like its
    # "already_dismissed" twin, not clear the note whenever `leftover` happens to be empty (review
    # finding): "unhandled" is not only "no rule identifies this at all" -- a per-label uniqueness
    # collision on a read that *does* fully cover every already-dismissed shape lands here too, and
    # which of the two branches a torn-down alert's read falls into from one round to the next is
    # not something the caller controls, so the diagnosis must not depend on it. Both notifications
    # and tracking get cleanly dismissed at different rounds, then both fades collide on the final
    # round: `leftover` comes back empty (every button is one of the two dismissed reads' own), but
    # tracking's own shape -- the more recently tapped one -- is still fully present, and that must
    # still be named rather than silently dropped.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    driver = _fake_with_alert(["Allow", "Don't Allow"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # Round 0's notifications fade is fully gone, so tracking's own alert dismisses cleanly.
            driver.system_alert_buttons = [_button("Allow"), _button("Ask App Not to Track")]
        elif settle_count == 2:
            # Both fades now linger together, colliding on "Allow" -- neither dismissed shape reads
            # uniquely, but each one's own labels are still fully accounted for between the two.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("Allow"),
                _button("Ask App Not to Track"),
            ]

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow"), AlertEvent(label="Allow")]
    assert guard.blocked_note == uncleared_prompt_note("Allow")


def test_the_end_of_step_guard_names_the_rule_that_actually_races_on_a_final_race() -> None:
    # The race branch was the one native round kind that passed `""` instead of
    # `_bound_exhaustion_note`, unlike its `already_dismissed` and `"unhandled"` siblings over the
    # identical evidence (BE-0418 review finding) -- so a call whose *final* round is a race never
    # named a native alert it tapped and never saw clear. Round 0 taps "Allow"; its own fade
    # outlasts the settle, and "OK"/"Cancel" joins the surface; rounds 1 and 2 both race away on
    # "OK". The fallback resolves the rule fresh against the final round's own `buttons` (BE-0418
    # review finding) rather than trusting round 0's own `native_dismiss_shape`, so it names "OK" --
    # the rule this round actually raced on and never confirmed cleared -- not "Allow", whose own
    # tap the `alerts` list already confirms landed.
    class _TapsAllowThenRacesOnOK(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            button = base.resolve_unique(self.system_alert_buttons, sel)
            if button["label"] == "OK":
                raise base.ElementNotFound("the prompt raced away")
            super().handle_system_alert(sel, timeout)

    driver = _TapsAllowThenRacesOnOK([])
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            # "Allow"'s own fade outlasts this settle, and "OK"/"Cancel" joins the surface.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("OK"),
                _button("Cancel"),
            ]

    rule_a = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}),
        tap_label="Allow",
        native=True,
        in_tree=False,
    )
    rule_b = ResolvedAlertRule(
        identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK", native=True, in_tree=False
    )
    guard = AlertGuardConfig(rules=[rule_a, rule_b])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert guard.blocked_note == uncleared_prompt_note("OK")


def test_the_end_of_step_guard_still_names_a_co_present_alert_no_rule_identifies() -> None:
    # `subtract_labels` subtracts a dismissed rule's own `identifying_labels`, not the
    # whole round-0 read -- a second, different, unidentified alert already up alongside the one
    # a scenario declares is the ordinary shape of a stacked pair queued by one action (review
    # finding: recording the whole read instead, tried for a different finding, swallowed exactly
    # this case). The scenario declares only `notifications`; SpringBoard's very first read already
    # holds a second, undeclared alert ("OK" / "Cancel") right alongside it.
    driver = _fake_with_alert(["Allow", "Don't Allow", "OK", "Cancel"])
    guard = AlertGuardConfig(rules=[guard_rule("Allow", identifying=("Allow", "Don't Allow"))])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=lambda: None)
    assert cleared and alerts == [AlertEvent(label="Allow")]  # notifications did clear
    # Names the still-unidentified "OK" / "Cancel" alert, not a claim that notifications itself
    # never cleared -- the tap the guard actually made is not what is still blocking the screen.
    assert "OK" in guard.blocked_note and "Cancel" in guard.blocked_note
    assert "Allow" not in guard.blocked_note


def test_the_end_of_step_guard_names_a_co_present_alert_on_a_dismissing_final_round() -> None:
    # The "dismissed" branch used to clear `note` unconditionally, the only round kind that
    # touched `note` without computing the leftover first -- so a co-present alert no rule
    # identifies was silently dropped whenever the call's *final* round happened to be a fresh
    # dismissal, since no later round is left to self-correct into "unhandled" or
    # "already_dismissed" (review finding). Round 0 dismisses notifications; round 1 collides with
    # tracking on the shared "Allow" label (an existing, already-correct "unhandled" round); round
    # 2, the last one, dismisses tracking cleanly -- with an undeclared "OK" / "Cancel" alert
    # sitting right alongside it throughout.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )
    driver = _fake_with_alert(["Allow", "Don't Allow"])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # notifications' fade collides with tracking's own alert on "Allow", and the undeclared
            # pair is already up alongside both.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("Allow"),
                _button("Ask App Not to Track"),
                _button("OK"),
                _button("Cancel"),
            ]
        elif settle_count == 2:
            # notifications' own fade is fully gone, so tracking now dismisses cleanly -- on the
            # call's own final round.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Ask App Not to Track"),
                _button("OK"),
                _button("Cancel"),
            ]

    guard = AlertGuardConfig(rules=[notifications, tracking])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow"), AlertEvent(label="Allow")]
    assert "OK" in guard.blocked_note and "Cancel" in guard.blocked_note


def test_the_end_of_step_guard_clears_a_native_leftover_note_once_the_surface_reads_absent() -> (
    None
):
    # An `already_dismissed` round's leftover note names a *native* alert `probe_native` actually
    # enumerated. A later round's own probe answering `"absent"` is a deterministic no-SpringBoard-
    # alert fact (`probe_native`'s own docstring), so that note is now stale evidence of an alert
    # this call has since watched go away -- unlike a tree diagnosis, which a merely-ambiguous tree
    # read cannot contradict this cleanly, and which this fix must not touch.
    driver = _fake_with_alert(["Allow", "Don't Allow"])
    guard = AlertGuardConfig(rules=[guard_rule("Allow", identifying=("Allow", "Don't Allow"))])
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # Round 0's own alert fades, and a second, unrelated one queues alongside it.
            driver.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("OK"),
                _button("Cancel"),
            ]
        elif settle_count == 2:
            # Both are gone by round 2 -- an interruption monitor answered the second one, say.
            driver.system_alert_buttons = []

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_reports_an_unhandled_native_alert_after_an_unrelated_tree_dismiss() -> (
    None
):
    # An "unhandled" round with nothing native dismissed ends the call rather than continuing
    # (BE-0418 review finding): a prior round's own tree activity -- dismissing T here -- does not
    # change that, since the recovery "unhandled" exists for is specifically a *native* alert's own
    # fade draining, and this call has never dismissed one. The diagnosis this round makes must
    # still come out, not get lost to a further round this call has no reason to spend.
    class _AbsentThenUnhandled(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("T")])
            self.probes = 0

        def system_alert_labels(self) -> list[str]:
            self.probes += 1
            return [] if self.probes == 1 else ["Weird Button"]

    driver = _AbsentThenUnhandled()
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"T"}), tap_label="T", native=False, in_tree=True
    )
    guard = AlertGuardConfig(rules=[rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=lambda: None)
    assert cleared and alerts == [AlertEvent(label="T")]  # T tapped on round 0
    assert "Weird Button" in guard.blocked_note  # round 1's own diagnosis, not lost
    assert driver.probes == 2  # ended on round 1 rather than spending the rest of the bound


def test_the_end_of_step_guard_keeps_a_stuck_tree_note_on_a_lingering_tree_round_too() -> None:
    # The other half of the fix above: a pending tree diagnosis must still survive a *lingering*
    # tree round (a shape this call already cleared, still enumerable) exactly as it survives the
    # "genuinely nothing matched" branch just below -- this round settling and continuing must not
    # be mistaken for evidence that the stuck prompt resolved.
    class _StuckDriver(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            if isinstance(sel, dict) and sel.get("label") == "Stuck":
                raise base.ElementNotTappable("Stuck's scrim never lifts")
            super().tap(sel)

    driver = _StuckDriver([_button("Stuck")])
    stuck = ResolvedAlertRule(
        identifying_labels=frozenset({"Stuck"}), tap_label="Stuck", native=False, in_tree=True
    )
    other = ResolvedAlertRule(
        identifying_labels=frozenset({"T"}), tap_label="T", native=False, in_tree=True
    )
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # Round 0's Stuck sheet moves off-screen (or behind another), and a distinct, unrelated
            # prompt takes its place -- T's own button lingers once dismissed, same as any fade.
            driver.screen = [_button("T")]

    guard = AlertGuardConfig(rules=[other, stuck])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="T")]  # only the unrelated prompt ever tapped
    assert "Stuck" in guard.blocked_note  # the stuck diagnosis, not silently cleared


def test_the_end_of_step_guard_keeps_a_stuck_tree_note_when_another_shape_shares_its_label() -> (
    None
):
    # The other half of the fix above, and the one label alone cannot tell apart: two `in_tree`
    # rules can share one tap label under different choices -- `savePassword`'s three shapes all
    # tap "Not Now" under `choice: deny` -- so comparing the *label* a later round's tap lands on
    # against the stuck prompt's own label would read a genuinely different prompt's success as
    # the stuck one finally landing (BE-0418 review finding). `a` and `b` tap the identical label;
    # only their shapes differ.
    class _StuckDriver(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            if (
                isinstance(sel, dict)
                and sel.get("label") == "Not Now"
                and any(el["label"] == "A1" for el in self.screen)
            ):
                raise base.ElementNotTappable("A's scrim never lifts")
            super().tap(sel)

    driver = _StuckDriver([_button("A1"), _button("A2"), _button("Not Now")])
    a = ResolvedAlertRule(
        identifying_labels=frozenset({"A1", "A2"}), tap_label="Not Now", native=False, in_tree=True
    )
    b = ResolvedAlertRule(
        identifying_labels=frozenset({"B1", "B2"}), tap_label="Not Now", native=False, in_tree=True
    )
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            # A's own sheet moves off-screen (or behind another), and a distinct prompt that shares
            # its tap label takes its place.
            driver.screen = [_button("B1"), _button("B2"), _button("Not Now")]

    guard = AlertGuardConfig(rules=[a, b])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Not Now")]  # only b ever actually tapped
    # a's own stuck diagnosis survives b's unrelated success, even though the note names the same
    # label either way -- an empty note here would mean the bug cleared it.
    assert guard.blocked_note == uncleared_prompt_note("Not Now")


def test_the_end_of_step_guard_clears_a_stuck_tree_note_when_a_wider_nested_shape_lands() -> None:
    # The stuck-shape clear used equality, but `exclude`'s own dedup (`dismissed_tree_shapes |=
    # {rule.identifying_labels}`, feeding `_resolve_alert_rule`'s subset test) can retire a *nested*
    # stuck shape without ever matching it exactly again. `savePassword`'s narrower shape N and
    # wider shape W both tap "Not Now" (N subset of W): round 0 catches N's own scrim before W's
    # third label has rendered; round 1 renders it, and the widest-first match lands on W instead,
    # closing the sheet N was itself naming. Equality left N marked stuck forever after that,
    # contradicting `exclude`, which already treats N as answered (BE-0418 review finding).
    class _NestedShapeStuckThenLands(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Save Password"), _button("Not Now")])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls == 1:
                raise base.ElementNotTappable("the scrim has not lifted yet")
            super().tap(sel)

    driver = _NestedShapeStuckThenLands()
    narrower = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    wider = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Never for This Website", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            # The sheet finishes rendering its third label.
            driver.screen = [
                _button("Save Password"),
                _button("Not Now"),
                _button("Never for This Website"),
            ]
        elif settle_calls == 2:
            # Round 1's tap genuinely closes it.
            driver.screen = []

    guard = AlertGuardConfig(rules=[narrower, wider])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Not Now")]  # tapped once, on the wider shape
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_does_not_retap_a_fading_alert_when_another_one_joins_it() -> None:
    # The native dedup keys on the matched rule's own shape, not the raw buttons read: that read
    # (`system_alert_labels()`) enumerates every alert SpringBoard currently holds, so a still-
    # fading alert's own button set would otherwise change the instant a second, distinct alert
    # queues up alongside it — read naively that looks like a genuinely new alert and re-taps the
    # first one a second time, landing on nothing or on whatever it has by then revealed.
    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            # The first alert is still fading (never removed) and a second, disjoint one joins it.
            d.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("OK"),
                _button("Cancel"),
            ]

    driver = _fake_with_alert(["Allow", "Don't Allow"], react=react)
    guard = AlertGuardConfig(rules=[guard_rule("Allow", identifying=("Allow", "Don't Allow"))])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert sum(1 for a in driver.actions if a[0] == "handle_system_alert") == 1
    # The joining alert ("OK" / "Cancel") is one this call never answered — `already_dismissed`
    # declining to re-tap the first alert must not read as "nothing else is up" and silently drop
    # it (BE-0418): the eventual failure still needs to name it, the same as a fresh "unhandled".
    assert "an unhandled system alert is blocking the screen" in guard.blocked_note
    assert "OK" in guard.blocked_note and "Cancel" in guard.blocked_note
    # ...and *only* those: the already-answered labels are filtered out of the note, so it never
    # re-names the alert this call just cleared. Without this the unfiltered `alert_block_note(
    # buttons)` passes every assertion above, on this branch and on the `"unhandled"` one alike.
    assert "Allow" not in guard.blocked_note and "Don't Allow" not in guard.blocked_note


def test_the_end_of_step_guard_keeps_a_pending_tree_note_through_a_repeated_native_alert() -> None:
    # The counterpart to the "preserves an uncleared tree note past an unrelated native dismissal"
    # test: once a native alert has already been dismissed and keeps reading back unchanged
    # (`probe_native`'s own `already_dismissed` decline), that round must not clear a still-open
    # tree diagnosis either.
    class _StuckTreeThenRepeatingNative(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Not Now")])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls == 1:
                self.system_alert_buttons = [_button("Allow")]
            raise base.ElementNotTappable("the scrim never lifts")

    driver = _StuckTreeThenRepeatingNative()
    guard = AlertGuardConfig(
        rules=[
            guard_rule("Not Now", native=False, in_tree=True),
            guard_rule("Allow", native=True, in_tree=False),
        ]
    )
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Allow")]
    assert driver.tap_calls == 1
    assert sum(1 for a in driver.actions if a[0] == "handle_system_alert") == 1
    assert "a system prompt the guard could not clear is still up" in guard.blocked_note
    assert "Not Now" in guard.blocked_note


def test_the_end_of_step_guard_never_retaps_a_label_it_already_cleared_from_the_tree() -> None:
    # The in-tree twin of the native case above, but closed a different way (BE-0418): a button a
    # round just tapped successfully stays in the tree (the fake models no removal), so a later
    # round's match would land on it again — and unlike the native path, re-tapping it risks landing
    # on an application button the closing sheet has by then revealed, not just the same fading
    # sheet. `dismiss_from_tree_once`'s own `exclude` withholds a label already cleared this call
    # from matching again at all, so the tap happens exactly once, not merely reported once. A sheet
    # that stays in the tree for the whole rest of the bound after being tapped is exactly the case
    # `uncleared_prompt_note` exists for (review finding): `exclude` guarantees this call can never
    # act on it again, so nothing later would otherwise report a sheet that accepted the tap without
    # actually closing.
    driver = FakeDriver([_button("Not Now")])  # never removed: models a fade past `settle`
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert guard.blocked_note == uncleared_prompt_note("Not Now")
    assert sum(1 for action in driver.actions if action[0] == "tap") == 1


def test_the_end_of_step_guard_reports_a_revealed_screen_sharing_a_dismissed_shapes_labels() -> (
    None
):
    # The other half of the test above, and the one a bare label-containment check cannot tell
    # apart from it: `savePassword`'s 26.5 shape names only ordinary UI vocabulary ("Save",
    # "Not Now"), so once the sheet genuinely closes, an underlying app screen (an edit form, say)
    # whose own ordinary buttons happen to carry those same two labels reads back identically to
    # the sheet's own labels lingering past `settle`. Comparing the whole tree's own identity
    # against the pre-tap read once distinguished the two, mirroring `_AlertGuardGate.
    # _dismiss_from_tree`'s own guard for the mid-wait path -- but that same requirement also ruled
    # out the guard's own motivating case, a sheet that accepts a tap without closing and
    # re-presents with a validation error, which changes the tree by construction (BE-0418 review
    # finding). Dropped in favor of the label-containment check alone: unlike the mid-wait gate,
    # this one-shot call never taps again regardless of which read it takes here (`exclude` already
    # forbids it), so the misreport this test pins is the accepted cost of naming the genuinely
    # still-stuck case the sibling test above exists for, rather than a second, unlicensed tap.
    class _RevealsAFormWithTheSameButtonLabels(FakeDriver):
        def __init__(self) -> None:
            super().__init__([_button("Save"), _button("Not Now")])

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            # The sheet closes, revealing a form whose own "Save"/"Not Now" buttons are a distinct
            # element from the sheet's own — modeled here by an extra, identified field alongside
            # them, changing the tree's own identity even though the two button labels read back
            # unchanged.
            self.screen = [
                _button("Save"),
                _button("Not Now"),
                {
                    "identifier": "email_field",
                    "label": None,
                    "traits": [],
                    "value": None,
                    "frame": (0, 0, 10, 10),
                    "nativeZ": None,
                },
            ]

    driver = _RevealsAFormWithTheSameButtonLabels()
    sheet = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    guard = AlertGuardConfig(rules=[sheet])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    # The sheet genuinely cleared on round 0, unlike the test above where the same labels really are
    # the sheet's own fade outlasting the bound -- but the two are indistinguishable from a bare
    # label check, and the misreport costs nothing this call would otherwise act on (BE-0418 review
    # finding): `exclude` already withholds this shape regardless of the note.
    assert guard.blocked_note == uncleared_prompt_note("Not Now")
    assert sum(1 for action in driver.actions if action[0] == "tap") == 1


def test_the_end_of_step_guard_names_a_sheet_that_re_presents_with_a_validation_error() -> None:
    # The motivating case the two tests above's own tree-identity check ruled out (BE-0418 review
    # finding): a sheet that accepts a tap without closing and re-presents itself with a validation
    # error changes the tree by construction (the new error row, at minimum), so requiring the whole
    # tree signature to match the pre-tap read never held for it -- the call fell through to a bare
    # `""` instead of naming the sheet `exclude` will never let it tap again. Dropping that
    # requirement in favor of the label-containment check alone is what lets this case through.
    class _ReRaisesWithAnErrorRow(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            self.screen = [
                _button("Save"),
                _button("Not Now"),
                {
                    "identifier": None,
                    "label": "Incorrect password",
                    "traits": [],
                    "value": None,
                    "frame": (0, 0, 10, 10),
                    "nativeZ": None,
                },
            ]

    driver = _ReRaisesWithAnErrorRow([_button("Save"), _button("Not Now")])
    sheet = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    guard = AlertGuardConfig(rules=[sheet])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert guard.blocked_note == uncleared_prompt_note("Not Now")


def test_dismiss_from_tree_once_does_not_promote_a_shadowed_choice_for_the_same_alert() -> None:
    # Two rules can share one alert's shape (`identifying_labels`) under different `tap_label`s —
    # a scenario's `choice` overriding a target's for the same prompt (BE-0177). Filtering the
    # candidate rules by `tap_label` before matching would drop only the already-tapped rule and
    # promote its sibling, matching the very same alert and tapping the opposite button on it.
    deny = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    grant = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Save",
        native=False,
        in_tree=True,
    )
    driver = FakeDriver([_button("Save"), _button("Not Now")])  # never removed: models a fade
    guard = AlertGuardConfig(rules=[deny, grant])
    cleared, alerts = _call(driver, guard)
    assert cleared and alerts == [AlertEvent(label="Not Now")]  # the scenario's own choice, once
    assert sum(1 for a in driver.actions if a[0] == "tap") == 1


def test_dismiss_from_tree_once_treats_a_narrower_rendering_of_an_excluded_shape_as_excluded() -> (
    None
):
    # A `savePassword`-style policy resolves to three rules, two of whose shapes nest rather than
    # share one exact shape: the widest names three labels, the narrower one names two of those
    # same three, and both tap the same button. Excluding only the exact shape a round tapped would
    # leave the narrower sibling free to match the very same still-fading sheet and tap it a second
    # time. Excluding by subset closes that: a shape wholly contained in one already excluded counts
    # as excluded too, so the narrower sibling declines along with the exact match. The third rule
    # neither nests with nor matches either of the other two ("Save", not "Save Password"), and is
    # here to confirm the subset test never excludes a genuinely unrelated shape by mistake.
    widest = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Never for This Website", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    narrower = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    unrelated = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    # Never removed: models the sheet's own dismiss animation outlasting `settle`.
    driver = FakeDriver(
        [_button("Save Password"), _button("Never for This Website"), _button("Not Now")]
    )
    guard = AlertGuardConfig(rules=[widest, narrower, unrelated])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Not Now")]  # tapped once, not once per nested rule
    assert sum(1 for a in driver.actions if a[0] == "tap") == 1


def test_tree_rules_tries_the_widest_nested_shape_first_regardless_of_declaration_order() -> None:
    # The subset test in `_resolve_alert_rule` excludes a candidate whose shape is *contained in*
    # an already-dismissed one, not the reverse -- so whichever nested shape `matching_alert_rule`
    # (itself first-match-in-list-order) happens to try first is the one that must be dismissed
    # first, or a narrower sibling dismissed on round 0 leaves the wider one free to match the same
    # still-fading sheet and tap it again on round 1. Declaring the narrower rule *first* here would
    # reproduce exactly that repeat tap if `tree_rules` returned rules in declaration order; sorting
    # widest-first removes the dependency on that order entirely (BE-0418 review finding).
    widest = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Never for This Website", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    narrower = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    # Never removed: models the sheet's own dismiss animation outlasting `settle`.
    driver = FakeDriver(
        [_button("Save Password"), _button("Never for This Website"), _button("Not Now")]
    )
    guard = AlertGuardConfig(rules=[narrower, widest])  # narrower declared first, deliberately
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Not Now")]  # tapped once, not once per nested shape
    assert sum(1 for a in driver.actions if a[0] == "tap") == 1


def test_tree_rules_widest_first_survives_a_non_nesting_rule_in_between() -> None:
    # Subset is a partial order, not a total one: an adjacent-swap pass only fixes a nested pair
    # that is already next to each other, so a rule nesting with neither sibling sitting between
    # them in declaration order blocks every swap and leaves the narrower shape matching first --
    # exactly the repeat tap the test above already covers, but the above only ever declares the
    # nested pair adjacent, so it cannot catch this gap on its own (review finding). Modeled on
    # savePassword's real three shapes, declared narrower, unrelated, widest -- today's catalogue
    # happens to declare the widest one first, so this is the one order that would misfire.
    widest = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Never for This Website", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    narrower = ResolvedAlertRule(
        identifying_labels=frozenset({"Save Password", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    unrelated = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),  # the 26.5 shape -- nests with neither
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    # Never removed: models the sheet's own dismiss animation outlasting `settle`.
    driver = FakeDriver(
        [_button("Save Password"), _button("Never for This Website"), _button("Not Now")]
    )
    guard = AlertGuardConfig(rules=[narrower, unrelated, widest])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Not Now")]  # tapped once, not once per nested shape
    assert sum(1 for a in driver.actions if a[0] == "tap") == 1


def test_the_end_of_step_guard_finds_a_stacked_alert_behind_a_fading_first_match() -> None:
    # `already_dismissed` must not end the round the instant the plain first match is one this
    # call already answered: a real, not-yet-answered alert can be enumerable right alongside that
    # fade (BE-0418's own stacked case), and `matching_alert_rule` always returns the first
    # candidate in list order, so the lingering alert being listed first must not hide a second,
    # disjoint one behind it.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    second = ResolvedAlertRule(identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK")

    taps = 0

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        nonlocal taps
        if kind != "handle_system_alert":
            return
        taps += 1
        if taps == 1:
            # The first tap's own alert is still fading (its buttons linger), and the second,
            # disjoint alert has now also raised — both enumerable together, which is exactly the
            # round `_resolve_alert_rule`'s retry must see past.
            d.system_alert_buttons = [
                _button("Allow"),
                _button("Don't Allow"),
                _button("OK"),
                _button("Cancel"),
            ]
        else:
            d.system_alert_buttons = []  # both alerts genuinely gone once the second is tapped

    driver = _fake_with_alert(["Allow", "Don't Allow"], react=react)
    guard = AlertGuardConfig(rules=[notifications, second])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Allow"), AlertEvent(label="OK")]
    assert guard.blocked_note == ""  # both alerts accounted for; nothing left over


def test_the_end_of_step_guard_gives_a_lingering_tree_exclusion_another_round_instead_of_ending_the_call() -> (
    None
):
    # `dismiss_from_tree_once` returns a bare `None` for two different facts: nothing matched at
    # all, and every match landed on a shape `exclude` already holds. The native path's twin of the
    # second fact (`already_dismissed`) settles and continues, precisely so a sheet stacked
    # underneath a still-fading one gets another round to be presented -- the tree path must do the
    # same, rather than ending the whole call the instant a round finds only the shape it already
    # excluded and nothing new yet.
    first = ResolvedAlertRule(
        identifying_labels=frozenset({"Not Now"}), tap_label="Not Now", native=False, in_tree=True
    )
    second = ResolvedAlertRule(
        identifying_labels=frozenset({"Later"}), tap_label="Later", native=False, in_tree=True
    )
    driver = FakeDriver([_button("Not Now")])  # the fade outlasts every settle in this test
    settle_count = 0

    def settle() -> None:
        nonlocal settle_count
        settle_count += 1
        if settle_count == 2:  # not revealed until the round *after* the one that tapped "Not Now"
            driver.screen = [*driver.screen, _button("Later")]
        elif settle_count == 3:
            # Round 2's own landing tap genuinely closes "Later", the way a real device's
            # settle_after_alert_dismiss would reflect by the time this call returns.
            driver.screen = []

    guard = AlertGuardConfig(rules=[first, second])
    cleared, alerts = _call(driver, guard, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label="Not Now"), AlertEvent(label="Later")]
    assert guard.blocked_note == ""


def test_the_end_of_step_guard_skips_the_post_loop_query_on_an_unsettled_terminal_tree_break() -> (
    None
):
    # The post-loop check's own fresh `_read_tree` call exists for a path that settled since the
    # tree was last read -- but the loop's own most common tree exit, a round whose tree read
    # matches nothing and ends the call right there (`note = ""; break`), settles nothing on its way
    # to that break. Nothing can have moved the screen since that round's own read already tested
    # this exact evidence and found it false, so a second `driver.query()` back-to-back against the
    # same screen would only reproduce the same false (BE-0418 review finding). Round 0 taps "Sheet"
    # and settle() genuinely closes it; round 1's own tree read finds nothing and ends the call
    # without settling again.
    class _CountingQueryDriver(FakeDriver):
        def __init__(self, screen: list[base.Element]) -> None:
            super().__init__(screen)
            self.query_calls = 0

        def query(self) -> list[base.Element]:
            self.query_calls += 1
            return super().query()

    driver = _CountingQueryDriver([_button("Sheet")])
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Sheet"}), tap_label="Sheet", native=False, in_tree=True
    )
    guard = AlertGuardConfig(rules=[tree_rule])
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            driver.screen = []  # the tap genuinely closed the sheet

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Sheet")]
    assert guard.blocked_note == ""
    # Round 0's own tap-time read, round 1's own terminal read -- no third, post-loop query.
    assert driver.query_calls == 2


def test_the_end_of_step_guard_still_checks_a_first_ever_tree_tap_on_the_final_round() -> None:
    # Twin of the skip above, for the one case it must never fire: recording `tree_read_round`
    # only on a read that goes on to *test* existing evidence (not the tap that just produced it,
    # per that branch's own `if not isinstance(tree_result, AlertEvent)` guard) means a call whose
    # first-ever tree interaction is a tap on its own final round leaves `tree_read_round` at `None`
    # -- nothing has settled *since* a read that tested this tap, because no such read ever ran.
    # `None` must still run the post-loop check, not be read as "nothing to verify" (BE-0418 review
    # finding): rounds 0 and 1 dismiss two unrelated native alerts, never touching the tree at all;
    # round 2 finally reads an empty SpringBoard surface and taps a sheet that accepts the tap and
    # re-presents itself instead of closing.
    rule_a = ResolvedAlertRule(identifying_labels=frozenset({"A1", "A2"}), tap_label="A1")
    rule_b = ResolvedAlertRule(identifying_labels=frozenset({"B1", "B2"}), tap_label="B1")
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Sheet"}), tap_label="Sheet", native=False, in_tree=True
    )
    driver = FakeDriver([_button("Sheet")])
    driver.system_alert_buttons = [_button("A1"), _button("A2")]
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 1:
            driver.system_alert_buttons = [_button("B1"), _button("B2")]
        elif settle_calls == 2:
            driver.system_alert_buttons = []
        # settle_calls == 3, after round 2's own tap: "Sheet" is deliberately left in place -- the
        # sheet accepted the tap without closing.

    guard = AlertGuardConfig(rules=[rule_a, rule_b, tree_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label="A1"), AlertEvent(label="B1"), AlertEvent(label="Sheet")]
    assert guard.blocked_note == uncleared_prompt_note("Sheet")


def test_dismiss_from_tree_once_declines_an_excluded_shape() -> None:
    # Direct unit coverage of the `exclude` parameter itself: a button that would otherwise resolve
    # and tap cleanly is withheld once its shape (`identifying_labels`) is excluded, exactly as if
    # no rule named it. Also confirms the buttons this round's own tree read found come back
    # alongside the result even when nothing was tapped, so a caller can resolve the same match
    # again without a second, possibly-different query.
    driver = FakeDriver([_button("Not Now")])
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    result, buttons, _signature = guard.dismiss_from_tree_once(
        driver, exclude=frozenset({frozenset({"Not Now"})})
    )
    assert result is None
    assert buttons == ["Not Now"]
    assert not any(action[0] == "tap" for action in driver.actions)


def test_dismiss_from_tree_once_keeps_declaration_order_for_a_non_nested_pair() -> None:
    # `_widest_first` must only ever reorder a *nested* pair -- moving a wider, unrelated rule
    # ahead of a narrower one that does not nest with it would invert BE-0177's scenario-before-
    # target precedence for any two rules of different width that both happen to match the same
    # read (review finding: a plain width sort did exactly that). `scenario` (declared first,
    # narrower) and `target` (declared second, wider) share no label at all, so neither nests in
    # the other -- both match this read independently, and declaration order alone must still
    # decide which one the tap goes to.
    scenario = ResolvedAlertRule(
        identifying_labels=frozenset({"X"}), tap_label="X", native=False, in_tree=True
    )
    target = ResolvedAlertRule(
        identifying_labels=frozenset({"Y", "Z"}), tap_label="Y", native=False, in_tree=True
    )
    driver = FakeDriver([_button("X"), _button("Y"), _button("Z")])
    guard = AlertGuardConfig(rules=[scenario, target])
    result, _buttons, _signature = guard.dismiss_from_tree_once(driver)
    assert result == AlertEvent(label="X")


def test_the_end_of_step_guard_finds_a_second_tree_alert_behind_an_excluded_first_match() -> None:
    # The tree twin of the native "stacked alert behind a fading first match" case: excluding the
    # winning match must not end the search the instant it lands back on an already-answered
    # shape — a real, differently-shaped second alert revealed once the first tap lands (BE-0418's
    # own stacked case) must still be found via the retry among the shapes not yet excluded. Neither
    # tapped sheet is ever actually removed from the tree (the fake models no removal), so the round
    # bound is spent with the most recently tapped one, "Later", still enumerable — the tree bound-
    # exhaustion diagnosis names it (review finding), the same way it would a native alert.
    first = ResolvedAlertRule(
        identifying_labels=frozenset({"Not Now"}), tap_label="Not Now", native=False, in_tree=True
    )
    second = ResolvedAlertRule(
        identifying_labels=frozenset({"Later"}), tap_label="Later", native=False, in_tree=True
    )

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == "Not Now":
            d.screen = [*d.screen, _button("Later")]  # revealed once the first tap lands

    driver = FakeDriver([_button("Not Now")], react=react)
    guard = AlertGuardConfig(rules=[first, second])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Not Now"), AlertEvent(label="Later")]
    assert guard.blocked_note == uncleared_prompt_note("Later")


def test_the_end_of_step_guard_names_an_uncleared_tree_sheet_after_a_native_final_round() -> None:
    # The tree bound-exhaustion diagnosis used to live only inside the `"absent"` branch's own
    # lingering-fade check, so a call whose *final* round takes any other path never ran it, unlike
    # the native diagnosis's own twin (reachable from both `already_dismissed` and `"unhandled"`).
    # Round 0 taps sheet "A", which accepts the tap but re-presents itself instead of closing;
    # round 1's tree read is unchanged, landing in the lingering-fade branch but not yet the final
    # round; round 2 is an unrelated native alert dismissed on the call's own last round, so the
    # tree is never read again — the diagnosis must still survive to the end (BE-0418 review
    # finding).
    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"A"}), tap_label="A", native=False, in_tree=True
    )
    native_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Ping"}), tap_label="Ping", native=True, in_tree=False
    )
    driver = FakeDriver([_button("A")])  # never removed: "A" accepts the tap without closing
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1
        if settle_calls == 2:  # right after round 1's own lingering-fade read
            driver.system_alert_buttons = [_button("Ping")]

    guard = AlertGuardConfig(rules=[tree_rule, native_rule])
    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label="A"), AlertEvent(label="Ping")]
    assert guard.blocked_note == uncleared_prompt_note("A")


def test_the_end_of_step_guard_reports_two_native_alerts_sharing_a_tap_label() -> None:
    # BE-0418's own under-reporting risk (Unit 3), reachable through the built-in catalogue itself:
    # `notifications` and `tracking` both resolve to a `choice: grant` tap of "Allow", so a scenario
    # declaring both gets two rules whose tap label is identical. Deduplicating a repeat dismissal by
    # label alone would silently drop the second, genuinely distinct alert — the dedup must key on
    # the alert's full button set too, not the tapped label in isolation.
    notifications = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
    )
    tracking = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
    )

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button("Allow"), _button("Ask App Not to Track")]

    driver = _fake_with_alert(["Allow", "Don't Allow"], react=react)
    guard = AlertGuardConfig(rules=[notifications, tracking])
    cleared, alerts = _call(driver, guard)
    assert cleared
    assert alerts == [AlertEvent(label="Allow"), AlertEvent(label="Allow")]


def test_the_end_of_step_guard_settles_after_the_round_that_exhausts_its_bound() -> None:
    # Three stacked prompts, each dismissed on its own round, with the third landing right as the
    # loop's round bound is spent. `settle` must still run after that last round: the caller reads
    # the screen the instant this call returns, and a still-animating sheet would fail the retry for
    # a reason that is not the retry's own.
    labels = ["First", "Second", "Third"]

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert" and d.system_alert_buttons:
            tapped = d.system_alert_buttons[0]["label"]
            assert tapped is not None
            remaining = labels[labels.index(tapped) + 1 :]
            d.system_alert_buttons = [_button(label) for label in remaining]

    driver = _fake_with_alert(labels, react=react)
    guard = AlertGuardConfig(rules=[guard_rule(label, in_tree=False) for label in labels])
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared
    assert alerts == [AlertEvent(label=label) for label in labels]
    assert settle_calls == 3  # once per dismissing round, the bound-exhausting one included


def test_the_end_of_step_guard_clears_a_single_alert_with_no_added_latency() -> None:
    # The regression guard for pre-BE-0418 behavior: one in-tree alert still clears, and nothing
    # here adds a fixed delay to the common single-alert case — every extra round this loop spends
    # confirming the screen is clear costs one more in-memory probe, never a sleep.
    prompt_button = _button("Not Now")
    driver = FakeDriver([_button("Sign In"), prompt_button], react=_clearing_tree_tap("Not Now"))
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    settle_calls = 0

    def settle() -> None:
        nonlocal settle_calls
        settle_calls += 1

    alerts: list[AlertEvent] = []
    cleared = guard(driver, alerts, settle=settle)
    assert cleared and alerts == [AlertEvent(label="Not Now")]
    assert settle_calls == 1  # one dismissing round; the next round finds nothing left to settle


def test_the_end_of_step_guard_declines_an_ambiguous_in_tree_label() -> None:
    # Determinism first, exactly as the mid-wait path: two buttons carrying the configured label is
    # not a prompt this guard may guess at. It reports nothing rather than tapping one.
    driver = FakeDriver([_button("Not Now"), _button("Not Now")])
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, _alerts = _call(driver, guard)
    assert not cleared
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_stays_off_the_tree_without_scenario_rules() -> None:
    # The in-tree surface is armed only by the scenario's own in-tree rules, never by the built-in
    # dismissive defaults — "Cancel" / "Close" are ordinary UI vocabulary a real screen can show.
    driver = FakeDriver([_button("Cancel")])
    guard = AlertGuardConfig()
    cleared, _alerts = _call(driver, guard)
    assert not cleared
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_declines_when_an_identified_button_shares_the_label() -> None:
    # `match_alert_rule` resolves over the identifier-less subset, so a same-named *identified* app
    # button does not stop it — but the tap sees the whole tree and would be ambiguous. The
    # whole-tree uniqueness pre-check is what catches that, exactly as the mid-wait path's does.
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"
    driver = FakeDriver([_button("Not Now"), app_button])
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, _alerts = _call(driver, guard)
    assert not cleared
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_reports_nothing_when_the_prompt_closes_itself() -> None:
    # The button left the tree between this guard's own read and its tap. Benign: the prompt is gone,
    # which is what the caller wanted, and the step's own outcome still decides the verdict.
    class _VanishingPrompt(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            raise base.ElementNotFound("the prompt closed itself")

    driver = _VanishingPrompt([_button("Not Now")])
    guard = AlertGuardConfig(rules=[guard_rule("Not Now")])
    cleared, _alerts = _call(driver, guard)
    assert not cleared


def test_a_blocked_tap_names_the_alert_in_the_step_s_own_failure() -> None:
    # BE-0402's promise for the *step* call site, which holds no mid-wait gate: a `tap` blocked by an
    # alert no rule or candidate label names would otherwise fail as a bare `element not found`, with
    # nothing to say a prompt was on screen at all.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    driver = _fake_with_alert(["Weird Button"])  # nothing on screen, and an unnamed prompt over it
    result = run_scenario(
        driver,
        load_scenarios("- name: t\n  steps:\n    - tap: { id: go }\n")[0],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    reason = result.steps[0].reason or ""
    assert "an unhandled system alert is blocking the screen (buttons: Weird Button)" in reason
    assert result.steps[0].alerts == []  # nothing was dismissed, and nothing claims otherwise


def test_a_blocked_wait_names_the_alert_exactly_once() -> None:
    # A guarded `wait` passes through both call sites: the mid-wait gate appends the note to the
    # timeout it returns, and the end-of-step guard then re-probes the same still-unanswered alert.
    # The note states one observation, so it must be said once — not doubled by the second look.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    driver = _fake_with_alert(["Weird Button"])
    result = run_scenario(
        driver,
        load_scenarios("- name: t\n  steps:\n    - wait: { for: { id: never }, timeout: 0.3 }\n")[
            0
        ],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    reason = result.steps[0].reason or ""
    assert reason.count("an unhandled system alert is blocking the screen") == 1


def test_a_blocked_expect_names_the_alert_in_the_scenario_s_own_failure() -> None:
    # The same promise for the `expect` phase, whose retry calls the guard directly too.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    driver = _fake_with_alert(["Weird Button"])
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: t\n"
            "  steps:\n    - wait: { until: settled, timeout: 0.1 }\n"
            "  expect:\n    - exists: { id: later }\n"
        )[0],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    assert (result.failure or "").startswith("expect: ")
    assert "an unhandled system alert is blocking the screen (buttons: Weird Button)" in (
        result.failure or ""
    )


def test_a_note_from_a_cleared_stacked_expect_call_still_reaches_the_scenario_s_own_failure() -> (
    None
):
    # The `expect` twin of `test_a_note_from_a_cleared_stacked_call_still_reaches_the_step_s_own_
    # failure`: `expect_block_note` must reach the scenario's own failure even though the guard's
    # call cleared something, since a multi-round call can clear a stacked alert while leaving a
    # second one unhandled — a regression that re-gates the note on `cleared` would pass every other
    # `expect` test here (all single-alert) yet silently drop this one's note.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = [_button("Weird Button")]

    # A trivially-passing step (its target is already on screen), so the alert reaches `expect`'s
    # own guard call untouched — a `wait` step here would let its own mid-wait gate dismiss "Allow"
    # first, attributing it to the step instead of to `expect`.
    real_button = _button("Real")
    real_button["identifier"] = "real"
    driver = _fake_with_alert(["Allow"], react=react)
    driver.screen = [real_button]
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: t\n  steps:\n    - tap: { id: real }\n  expect:\n    - exists: { id: later }\n"
        )[0],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    assert (result.failure or "").startswith("expect: ")
    assert "an unhandled system alert is blocking the screen (buttons: Weird Button)" in (
        result.failure or ""
    )
    assert result.expect_alerts == [AlertEvent(label="Allow")]  # the first alert did clear


def test_a_step_failing_with_no_alert_up_keeps_its_own_bare_reason() -> None:
    # The note is not a blanket suffix on every failure: with no alert on screen there is nothing to
    # report, and a step that failed for its own reasons must not be made to look blocked.
    from bajutsu.common.orchestrator import run_scenario
    from bajutsu.common.scenario import load_scenarios

    result = run_scenario(
        FakeDriver([]),
        load_scenarios("- name: t\n  steps:\n    - tap: { id: go }\n")[0],
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
    )
    assert not result.ok
    assert "system alert" not in (result.steps[0].reason or "")


def test_the_decline_give_up_follows_a_tuned_poll_interval() -> None:
    # The bound is checked *before* the tap, so a horizon shorter than two intervals is spent on the
    # first attempt and the label is never re-tapped. A scenario that raises `pollInterval` — the
    # save-password one sets 5 — would hit exactly that with a fixed 2s horizon, which is the
    # zero-retry case the value exists to avoid. Deriving it from the interval keeps the rationale
    # true at every cadence, and the floor keeps the animation horizon at short ones.
    from bajutsu.common.orchestrator.waits import _decline_giveup

    assert _decline_giveup(0.2) == 2.0  # floored at the presentation-animation horizon
    assert _decline_giveup(1.0) == 2.0  # the default cadence: two intervals is the floor exactly
    assert _decline_giveup(5.0) == 10.0  # a tuned interval still buys a retry


def test_probe_native_reports_an_ambiguous_alert_as_unhandled_not_absent() -> None:
    # `AmbiguousSelector` from the tap is not the same race as `ElementNotFound`: the alert is still
    # up, now offering the label twice. Calling that "absent" would tell `_observe_native` no system
    # alert is showing, which is the one thing licensing an in-tree tap — and a tap made under a live
    # alert is what XCUITest answers with its own default button.
    class _AmbiguousOnTap(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.AmbiguousSelector("the alert offers this label twice")

    driver = _AmbiguousOnTap([])
    driver.system_alert_buttons = [_button("Don't Allow"), _button("Allow")]
    guard = AlertGuardConfig(rules=[guard_rule("Don't Allow")])
    assert guard.probe_native(driver) == ("unhandled", None, ["Don't Allow", "Allow"])


def test_probe_native_still_reports_a_vanished_alert_as_absent() -> None:
    # The other half of the same race keeps its answer: the alert really did go away between the
    # presence query and the tap, so that one alert is no longer blocking. The original, non-empty
    # read still comes back alongside "absent" (BE-0418 review finding) — and is what now withholds
    # the in-tree path rather than licensing it: both consumers require an empty read (`__call__`'s
    # `if not buttons`, `_observe_native`'s `state == "absent" and not buttons`).
    class _VanishedOnTap(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the alert vanished")

    driver = _VanishedOnTap([])
    driver.system_alert_buttons = [_button("Don't Allow"), _button("Allow")]
    guard = AlertGuardConfig(rules=[guard_rule("Don't Allow")])
    assert guard.probe_native(driver) == ("absent", None, ["Don't Allow", "Allow"])

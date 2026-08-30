"""Tests for the reactive native system-alert guard path (BE-0315).

The reactive guard clears SpringBoard prompts automatically, preferring a deterministic native path
built on BE-0316's primitives — `system_alert_labels()` (a read of BE-0316's `/systemAlert/query`)
to see the alert's buttons, then `handle_system_alert()` to tap a policy-named one — over the vision
fallback. Exercised against `FakeDriver`, which advertises `HANDLE_SYSTEM_ALERT` and can be seeded
with alert buttons, so nothing here needs a Simulator; the on-device confirmation is a separate lane.
"""

from __future__ import annotations

import logging
from typing import cast

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import AlertEvent, AlertGuardConfig
from bajutsu.orchestrator.types import (
    DEFAULT_DISMISSIVE_LABELS,
    ResolvedAlertRule,
    match_alert_rule,
    pick_alert_label,
)
from bajutsu.scenario import Wait


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


def _never_vision(_driver: base.Driver) -> AlertEvent | None:
    raise AssertionError("the vision fallback must not be called on the native path")


# --- pick_alert_label -------------------------------------------------------------------------------


def test_pick_alert_label_returns_first_uniquely_present_candidate() -> None:
    assert pick_alert_label(["Allow", "OK"], ["Don't Allow", "Allow"]) == "Allow"
    assert pick_alert_label(["Grant", "OK"], ["Cancel", "OK"]) == "OK"


def test_pick_alert_label_none_when_no_candidate_present() -> None:
    assert pick_alert_label(["Allow"], ["Cancel", "Close"]) is None


def test_pick_alert_label_none_on_an_empty_button_list() -> None:
    assert pick_alert_label(["Allow", "OK"], []) is None


def test_pick_alert_label_skips_an_ambiguous_candidate() -> None:
    # A label present twice cannot resolve to one button, so it is skipped rather than tapping
    # whichever matched first (determinism first, mirroring resolve_unique).
    assert pick_alert_label(["OK", "Cancel"], ["OK", "OK", "Cancel"]) == "Cancel"
    assert pick_alert_label(["OK"], ["OK", "OK"]) is None


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


def test_match_alert_rule_requires_each_identifying_label_exactly_once() -> None:
    # Two buttons carrying the same label cannot uniquely identify the prompt, mirroring
    # pick_alert_label's own exactly-once rule.
    assert match_alert_rule([_NOTIF_RULE], ["Allow", "Allow", "Don't Allow"]) is None


# --- AlertGuardConfig.probe_native ------------------------------------------------------------------


def test_probe_native_incapable_backend() -> None:
    guard = AlertGuardConfig(vision=_never_vision)
    assert guard.probe_native(_Incapable()) == ("incapable", None)  # type: ignore[arg-type]


def test_probe_native_absent_when_no_alert() -> None:
    guard = AlertGuardConfig(vision=_never_vision)
    assert guard.probe_native(FakeDriver([])) == ("absent", None)  # capable, but no alert seeded


def test_probe_native_dismisses_a_named_button() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Allow")
    # It tapped through BE-0316's handle_system_alert with the picked label.
    assert ("handle_system_alert", ({"label": "Allow"}, 0.0)) in driver.actions


def test_probe_native_uses_default_dismissive_labels_when_none_configured() -> None:
    guard = AlertGuardConfig(vision=_never_vision)  # no labels → default dismissive policy
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event is not None and event.label == "Don't Allow"
    assert event.label in DEFAULT_DISMISSIVE_LABELS


def test_probe_native_prefers_a_matching_rule_over_the_candidate_labels() -> None:
    # A rule identifying the tracking prompt taps its own choice, even though "Allow" is also a
    # candidate label that would otherwise resolve first via pick_alert_label.
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"], rules=[_TRACKING_RULE])
    driver = _fake_with_alert(["Allow", "Ask App Not to Track"])
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Ask App Not to Track")


def test_probe_native_falls_back_to_labels_when_no_rule_matches() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"], rules=[_TRACKING_RULE])
    driver = _fake_with_alert(
        ["Don't Allow", "Allow"]
    )  # notifications prompt: no rule identifies it
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Allow")


def test_probe_native_unhandled_when_no_candidate_resolves() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    driver = _fake_with_alert(["Weird Button"])
    assert guard.probe_native(driver) == ("unhandled", None)


def test_probe_native_treats_a_dismiss_race_as_absent() -> None:
    # TOCTOU: the alert vanishes between the presence query and the tap, so handle_system_alert
    # raises ElementNotFound. That is a benign self-resolved race — reported as absent, not a failure.
    class _RaceDriver(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the alert vanished before the tap")

    driver = _RaceDriver([])
    driver.system_alert_buttons = [_button("Allow")]
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    assert guard.probe_native(driver) == ("absent", None)


# --- AlertGuardConfig.__call__ (native-first, vision fallback) --------------------------------------


def test_call_returns_native_event_without_touching_vision() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    assert guard(_fake_with_alert(["Allow"])) == AlertEvent(label="Allow")


def test_call_falls_back_to_vision_when_native_cannot_act() -> None:
    calls = {"n": 0}

    def vision(_driver: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        return AlertEvent(label="vision")

    guard = AlertGuardConfig(vision=vision, labels=["Allow"])
    assert guard(_fake_with_alert(["Weird Button"])) == AlertEvent(
        label="vision"
    )  # unhandled → vision
    assert calls["n"] == 1


def test_call_falls_back_to_vision_on_an_incapable_backend() -> None:
    def vision(_driver: base.Driver) -> AlertEvent | None:
        return AlertEvent(label="vision")

    guard = AlertGuardConfig(vision=vision)
    assert guard(FakeDriver([])) == AlertEvent(label="vision")


# --- the mid-wait gate on the native path -----------------------------------------------------------


def _for_wait(target_id: str, timeout: float) -> Wait:

    return Wait.model_validate({"for": {"id": target_id}, "timeout": timeout})


def test_gate_dismisses_natively_mid_wait_and_records_the_alert() -> None:
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []  # the alert cleared
            d.screen = [target]  # and the awaited element is revealed

    driver = _fake_with_alert(["Allow"], react=react)
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)
    ok, reason, _tree = _wait(
        _OneFrameCollapse(), _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert ok and reason == ""  # revealed after one transient collapse; vision never called


def test_gate_absent_native_alert_still_drives_vision_for_a_persistent_collapse() -> None:
    # "absent" only rules out a *SpringBoard* alert: an action sheet or a WKWebView JS dialog the
    # native query cannot enumerate reads as absent too while still collapsing the tree. A persistent
    # collapse under "absent" must still drive the debounced vision fallback mid-wait — restoring the
    # BE-0269 recovery the native path would otherwise skip on the now-default backend (PR #1330).
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    calls = {"n": 0}

    def vision(d: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        assert isinstance(d, FakeDriver)
        d.screen = [target]  # vision clears the non-SpringBoard surface the native query cannot see
        return AlertEvent(label="Dismiss")

    driver = FakeDriver(
        []
    )  # capable, collapsed, no SpringBoard alert seeded → probe returns "absent"
    # poll_interval=1.0 (the realistic default) but a tight 2s budget: the collapsed-tree debounce must
    # sample every _POLL, not once per interval, or vision would first fire at ~3x poll_interval and
    # miss this budget entirely (the regression this locks in). It recovers within a few _POLL ticks.
    guard = AlertGuardConfig(vision=vision, poll_interval=1.0)
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 2.0), clock, alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert (
        clock.now() < 1.0
    )  # recovered at proxy latency, not throttled to the native-probe cadence
    assert calls["n"] == 1  # the vision fallback fired mid-wait for the non-SpringBoard collapse
    assert alerts == [AlertEvent(label="Dismiss")]


def test_gate_polls_the_native_query_on_its_own_interval_not_every_tick() -> None:
    # The native query is rate-limited to one per poll_interval, decoupled from the 50ms condition
    # poll, so it does not roughly double the single-threaded runner's load (BE-0315).
    from bajutsu.orchestrator.waits import _wait

    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def system_alert_labels(self) -> list[str]:
            probes["n"] += 1
            return []  # never an alert, so the wait runs to its full budget

    # App UI is visible (a non-collapsed tree), so the collapsed-tree vision fallback never enters —
    # this isolates the native probe's cadence from the "absent + collapsed" fallback path.
    app_ui = _button("home")
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _wait

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
        vision=_never_vision, labels=["今はしない", "Not Now"], poll_interval=1.0
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _TREE_DISMISS_MAX_TAPS, _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    # A 30s budget, far longer than `_TREE_DISMISS_MAX_TAPS` taps spaced by `_TREE_RETAP_DELAY`, so
    # what stops the retries here is the tap ceiling rather than the wait running out: no retry at all
    # taps exactly once, an unbounded one ~30 times over this wait, and only the bound gives 3.
    with caplog.at_level(logging.WARNING, logger="bajutsu.orchestrator.waits"):
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


def test_dismiss_from_tree_retry_that_lands_clears_the_prompt_and_passes_the_wait() -> None:
    # The payoff the retry exists for, which the never-clears test above cannot show: the second tap
    # *does* land, so the wait passes at retry latency instead of burning its timeout. Without this,
    # a change that left the retry inert — an off-by-one in the ceiling, a delay that never elapses
    # under the real clock — would keep every other in-tree test green while the sheet stays up on
    # device, since they all either tap once successfully or never clear at all.
    from bajutsu.orchestrator.waits import _TREE_RETAP_DELAY, _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _POLL, _wait

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
        vision=_never_vision, labels=["今はしない", "あとで"], poll_interval=1.0
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _decline_giveup, _wait

    prompt_button = _button("今はしない")

    class _PermanentlyCoveredDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            raise base.ElementNotTappable("covered by a scrim that never lifts")

    driver = _PermanentlyCoveredDismiss()
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 3.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # "ready" never appears; the wait times out on its own deadline
    # Attempted at t=0 and t=1.0 (one `poll_interval` apart), then declined outright at t=2.0 once
    # the give-up horizon is reached — bounded, not one attempt per poll for the whole 3s wait.
    assert driver.tap_calls == 2
    assert _decline_giveup(1.0) == 2.0  # the horizon the count above is derived from


def test_dismiss_from_tree_declines_on_an_in_app_label_collision() -> None:
    # A system-owned identifier-less button and an app-authored one share a configured label:
    # pick_alert_label resolves uniquely over the identifier-less subset, but the whole-tree tap
    # sees both and must decline rather than tap the wrong one (determinism first). A *persistent*
    # collision (unlike a vanish race) must decline before ever attempting the tap: the collision
    # never clears, so `_tree_dismiss_pending` (only armed on a successful tap) never guards it, and
    # a decline reached only via `except AmbiguousSelector` would re-issue the on-device tap every
    # `_POLL` for the rest of the wait — count `tap()` calls directly, not `driver.actions`, since a
    # raised `AmbiguousSelector` never reaches `_record` either way.
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
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
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"  # an app-authored button, not a prompt

    driver = FakeDriver([app_button])  # capable; no SpringBoard alert seeded; never reveals "ready"
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # the in-app button was never tapped, so "ready" never appears
    assert driver.actions == []  # confirms no tap was issued against it


def test_dismiss_from_tree_never_fires_on_a_non_native_backend() -> None:
    # The in-tree match is native-only (the app-attached-sheet case it targets is an iOS one): a
    # web/Android-shaped backend must keep its pre-existing behavior untouched — only the
    # collapsed-tree + vision path (BE-0269) can act there, never the fast in-tree tap.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "今はしない"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = _NonNativeDriver([prompt_button], react=react)
    guard = AlertGuardConfig(
        vision=_never_vision, labels=["今はしない", "Not Now"], poll_interval=1.0
    )
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # never tapped, so "ready" never appears; the wait runs to its full budget
    assert driver.actions == []


def test_dismiss_from_tree_never_fires_on_default_dismissive_labels_alone() -> None:
    # Without an explicit `systemAlertHandling.instruction`, only `DEFAULT_DISMISSIVE_LABELS`
    # applies — generic English UI vocabulary ("Cancel", "Close", …) a real app screen can
    # legitimately show. The fast in-tree path must stay off unless the scenario author opted in
    # with their own `labels`; a default-only guard falls back to the collapsed-tree + vision path,
    # same as before this path existed.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    cancel_button = _button("Cancel")  # identifier-less; matches DEFAULT_DISMISSIVE_LABELS

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Cancel"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = FakeDriver([cancel_button], react=react)  # native-capable; no labels configured
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)  # labels=[] (default)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert driver.actions == []


def test_gate_unhandled_native_alert_falls_back_to_vision_bounded() -> None:
    # An alert is up but no policy label resolves (unknown button): the gate routes to the vision
    # fallback, bounded by the same attempt ceiling as the collapsed-tree path — never an unbounded
    # per-interval stream of AI-vision calls (BE-0315).
    from bajutsu.orchestrator.waits import _GUARD_MAX_ATTEMPTS, _wait

    calls = {"n": 0}

    def vision(_driver: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        return None  # vision can't clear it either, so the wait runs to its full budget

    driver = _fake_with_alert(["Weird Button"])  # capable; alert stays up (never dismissed)
    guard = AlertGuardConfig(vision=vision, labels=["Allow"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("never", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert calls["n"] == _GUARD_MAX_ATTEMPTS  # bounded, not one call per interval for 30s


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
    from bajutsu.orchestrator.waits import _wait

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
        vision=_never_vision, labels=["Don't Allow", "Not Now"], poll_interval=1.0
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

    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=2.0)
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
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
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

    from bajutsu.orchestrator.waits import _TREE_DISMISS_MAX_TAPS, _TREE_RETAP_DELAY, _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=0.2)
    ok, _reason, _tree = _wait(driver, _for_wait("ready", 5.0), clock, alert_guard=guard, alerts=[])
    assert not ok  # "ready" never appears; the wait times out on its own deadline
    assert len(driver.tap_times) == _TREE_DISMISS_MAX_TAPS
    gaps = [b - a for a, b in pairwise(driver.tap_times)]
    assert all(gap >= _TREE_RETAP_DELAY for gap in gaps), gaps


# --- the interruption policy pushed to the backend ---------------------------------------------


def test_push_interruption_policy_hands_the_backend_the_guard_s_own_labels() -> None:
    # The decision stays here: what the backend receives is exactly what `probe_native` would resolve
    # from — the scenario's rules, then its ordered candidates. Without this the backend answers an
    # alert that interrupts one of its own interactions with the alert's *default* button, which is
    # the opposite of the least-destructive policy and reaches no report.
    from bajutsu.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Don't Allow"
    )
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], rules=[rule])
    push_interruption_policy(driver, guard)
    assert driver.interruption_policy == ([({"Allow", "Don't Allow"}, "Don't Allow")], ["Not Now"])


def test_push_interruption_policy_falls_back_to_the_dismissive_defaults() -> None:
    # A scenario that names no labels of its own still gets a policy, and it is the same
    # least-destructive list `probe_native` falls back to — not an empty one, which would leave the
    # backend's own default handler in charge.
    from bajutsu.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, AlertGuardConfig(vision=_never_vision))
    assert driver.interruption_policy is not None
    assert driver.interruption_policy[1] == list(DEFAULT_DISMISSIVE_LABELS)


def test_push_interruption_policy_clears_it_when_the_scenario_disables_the_guard() -> None:
    # `systemAlertHandling: false` must not inherit the previous scenario's policy from the resident
    # runner, so the push happens with an empty policy rather than being skipped.
    from bajutsu.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, None)
    assert driver.interruption_policy == ([], [])


def test_drain_interruptions_reports_what_the_backend_answered_as_alert_events() -> None:
    # A prompt answered inside the backend's interruption handling is still a prompt this run
    # dismissed; reporting it is what keeps that dismissal out of the silence the mechanism exists
    # to end.
    from bajutsu.orchestrator import drain_interruptions

    driver = FakeDriver([])
    driver.interruptions_to_drain = ["Don't Allow", "Not Now"]
    assert drain_interruptions(driver) == [
        AlertEvent(label="Don't Allow"),
        AlertEvent(label="Not Now"),
    ]
    assert drain_interruptions(driver) == []  # drained, not repeated onto the next step


def test_a_gone_wait_is_guarded_so_an_in_app_prompt_can_be_cleared() -> None:
    # `gone` went unguarded on the reasoning that a blocking prompt collapses the tree, which already
    # satisfies "gone". That holds for a SpringBoard prompt and only for those: iOS's "Save Password"
    # alert is drawn in the app's own process, so it collapses nothing and *adds* its buttons to the
    # tree. A `gone` wait on one of them then sits unsatisfied for its whole timeout with nothing to
    # clear it — measured on-device before this branch was guarded.
    from bajutsu.orchestrator.waits import _wait

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
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
    from bajutsu.orchestrator import drain_interruptions, push_interruption_policy

    class _NoOptIn:
        """A backend stub carrying neither half of the opt-in (a web / Android shape)."""

    driver = cast("base.Driver", _NoOptIn())
    push_interruption_policy(driver, AlertGuardConfig(vision=_never_vision))
    assert drain_interruptions(driver) == []


# --- the end-of-step / expect guard's own in-tree dismissal --------------------------------------


def test_the_end_of_step_guard_clears_an_app_owned_prompt_from_the_tree() -> None:
    # Measured on iOS 26.3/26.4/26.5: the save-password alert can arrive *after* a scenario's last
    # wait has returned, so the only guard left to meet it is the end-of-step / expect one. Its
    # native probe reads `springboard.alerts` and sees nothing there, and the vision fallback no-ops
    # without a credential — so without this the prompt covered the screen and `expect` read a
    # covered tree.
    prompt_button = _button("Not Now")
    driver = FakeDriver([_button("Sign In"), prompt_button])
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"])
    assert guard(driver) == AlertEvent(label="Not Now")
    assert driver.actions and driver.actions[-1][0] == "tap"


def test_the_end_of_step_guard_leaves_the_tree_alone_while_a_springboard_alert_is_up() -> None:
    # The same licence the mid-wait gate needs: XCUITest answers an interrupting out-of-process alert
    # before it synthesizes any element interaction, so an app tap issued while one is up is not this
    # guard's to make. The native path answers that alert first; the tree is next time's business.
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    driver.screen = [_button("Not Now")]
    guard = AlertGuardConfig(vision=_never_vision, labels=["Don't Allow", "Not Now"])
    assert guard(driver) == AlertEvent(label="Don't Allow")  # the SpringBoard alert, natively
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_declines_an_ambiguous_in_tree_label() -> None:
    # Determinism first, exactly as the mid-wait path: two buttons carrying the configured label is
    # not a prompt this guard may guess at. It falls through to vision instead of tapping one.
    seen: list[str] = []

    def _vision(_driver: base.Driver) -> AlertEvent | None:
        seen.append("vision")
        return None

    driver = FakeDriver([_button("Not Now"), _button("Not Now")])
    guard = AlertGuardConfig(vision=_vision, labels=["Not Now"])
    assert guard(driver) is None
    assert seen == ["vision"]
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_stays_off_the_tree_without_scenario_labels() -> None:
    # The in-tree surface is armed only by the scenario's own `instruction`, never by the built-in
    # dismissive defaults — "Cancel" / "Close" are ordinary UI vocabulary a real screen can show.
    driver = FakeDriver([_button("Cancel")])
    guard = AlertGuardConfig(vision=lambda _d: None)
    assert guard(driver) is None
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_declines_when_an_identified_button_shares_the_label() -> None:
    # `pick_alert_label` resolves over the identifier-less subset, so a same-named *identified* app
    # button does not stop it — but the tap sees the whole tree and would be ambiguous. The
    # whole-tree uniqueness pre-check is what catches that, exactly as the mid-wait path's does.
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"
    driver = FakeDriver([_button("Not Now"), app_button])
    guard = AlertGuardConfig(vision=lambda _d: None, labels=["Not Now"])
    assert guard(driver) is None
    assert not any(action[0] == "tap" for action in driver.actions)


def test_the_end_of_step_guard_reports_nothing_when_the_prompt_closes_itself() -> None:
    # The button left the tree between this guard's own read and its tap. Benign: the prompt is gone,
    # which is what the caller wanted, and the step's own outcome still decides the verdict.
    class _VanishingPrompt(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            raise base.ElementNotFound("the prompt closed itself")

    driver = _VanishingPrompt([_button("Not Now")])
    guard = AlertGuardConfig(vision=lambda _d: None, labels=["Not Now"])
    assert guard(driver) is None


def test_the_decline_give_up_follows_a_tuned_poll_interval() -> None:
    # The bound is checked *before* the tap, so a horizon shorter than two intervals is spent on the
    # first attempt and the label is never re-tapped. A scenario that raises `pollInterval` — the
    # save-password one sets 5 — would hit exactly that with a fixed 2s horizon, which is the
    # zero-retry case the value exists to avoid. Deriving it from the interval keeps the rationale
    # true at every cadence, and the floor keeps the animation horizon at short ones.
    from bajutsu.orchestrator.waits import _decline_giveup

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
    guard = AlertGuardConfig(vision=_never_vision, labels=["Don't Allow"])
    assert guard.probe_native(driver) == ("unhandled", None)


def test_probe_native_still_reports_a_vanished_alert_as_absent() -> None:
    # The other half of the same race keeps its answer: the alert really did go away between the
    # presence query and the tap, so nothing is blocking and the in-tree path may proceed.
    class _VanishedOnTap(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the alert vanished")

    driver = _VanishedOnTap([])
    driver.system_alert_buttons = [_button("Don't Allow"), _button("Allow")]
    guard = AlertGuardConfig(vision=_never_vision, labels=["Don't Allow"])
    assert guard.probe_native(driver) == ("absent", None)

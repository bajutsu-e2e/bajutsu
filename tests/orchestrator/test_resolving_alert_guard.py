"""The guard on a backend that answers alerts inside its own reads (BE-0461 Units 4 and 5).

Driven through `run_scenario` against `ResolvingFakeDriver`, the fake that models the runner's
`AlertResolver`: it presses what the pushed policy names inside `query_resolving`, and a test that
needs a reply the model does not produce (`withheld`, a give-up) queues it.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from _orch import FakeClock
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver, ResolvingFakeDriver
from bajutsu.common.orchestrator import AlertEvent, AlertGuardConfig, RunResult, run_scenario
from bajutsu.common.orchestrator.types import (
    ResolvedAlertRule,
    alert_block_note,
    push_interruption_policy,
    uncleared_prompt_note,
)
from bajutsu.common.scenario import load_scenarios

_NOTIFICATIONS = ResolvedAlertRule(
    identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Don't Allow"
)
_SAVE_PASSWORD = ResolvedAlertRule(
    identifying_labels=frozenset({"Save Password", "Not Now"}),
    tap_label="Not Now",
    native=False,
    in_tree=True,
)


def _button(label: str) -> base.Element:
    return el(label=label, traits=[base.Trait.BUTTON])


def _alert(*labels: str) -> list[base.Element]:
    return [_button(label) for label in labels]


def _guard(*rules: ResolvedAlertRule, frozen: float = 10.0) -> AlertGuardConfig:
    return AlertGuardConfig(rules=list(rules), frozen_screen_timeout=frozen)


def _driver(
    screen: list[base.Element],
    guard: AlertGuardConfig,
    react: Callable[[FakeDriver, str, object], None] | None = None,
) -> ResolvingFakeDriver:
    driver = ResolvingFakeDriver(screen=screen, react=react)
    push_interruption_policy(driver, guard, scenario_start=True)
    return driver


def _run(driver: FakeDriver, yaml: str, guard: AlertGuardConfig | None) -> RunResult:
    return run_scenario(driver, load_scenarios(yaml)[0], clock=FakeClock(), alert_guard=guard)


def _calls(driver: FakeDriver, kind: str) -> list[object]:
    return [arg for action, arg in driver.actions if action == kind]


_WAIT_GO = "- name: t\n  steps:\n    - wait: { for: { id: go }, timeout: 2 }\n"
_TAP_GO = "- name: t\n  steps:\n    - tap: { id: go }\n"


# --- Unit 4: guarded reads resolve -------------------------------------------------------------


def test_a_guarded_wait_reads_through_the_backend_and_reports_its_answer_once() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("go", "Go")], guard)
    driver.system_alert_buttons = _alert("Allow", "Don't Allow")
    result = _run(driver, _WAIT_GO, guard)
    assert result.ok
    assert ("true", False) in _calls(driver, "query_resolving")
    assert _calls(driver, "resolve_press") == ["Don't Allow"]
    assert result.steps[0].alerts == [AlertEvent(label="Don't Allow")]


def test_a_wait_with_the_guard_off_sends_no_resolving_read() -> None:
    driver = _driver([el("go", "Go")], _guard(_NOTIFICATIONS))
    assert _run(driver, _WAIT_GO, None).ok
    assert _calls(driver, "query_resolving") == []


def test_an_action_s_selector_read_resolves_and_its_answer_reaches_the_step() -> None:
    guard = _guard(_SAVE_PASSWORD)
    sheet = _alert("Save Password", "Not Now")
    go = el("go", "Go", traits=[base.Trait.BUTTON])

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == "Not Now":
            d.screen = [go]

    driver = _driver([*sheet, go], guard, react)
    yaml = "- name: t\n  steps:\n    - swipe: { on: { id: go }, direction: up }\n"
    result = _run(driver, yaml, guard)
    assert result.ok, result.steps[0].reason
    assert _calls(driver, "query_resolving")
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_an_assert_step_keeps_the_plain_read() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("go", "Go")], guard)
    yaml = "- name: t\n  steps:\n    - assert: [{ exists: { id: go } }]\n"
    assert _run(driver, yaml, guard).ok
    # Only the scenario-entry read resolves; the assertion's own reads stay plain.
    assert _calls(driver, "query_resolving") == [("true", True)]


def test_an_unidentified_alert_names_the_timeout_and_leaves_the_wait_running() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)
    driver.system_alert_buttons = _alert("Save", "Cancel")
    result = _run(driver, _WAIT_GO, guard)
    reason = result.steps[0].reason
    assert alert_block_note(["Save", "Cancel"]) in reason
    assert "wait timeout" in reason  # the wait ran to its own deadline


def test_a_give_up_names_the_button_a_rule_chose_and_outlasts_collapsed_polls() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)
    gave_up = base.DrainedInterruptions(
        tapped=[],
        declined=[],
        banners=[],
        gave_up=[base.GaveUpAlert(["Allow", "Don't Allow"], "native", "Don't Allow")],
    )
    # The entry read, then one poll with the give-up; the later collapsed polls carry nothing and
    # never rule a SpringBoard alert out, so the alert the note names may still be up.
    nothing = base.ResolvingRead(elements=[], drained=base.DrainedInterruptions.empty())
    driver.resolving_reads = [nothing, base.ResolvingRead(elements=[], drained=gave_up)]
    driver.resolving_reads += [nothing] * 200
    result = _run(driver, _WAIT_GO, guard)
    reason = result.steps[0].reason
    assert uncleared_prompt_note("Don't Allow") in reason
    assert alert_block_note([]) not in reason


def test_the_collapsed_proxy_stops_a_wait_early_only_on_a_checked_reply() -> None:
    guard = _guard(_SAVE_PASSWORD, frozen=0.5)
    unchecked = _driver([], guard)
    unchecked.resolving_reads = [
        base.ResolvingRead(elements=[], drained=base.DrainedInterruptions.empty())
        for _ in range(200)
    ]
    assert "wait timeout" in _run(unchecked, _WAIT_GO, guard).steps[0].reason
    checked = _driver([], guard)  # the model's reads report no SpringBoard alert
    assert "wait stopped" in _run(checked, _WAIT_GO, guard).steps[0].reason


# --- Unit 3/4: the handleSystemAlert step -------------------------------------------------------

_HSA_SAVE = "- name: t\n  steps:\n    - handleSystemAlert: { sel: { label: Save }, timeout: 3 }\n"


def test_a_handle_system_alert_step_polls_in_tree_and_resolves_an_alert_not_its_own() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.system_alert_buttons = _alert("Allow", "Don't Allow")

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "resolve_press":
            d.system_alert_buttons = _alert("Save", "Cancel")

    driver._react = react
    result = _run(driver, _HSA_SAVE, guard)
    assert result.ok, result.steps[0].reason
    reads = _calls(driver, "query_resolving")
    # Every poll's tree read leaves SpringBoard to the step.
    assert reads and set(reads) == {("inTree", False)}
    assert _calls(driver, "resolve_system_alert")[0] == ["Allow", "Don't Allow"]
    assert AlertEvent(label="Don't Allow") in result.steps[0].alerts


def test_a_resolve_give_up_reaches_the_step_s_timeout() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.system_alert_buttons = _alert("Allow", "Don't Allow")
    driver.alert_resolutions = [
        base.SystemAlertResolution(
            "pending",
            base.DrainedInterruptions(
                tapped=[],
                declined=[],
                banners=[],
                gave_up=[base.GaveUpAlert(["Allow", "Don't Allow"], "native", "Don't Allow")],
            ),
        )
    ]
    driver.sticky_alert = True
    result = _run(driver, _HSA_SAVE, guard)
    reason = result.steps[0].reason
    assert "the alert on screen offered: Allow, Don't Allow" in reason
    assert uncleared_prompt_note("Don't Allow") in reason


def test_resolve_is_paced_to_the_guard_s_poll_interval() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.system_alert_buttons = _alert("Allow", "Don't Allow")
    driver.alert_resolutions = [
        base.SystemAlertResolution("pending", base.DrainedInterruptions.empty()) for _ in range(100)
    ]
    _run(driver, _HSA_SAVE, guard)
    # A 3-second step with a one-second interval asks at most four times, not on every 0.2s poll.
    assert len(_calls(driver, "resolve_system_alert")) <= 4


def test_an_unidentified_note_from_resolve_clears_once_the_step_no_longer_reads_it() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.system_alert_buttons = _alert("Weird", "Odd")
    clock = FakeClock()

    def on_sleep(t: float) -> None:
        if t > 1.5:
            driver.system_alert_buttons = []

    clock.on_sleep = on_sleep
    result = run_scenario(driver, load_scenarios(_HSA_SAVE)[0], clock=clock, alert_guard=guard)
    reason = result.steps[0].reason
    assert "no system alert appeared" in reason
    assert alert_block_note(["Weird", "Odd"]) not in reason


def test_an_in_tree_answer_sharing_the_step_s_label_never_passes_the_step() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.resolving_reads = [
        base.ResolvingRead(
            elements=[el("home", "Home")],
            drained=base.DrainedInterruptions(
                tapped=["Save"],
                declined=[],
                banners=[],
                tapped_alerts=[["Save", "Not Now"]],
                tapped_surfaces=["inTree"],
            ),
        )
        for _ in range(3)
    ]
    result = _run(driver, _HSA_SAVE, guard)
    assert not result.ok
    assert AlertEvent(label="Save") in result.steps[0].alerts


def test_a_step_s_own_alert_the_monitor_answered_between_polls_still_passes() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.interruptions_to_drain = ["Save"]
    driver.interruption_alerts_to_drain = [["Save", "Cancel"]]
    result = _run(driver, _HSA_SAVE, guard)
    assert result.ok, result.steps[0].reason


# --- Unit 5: the narrowed retry ------------------------------------------------------------------


def test_a_refused_tap_takes_one_forced_read_and_re_runs_on_an_answer() -> None:
    # The sheet covers the target, and no read before the tap answered it: a rate-limited check left
    # it for a later request (Unit 2, step 3). The forced recovery read answers it.
    guard = _guard(_SAVE_PASSWORD)
    go = el("go", "Go", traits=[base.Trait.BUTTON])
    sheet = _alert("Save Password", "Not Now")

    class _Deferred(ResolvingFakeDriver):
        def query_resolving(
            self, mode: base.ResolveMode, *, force_check: bool = False
        ) -> base.ResolvingRead:
            if force_check:
                return super().query_resolving(mode, force_check=True)
            self._record("query_resolving", (mode, force_check))
            return base.ResolvingRead(self.query(), base.DrainedInterruptions.empty())

        def tap(self, sel: base.Selector) -> None:
            if sel == {"id": "go"} and any(e["label"] == "Not Now" for e in self.screen):
                raise base.ElementNotTappable("covered by the sheet")
            super().tap(sel)
            if sel.get("label") == "Not Now":
                self.screen = [go]

    driver = _Deferred(screen=[go, *sheet])
    push_interruption_policy(driver, guard, scenario_start=True)
    result = _run(driver, _TAP_GO, guard)
    assert result.ok, result.steps[0].reason
    assert ("true", True) in _calls(driver, "query_resolving")
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_a_refused_tap_with_nothing_answered_does_not_re_run_but_names_the_block() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)
    driver.system_alert_buttons = _alert("Weird", "Odd")
    result = _run(driver, _TAP_GO, guard)
    reason = result.steps[0].reason
    assert alert_block_note(["Weird", "Odd"]) in reason
    assert _calls(driver, "query_resolving").count(("true", True)) == 2  # entry, then recovery


def test_a_withheld_recovery_read_settles_and_re_runs() -> None:
    guard = _guard(_NOTIFICATIONS)
    go = el("go", "Go", traits=[base.Trait.BUTTON])
    driver = _driver([], guard)
    empty = base.DrainedInterruptions.empty()

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "query_resolving" and d.actions.count(("query_resolving", ("true", True))) == 2:
            d.screen = [go]

    driver._react = react
    driver.resolving_reads = [
        base.ResolvingRead(elements=[], drained=empty),  # entry
        base.ResolvingRead(elements=[], drained=empty, withheld=True),  # the recovery
    ]
    result = _run(driver, _TAP_GO, guard)
    assert result.ok, result.steps[0].reason


def test_an_answer_the_body_s_own_read_made_licenses_the_re_run() -> None:
    guard = _guard(_SAVE_PASSWORD)
    go = el("go", "Go", traits=[base.Trait.BUTTON])
    answered = base.DrainedInterruptions(
        tapped=["Not Now"], declined=[], banners=[], tapped_surfaces=["inTree"]
    )
    driver = _driver([], guard)

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "query_resolving" and ("query_resolving", ("true", True)) in d.actions:
            d.screen = [go]

    driver._react = react
    # The swipe's own read answered a sheet that is gone by the recovery read. An in-tree-only
    # guard makes no scenario-entry read, so this is the first read of the run.
    driver.resolving_reads = [base.ResolvingRead(elements=[], drained=answered)]
    yaml = "- name: t\n  steps:\n    - swipe: { on: { id: go }, direction: up }\n"
    result = _run(driver, yaml, guard)
    assert result.ok, result.steps[0].reason


def test_a_failed_assert_takes_one_forced_read_and_re_runs_on_an_answer() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "resolve_press":
            d.screen = [el("go", "Go")]

    driver._react = react
    yaml = "- name: t\n  steps:\n    - assert: [{ exists: { id: go } }]\n"
    # The alert arrives after the entry read: only the recovery read meets it.
    clock = FakeClock()
    driver.system_alert_buttons = []
    original = driver.query_resolving

    def arrive_then_resolve(
        mode: base.ResolveMode, *, force_check: bool = False
    ) -> base.ResolvingRead:
        if len(_calls(driver, "query_resolving")) == 1:
            driver.system_alert_buttons = _alert("Allow", "Don't Allow")
        return original(mode, force_check=force_check)

    driver.query_resolving = arrive_then_resolve  # type: ignore[method-assign]
    result = run_scenario(driver, load_scenarios(yaml)[0], clock=clock, alert_guard=guard)
    assert result.ok, result.steps[0].reason
    assert _calls(driver, "resolve_press") == ["Don't Allow"]


def test_a_failed_wait_takes_no_end_of_step_recovery() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)
    _run(driver, _WAIT_GO, guard)
    assert ("true", True) in _calls(driver, "query_resolving")  # the entry read
    assert _calls(driver, "query_resolving").count(("true", True)) == 1


def test_a_label_named_handle_system_alert_step_takes_no_recovery() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    _run(driver, _HSA_SAVE, guard)
    assert ("true", True) not in _calls(driver, "query_resolving")


def test_the_guard_s_own_call_never_runs_under_the_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_a: object, **_k: object) -> bool:
        raise AssertionError("AlertGuardConfig.__call__ ran under RESOLVE_ALERTS")

    monkeypatch.setattr(AlertGuardConfig, "__call__", refuse)
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)
    yaml = "- name: t\n  steps:\n    - tap: { id: go }\n  expect: [{ exists: { id: go } }]\n"
    assert not _run(driver, yaml, guard).ok
    yaml_expect = "- name: t\n  steps: []\n  expect: [{ exists: { id: go } }]\n"
    assert not _run(_driver([], guard), yaml_expect, guard).ok


def test_a_failed_expect_recovers_once_and_its_drain_reports_the_answer() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([], guard)

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "resolve_press":
            d.screen = [el("go", "Go")]

    driver._react = react
    original = driver.query_resolving

    def arrive_then_resolve(
        mode: base.ResolveMode, *, force_check: bool = False
    ) -> base.ResolvingRead:
        # A scenario with no steps makes no entry read: the recovery read is the first.
        if not _calls(driver, "query_resolving"):
            driver.system_alert_buttons = _alert("Allow", "Don't Allow")
        return original(mode, force_check=force_check)

    driver.query_resolving = arrive_then_resolve  # type: ignore[method-assign]
    yaml = "- name: t\n  steps: []\n  expect: [{ exists: { id: go } }]\n"
    result = _run(driver, yaml, guard)
    assert result.ok
    assert AlertEvent(label="Don't Allow") in result.expect_alerts
    assert driver.drain_interruptions().tapped == []  # nothing left for the next scenario


def test_the_scenario_entry_read_answers_a_launch_time_prompt() -> None:
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("go", "Go")], guard)
    driver.system_alert_buttons = _alert("Allow", "Don't Allow")
    result = _run(driver, _WAIT_GO, guard)
    assert _calls(driver, "query_resolving")[0] == ("true", True)
    assert result.steps[0].alerts == [AlertEvent(label="Don't Allow")]


def test_a_failed_position_rule_step_takes_one_forced_read_for_its_note() -> None:
    # A position rule (BE-0445) waits without the gate, so the end-of-step site is the only place a
    # failed one learns what blocked the screen.
    guard = _guard(_NOTIFICATIONS)
    driver = _driver([el("home", "Home")], guard)
    driver.system_alert_buttons = _alert(
        "Einmal erlauben", "Beim Verwenden erlauben", "Nicht erlauben"
    )
    yaml = (
        "- name: t\n  steps:\n"
        "    - handleSystemAlert: { prompt: notifications, choice: grant, timeout: 2 }\n"
    )
    result = run_scenario(
        driver, load_scenarios(yaml)[0], clock=FakeClock(), alert_guard=guard, locale="de_DE"
    )
    assert not result.ok
    assert _calls(driver, "query_resolving") == [("true", True)]
    assert alert_block_note(["Einmal erlauben", "Beim Verwenden erlauben", "Nicht erlauben"]) in (
        result.steps[0].reason or ""
    )


def test_the_tipkit_retry_s_re_run_still_reads_through_the_backend() -> None:
    scrim, container = "tip.scrim", "tip.container"
    guard = _guard(_NOTIFICATIONS)

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == scrim:
            d.screen = [el("go", "Go")]

    driver = _driver([el(scrim, "dismiss popup"), el(container)], guard, react)
    driver.tipkit_dismiss_id, driver.tipkit_container_id = scrim, container
    # A swipe, not a wait: a wait's mid-wait hook would clear the tip itself, while a failed swipe
    # reaches the end-of-step TipKit retry, whose re-run resolves its target again.
    yaml = (
        "- name: t\n  iosTipKitHandling: true\n  steps:\n"
        "    - swipe: { on: { id: go }, direction: up }\n"
    )
    result = _run(driver, yaml, guard)
    assert result.ok, result.steps[0].reason
    assert _calls(driver, "tap")[0] == {"id": scrim}
    reads = _calls(driver, "query_resolving")
    tip_tap = driver.actions.index(("tap", {"id": scrim}))
    assert ("query_resolving", ("true", False)) in driver.actions[tip_tap:]
    assert set(reads) == {("true", True), ("true", False)}


# --- the gate's own lift rules ---------------------------------------------------------------------

_SAVE_PASSWORD_WIDE = ResolvedAlertRule(
    identifying_labels=frozenset({"Save Password", "Not Now", "Never for This Website"}),
    tap_label="Not Now",
    native=False,
    in_tree=True,
)


def _gate(guard: AlertGuardConfig) -> tuple[object, ResolvingFakeDriver]:
    from bajutsu.common.orchestrator.waits import ResolvingReads, _ResolvingAlertGate

    driver = _driver([], guard)
    return _ResolvingAlertGate(clock=FakeClock(), guard=guard, reads=ResolvingReads(driver)), driver


def _gave_up(identify: list[str], surface: str) -> base.DrainedInterruptions:
    return base.DrainedInterruptions(
        tapped=[],
        declined=[],
        banners=[],
        gave_up=[base.GaveUpAlert(identify, surface, "Not Now")],  # type: ignore[arg-type]
    )


def test_an_in_tree_give_up_holds_while_a_nesting_shape_shows_and_lifts_once_it_is_gone() -> None:
    from bajutsu.common.orchestrator.waits import _ResolvingAlertGate

    guard = _guard(_SAVE_PASSWORD_WIDE, _SAVE_PASSWORD)
    gate, _ = _gate(guard)
    assert isinstance(gate, _ResolvingAlertGate)
    gate.note_records(_gave_up(["Save Password", "Not Now", "Never for This Website"], "inTree"))
    narrower = [el("app", "App"), *_alert("Save Password", "Not Now")]
    gate.observe(narrower)
    assert gate.blocked_note == uncleared_prompt_note("Not Now")
    gate.observe([el("app", "App"), *_alert("Not Now")])
    assert gate.blocked_note == ""


def test_an_in_tree_give_up_survives_a_collapsed_read() -> None:
    from bajutsu.common.orchestrator.waits import _ResolvingAlertGate

    gate, _ = _gate(_guard(_SAVE_PASSWORD))
    assert isinstance(gate, _ResolvingAlertGate)
    gate.note_records(_gave_up(["Save Password", "Not Now"], "inTree"))
    for _ in range(5):
        gate.observe([])
    assert gate.blocked_note == uncleared_prompt_note("Not Now")


def test_a_native_note_lifts_on_a_checked_read_and_not_on_an_unchecked_one() -> None:
    from bajutsu.common.orchestrator.waits import _ResolvingAlertGate

    gate, driver = _gate(_guard(_NOTIFICATIONS))
    assert isinstance(gate, _ResolvingAlertGate)
    gate.note_records(
        base.DrainedInterruptions(tapped=[], declined=[], banners=[], unidentified=[["A", "B"]])
    )
    gate.reads.read("inTree")  # an in-tree read checks nothing
    gate.observe([])
    assert gate.blocked_note == alert_block_note(["A", "B"])
    gate.reads.read("true")  # the model's clear screen: checked
    gate.observe([el("app", "App")])
    assert gate.blocked_note == ""
    assert driver is not None

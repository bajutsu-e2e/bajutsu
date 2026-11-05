"""Every guarded wait branch reads through the backend's alert resolution (BE-0461 Unit 4).

`_wait` and `wait_for_system_alert` driven directly against `ResolvingFakeDriver`, so each branch's
gate is seen to take the reads it made: `gone`, `screenChanged`, `settled` (tree-diff and signal),
and the `handleSystemAlert` step's own edge cases.
"""

from __future__ import annotations

import pytest
from _orch import FakeClock
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver, ResolvingFakeDriver
from bajutsu.common.evidence.network import ScreenTransition
from bajutsu.common.orchestrator import AlertEvent, AlertGuardConfig, run_scenario
from bajutsu.common.orchestrator.types import (
    ResolvedAlertRule,
    alert_block_note,
    drain_actuations,
    observed_alert_labels,
    push_interruption_policy,
)
from bajutsu.common.orchestrator.waits import (
    ResolvingReads,
    _AlertGuardGate,
    _wait,
    wait_for_system_alert,
)
from bajutsu.common.scenario import Wait, load_scenarios

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


def _driver(
    screen: list[base.Element], *rules: ResolvedAlertRule, frozen: float = 10.0
) -> tuple[ResolvingFakeDriver, AlertGuardConfig]:
    guard = AlertGuardConfig(rules=list(rules), frozen_screen_timeout=frozen)
    driver = ResolvingFakeDriver(screen=screen)
    push_interruption_policy(driver, guard, scenario_start=True)
    return driver, guard


def _wait_for(spec: dict[str, object]) -> Wait:
    return Wait.model_validate(spec)


def _unidentified(*buttons: str) -> base.ResolvingRead:
    return base.ResolvingRead(
        elements=[],
        drained=base.DrainedInterruptions(
            tapped=[], declined=[], banners=[], unidentified=[list(buttons)]
        ),
    )


def test_a_gone_wait_resolves_an_in_app_prompt_and_passes() -> None:
    # An in-app prompt adds its buttons to the tree rather than collapsing it, so a `gone` wait on
    # one of them holds until something clears it: here, the wait's own read.
    sheet = [_button("Save Password"), _button("Not Now")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == "Not Now":
            d.screen = [el("app", "App")]

    driver, guard = _driver([el("app", "App"), *sheet], _SAVE_PASSWORD)
    driver._react = react
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": {"gone": {"label": "Not Now"}}, "timeout": 5}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
    )
    assert ok, reason
    assert driver.drain_interruptions().tapped == ["Not Now"]


def test_a_gone_wait_names_an_unidentified_alert_at_its_timeout() -> None:
    driver, guard = _driver([el("spinner", "Loading")], _NOTIFICATIONS)
    spinner = _unidentified("Weird", "Odd")
    driver.resolving_reads = [
        base.ResolvingRead(elements=[el("spinner", "Loading")], drained=spinner.drained)
        for _ in range(400)
    ]
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": {"gone": {"id": "spinner"}}, "timeout": 2}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
    )
    assert not ok
    assert alert_block_note(["Weird", "Odd"]) in reason


def test_a_screen_changed_wait_stops_on_a_frozen_screen_a_read_checked() -> None:
    driver, guard = _driver([], _SAVE_PASSWORD, frozen=0.5)
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": "screenChanged", "timeout": 5}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
    )
    assert not ok
    assert reason.startswith("wait stopped: screenChanged")


def test_a_screen_changed_wait_runs_its_interrupt_hook_and_honors_cancel() -> None:
    driver, guard = _driver([el("app", "App")], _NOTIFICATIONS)
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": "screenChanged", "timeout": 5}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
        on_interrupt_poll=lambda _tree: True,
    )
    assert (ok, reason) == (False, "interrupt recovery failed")
    from bajutsu.common.cancellation import RunCancelled

    with pytest.raises(RunCancelled):
        _wait(
            driver,
            _wait_for({"until": "screenChanged", "timeout": 5}),
            FakeClock(),
            alert_guard=guard,
            resolving=ResolvingReads(driver),
            cancelled=lambda: True,
        )


def test_a_screen_changed_wait_times_out_naming_the_block() -> None:
    driver, guard = _driver([el("app", "App")], _NOTIFICATIONS)
    driver.resolving_reads = [
        base.ResolvingRead(
            elements=[el("app", "App")],
            drained=base.DrainedInterruptions(
                tapped=[], declined=[], banners=[], unidentified=[["Weird"]]
            ),
        )
        for _ in range(400)
    ]
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": "screenChanged", "timeout": 1}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
        on_tick=lambda _remaining: None,
    )
    assert not ok
    assert alert_block_note(["Weird"]) in reason


def test_a_settled_wait_reads_through_the_backend_and_runs_its_interrupt_hook() -> None:
    driver, guard = _driver([el("app", "App")], _NOTIFICATIONS)
    ok, _, _ = _wait(
        driver,
        _wait_for({"until": "settled", "timeout": 5}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
    )
    assert ok
    assert ("query_resolving", ("true", False)) in driver.actions
    ok, reason, _ = _wait(
        driver,
        _wait_for({"until": "settled", "timeout": 5}),
        FakeClock(),
        alert_guard=guard,
        resolving=ResolvingReads(driver),
        on_interrupt_poll=lambda _tree: True,
    )
    assert (ok, reason) == (False, "interrupt recovery failed")


def test_a_settled_wait_on_the_transition_signal_reads_through_the_backend() -> None:
    driver, guard = _driver([el("app", "App")], _NOTIFICATIONS)
    clock = FakeClock()
    ok, _, _ = _wait(
        driver,
        _wait_for({"until": "settled", "timeout": 5}),
        clock,
        alert_guard=guard,
        resolving=ResolvingReads(driver),
        transitions=lambda: [(ScreenTransition(kind="screenChanged"), clock.now())],
        on_tick=lambda _remaining: None,
    )
    assert ok
    assert driver.actions.count(("query_resolving", ("true", False))) >= 2


def test_a_request_wait_ticks_while_it_waits() -> None:
    ticks: list[float] = []
    driver, _ = _driver([], _NOTIFICATIONS)
    ok, _, _ = _wait(
        driver,
        _wait_for({"until": {"request": {"method": "GET", "path": "/x"}}, "timeout": 0.2}),
        FakeClock(),
        on_tick=ticks.append,
    )
    assert not ok
    assert ticks


# --- the handleSystemAlert step's edge cases ---------------------------------------------------------


def test_a_backend_without_system_alerts_refuses_the_step_at_once() -> None:
    class _NoAlerts(FakeDriver):
        # A backend that neither advertises the capability nor raises on the call itself: the
        # step still refuses rather than passing a step nothing answered.
        def capabilities(self) -> set[str]:
            return super().capabilities() - {base.Capability.HANDLE_SYSTEM_ALERT}

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            return None

    with pytest.raises(base.UnsupportedAction):
        wait_for_system_alert(_NoAlerts([]), {"label": "Allow"}, 1.0, FakeClock())


def test_a_monitor_answer_the_step_did_not_see_still_passes_and_is_recorded() -> None:
    driver, guard = _driver([el("home", "Home")], _NOTIFICATIONS)
    driver.interruptions_to_drain = ["Allow"]
    alerts: list[AlertEvent] = []
    tapped: list[str] = []
    ok, _ = wait_for_system_alert(
        driver,
        {"label": "Allow"},
        1.0,
        FakeClock(),
        alert_guard=guard,
        alerts=alerts,
        tapped=tapped,
        resolving=ResolvingReads(driver),
    )
    assert ok
    assert alerts == [AlertEvent(label="Allow")]
    assert tapped == ["Allow"]


def test_an_alert_whose_title_rules_out_the_step_s_prompt_is_named_as_another_prompt() -> None:
    driver, guard = _driver([el("home", "Home")], _NOTIFICATIONS)
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    driver.sticky_alert = True
    ok, reason = wait_for_system_alert(
        driver,
        {"label": "Allow"},
        0.5,
        FakeClock(),
        alert_guard=guard,
        title_check=lambda _observed: False,
        resolving=ResolvingReads(driver),
    )
    assert not ok
    assert "its title is not the step's prompt" in reason


# --- the expect recovery and the gate's own lift rules -----------------------------------------------


def test_a_failed_expect_on_a_backend_without_the_capability_takes_no_recovery() -> None:
    driver = FakeDriver([])
    yaml = "- name: t\n  steps: []\n  expect: [{ exists: { id: go } }]\n"
    result = run_scenario(
        driver,
        load_scenarios(yaml)[0],
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(rules=[_NOTIFICATIONS]),
    )
    assert not result.ok
    assert result.failure == "expect: expected present but was absent"


def test_a_failed_expect_names_what_the_recovery_read_found() -> None:
    driver, guard = _driver([], _NOTIFICATIONS)
    driver.resolving_reads = [_unidentified("Weird", "Odd")]
    yaml = "- name: t\n  steps: []\n  expect: [{ exists: { id: go } }]\n"
    result = run_scenario(driver, load_scenarios(yaml)[0], clock=FakeClock(), alert_guard=guard)
    assert not result.ok
    assert result.failure is not None
    assert alert_block_note(["Weird", "Odd"]) in result.failure


def test_a_native_give_up_lifts_on_a_checked_read_and_on_the_step_s_own_read() -> None:
    gave_up = base.DrainedInterruptions(
        tapped=[],
        declined=[],
        banners=[],
        gave_up=[base.GaveUpAlert(["Allow", "Don't Allow"], "native", "Don't Allow")],
    )
    driver, guard = _driver([], _NOTIFICATIONS)
    reads = ResolvingReads(driver)
    gate = _AlertGuardGate(clock=FakeClock(), guard=guard, reads=reads)
    gate.note_records(gave_up)
    reads.read("true")  # the model's clear screen: checked
    gate.observe([el("app", "App")])
    assert gate.blocked_note == ""
    gate.note_records(gave_up)
    gate.note_step_read(["Allow", "Don't Allow"], own=False)
    assert gate.blocked_note != ""
    gate.note_step_read([], own=False)
    assert gate.blocked_note == ""


# --- the shared helpers the step still reads ---------------------------------------------------------


def test_observed_alert_labels_joins_each_title_as_a_marker() -> None:
    class _Titled(FakeDriver):
        def system_alert_titles(self) -> list[str]:
            return ["“Showcase” Would Like to Send You Notifications"]

    driver = _Titled([])
    driver.system_alert_buttons = [_button("Allow")]
    labels = observed_alert_labels(driver)
    assert labels[0] == "Allow"
    assert labels[1].startswith("title: ")


def test_drain_actuations_is_empty_for_a_backend_that_reports_none() -> None:
    class _Silent:
        pass

    drained = drain_actuations(_Silent(), 0.0)  # type: ignore[arg-type]
    assert (drained.records, drained.dropped) == ([], 0)

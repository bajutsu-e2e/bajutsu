"""The run loop resolving `handleSystemAlert`'s prompt/choice form against the run's locale (BE-0320).

`run_scenario` is handed the same locale the lease pinned the Simulator's system language to, so the
label the step taps is the one SpringBoard is actually rendering. These drive that end to end over
the fake driver: what gets tapped, that nesting is covered, and that an uncovered language fails the
step rather than tapping something guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import patch

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertEvent, AlertGuardConfig, run_scenario
from bajutsu.common.orchestrator.actions import handle_system_alert_selector
from bajutsu.common.scenario import ResolvedAlertShape, Scenario, Step, SystemAlertRole


def _fake_with_alert(*labels: str) -> FakeDriver:
    driver = FakeDriver([el("home.title", "home")])
    driver.system_alert_buttons = [el(None, label, ["button"]) for label in labels]
    return driver


def _grant_scenario(steps: list[dict[str, object]] | None = None) -> Scenario:
    return _scenario(
        {
            "name": "grant the prompt",
            "steps": steps
            or [
                {"handleSystemAlert": {"prompt": "notifications", "choice": "grant", "timeout": 5}}
            ],
        }
    )


def test_the_prompt_form_taps_the_label_the_locale_renders() -> None:
    driver = _fake_with_alert("許可", "許可しない")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="ja_JP")

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 0.0))]


def test_the_step_reserves_its_own_prompt_on_the_interruption_policy_while_it_waits() -> None:
    # A `handleSystemAlert` step's own prompt need not be in the scenario's own
    # `systemAlertHandling.rules` — the whole reason the step form exists. Without a reservation,
    # the runner's interruption monitor would not recognize this alert if it happened to meet it
    # first (through some earlier, unrelated action), and would decline and fail a step for it that
    # this one was a few lines away from answering correctly (BE-0406 Unit 2b review finding).
    class _RecordingDriver(FakeDriver):
        def __init__(self, screen: list[object]) -> None:
            super().__init__(screen)  # type: ignore[arg-type]
            self.policy_pushes: list[
                tuple[list[tuple[set[str], str] | tuple[set[str], str, set[str]]], bool]
            ] = []

        def set_interruption_policy(
            self, rules: Sequence[tuple[frozenset[str], str, frozenset[str]]], governs: bool
        ) -> None:
            super().set_interruption_policy(rules, governs)
            assert self.interruption_policy is not None
            self.policy_pushes.append(self.interruption_policy)

    driver = _RecordingDriver([el("home.title", "home")])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don’t Allow", ["button"]),
    ]

    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="en_US", alert_guard=AlertGuardConfig()
    )
    assert result.ok, result.failure
    # Pushed once to reserve the prompt for the step's own wait, and once more to restore the
    # scenario's steady-state policy once it is answered — never left reserved for later steps.
    assert len(driver.policy_pushes) == 2
    reserved_rules, reserved_governs = driver.policy_pushes[0]
    assert reserved_governs is True
    assert ({"Allow", "Don’t Allow"}, "Allow") in [(set(r[0]), r[1]) for r in reserved_rules]
    restored_rules, restored_governs = driver.policy_pushes[1]
    assert restored_governs is True
    assert restored_rules == []  # the scenario's own guard carried no rules of its own


def test_the_reservation_is_restored_even_when_the_step_times_out() -> None:
    # The push/restore wraps the step in a `try`/`finally` precisely so a failure or timeout still
    # restores the scenario's own policy — reserving one step's prompt must never leak into the
    # next step's or scenario's interruption policy.
    class _RecordingDriver(FakeDriver):
        def __init__(self, screen: list[object]) -> None:
            super().__init__(screen)  # type: ignore[arg-type]
            self.policy_pushes: list[
                tuple[list[tuple[set[str], str] | tuple[set[str], str, set[str]]], bool]
            ] = []

        def set_interruption_policy(
            self, rules: Sequence[tuple[frozenset[str], str, frozenset[str]]], governs: bool
        ) -> None:
            super().set_interruption_policy(rules, governs)
            assert self.interruption_policy is not None
            self.policy_pushes.append(self.interruption_policy)

    driver = _RecordingDriver([el("home.title", "home")])  # no alert ever appears

    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="en_US", alert_guard=AlertGuardConfig()
    )
    assert not result.ok  # the step times out waiting for a prompt that never appears
    assert len(driver.policy_pushes) == 2
    restored_rules, restored_governs = driver.policy_pushes[1]
    assert restored_governs is True
    assert restored_rules == []


def test_a_decline_recorded_before_the_reservation_push_is_not_silently_lost() -> None:
    # `setPolicy` clears the monitor's pending drain along with the policy it installs
    # (`InterruptionPolicyStore.setPolicy`) — so a decline an earlier, unreserved query already met
    # (the pre-step baseline capture, `before`, or `guard.clear_before_act`, all read before this
    # step's own reservation exists) would otherwise be wiped here, unread, by the very push meant
    # to start covering this step (BE-0406 Unit 2b review finding). The step still taps its own
    # prompt correctly; the unrelated, already-lost decline still fails it, exactly as any other
    # drain site would.
    driver = _fake_with_alert("許可", "許可しない")
    driver.interruptions_declined_to_drain = [["Save", "Not Now"]]

    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="ja_JP", alert_guard=AlertGuardConfig()
    )

    assert not result.ok
    assert result.failure is not None
    assert "undeclared system alert" in result.failure
    assert "Save" in result.failure
    assert "Not Now" in result.failure
    # The step's own prompt was still answered correctly — the undeclared decline is reported
    # independently of that, not instead of it.
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 0.0))]


def test_a_tap_recorded_before_the_reservation_push_is_folded_in_without_failing_the_step() -> None:
    # The mirror image of the decline case above: a *tap* an earlier, unreserved query already
    # caught is a dismissal that happened correctly on the scenario's behalf (some other declared
    # rule answered it) — it belongs on this step's own outcome, but unlike an undeclared decline it
    # is not itself a failure.
    driver = _fake_with_alert("許可", "許可しない")
    driver.interruptions_to_drain = ["Not Now"]

    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="ja_JP", alert_guard=AlertGuardConfig()
    )

    assert result.ok, result.failure
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_an_excluded_shape_is_reserved_with_its_exclusion() -> None:
    # The monitor applies exclusions now, so a shape needing one is reserved with it rather than
    # skipped: `notifications` must keep out the Local Network prompt that shares its buttons.
    driver = _fake_with_alert("Allow")

    with patch(
        "bajutsu.common.orchestrator.loop._step_runner.system_alert_shapes",
        return_value=(
            ResolvedAlertShape(
                identifying_labels=frozenset({"Allow"}),
                tap_label="Allow",
                excluded_labels=frozenset({"Never"}),
            ),
        ),
    ):
        result = run_scenario(
            driver,
            _grant_scenario(),
            clock=FakeClock(),
            locale="en_US",
            alert_guard=AlertGuardConfig(),
        )

    assert result.ok, result.failure
    # The last push restores the scenario's own (empty) policy; the reservation came before it.
    assert driver.interruption_policy == ([], True)


def test_the_same_scenario_taps_the_english_label_under_en_us() -> None:
    # The point of the form: one scenario file, two locales, no hand-typed text — and the English
    # deny label carries a typographic apostrophe no author would reliably transcribe.
    driver = _fake_with_alert("Allow", "Don’t Allow")
    result = run_scenario(
        driver,
        _grant_scenario(
            [{"handleSystemAlert": {"prompt": "notifications", "choice": "deny", "timeout": 5}}]
        ),
        clock=FakeClock(),
        locale="en_US",
    )

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "Don’t Allow"}, 0.0))]


def test_a_sel_form_is_unaffected_by_the_locale() -> None:
    # Every alert outside the covered prompts keeps naming its button literally, unchanged.
    driver = _fake_with_alert("Allow")
    result = run_scenario(
        driver,
        _grant_scenario([{"handleSystemAlert": {"sel": {"label": "Allow"}, "timeout": 5}}]),
        clock=FakeClock(),
        locale="ja_JP",
    )

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "Allow"}, 0.0))]


def test_a_nested_step_is_resolved_too() -> None:
    # Resolution sits on the loop's one step-rewrite seam, so an `if` branch — and equally a
    # `forEach` body or an interrupt's recovery — arrives resolved without its own wiring.
    driver = _fake_with_alert("許可")
    result = run_scenario(
        driver,
        _grant_scenario(
            [
                {
                    "if": {
                        "condition": {"exists": {"id": "home.title"}},
                        "then": [
                            {
                                "handleSystemAlert": {
                                    "prompt": "notifications",
                                    "choice": "grant",
                                    "timeout": 5,
                                }
                            }
                        ],
                    }
                }
            ]
        ),
        clock=FakeClock(),
        locale="ja_JP",
    )

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 0.0))]


def test_a_foreach_body_is_resolved_too() -> None:
    # The same seam covers a `forEach` body, which re-enters the step loop the way an `if` branch
    # does — worth pinning separately so a future dispatch that bypasses the seam is caught.
    driver = _fake_with_alert("許可")
    result = run_scenario(
        driver,
        _grant_scenario(
            [
                {
                    "forEach": {
                        "sel": {"id": "home.title"},
                        "as": "row",
                        "steps": [
                            {
                                "handleSystemAlert": {
                                    "prompt": "notifications",
                                    "choice": "grant",
                                    "timeout": 5,
                                }
                            }
                        ],
                    }
                }
            ]
        ),
        clock=FakeClock(),
        locale="ja_JP",
    )

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 0.0))]


@pytest.mark.usefixtures("no_position_rules")
def test_an_uncovered_language_fails_the_step_instead_of_guessing() -> None:
    # Only for a prompt with no position rule (the fixture clears them all): one that has a rule is
    # answered by position instead, below.
    driver = _fake_with_alert("Erlauben")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert result.failure is not None and "language 'de'" in result.failure
    assert driver.actions == []  # nothing was tapped
    # The failed step is still recorded, so the report and the run matrix show *which* step failed
    # rather than only that the scenario did.
    assert [(o.index, o.action, o.ok) for o in result.steps] == [(0, "handle_system_alert", False)]


@pytest.mark.usefixtures("no_position_rules")
def test_an_uncovered_language_still_reports_an_interruption_the_baseline_capture_met() -> None:
    # `UncoveredSystemAlertLocale` raises before anything actuates, but the pre-step baseline
    # capture just above it is itself a query the runner's interruption monitor can meet — the one
    # early return that used to skip the interruption drain, stranding a decline until the next
    # scenario's `setPolicy` wiped it (BE-0406 Unit 2b).
    driver = _fake_with_alert("Erlauben")
    driver.interruptions_declined_to_drain = [["Save", "Not Now"]]
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert result.failure is not None
    assert "language 'de'" in result.failure  # the original detail survives
    assert "undeclared system alert" in result.failure
    assert "Save" in result.failure
    assert "Not Now" in result.failure


@pytest.mark.usefixtures("no_position_rules")
def test_a_run_with_no_locale_fails_the_step_loudly() -> None:
    # A caller that supplies no locale (`record`'s replay) cannot know the label; for a prompt with
    # no position rule the step fails rather than being silently skipped.
    driver = _fake_with_alert("Allow")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock())

    assert not result.ok
    assert result.failure is not None and "locale" in result.failure
    assert driver.actions == []


def _answer(choice: str) -> Scenario:
    return _grant_scenario(
        [{"handleSystemAlert": {"prompt": "notifications", "choice": choice, "timeout": 5}}]
    )


@pytest.mark.parametrize(
    ("choice", "tapped", "rule"),
    [("grant", "Erlauben", "button 2 of 2"), ("deny", "Nicht erlauben", "button 1 of 2")],
)
def test_an_uncovered_language_answers_by_position(choice: str, tapped: str, rule: str) -> None:
    # BE-0445: the deny button comes first and the grant button second in every language measured,
    # so a language the label table has never seen is still answered — and the report says how.
    driver = _fake_with_alert("Nicht erlauben", "Erlauben")
    result = run_scenario(driver, _answer(choice), clock=FakeClock(), locale="de_DE")

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": tapped}, 0.0))]
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == (tapped, f"position: {rule}")


def test_a_position_rule_never_taps_an_alert_of_another_size() -> None:
    # A three-button alert is not the prompt the rule was measured on: the step waits it out and
    # names the rule and what was on screen, rather than tapping the second button of it.
    driver = _fake_with_alert("Einmal erlauben", "Beim Verwenden erlauben", "Nicht erlauben")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert driver.actions == []
    assert result.failure is not None
    assert "button 2 of 2" in result.failure
    assert "Einmal erlauben" in result.failure
    assert "add index" not in result.failure  # an `index` is no fix a rule's author can make


def test_a_position_rule_times_out_naming_itself_when_no_alert_appears() -> None:
    driver = FakeDriver([el("home.title", "home")])
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert result.failure is not None
    assert "no system alert appeared" in result.failure
    assert "button 2 of 2" in result.failure


def test_a_run_with_no_locale_answers_by_position() -> None:
    # `record`'s replay supplies no locale; the rule needs none.
    driver = _fake_with_alert("Don’t Allow", "Allow")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock())

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "Allow"}, 0.0))]


def test_an_uncovered_language_with_the_guard_on_reserves_nothing() -> None:
    # The reservation needs the prompt's labels, which only the label table knows; under a language
    # it does not cover the step behaves like a `sel`-form step rather than failing on the lookup.
    driver = _fake_with_alert("Nicht erlauben", "Erlauben")
    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="de_DE", alert_guard=AlertGuardConfig()
    )

    assert result.ok, result.failure
    # `run_scenario` pushes only the step's own reservation (the steady-state push is the
    # pipeline's), so nothing reaching the monitor here means nothing was reserved.
    assert driver.interruption_policy is None


def test_a_covered_language_reports_the_label_table_as_its_rule() -> None:
    driver = _fake_with_alert("許可しない", "許可")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="ja_JP")

    assert result.ok, result.failure
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == ("許可", "label table: ja_JP")


def test_a_sel_form_step_reports_its_own_selector_as_its_rule() -> None:
    driver = _fake_with_alert("Don’t Allow", "Allow")
    scenario = _grant_scenario([{"handleSystemAlert": {"sel": {"label": "Allow"}, "timeout": 5}}])
    result = run_scenario(driver, scenario, clock=FakeClock(), locale="en_US")

    assert result.ok, result.failure
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == ("Allow", "sel")


def test_a_position_rule_keeps_its_own_button_when_both_share_a_label() -> None:
    # The rule knows its ordinal, so two buttons reading the same text are not ambiguous to it.
    driver = FakeDriver([el("home.title", "home")])
    driver.system_alert_buttons = [
        el(None, "OK", ["button"], frame=(0.0, 0.0, 10.0, 10.0)),
        el(None, "OK", ["button"], frame=(20.0, 0.0, 10.0, 10.0)),
    ]
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert result.ok, result.failure
    assert driver.actions == [("handle_system_alert", ({"label": "OK", "index": 1}, 0.0))]


def test_a_position_rule_timeout_says_why_it_named_nothing() -> None:
    driver = _fake_with_alert("Einmal erlauben", "Beim Verwenden erlauben", "Nicht erlauben")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert result.failure is not None
    assert "offering 3" in result.failure
    assert "sel.label" in result.failure


def test_a_step_that_tapped_and_then_failed_still_records_its_tap() -> None:
    # A later check failing the step is exactly where a reader needs to know what was pressed.
    driver = _fake_with_alert("Nicht erlauben", "Erlauben")
    driver.interruptions_declined_to_drain = [["Save", "Not Now"]]
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == ("Erlauben", "position: button 2 of 2")


def test_a_monitor_tap_before_any_read_is_not_the_position_rules_own() -> None:
    # With no alert read yet the rule names no label, and nothing reserved one for the monitor, so a
    # label the monitor tapped is recorded as some other alert rather than credited to this step.
    driver = FakeDriver([el("home.title", "home")])
    driver.interruptions_to_drain = ["Erlauben"]
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert AlertEvent(label="Erlauben") in result.steps[0].alerts
    assert result.steps[0].system_alert is None


def test_a_sel_with_an_index_reports_the_button_it_tapped() -> None:
    driver = FakeDriver([el("home.title", "home")])
    driver.system_alert_buttons = [
        el(None, "Allow Once", ["button"], frame=(0.0, 0.0, 10.0, 10.0)),
        el(None, "Allow", ["button"], frame=(20.0, 0.0, 10.0, 10.0)),
    ]
    step: dict[str, object] = {
        "handleSystemAlert": {"sel": {"labelMatches": "^Allow", "index": 1}, "timeout": 5}
    }
    result = run_scenario(driver, _grant_scenario([step]), clock=FakeClock(), locale="en_US")

    assert result.ok, result.failure
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == ("Allow", "sel")


def _step(hsa: dict[str, object]) -> Step:
    return Step.model_validate({"handleSystemAlert": hsa})


def test_the_registry_path_answers_a_prompt_step_by_its_position_rule() -> None:
    # `record`'s replay dispatches through the action registry, with no locale to resolve a label.
    step = _step({"prompt": "notifications", "choice": "deny", "timeout": 0})
    assert handle_system_alert_selector(step) == SystemAlertRole(ordinal=0, count=2)
    assert handle_system_alert_selector(_step({"sel": {"label": "Allow"}, "timeout": 0})) == {
        "label": "Allow"
    }


@pytest.mark.usefixtures("no_position_rules")
def test_the_registry_path_refuses_a_prompt_with_no_position_rule() -> None:
    step = _step({"prompt": "notifications", "choice": "grant", "timeout": 0})
    with pytest.raises(base.UnsupportedAction):
        handle_system_alert_selector(step)


def test_a_position_rule_waits_without_querying_the_app_tree() -> None:
    # Under an uncovered language no guard rule can exist, so the gate's per-poll app query could
    # only meet the step's own prompt first and hand it to the unreserved monitor (BE-0445).
    class _CountingDriver(FakeDriver):
        queries = 0

        def query(self) -> list[base.Element]:
            type(self).queries += 1
            return super().query()

    driver = _CountingDriver([el("home.title", "home")])  # no alert ever appears
    run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="de_DE", alert_guard=AlertGuardConfig()
    )
    during_wait = _CountingDriver.queries
    _CountingDriver.queries = 0
    run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="en_US", alert_guard=AlertGuardConfig()
    )
    # The label path's gate queries the tree on every poll; the rule's wait does not.
    assert during_wait < _CountingDriver.queries


def test_a_tap_the_monitor_made_for_the_step_still_reports_it() -> None:
    # The interruption monitor can answer the step's own alert between two polls; the report must
    # still show which button was pressed, as it does for the step's own tap.
    driver = FakeDriver([el("home.title", "home")])
    driver.interruptions_to_drain = ["Allow"]
    step: dict[str, object] = {"handleSystemAlert": {"sel": {"label": "Allow"}, "timeout": 5}}
    result = run_scenario(driver, _grant_scenario([step]), clock=FakeClock(), locale="en_US")

    assert result.ok, result.failure
    tap = result.steps[0].system_alert
    assert tap is not None
    assert (tap.label, tap.rule) == ("Allow", "sel")


def test_a_failed_position_rule_step_still_gets_the_end_of_step_guards_note() -> None:
    # The rule's wait runs without the guard's gate, so the end-of-step guard is what names a screen
    # the step could not see past — the note a label-path step gets from its own wait.
    class _SpyGuard(AlertGuardConfig):
        calls = 0

        def __call__(self, *args: object, **kwargs: object) -> bool:
            type(self).calls += 1
            return False

    driver = _fake_with_alert("Einmal erlauben", "Beim Verwenden erlauben", "Nicht erlauben")
    result = run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="de_DE", alert_guard=_SpyGuard()
    )
    assert not result.ok
    assert _SpyGuard.calls == 1

    _SpyGuard.calls = 0
    driver = _fake_with_alert("Allow Once", "Allow While Using", "Don’t Allow")
    run_scenario(
        driver, _grant_scenario(), clock=FakeClock(), locale="en_US", alert_guard=_SpyGuard()
    )
    assert _SpyGuard.calls == 0  # the label path's own wait already drove the guard

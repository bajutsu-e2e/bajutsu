"""The run loop resolving `handleSystemAlert`'s prompt/choice form against the run's locale (BE-0320).

`run_scenario` is handed the same locale the lease pinned the Simulator's system language to, so the
label the step taps is the one SpringBoard is actually rendering. These drive that end to end over
the fake driver: what gets tapped, that nesting is covered, and that an uncovered language fails the
step rather than tapping something guessed.
"""

from __future__ import annotations

from collections.abc import Sequence

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertGuardConfig, run_scenario
from bajutsu.common.scenario import Scenario


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
            self.policy_pushes: list[tuple[list[tuple[set[str], str]], bool]] = []

        def set_interruption_policy(
            self, rules: Sequence[tuple[frozenset[str], str]], governs: bool
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
    assert (set({"Allow", "Don’t Allow"}), "Allow") in [
        (set(labels), tap) for labels, tap in reserved_rules
    ]
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
            self.policy_pushes: list[tuple[list[tuple[set[str], str]], bool]] = []

        def set_interruption_policy(
            self, rules: Sequence[tuple[frozenset[str], str]], governs: bool
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


def test_an_uncovered_language_fails_the_step_instead_of_guessing() -> None:
    driver = _fake_with_alert("Erlauben")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert result.failure is not None and "language 'de'" in result.failure
    assert driver.actions == []  # nothing was tapped
    # The failed step is still recorded, so the report and the run matrix show *which* step failed
    # rather than only that the scenario did.
    assert [(o.index, o.action, o.ok) for o in result.steps] == [(0, "handle_system_alert", False)]


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


def test_a_run_with_no_locale_fails_the_step_loudly() -> None:
    # A caller that supplies no locale (`record`'s replay) cannot know the label; the step fails
    # rather than being silently skipped.
    driver = _fake_with_alert("Allow")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock())

    assert not result.ok
    assert result.failure is not None and "locale" in result.failure
    assert driver.actions == []

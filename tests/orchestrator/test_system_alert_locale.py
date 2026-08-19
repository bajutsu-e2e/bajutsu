"""The run loop resolving `handleSystemAlert`'s prompt/choice form against the run's locale (BE-0320).

`run_scenario` is handed the same locale the lease pinned the Simulator's system language to, so the
label the step taps is the one SpringBoard is actually rendering. These drive that end to end over
the fake driver: what gets tapped, that nesting is covered, and that an uncovered language fails the
step rather than tapping something guessed.
"""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


def _fake_with_alert(*labels: str) -> FakeDriver:
    driver = FakeDriver([el("home.title", "home")])
    driver.system_alert_buttons = [el(None, label, ["button"]) for label in labels]
    return driver


def _grant_scenario(steps: list[dict[str, object]] | None = None) -> object:
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
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 5.0))]


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
    assert driver.actions == [("handle_system_alert", ({"label": "Don’t Allow"}, 5.0))]


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
    assert driver.actions == [("handle_system_alert", ({"label": "Allow"}, 5.0))]


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
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 5.0))]


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
    assert driver.actions == [("handle_system_alert", ({"label": "許可"}, 5.0))]


def test_an_uncovered_language_fails_the_step_instead_of_guessing() -> None:
    driver = _fake_with_alert("Erlauben")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock(), locale="de_DE")

    assert not result.ok
    assert result.failure is not None and "language 'de'" in result.failure
    assert driver.actions == []  # nothing was tapped
    # The failed step is still recorded, so the report and the run matrix show *which* step failed
    # rather than only that the scenario did.
    assert [(o.index, o.action, o.ok) for o in result.steps] == [(0, "handle_system_alert", False)]


def test_a_run_with_no_locale_fails_the_step_loudly() -> None:
    # A caller that supplies no locale (`record`'s replay) cannot know the label; the step fails
    # rather than being silently skipped.
    driver = _fake_with_alert("Allow")
    result = run_scenario(driver, _grant_scenario(), clock=FakeClock())

    assert not result.ok
    assert result.failure is not None and "locale" in result.failure
    assert driver.actions == []

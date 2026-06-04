"""Tests for XCUITest code generation (structural scenario -> Swift)."""

from __future__ import annotations

from bajutsu.codegen import class_name_for, to_xcuitest
from bajutsu.scenario import load_scenarios


def _gen(yaml: str, env: dict[str, str] | None = None) -> str:
    scenarios = load_scenarios(yaml)
    return to_xcuitest(scenarios, "FixtureUITests", env or {})


def test_header_and_helpers() -> None:
    code = _gen("- name: x\n  steps:\n    - tap: { id: a }\n")
    assert "import XCTest" in code
    assert "final class FixtureUITests: XCTestCase {" in code
    assert "private func el(_ id: String) -> XCUIElement" in code
    assert 'NSPredicate(format: "identifier LIKE %@", glob)' in code


def test_launch_env_merges_app_and_scenario() -> None:
    code = _gen(
        "- name: x\n  preconditions:\n    launchEnv: { B: '2' }\n  steps:\n    - tap: { id: a }\n",
        env={"A": "1"},
    )
    assert 'app.launchEnvironment["A"] = "1"' in code
    assert 'app.launchEnvironment["B"] = "2"' in code
    assert "app.launch()" in code


def test_method_name_is_sanitized() -> None:
    code = _gen("- name: onboard, log in!\n  steps:\n    - tap: { id: a }\n")
    assert "func test_onboard_log_in() {" in code


def test_tap_type_and_wait() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        "    - tap: { id: onboarding.start }\n"
        "    - type: { text: a@b.com, into: { id: auth.email } }\n"
        "    - wait: { for: { id: home.title }, timeout: 5 }\n"
    )
    assert 'el("onboarding.start").tap()' in code
    assert 'el("auth.email").tap()' in code
    assert 'el("auth.email").typeText("a@b.com")' in code
    assert 'el("home.title").waitForExistence(timeout: 5.0)' in code


def test_long_press_and_swipe() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        "    - longPress: { sel: { id: comp.longpress }, duration: 0.6 }\n"
        "    - swipe: { on: { id: comp.area }, direction: left }\n"
    )
    assert 'el("comp.longpress").press(forDuration: 0.6)' in code
    assert 'el("comp.area").swipeLeft()' in code


def test_wait_until_gone() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - wait: { until: { gone: { id: spinner } }, timeout: 3 }\n"
    )
    assert 'el("spinner").waitForNonExistence(timeout: 3.0)' in code


def test_tap_by_label() -> None:
    code = _gen('- name: x\n  steps:\n    - tap: { label: "Delete" }\n')
    assert 'byLabel("Delete").tap()' in code


def test_assertions_exists_value_count() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - tap: { id: a }\n  expect:\n"
        "    - exists: { id: home.title }\n"
        "    - exists: { id: spinner, negate: true }\n"
        "    - value: { sel: { id: counter }, equals: '2' }\n"
        "    - count: { sel: { idMatches: 'list.row.*' }, equals: 5 }\n"
    )
    assert 'XCTAssertTrue(el("home.title").exists)' in code
    assert 'XCTAssertFalse(el("spinner").exists)' in code
    assert 'XCTAssertEqual(el("counter").value as? String, "2")' in code
    assert 'XCTAssertEqual(matchingId("list.row.*").count, 5)' in code


def test_state_assertions() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - tap: { id: a }\n  expect:\n"
        "    - enabled: { id: auth.submit }\n"
        "    - disabled: { id: auth.cancel }\n"
        "    - selected: { id: toggle }\n"
    )
    assert 'XCTAssertTrue(el("auth.submit").isEnabled)' in code
    assert 'XCTAssertFalse(el("auth.cancel").isEnabled)' in code
    assert 'XCTAssertTrue(el("toggle").isSelected)' in code


def test_class_name_for() -> None:
    assert class_name_for("smoke") == "SmokeUITests"
    assert class_name_for("my-flow_v2") == "MyFlowV2UITests"

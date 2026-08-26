"""Codegen for the `before` / `after` lifecycle phases (BE-0392).

`before` needs no per-target construct — emitting its steps inline at the top of the test body is
exactly its runtime meaning. `after` does, and the three targets reach it differently: Playwright and
UI Automator wrap the body (their assertions throw, so a `catch` sees the verdict), XCUITest
registers one `addTeardownBlock` (`XCTAssert` records rather than throws, so a `catch` never would).
"""

from __future__ import annotations

import pytest

from bajutsu.codegen.common import CodegenError
from bajutsu.codegen.playwright import to_playwright
from bajutsu.codegen.uiautomator import to_uiautomator
from bajutsu.codegen.xcuitest import to_xcuitest
from bajutsu.scenario import Scenario, load_scenarios

_LIFECYCLE = """
- name: lifecycle
  before:
    - tap: { id: seed }
  steps:
    - tap: { id: login }
  after:
    - on: always
      steps: [{ tap: { id: logout } }]
    - on: success
      steps: [{ tap: { id: cleanup } }]
    - on: error
      steps: [{ tap: { id: diagnostics } }]
"""


def _scenarios(text: str = _LIFECYCLE) -> list[Scenario]:
    return load_scenarios(text)


def _playwright(text: str = _LIFECYCLE) -> str:
    return to_playwright(_scenarios(text), "demo", "http://localhost")


def _xcuitest(text: str = _LIFECYCLE) -> str:
    return to_xcuitest(_scenarios(text), "DemoUITests")


def _uiautomator(text: str = _LIFECYCLE) -> str:
    return to_uiautomator(_scenarios(text), "DemoUITest", "com.example")


@pytest.mark.parametrize("render", [_playwright, _xcuitest, _uiautomator])
def test_before_steps_are_emitted_inline_under_their_own_divider(render: object) -> None:
    out = render()  # type: ignore[operator]
    assert "// before (bajutsu lifecycle phase)" in out
    # Inline at the top of the body is the phase's exact meaning: first, and a failure aborts what
    # follows — no framework construct needed, unlike `after`.
    assert out.index("seed") < out.index("login")


def test_playwright_wraps_the_body_and_branches_on_the_caught_failure() -> None:
    out = _playwright()
    assert "let bajutsuFailed = false;" in out
    assert "} catch (bajutsuError) {" in out
    assert "} finally {" in out
    assert "if (!bajutsuFailed) {" in out  # the `success` rule
    assert "if (bajutsuFailed) {" in out  # the `error` rule
    # The rethrow keeps the test failing: teardown must not swallow the verdict it branched on.
    assert "throw bajutsuError;" in out


def test_xcuitest_registers_one_teardown_block_in_declaration_order() -> None:
    out = _xcuitest()
    # One block, not one per rule: XCTest runs registered blocks last-in-first-out, so separate
    # blocks would invert the order the runtime phase preserves.
    assert out.count("addTeardownBlock") == 1
    assert "let bajutsuPassed = testRun?.hasSucceeded == true" in out
    assert out.index("logout") < out.index("cleanup") < out.index("diagnostics")


def test_uiautomator_wraps_the_body_with_a_throwable_catch() -> None:
    out = _uiautomator()
    assert "var bajutsuFailed = false" in out
    assert "} catch (bajutsuError: Throwable) {" in out
    assert "if (!bajutsuFailed) {" in out
    assert "throw bajutsuError" in out


@pytest.mark.parametrize("render", [_playwright, _xcuitest, _uiautomator])
def test_an_unconditional_teardown_needs_no_outcome_flag(render: object) -> None:
    out = render(  # type: ignore[operator]
        """
- name: lifecycle
  steps: [{ tap: { id: login } }]
  after:
    - on: always
      steps: [{ tap: { id: logout } }]
"""
    )
    assert "logout" in out
    # No branch to take, so no flag to declare — an unused one would only earn a compiler warning.
    assert "bajutsuFailed" not in out
    assert "bajutsuPassed" not in out


@pytest.mark.parametrize("render", [_playwright, _xcuitest, _uiautomator])
def test_a_scenario_without_the_phases_is_unchanged(render: object) -> None:
    out = render("- name: plain\n  steps: [{ tap: { id: login } }]\n")  # type: ignore[operator]
    assert "before (bajutsu lifecycle phase)" not in out
    assert "finally" not in out
    assert "addTeardownBlock" not in out


@pytest.mark.parametrize("render", [_playwright, _xcuitest, _uiautomator])
def test_a_runtime_only_construct_in_a_hook_is_rejected_loudly(render: object) -> None:
    # The same guard the scenario's own steps get (BE-0297): a construct no static test can express
    # fails generation rather than emitting a stub that would fake a pass.
    with pytest.raises(CodegenError):
        render(  # type: ignore[operator]
            """
- name: lifecycle
  steps: [{ tap: { id: login } }]
  after:
    - on: always
      steps:
        - if: { condition: { exists: { id: x } }, then: [{ tap: { id: y } }] }
"""
        )

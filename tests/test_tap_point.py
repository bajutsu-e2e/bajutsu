"""Coordinate tap (`tapPoint`): the stability-ladder bottom rung for a control the accessibility
tree does not expose — most often a tab-bar tab on an app with no accessibility ids.

Covers the DSL parse + normalized-range validation, the orchestrator dispatch that turns a
normalized point into an absolute coordinate tap against the current screen, the agent tool that
`record` emits it from, and the codegen fallback.
"""

from __future__ import annotations

import pytest
from conftest import FakeBackend, FakeBlock

from bajutsu.claude_agent import ClaudeAgent, proposal_from_call
from bajutsu.codegen import to_xcuitest
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import _action_of, run_scenario
from bajutsu.scenario import Step, load_scenarios


def _app(w: float, h: float) -> base.Element:
    """An application-root element carrying the window frame — what `tapPoint` scales against."""
    return {"identifier": None, "label": None, "traits": ["application"], "value": None,
            "frame": (0.0, 0.0, w, h)}  # fmt: skip


# --- DSL parse + validation ---


def test_parse_tap_point() -> None:
    step = load_scenarios("- name: t\n  steps:\n    - tapPoint: { x: 0.5, y: 0.96 }\n")[0].steps[0]
    assert step.tap_point is not None
    assert (step.tap_point.x, step.tap_point.y) == (0.5, 0.96)
    assert _action_of(step) == "tap_point"


@pytest.mark.parametrize(("x", "y"), [(1.5, 0.5), (0.5, -0.1), (2.0, 2.0)])
def test_tap_point_rejects_out_of_unit_range(x: float, y: float) -> None:
    with pytest.raises(ValueError, match=r"0\.\.1"):
        Step.model_validate({"tapPoint": {"x": x, "y": y}})


def test_tap_point_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios(
            "- name: t\n  steps:\n    - tapPoint: { x: 0.5, y: 0.5 }\n      tap: { id: a }\n"
        )


# --- Orchestrator dispatch: normalized point -> absolute coordinate tap ---


def test_dispatch_scales_against_the_app_window() -> None:
    driver = FakeDriver(screen=[_app(400.0, 800.0)])
    scenario = load_scenarios("- name: t\n  steps:\n    - tapPoint: { x: 0.5, y: 0.96 }\n")[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert driver.actions == [("tap_point", (200.0, 768.0))]


def test_dispatch_scales_by_the_screen_extent_shared_helper() -> None:
    # No application element: the screen size is the max element edge (screen_size_from_elements —
    # the one helper the crawl and alert guard also use), here (10+100, 20+200) = (110, 220).
    el: base.Element = {"identifier": "x", "label": None, "traits": [], "value": None,
                        "frame": (10.0, 20.0, 100.0, 200.0)}  # fmt: skip
    driver = FakeDriver(screen=[el])
    scenario = load_scenarios("- name: t\n  steps:\n    - tapPoint: { x: 0.5, y: 0.5 }\n")[0]
    assert run_scenario(driver, scenario).ok
    assert driver.actions == [("tap_point", (55.0, 110.0))]


# --- Agent: the tool `record` emits it from ---


def test_agent_tap_point_tool_maps_to_step() -> None:
    block = FakeBlock(
        "tap_point", {"x": 0.5, "y": 0.96, "reason": "the Log tab is the 3rd of 5 tabs"}
    )
    proposal = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs())
    assert proposal.step is not None and proposal.step.tap_point is not None
    assert (proposal.step.tap_point.x, proposal.step.tap_point.y) == (0.5, 0.96)
    # The reason is recorded as the step's provenance (BE-0044).
    assert proposal.step.from_ == "the Log tab is the 3rd of 5 tabs"


def test_tap_point_proposal_reason_is_the_note() -> None:
    p = proposal_from_call("tap_point", {"x": 0.1, "y": 0.9, "reason": "switch to the Log tab"})
    assert p.note == "switch to the Log tab"


# --- codegen: coordinate taps have no faithful selector form, so they degrade gracefully ---


def test_codegen_marks_tap_point_unsupported() -> None:
    scenarios = load_scenarios("- name: t\n  steps:\n    - tapPoint: { x: 0.5, y: 0.96 }\n")
    assert "TODO" in to_xcuitest(scenarios, "T")


def _obs() -> object:
    from bajutsu.agent_protocols import Observation

    return Observation(goal="switch tabs", screen=[], history=[])

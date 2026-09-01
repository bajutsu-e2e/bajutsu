"""Tests for the `generate` step during a run: vars.* binding and the recorded value (BE-0377)."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.orchestrator import run_scenario
from bajutsu.drivers.fake import FakeDriver


def test_generated_value_is_available_to_later_steps() -> None:
    # A single-value range makes the draw predictable, so the assertion checks the binding rather
    # than the arithmetic (which test_generate_step.py covers).
    driver = FakeDriver([el("field", "Quantity", value="7")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "generate into vars",
                "steps": [
                    {"generate": {"random": {"int": {"min": 7, "max": 7}}, "into": {"var": "n"}}},
                    {"assert": [{"value": {"sel": {"id": "field"}, "equals": "${vars.n}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_the_produced_value_is_recorded_on_the_step_outcome() -> None:
    # Evidence, not verdict: the run's record shows which value this run actually used.
    driver = FakeDriver([el("field", "Order")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "generate records its value",
                "steps": [
                    {"generate": {"random": {"uuid": {}}, "into": {"var": "orderRef"}}},
                    {"tap": {"id": "field"}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].generated is not None
    assert len(result.steps[0].generated) == 36  # a version-4 UUID in its canonical text form
    # Every other action records nothing, so the field never reads as "this step produced a value".
    assert result.steps[1].generated is None

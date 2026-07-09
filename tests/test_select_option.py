"""`selectOption`: set a native ``<select>`` to a given option value (BE-0191 unit 5).

A ``<select>`` is a web-only control — its dropdown is not in the DOM, so a coordinate click
cannot switch it deterministically. This action resolves the ``<select>`` through the shared
determinism core (a unique match) and sets its value, giving the theme picker (and any web
``<select>``) a deterministic switch. iOS / Android have no native ``<select>``, so those backends
refuse loudly.

Covers the DSL parse + one-action rule, the orchestrator dispatch to the driver, the fake driver
recording, and the mobile backends' UnsupportedAction refusal.
"""

from __future__ import annotations

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import _action_of, run_scenario
from bajutsu.scenario import load_scenarios


def _select(value: str) -> base.Element:
    """A ``<select>`` element whose current option value is `value` (what the driver reads)."""
    return {"identifier": "nav.theme-picker", "label": None, "traits": ["button"],
            "value": value, "frame": (0.0, 0.0, 100.0, 30.0)}  # fmt: skip


# --- DSL parse + validation ---


def test_parse_select_option() -> None:
    step = load_scenarios(
        "- name: t\n  steps:\n    - selectOption: { sel: { id: nav.theme-picker }, option: midnight }\n"
    )[0].steps[0]
    assert step.select_option is not None
    assert step.select_option.option == "midnight"
    assert step.select_option.sel.id == "nav.theme-picker"
    assert _action_of(step) == "select_option"


def test_select_option_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios(
            "- name: t\n  steps:\n"
            "    - selectOption: { sel: { id: p }, option: a }\n      tap: { id: b }\n"
        )


# --- Orchestrator dispatch: selectOption -> driver.select_option(sel, option) ---


def test_dispatch_calls_driver_select_option() -> None:
    driver = FakeDriver(screen=[_select("daylight")])
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectOption: { sel: { id: nav.theme-picker }, option: midnight }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert driver.actions == [("select_option", ({"id": "nav.theme-picker"}, "midnight"))]


def test_dispatch_fails_on_ambiguous_select() -> None:
    # Two selects with the same id: a single action must fail rather than pick one (determinism core).
    driver = FakeDriver(screen=[_select("daylight"), _select("midnight")])
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - selectOption: { sel: { id: nav.theme-picker }, option: midnight }\n"
    )[0]
    assert not run_scenario(driver, scenario).ok


# --- Mobile backends have no native <select>: refuse loudly ---


def test_fake_records_resolved_select_option() -> None:
    driver = FakeDriver(screen=[_select("daylight")])
    driver.select_option({"id": "nav.theme-picker"}, "midnight")
    assert driver.actions == [("select_option", ({"id": "nav.theme-picker"}, "midnight"))]


def test_fake_select_option_requires_unique_match() -> None:
    driver = FakeDriver(screen=[])
    with pytest.raises(base.ElementNotFound):
        driver.select_option({"id": "missing"}, "midnight")

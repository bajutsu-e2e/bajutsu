"""`setPickerValue`: move a picker wheel to an exact value (BE-0356).

A wheel-style `UIPickerView` / `UIDatePicker` exposes no separately addressable row, so no
coordinate drag can guarantee stopping on one — the non-determinism prime directive 2 rules out.
XCUITest's `adjust(toPickerWheelValue:)` acts on the resolved element instead, which is what makes
landing on a named row deterministic. iOS-only: every other backend refuses loudly.

Covers the DSL parse + one-action rule, the orchestrator dispatch to the driver, the absent-value
failure (`ElementNotFound`), multi-component addressing through `within` / `traits` / `index`, and
the preflight rejection on a backend without the capability.
"""

from __future__ import annotations

import pytest

from bajutsu.capability_preflight import unsupported
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import _action_of, run_scenario
from bajutsu.scenario import load_scenarios


def _wheel(value: str, *, identifier: str | None = None, y: float = 0.0) -> base.Element:
    """One `pickerWheel` component showing `value`. A sibling component carries no id of its own."""
    return {"identifier": identifier, "label": None, "traits": ["pickerWheel"],
            "value": value, "frame": (0.0, y, 100.0, 40.0), "nativeZ": None}  # fmt: skip


def _container(identifier: str) -> base.Element:
    """The `UIDatePicker` parent the sibling wheels are scoped to — `other`, per `typeName`."""
    return {"identifier": identifier, "label": None, "traits": ["other"],
            "value": None, "frame": (0.0, 0.0, 100.0, 80.0), "nativeZ": None}  # fmt: skip


def _seeded(
    *wheels: tuple[base.Element, list[str]], extra: list[base.Element] | None = None
) -> FakeDriver:
    """A fake showing `wheels` (each with its rows seeded by object identity) plus `extra`."""
    driver = FakeDriver(screen=[*(extra or []), *(w for w, _ in wheels)])
    for wheel, options in wheels:
        driver.picker_wheel_options[id(wheel)] = options
    return driver


# --- DSL parse + validation ---


def test_parse_set_picker_value() -> None:
    step = load_scenarios(
        "- name: t\n  steps:\n    - setPickerValue: { sel: { id: form.school }, value: 大学 }\n"
    )[0].steps[0]
    assert step.set_picker_value is not None
    assert step.set_picker_value.value == "大学"
    assert step.set_picker_value.sel.id == "form.school"
    assert _action_of(step) == "set_picker_value"


def test_set_picker_value_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios(
            "- name: t\n  steps:\n"
            "    - setPickerValue: { sel: { id: p }, value: a }\n      tap: { id: b }\n"
        )


# --- Orchestrator dispatch: setPickerValue -> driver.set_picker_value(sel, value) ---


def test_dispatch_calls_driver_set_picker_value() -> None:
    wheel = _wheel("高校", identifier="form.school")
    driver = _seeded((wheel, ["中学", "高校", "大学"]))
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - setPickerValue: { sel: { id: form.school }, value: 大学 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert driver.actions == [("set_picker_value", ({"id": "form.school"}, "大学"))]


def test_dispatch_fails_when_the_wheel_has_no_such_value() -> None:
    # The central behavior: a value the wheel does not carry fails rather than leaving the wheel
    # wherever it stopped. On-device this is what the runner's value read-back detects, since
    # `adjust(toPickerWheelValue:)` reports nothing at all.
    wheel = _wheel("高校", identifier="form.school")
    driver = _seeded((wheel, ["中学", "高校", "大学"]))
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - setPickerValue: { sel: { id: form.school }, value: 大学院 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert not result.ok
    assert driver.actions == []  # nothing was recorded as actuated


def test_dispatch_fails_on_ambiguous_wheel() -> None:
    # Two indistinguishable wheels: a single action must fail rather than pick one, and the author
    # is expected to disambiguate with `within` / `traits` / `index` (the determinism core).
    first, second = _wheel("2015年"), _wheel("4月", y=40.0)
    driver = _seeded((first, ["2015年", "2016年"]), (second, ["4月", "5月"]))
    scenario = load_scenarios(
        "- name: t\n  steps:\n"
        "    - setPickerValue: { sel: { traits: [pickerWheel] }, value: 2016年 }\n"
    )[0]
    assert not run_scenario(driver, scenario).ok


# --- A multi-component picker: each wheel is one step, addressed by the shared selector fields ---


def test_multi_component_picker_addresses_each_wheel_by_index() -> None:
    # A wheel-mode `UIDatePicker` lays its year and month out as two `pickerWheel` children with no
    # identifier of their own. `within` + `traits` + `index` is the existing addressing mechanism,
    # so `value` stays a plain string and the two components are two separate steps (BE-0356).
    container = _container("birthdate.picker")
    year, month = _wheel("2015年"), _wheel("4月", y=40.0)
    driver = _seeded((year, ["2015年", "2016年"]), (month, ["4月", "5月"]), extra=[container])
    scenario = load_scenarios(
        "- name: t\n  steps:\n"
        "    - setPickerValue:\n"
        "        sel: { within: { id: birthdate.picker }, traits: [pickerWheel], index: 0 }\n"
        "        value: 2016年\n"
        "    - setPickerValue:\n"
        "        sel: { within: { id: birthdate.picker }, traits: [pickerWheel], index: 1 }\n"
        "        value: 5月\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert [value for _, (_, value) in driver.actions] == ["2016年", "5月"]


def test_a_sibling_wheels_options_do_not_leak_to_the_other() -> None:
    # The seed is keyed by object identity, not identifier, precisely so two identifier-less
    # siblings keep their own rows: the month wheel must not accept a year the year wheel offers.
    container = _container("birthdate.picker")
    year, month = _wheel("2015年"), _wheel("4月", y=40.0)
    driver = _seeded((year, ["2015年", "2016年"]), (month, ["4月", "5月"]), extra=[container])
    with pytest.raises(base.ElementNotFound):
        driver.set_picker_value(
            {"within": {"id": "birthdate.picker"}, "traits": ["pickerWheel"], "index": 1},
            "2016年",
        )


# --- The fake's own contract ---


def test_fake_records_the_resolved_wheel() -> None:
    wheel = _wheel("高校", identifier="form.school")
    driver = _seeded((wheel, ["高校", "大学"]))
    driver.set_picker_value({"id": "form.school"}, "大学")
    assert driver.actions == [("set_picker_value", ({"id": "form.school"}, "大学"))]


def test_fake_set_picker_value_requires_unique_match() -> None:
    with pytest.raises(base.ElementNotFound):
        FakeDriver(screen=[]).set_picker_value({"id": "missing"}, "大学")


def test_fake_an_unseeded_wheel_is_a_fixture_error_not_an_absent_value() -> None:
    # A resolved wheel carrying no seed means the fixture is wrong (a stale key from a `react`
    # callback that rebuilt `screen`), so it fails distinctly — otherwise the absent-value test
    # above could pass for entirely the wrong reason.
    driver = FakeDriver(screen=[_wheel("高校", identifier="form.school")])
    with pytest.raises(LookupError, match="fixture error"):
        driver.set_picker_value({"id": "form.school"}, "大学")


# --- Preflight: a backend without the capability is rejected before any device work ---


def test_preflight_rejects_a_backend_without_the_picker_wheel_capability() -> None:
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - setPickerValue: { sel: { id: form.school }, value: 大学 }\n"
    )[0]
    assert base.Capability.PICKER_WHEEL not in AdbDriver.CAPABILITIES
    reasons = unsupported(scenario, AdbDriver.CAPABILITIES)
    assert any("setPickerValue" in r and "pickerWheel" in r for r in reasons)


def test_preflight_accepts_a_backend_that_advertises_the_capability() -> None:
    scenario = load_scenarios(
        "- name: t\n  steps:\n    - setPickerValue: { sel: { id: form.school }, value: 大学 }\n"
    )[0]
    assert unsupported(scenario, FakeDriver.CAPABILITIES) == []

"""Tests for the action-capture record core (BE-0012).

Pure-core tests over literal Element lists — no Simulator, no mocks beyond Redactor.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.record_capture import (
    hit_test,
    resolve_capture,
    screen_size_from_elements,
    selector_for_element,
    step_for_swipe,
    step_for_tap,
    step_for_type,
)
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact, load_scenarios
from bajutsu.scenario.models import Scenario, Selector
from bajutsu.scenario.serialize import dump_scenario_file


def _el(
    identifier: str | None,
    traits: list[str],
    frame: base.Frame = (0.0, 0.0, 10.0, 10.0),
    *,
    label: str | None = "x",
    value: str | None = None,
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": value,
        "frame": frame,
    }


# ---------------------------------------------------------------------------
# hit_test
# ---------------------------------------------------------------------------


def test_hit_test_picks_smallest_actionable() -> None:
    window = _el(None, ["window"], (0, 0, 320, 568))
    button = _el("ok", ["button"], (100, 200, 60, 44))
    elements = [window, button]
    result = hit_test(elements, (120.0, 220.0))
    assert result is not None
    assert result["identifier"] == "ok"


def test_hit_test_ignores_non_actionable() -> None:
    text = _el(None, ["staticText"], (50, 50, 100, 20))
    elements = [text]
    assert hit_test(elements, (60.0, 55.0)) is None


def test_hit_test_point_outside() -> None:
    button = _el("btn", ["button"], (100, 200, 60, 44))
    elements = [button]
    assert hit_test(elements, (0.0, 0.0)) is None


# ---------------------------------------------------------------------------
# selector_for_element
# ---------------------------------------------------------------------------


def test_selector_for_element_id() -> None:
    el = _el("auth.email", ["textField"], (10, 10, 200, 30))
    elements = [el]
    sel = selector_for_element(el, elements)
    assert sel is not None
    assert sel.id == "auth.email"


def test_selector_for_element_label() -> None:
    el = _el(None, ["button"], (10, 10, 80, 44), label="Submit")
    elements = [el]
    sel = selector_for_element(el, elements)
    assert sel is not None
    assert sel.label == "Submit"
    assert sel.index is None


def test_selector_for_element_label_index() -> None:
    el_a = _el(None, ["button"], (10, 10, 80, 44), label="Cell")
    el_b = _el(None, ["button"], (10, 60, 80, 44), label="Cell")
    elements = [el_a, el_b]
    sel_a = selector_for_element(el_a, elements)
    sel_b = selector_for_element(el_b, elements)
    assert sel_a is not None
    assert sel_a.label == "Cell"
    assert sel_a.index == 0
    assert sel_b is not None
    assert sel_b.index == 1


def test_selector_for_element_neither() -> None:
    el = _el(None, ["button"], (10, 10, 80, 44), label=None)
    elements = [el]
    assert selector_for_element(el, elements) is None


# ---------------------------------------------------------------------------
# resolve_capture
# ---------------------------------------------------------------------------


def test_resolve_capture_success() -> None:
    el = _el("login.submit", ["button"], (100, 400, 80, 44))
    elements = [el]
    result = resolve_capture(elements, (120.0, 420.0), [])
    assert result.refused is None
    assert result.selector is not None
    assert result.selector.id == "login.submit"
    assert result.rung == "id"
    assert result.ambiguity is None


def test_resolve_capture_ambiguous() -> None:
    el_a = _el("dup", ["button"], (10, 10, 80, 44))
    el_b = _el("dup", ["button"], (10, 60, 80, 44))
    elements = [el_a, el_b]
    result = resolve_capture(elements, (30.0, 30.0), [])
    assert result.ambiguity is not None
    assert len(result.ambiguity) == 2


def test_resolve_capture_refused_no_id_no_label() -> None:
    el = _el(None, ["button"], (10, 10, 80, 44), label=None)
    elements = [el]
    result = resolve_capture(elements, (20.0, 20.0), [])
    assert result.refused is not None
    assert "accessibilityIdentifier" in result.refused


# ---------------------------------------------------------------------------
# step_for_tap
# ---------------------------------------------------------------------------


def test_step_for_tap() -> None:
    sel = Selector(id="settings.open")
    step = step_for_tap(sel)
    assert step.tap is not None
    assert step.tap.id == "settings.open"


# ---------------------------------------------------------------------------
# step_for_type
# ---------------------------------------------------------------------------


def test_step_for_type_with_redaction() -> None:
    sel = Selector(id="auth.password")
    redactor = Redactor(Redact(fields=["password"]), values=["s3cret"])
    step = step_for_type(sel, "s3cret", redactor)
    assert step.type is not None
    assert step.type.text == "[REDACTED]"
    assert step.type.into is not None
    assert step.type.into.id == "auth.password"


def test_step_for_type_plain() -> None:
    sel = Selector(id="auth.email")
    step = step_for_type(sel, "test@example.com")
    assert step.type is not None
    assert step.type.text == "test@example.com"


# ---------------------------------------------------------------------------
# step_for_swipe
# ---------------------------------------------------------------------------


def test_step_for_swipe_two_points() -> None:
    step = step_for_swipe((0.5, 0.8), (0.5, 0.2))
    assert step.swipe is not None
    assert step.swipe.from_ is not None
    assert step.swipe.to is not None
    assert step.swipe.on is None


def test_step_for_swipe_same_element_upgrades_to_direction() -> None:
    el = _el("list", ["cell"], (0, 0, 320, 568))
    elements = [el]
    step = step_for_swipe((0.5, 0.8), (0.5, 0.2), elements, (320.0, 568.0))
    assert step.swipe is not None
    assert step.swipe.on is not None
    assert step.swipe.on.id == "list"
    assert step.swipe.direction == "up"


# ---------------------------------------------------------------------------
# screen_size_from_elements
# ---------------------------------------------------------------------------


def test_screen_size_from_elements() -> None:
    elements = [
        _el(None, ["window"], (0, 0, 320, 568)),
        _el("btn", ["button"], (10, 10, 60, 44)),
    ]
    w, h = screen_size_from_elements(elements)
    assert w == 320.0
    assert h == 568.0


# ---------------------------------------------------------------------------
# scenario roundtrip
# ---------------------------------------------------------------------------


def test_scenario_roundtrip() -> None:
    sel = Selector(id="auth.email")
    steps = [
        step_for_tap(sel),
        step_for_type(sel, "test@example.com"),
        step_for_swipe((0.5, 0.8), (0.5, 0.2)),
    ]
    scenario = Scenario(name="captured", steps=steps)
    yaml_text = dump_scenario_file([scenario])
    loaded = load_scenarios(yaml_text)
    assert len(loaded) == 1
    assert loaded[0].name == "captured"
    assert len(loaded[0].steps) == 3

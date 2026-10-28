"""The `attribute` assertion (BE-0466): the model, the evaluator, and the runner's on-demand read."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import el
from pydantic import ValidationError

from bajutsu.common import backends
from bajutsu.common.assertions import (
    AssertionResult,
    AttributeRead,
    EvalContext,
    attribute_key,
    evaluate_one,
)
from bajutsu.common.capability import capability_preflight
from bajutsu.common.config import load_config
from bajutsu.common.config.schema.platform.registry import PLATFORM_SCHEMAS
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb.adb_driver import AdbDriver
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.webview.web_context_driver import WebContextDriver
from bajutsu.common.drivers.xcuitest.xcuitest_driver import XcuitestDriver
from bajutsu.common.drivers.xcuitest_live.xcuitest_live_driver import XcuitestLiveDriver
from bajutsu.common.runner.pipeline import _steps_for_target
from bajutsu.common.scenario import Assertion, Scenario
from bajutsu.record.loop import settle_step


def _attr(**fields: object) -> Assertion:
    return Assertion.model_validate({"attribute": {"sel": {"id": "docs.link"}, **fields}})


# --- Unit 1: the scenario model ----------------------------------------------------------------


@pytest.mark.parametrize(
    "op",
    [{"equals": "/docs"}, {"contains": "doc"}, {"matches": "^/d"}, {"absent": True}],
)
def test_each_operator_loads(op: dict[str, object]) -> None:
    a = _attr(name="href", **op)
    assert a.attribute is not None
    assert a.attribute.name == "href"


def test_name_is_kept_as_written() -> None:
    # Never lowercased: `getAttribute` decides case-sensitivity per element namespace, as the DOM does.
    a = _attr(name="viewBox", equals="0 0 1 1")
    assert a.attribute is not None
    assert a.attribute.name == "viewBox"


@pytest.mark.parametrize("name", ["", "a b", "a=b", 'a"', "a/b", "a>b"])
def test_invalid_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match="attribute name"):
        _attr(name=name, equals="x")


@pytest.mark.parametrize(
    "ops",
    [{}, {"equals": "a", "contains": "b"}, {"matches": "a", "absent": True}],
)
def test_exactly_one_operator(ops: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="exactly one of equals/contains/matches/absent"):
        _attr(name="href", **ops)


def test_absent_false_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _attr(name="href", absent=False)


def test_attribute_is_one_kind_among_the_others() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        Assertion.model_validate(
            {
                "attribute": {"sel": {"id": "a"}, "name": "href", "equals": "/"},
                "exists": {"id": "a"},
            }
        )


# --- Unit 2: driver protocol, capability, preflight, config validation -------------------------

_WEB_CAPS = frozenset(
    {base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.ELEMENT_ATTRIBUTE}
)
_MOBILE_CAPS = frozenset({base.Capability.QUERY, base.Capability.ELEMENTS})
_ATTR = {"attribute": {"sel": {"id": "docs.link"}, "name": "href", "equals": "/docs"}}


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [], **body})


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"expect": [_ATTR]}, "expect[0]"),
        ({"steps": [{"assert": [_ATTR]}]}, "step 1 > assert[0]"),
        (
            {"steps": [{"if": {"condition": _ATTR, "then": [{"tap": {"id": "a"}}]}}]},
            "if > condition",
        ),
        (
            {
                "steps": [
                    {"if": {"condition": {"exists": {"id": "a"}}, "then": [{"assert": [_ATTR]}]}}
                ]
            },
            "if > then",
        ),
        (
            {"steps": [{"web": {"within": {"id": "wv"}, "steps": [{"assert": [_ATTR]}]}}]},
            "step 1 > web",
        ),
        (
            {"interrupts": [{"condition": _ATTR, "steps": [{"tap": {"id": "a"}}]}]},
            "interrupts[0] > condition",
        ),
        (
            {
                "interrupts": [
                    {"condition": {"exists": {"id": "a"}}, "steps": [{"assert": [_ATTR]}]}
                ]
            },
            "interrupts[0]",
        ),
    ],
)
def test_preflight_fails_an_attribute_without_the_capability(
    body: dict[str, object], path: str
) -> None:
    sc = _sc(**body)
    reasons = capability_preflight.unsupported(sc, _MOBILE_CAPS)
    assert len(reasons) == 1
    assert path in reasons[0] and "attribute" in reasons[0] and "elementAttribute" in reasons[0]
    assert capability_preflight.unsupported(sc, _WEB_CAPS) == []


@pytest.mark.parametrize(
    "driver_cls",
    [AdbDriver, XcuitestDriver, XcuitestLiveDriver, WebContextDriver],
)
def test_non_web_drivers_neither_advertise_nor_read(driver_cls: type) -> None:
    driver: Any = object.__new__(driver_cls)  # the refusal touches no device, so no construction
    with pytest.raises(base.UnsupportedAction, match="attribute"):
        driver.read_attribute({"id": "a"}, "href")
    caps: frozenset[str] = getattr(driver_cls, "CAPABILITIES", frozenset())
    assert base.Capability.ELEMENT_ATTRIBUTE not in caps


def test_platform_schema_flag_matches_the_backend_capabilities() -> None:
    # The config check reads `PlatformSchema.element_attribute` so it never imports a driver; this
    # pins that flag to what each platform's backends actually advertise.
    for platform, actuators in backends.PLATFORMS.items():
        implemented = [a for a in actuators if a in backends.IMPLEMENTED]
        advertised = {
            base.Capability.ELEMENT_ATTRIBUTE in backends.capabilities_for(a) for a in implemented
        }
        assert advertised <= {PLATFORM_SCHEMAS[platform].element_attribute}, platform


def _multi_target(interrupts: list[dict[str, object]], steps: list[dict[str, object]]) -> Scenario:
    return _sc(targets=["app", "site"], primaryTarget="app", steps=steps, interrupts=interrupts)


def test_a_web_only_interrupt_does_not_fail_another_target() -> None:
    sc = _multi_target(
        [{"target": "site", "condition": _ATTR, "steps": [{"tap": {"id": "a"}}]}],
        [{"tap": {"id": "a"}}],
    )
    assert capability_preflight.unsupported(_steps_for_target(sc, "app"), _MOBILE_CAPS) == []
    assert capability_preflight.unsupported(_steps_for_target(sc, "site"), _WEB_CAPS) == []
    assert capability_preflight.unsupported(_steps_for_target(sc, "site"), _MOBILE_CAPS) != []


def test_an_untargeted_interrupt_follows_the_primary() -> None:
    untargeted: list[dict[str, object]] = [{"condition": _ATTR, "steps": [{"tap": {"id": "a"}}]}]
    sc = _multi_target(untargeted, [{"tap": {"id": "a"}}])
    assert capability_preflight.unsupported(_steps_for_target(sc, "app"), _MOBILE_CAPS) != []
    assert _steps_for_target(sc, "site").interrupts == []
    # A `setPrimaryTarget` makes "site" primary too, so the entry polls it and is judged there.
    moved = _multi_target(
        untargeted, [{"tap": {"id": "a"}}, {"setPrimaryTarget": {"target": "site"}}]
    )
    assert capability_preflight.unsupported(_steps_for_target(moved, "site"), _MOBILE_CAPS) != []


_CONFIG = """\
targets:
  myapp:
    platform: {platform}
    app: {app}
    hooks:
      interrupts:
      - condition:
          attribute: {{sel: {{id: banner}}, name: data-state, equals: open}}
        steps:
        - tap: {{id: banner.close}}
"""


@pytest.mark.parametrize(
    ("platform", "app"), [("ios", "{id: com.example.app}"), ("android", "{id: com.example.app}")]
)
def test_config_rejects_an_attribute_interrupt_on_a_non_web_target(platform: str, app: str) -> None:
    with pytest.raises(ValidationError, match="needs a web target"):
        load_config(_CONFIG.format(platform=platform, app=app))


def test_config_rejects_an_attribute_inside_interrupt_recovery() -> None:
    text = (
        _CONFIG.format(platform="ios", app="{id: com.example.app}")
        .replace(
            "- tap: {id: banner.close}",
            "- assert: [{attribute: {sel: {id: x}, name: href, absent: true}}]",
        )
        .replace(
            "attribute: {sel: {id: banner}, name: data-state, equals: open}", "exists: {id: b}"
        )
    )
    with pytest.raises(ValidationError, match="needs a web target"):
        load_config(text)


def test_config_accepts_an_attribute_interrupt_on_a_web_target() -> None:
    cfg = load_config(_CONFIG.format(platform="web", app="{url: 'http://x'}"))
    assert cfg is not None


# --- Unit 3: the evaluator ---------------------------------------------------------------------


def _eval(a: Assertion, read: AttributeRead | None) -> AssertionResult:
    assert a.attribute is not None
    reads = {} if read is None else {attribute_key(a.attribute): read}
    return evaluate_one([], a, [], ctx=EvalContext(attributes=reads))


@pytest.mark.parametrize(
    ("op", "value", "ok"),
    [
        ({"equals": "/docs"}, "/docs", True),
        ({"equals": "/docs"}, "/about", False),
        ({"contains": "doc"}, "/docs", True),
        ({"matches": "^/d.*s$"}, "/docs", True),
        ({"matches": "^x"}, "/docs", False),
        ({"absent": True}, None, True),
        ({"absent": True}, "/docs", False),
    ],
)
def test_each_operator_compares_the_read_value(
    op: dict[str, object], value: str | None, ok: bool
) -> None:
    assert _eval(_attr(name="href", **op), AttributeRead(value=value)).ok is ok


def test_present_but_empty_differs_from_absent() -> None:
    empty = AttributeRead(value="")
    assert _eval(_attr(name="hidden", equals=""), empty).ok
    assert not _eval(_attr(name="hidden", absent=True), empty).ok


def test_a_missing_attribute_fails_value_operators_naming_its_absence() -> None:
    for op in ({"equals": ""}, {"contains": "x"}, {"matches": ".*"}):
        r = _eval(_attr(name="href", **op), AttributeRead(value=None))
        assert not r.ok
        assert "attribute 'href' is absent" in r.reason


def test_a_failed_read_becomes_the_reason() -> None:
    r = _eval(_attr(name="href", equals="/"), AttributeRead(error="no element matched"))
    assert not r.ok and r.reason == "no element matched"


def test_no_read_fails_cleanly() -> None:
    r = _eval(_attr(name="href", equals="/"), None)
    assert not r.ok and "no attribute read" in r.reason


def test_the_mismatch_reason_prints_the_actual_value() -> None:
    r = _eval(_attr(name="href", equals="/docs"), AttributeRead(value="/about"))
    assert r.kind == "attribute"
    assert r.reason == "expected equals='/docs' but actual='/about'"


def test_the_fake_driver_reads_seeded_attributes() -> None:
    driver = FakeDriver([el("docs.link", "Docs", ["link"]), el("other", "Other")])
    driver.attributes[id(driver.screen[0])] = {"href": "/docs"}
    assert driver.read_attribute({"id": "docs.link"}, "href") == "/docs"
    assert driver.read_attribute({"id": "docs.link"}, "alt") is None
    assert driver.read_attribute({"id": "other"}, "href") is None
    with pytest.raises(base.ElementNotFound):
        driver.read_attribute({"id": "missing"}, "href")
    assert base.Capability.ELEMENT_ATTRIBUTE in driver.capabilities()


def test_record_settles_on_an_attribute_assertions_element() -> None:
    # The asserted element must be present before its attribute can be read, so `record` waits for it.
    step = settle_step([_attr(name="href", equals="/docs")])
    assert step is not None and step.wait is not None and step.wait.for_ is not None
    assert step.wait.for_.id == "docs.link"


def test_config_rejects_an_attribute_in_a_recovery_if_condition() -> None:
    text = (
        _CONFIG.format(platform="android", app="{id: com.example.app}")
        .replace(
            "attribute: {sel: {id: banner}, name: data-state, equals: open}", "exists: {id: b}"
        )
        .replace(
            "- tap: {id: banner.close}",
            "- if: {condition: {attribute: {sel: {id: x}, name: open, absent: true}},"
            " then: [{tap: {id: x}}]}",
        )
    )
    with pytest.raises(ValidationError, match="needs a web target"):
        load_config(text)


def test_config_accepts_a_non_web_target_whose_interrupts_read_no_attribute() -> None:
    text = _CONFIG.format(platform="ios", app="{id: com.example.app}").replace(
        "attribute: {sel: {id: banner}, name: data-state, equals: open}", "exists: {id: b}"
    )
    assert load_config(text) is not None

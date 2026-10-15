"""`navigate`: open a URL mid-scenario on a backend that advertises `urlNavigation` (BE-0464).

Covers the DSL's string shorthand and its scheme rule at load time, the string round-trip every
dump-and-revalidate path relies on, the generated schema, the capability preflight, the handler's
run-time re-check after substitution, and the web driver's URL resolution and error mapping.
"""

from __future__ import annotations

import sys
from typing import Any, cast

import pytest
from pydantic import ValidationError

from bajutsu.common.backend_cli import simctl
from bajutsu.common.capability import capability_preflight
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb.adb_driver import AdbDriver
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.playwright import PlaywrightDriver
from bajutsu.common.drivers.webview.web_context_driver import WebContextDriver
from bajutsu.common.drivers.xcuitest.xcuitest_driver import XcuitestDriver
from bajutsu.common.drivers.xcuitest_live.xcuitest_live_driver import XcuitestLiveDriver
from bajutsu.common.orchestrator import _action_of, run_scenario
from bajutsu.common.scenario import Scenario, Step, dump_scenarios, load_scenarios
from bajutsu.common.scenario.models.actions import navigate_url_problem


def _one(body: str) -> str:
    return f"- name: t\n  steps:\n    - {body}\n"


# --- DSL parse + validation ---


def test_parse_string_shorthand() -> None:
    step = load_scenarios(_one("navigate: /settings"))[0].steps[0]
    assert step.navigate is not None
    assert step.navigate.url == "/settings"
    assert _action_of(step) == "navigate"


def test_mapping_form_is_rejected() -> None:
    with pytest.raises(ValidationError, match="navigate takes a URL string"):
        Step.model_validate({"navigate": {"url": "/settings"}})


def test_navigate_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios("- name: t\n  steps:\n    - navigate: /a\n      back: {}\n")


@pytest.mark.parametrize(
    "value", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "JavaScript:x"]
)
def test_non_http_scheme_is_rejected_at_load(value: str) -> None:
    with pytest.raises(ValueError, match="only opens http/https"):
        load_scenarios(_one(f"navigate: '{value}'"))


def test_empty_value_is_rejected_at_load() -> None:
    with pytest.raises(ValueError, match="non-empty URL"):
        load_scenarios(_one("navigate: ''"))


def test_nested_value_is_checked_too() -> None:
    # The scenario-level check walks nested step lists, a `web:` block's included.
    yaml = (
        "- name: t\n  steps:\n    - web:\n        within: { id: host }\n        steps:\n"
        "          - navigate: 'javascript:alert(1)'\n"
    )
    with pytest.raises(ValueError, match="only opens http/https"):
        load_scenarios(yaml)


_BAD = {"navigate": "javascript:alert(1)"}


@pytest.mark.parametrize(
    "body",
    [
        {"setup": [_BAD]},
        {"cleanup": [{"on": "always", "steps": [_BAD]}]},
        {"interrupts": [{"condition": {"exists": {"id": "x"}}, "steps": [_BAD]}]},
        {"steps": [{"if": {"condition": {"exists": {"id": "x"}}, "then": [], "else": [_BAD]}}]},
        {"steps": [{"forEach": {"sel": {"id": "row"}, "as": "r", "steps": [_BAD]}}]},
        {"steps": [{"app": {"bundleId": "com.example.other", "steps": [_BAD]}}]},
        {"steps": [{"group": {"name": "g", "steps": [_BAD]}}]},
    ],
    ids=["setup", "cleanup", "interrupt", "if-else", "forEach", "app", "group"],
)
def test_every_phase_and_container_is_checked(body: dict[str, object]) -> None:
    # Also pins `_iter_steps`, which the `http` extractBody check now shares.
    with pytest.raises(ValueError, match="only opens http/https"):
        Scenario.model_validate({"name": "s", "steps": [], **body})


def test_token_value_is_left_to_run_time() -> None:
    # A `${...}` value can only be judged once substituted; the handler re-checks it then.
    step = load_scenarios(_one("navigate: '${vars.url}'"))[0].steps[0]
    assert step.navigate is not None and step.navigate.url == "${vars.url}"


@pytest.mark.parametrize(
    "value", ["/settings", "settings", "https://other.example/x", "HTTP://h/x", "//host/x"]
)
def test_allowed_values(value: str) -> None:
    assert navigate_url_problem(value) is None


# --- The plain string survives every dump-and-revalidate path ---


def test_dump_round_trips_as_a_string() -> None:
    scenarios = load_scenarios(_one("navigate: /items/1"))
    text = dump_scenarios(scenarios)
    assert "navigate: /items/1" in text
    assert load_scenarios(text)[0].steps[0].navigate == scenarios[0].steps[0].navigate


def test_schema_states_the_string_form() -> None:
    assert Step.model_json_schema(by_alias=True)["$defs"]["Navigate"]["type"] == "string"


# --- Capability preflight: only a backend advertising urlNavigation runs it ---

_MOBILE = frozenset({base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.WEBVIEW})
_WEB = _MOBILE | {base.Capability.URL_NAVIGATION}


def _sc(*steps: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": list(steps)})


def test_preflight_rejects_navigate_without_the_capability() -> None:
    sc = _sc({"tap": {"id": "ok"}}, {"navigate": "/settings"})
    reasons = capability_preflight.unsupported(sc, _MOBILE)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 2: navigate")
    assert "urlNavigation" in reasons[0]
    assert capability_preflight.unsupported(sc, _WEB) == []


def test_preflight_sees_navigate_inside_a_web_block() -> None:
    # The preflight skips `web.steps` for every other construct; a nested `navigate` must still be
    # caught before an iOS device is leased, since the WebView context cannot open a URL either.
    sc = _sc(
        {"web": {"within": {"id": "host"}, "steps": [{"tap": {"id": "a"}}, {"navigate": "/x"}]}}
    )
    reasons = capability_preflight.unsupported(sc, _MOBILE)
    assert reasons == ["step 1 > web[1]: navigate (open a URL; web only, not supported on iOS / "
                       "Android) needs 'urlNavigation'"]  # fmt: skip


def test_preflight_sees_navigate_inside_an_app_block_and_a_lifecycle_phase() -> None:
    sc = Scenario.model_validate(
        {
            "name": "s",
            "setup": [{"navigate": "/a"}],
            "steps": [{"app": {"bundleId": "com.example.other", "steps": [{"navigate": "/b"}]}}],
        }
    )
    reasons = capability_preflight.unsupported(sc, _MOBILE)
    navigate = [r.split(":")[0] for r in reasons if "urlNavigation" in r]
    assert navigate == ["setup [0]", "step 1 > app[0]"]


# --- Handler: dispatch, substitution, and the run-time scheme re-check ---


def test_fake_run_dispatches_goto() -> None:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(_one("navigate: /settings"))[0])
    assert result.ok, result.failure
    assert driver.actions == [("goto", "/settings")]


def test_surrounding_whitespace_is_dropped_before_goto() -> None:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(_one("navigate: '  /settings  '"))[0])
    assert result.ok, result.failure
    assert driver.actions == [("goto", "/settings")]


def test_fake_goto_keeps_the_url_out_of_the_actuation_record() -> None:
    driver = FakeDriver(screen=[])
    driver.goto("/secret-path")
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via) == ("navigate", "url")
    assert "/secret-path" not in repr(record)


def test_token_is_expanded_before_goto() -> None:
    driver = FakeDriver(screen=[])
    scenario = load_scenarios(_one("navigate: '/items/${vars.itemId}'"))[0]
    result = run_scenario(driver, scenario, bindings={"vars.itemId": "42"})
    assert result.ok, result.failure
    assert driver.actions == [("goto", "/items/42")]


@pytest.mark.parametrize("value", ["javascript:alert(1)", "file:///etc/passwd"])
def test_expanded_bad_scheme_fails_the_step(value: str) -> None:
    driver = FakeDriver(screen=[])
    scenario = load_scenarios(_one("navigate: '${vars.url}'"))[0]
    result = run_scenario(driver, scenario, bindings={"vars.url": value})
    assert not result.ok
    assert "only opens http/https" in (result.failure or "")
    assert driver.actions == []


def test_unresolved_token_fails_the_step() -> None:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(_one("navigate: '${vars.missing}'"))[0])
    assert not result.ok
    assert "unresolved token" in (result.failure or "")
    assert driver.actions == []


@pytest.mark.parametrize(
    "driver_cls", [AdbDriver, XcuitestDriver, XcuitestLiveDriver, WebContextDriver]
)
def test_non_web_backends_refuse(driver_cls: Any) -> None:
    # Called unbound: the refusal reads no driver state, so no device is needed to pin it.
    with pytest.raises(base.UnsupportedAction, match="navigate"):
        driver_cls.goto(cast(Any, None), "/x")
    assert base.Capability.URL_NAVIGATION not in getattr(driver_cls, "CAPABILITIES", frozenset())


# --- Playwright `goto`: resolution against the base URL and error mapping ---


class _Page:
    """The slice of a Playwright page `goto` touches; `fail` makes the next `goto` raise it."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.urls: list[str] = []

    def goto(self, url: str) -> object:
        if self.fail is not None:
            raise self.fail
        self.urls.append(url)
        return None

    def on(self, event: str, handler: object) -> None:
        pass


def _web(page: _Page, base_url: str = "https://app.test/app/index.html") -> PlaywrightDriver:
    return PlaywrightDriver(base_url, page=cast(Any, page))


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("/settings", "https://app.test/settings"),
        ("settings", "https://app.test/app/settings"),
        ("https://other.example/x", "https://other.example/x"),
        ("?tab=2", "https://app.test/app/index.html?tab=2"),
    ],
)
def test_goto_resolves_against_the_base_url(ref: str, expected: str) -> None:
    page = _Page()
    drv = _web(page)
    drv.goto(ref)
    assert page.urls == [expected]
    assert [(r.gesture, r.via) for r in drv.drain_actuations().records] == [("navigate", "url")]
    assert base.Capability.URL_NAVIGATION in drv.capabilities()


class _PwError(Exception):
    """Stands in for Playwright's `Error`, which the gate env does not install."""


class TargetClosedError(_PwError):
    """Matched by name, as the driver matches Playwright's private `TargetClosedError`."""


@pytest.fixture
def _pw_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["bajutsu.common.drivers.playwright._functions"], "_PW_ERRORS", (_PwError,)
    )


@pytest.mark.usefixtures("_pw_errors")
def test_a_failed_lookup_fails_the_step_not_the_browser() -> None:
    # The message is real Chromium's for an unresolvable host; it must not become a wedge, which the
    # crawl answers by relaunching the browser and a run would report as a device fault.
    drv = _web(_Page(_PwError("Page.goto: net::ERR_NAME_NOT_RESOLVED at http://nope.invalid/")))
    with pytest.raises(base.SelectorError, match="ERR_NAME_NOT_RESOLVED"):
        drv.goto("http://nope.invalid/")


@pytest.mark.usefixtures("_pw_errors")
def test_an_empty_error_message_names_the_error_type() -> None:
    with pytest.raises(base.SelectorError, match="_PwError"):
        _web(_Page(_PwError(""))).goto("/x")


@pytest.mark.usefixtures("_pw_errors")
def test_a_crashed_page_stays_a_wedge() -> None:
    drv = _web(_Page(_PwError("Page.goto: Navigation failed because page crashed!")))
    with pytest.raises(simctl.DeviceError):
        drv.goto("/x")


@pytest.mark.usefixtures("_pw_errors")
def test_a_blank_first_line_does_not_empty_the_reason() -> None:
    with pytest.raises(base.SelectorError, match="net::ERR_FAILED"):
        _web(_Page(_PwError("\nnet::ERR_FAILED at /x"))).goto("/x")


@pytest.mark.usefixtures("_pw_errors")
def test_a_closed_page_stays_a_wedge() -> None:
    drv = _web(
        _Page(TargetClosedError("Page.goto: Target page, context or browser has been closed"))
    )
    with pytest.raises(simctl.DeviceError):
        drv.goto("/x")


def test_a_404_page_loads_and_passes() -> None:
    # `goto` never reads the response status; the next assertion decides the verdict.
    page = _Page()
    _web(page).goto("/missing")
    assert page.urls == ["https://app.test/missing"]

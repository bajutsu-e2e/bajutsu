"""`openUrl`: hand a URL to the OS mid-scenario on a mobile backend that advertises it (BE-0487).

Covers the DSL's string shorthand and its scheme rule at load time, the ban inside `web:` blocks,
the string round-trip, the generated schema, the capability preflight, the handler's run-time
re-check after substitution, and each driver's route: the adb driver's `am start` output reading
and foreground wait, and the XCUITest driver's mapping of the runner's replies.
"""

from __future__ import annotations

import plistlib
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import bajutsu.common.drivers.adb.adb_driver as adb_driver_impl
from bajutsu.common.backend_cli import adb
from bajutsu.common.capability import capability_preflight
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.playwright import PlaywrightDriver
from bajutsu.common.drivers.webview.web_context_driver import WebContextDriver
from bajutsu.common.drivers.xcuitest import (
    RunnerTarget,
    XcuitestChannelError,
    XcuitestDriver,
    _decode,
    _Reply,
)
from bajutsu.common.drivers.xcuitest_live.xcuitest_live_driver import XcuitestLiveDriver
from bajutsu.common.orchestrator import _action_of, run_scenario
from bajutsu.common.platform_lifecycle.environments.xcuitest._functions import (
    _declared_url_schemes,
)
from bajutsu.common.platform_lifecycle.environments.xcuitest.xcuitest_environment import (
    XcuitestEnvironment,
)
from bajutsu.common.scenario import (
    OpenUrl,
    Scenario,
    Step,
    dump_scenarios,
    expand_components,
    load_component,
    load_scenarios,
)
from bajutsu.common.scenario.models.actions import open_url_problem


def _one(body: str) -> str:
    return f"- name: t\n  steps:\n    - {body}\n"


# --- DSL parse + validation ---


def test_parse_string_shorthand() -> None:
    step = load_scenarios(_one("openUrl: 'myapp://orders/42'"))[0].steps[0]
    assert step.open_url is not None
    assert step.open_url.url == "myapp://orders/42"
    assert _action_of(step) == "open_url"


@pytest.mark.parametrize("value", [{"url": "myapp://a"}, 8080, ["myapp://a"], True])
def test_anything_but_a_string_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError, match="openUrl takes a URL string"):
        Step.model_validate({"openUrl": value})


def test_the_model_still_constructs_from_its_field() -> None:
    assert OpenUrl(url="myapp://x").url == "myapp://x"


def test_open_url_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        load_scenarios("- name: t\n  steps:\n    - openUrl: 'a://b'\n      back: {}\n")


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("/settings", "absolute URL with a scheme"),
        ("settings", "absolute URL with a scheme"),
        ("javascript:alert(1)", "does not open javascript: URLs"),
        ("JavaScript:x", "does not open javascript: URLs"),
        ("data:text/html,hi", "does not open data: URLs"),
        ("file:///etc/passwd", "does not open file: URLs"),
        ("", "non-empty URL"),
    ],
)
def test_bad_value_is_rejected_at_load(value: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_scenarios(_one(f"openUrl: '{value}'"))


@pytest.mark.parametrize(
    "value",
    [
        "myapp://orders/42",
        "https://example.com/promo?a=1&b=2",
        "HTTP://h/x",
        "mailto:a@example.com",
    ],
)
def test_allowed_values(value: str) -> None:
    assert open_url_problem(value) is None


_BAD = {"openUrl": "/relative"}


@pytest.mark.parametrize(
    "body",
    [
        {"setup": [_BAD]},
        {"cleanup": [{"on": "always", "steps": [_BAD]}]},
        {"interrupts": [{"condition": {"exists": {"id": "x"}}, "steps": [_BAD]}]},
        {"steps": [{"if": {"condition": {"exists": {"id": "x"}}, "then": [_BAD]}}]},
        {"steps": [{"if": {"condition": {"exists": {"id": "x"}}, "then": [], "else": [_BAD]}}]},
        {"steps": [{"forEach": {"sel": {"id": "row"}, "as": "r", "steps": [_BAD]}}]},
        {"steps": [{"app": {"bundleId": "com.example.other", "steps": [_BAD]}}]},
        {"steps": [{"group": {"name": "g", "steps": [_BAD]}}]},
    ],
    ids=["setup", "cleanup", "interrupt", "if-then", "if-else", "forEach", "app", "group"],
)
def test_every_phase_and_container_is_checked(body: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="absolute URL with a scheme"):
        Scenario.model_validate({"name": "s", "steps": [], **body})


def test_token_value_is_left_to_run_time() -> None:
    step = load_scenarios(_one("openUrl: '${vars.url}'"))[0].steps[0]
    assert step.open_url is not None and step.open_url.url == "${vars.url}"


def _web(*steps: object) -> dict[str, object]:
    return {"web": {"within": {"id": "host"}, "steps": list(steps)}}


@pytest.mark.parametrize(
    "steps",
    [
        [_web({"openUrl": "myapp://a"})],
        [_web({"if": {"condition": {"exists": {"id": "x"}}, "then": [{"openUrl": "myapp://a"}]}})],
        [{"app": {"bundleId": "com.example.other", "steps": [_web({"openUrl": "myapp://a"})]}}],
        # A token value is no exemption: the rule is about where the step runs, not its URL.
        [_web({"openUrl": "${vars.url}"})],
    ],
    ids=["direct", "nested-if", "web-in-app", "token"],
)
def test_open_url_inside_a_web_block_is_rejected_at_load(steps: list[object]) -> None:
    with pytest.raises(ValueError, match="not allowed inside a web: block"):
        Scenario.model_validate({"name": "s", "steps": steps})


def test_open_url_beside_a_web_block_is_allowed() -> None:
    sc = Scenario.model_validate(
        {"name": "s", "steps": [_web({"tap": {"id": "a"}}), {"openUrl": "myapp://a"}]}
    )
    assert sc.steps[1].open_url is not None


# --- The plain string survives every dump-and-revalidate path ---


def test_dump_round_trips_as_a_string() -> None:
    scenarios = load_scenarios(_one("openUrl: 'myapp://items/1?a=1&b=2'"))
    text = dump_scenarios(scenarios)
    assert "openUrl: myapp://items/1?a=1&b=2" in text
    assert load_scenarios(text)[0].steps[0].open_url == scenarios[0].steps[0].open_url


def test_schema_states_the_string_form() -> None:
    assert Step.model_json_schema(by_alias=True)["$defs"]["OpenUrl"]["type"] == "string"


# --- Capability preflight: only a backend advertising openUrl runs it ---

_WEB_ONLY = frozenset(
    {base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.URL_NAVIGATION}
)
_MOBILE = frozenset({base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.OPEN_URL})


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [], **body})


def test_preflight_rejects_open_url_without_the_capability() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"openUrl": "myapp://a"}])
    reasons = capability_preflight.unsupported(sc, _WEB_ONLY)
    assert reasons == [
        "step 2: openUrl (hand a URL to the OS; iOS and Android only) needs 'openUrl'"
    ]
    assert capability_preflight.unsupported(sc, _MOBILE) == []


def test_preflight_follows_app_blocks_lifecycle_phases_and_interrupts() -> None:
    sc = _sc(
        setup=[{"openUrl": "myapp://a"}],
        steps=[{"app": {"bundleId": "com.example.other", "steps": [{"openUrl": "https://x/"}]}}],
        interrupts=[{"condition": {"exists": {"id": "x"}}, "steps": [{"openUrl": "myapp://b"}]}],
    )
    reasons = capability_preflight.unsupported(sc, _WEB_ONLY)
    assert [r.split(":")[0] for r in reasons if "'openUrl'" in r] == [
        "setup [0]",
        "step 1 > app[0]",
        "interrupts[0] [0]",
    ]


# --- Handler: dispatch, substitution, and the run-time scheme re-check ---


def test_fake_run_dispatches_open_url() -> None:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(_one("openUrl: '  myapp://a  '"))[0])
    assert result.ok, result.failure
    assert driver.actions == [("open_url", "myapp://a")]


def test_fake_open_url_keeps_the_url_out_of_the_actuation_record() -> None:
    driver = FakeDriver(screen=[])
    driver.open_url("myapp://secret")
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via) == ("openUrl", "url")
    assert "secret" not in repr(record)


def test_token_is_expanded_before_open_url() -> None:
    driver = FakeDriver(screen=[])
    scenario = load_scenarios(_one("openUrl: 'myapp://items/${vars.itemId}'"))[0]
    result = run_scenario(driver, scenario, bindings={"vars.itemId": "42"})
    assert result.ok, result.failure
    assert driver.actions == [("open_url", "myapp://items/42")]


@pytest.mark.parametrize(
    ("value", "match"),
    [("javascript:alert(1)", "does not open javascript"), ("/rel", "absolute URL")],
)
def test_expanded_bad_value_fails_the_step(value: str, match: str) -> None:
    driver = FakeDriver(screen=[])
    scenario = load_scenarios(_one("openUrl: '${vars.url}'"))[0]
    result = run_scenario(driver, scenario, bindings={"vars.url": value})
    assert not result.ok
    assert match in (result.failure or "")
    assert driver.actions == []


def test_unresolved_token_fails_the_step() -> None:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(_one("openUrl: '${vars.missing}'"))[0])
    assert not result.ok
    assert "unresolved token" in (result.failure or "")
    assert driver.actions == []


@pytest.mark.parametrize("driver_cls", [PlaywrightDriver, XcuitestLiveDriver, WebContextDriver])
def test_other_backends_refuse(driver_cls: Any) -> None:
    # Called unbound: the refusal reads no driver state, so no device is needed to pin it.
    with pytest.raises(base.UnsupportedAction, match="openUrl"):
        driver_cls.open_url(cast(Any, None), "myapp://x")
    assert base.Capability.OPEN_URL not in getattr(driver_cls, "CAPABILITIES", frozenset())


# --- Android: the `VIEW` intent command, its output reading, and the driver's route ---

_PKG = "com.example.subject"
_OTHER = "com.android.chrome"


def test_view_intent_cmd_quotes_the_url_into_one_shell_line() -> None:
    url = "myapp://items?a=1&b=$(reboot)"
    cmd = adb.view_intent_cmd("S", url, _PKG, single_top=True)
    assert cmd[:4] == ["adb", "-s", "S", "shell"]
    assert len(cmd) == 5  # one line the device's shell parses, so quoting is what keeps it whole
    assert cmd[4] == (
        "am start -W --activity-single-top -a android.intent.action.VIEW "
        f"-d {shlex.quote(url)} {_PKG} 2>&1"
    )
    # The device shell splits the line back into exactly the intended argv, URL intact.
    assert shlex.split(cmd[4])[7:9] == [url, _PKG]


@pytest.mark.parametrize("value", ["myapp://x\nError: boom", "myapp://x\rStatus: ok", "a://b\tc"])
def test_a_line_break_is_refused_by_the_rule_and_the_command(value: str) -> None:
    # `am start` echoes the URL, so a line break inside it would forge a line of the verdict.
    assert "no line break or tab" in (open_url_problem(value) or "")
    with pytest.raises(adb.DeviceError, match="line break or tab"):
        adb.view_intent_cmd("S", value, _PKG)
    with pytest.raises(adb.DeviceError, match="line break or tab"):
        adb.deeplink_cmd("S", value, _PKG)


def test_launch_deeplink_cmd_waits_but_is_not_single_top() -> None:
    line = adb.deeplink_cmd("S", "showcase://tabs/settings", _PKG)[4]
    assert line.startswith("am start -W -a android.intent.action.VIEW")
    assert "--activity-single-top" not in line


_DELIVERED = (
    "Warning: Activity not started, intent has been delivered to currently running top-most "
    "instance."
)
_FRONTED = "Warning: Activity not started, its current task has been brought to the front"
_UNRESOLVED = "Error: Activity not started, unable to resolve Intent { act=VIEW dat=myapp:// }"


@pytest.mark.parametrize(
    ("out", "reason"),
    [
        ("Starting: Intent { act=VIEW }\nStatus: ok\nLaunchState: WARM\nTotalTime: 80\n", None),
        (f"Starting: Intent {{}}\n{_DELIVERED}\nStatus: ok\n", None),
        (f"{_DELIVERED}\n", None),
        (f"Starting: Intent {{}}\n{_UNRESOLVED}\n", _UNRESOLVED),
        # A rejecting line wins over the `Status: ok` that `-W` prints after a warning.
        (f"Starting: Intent {{}}\n{_FRONTED}\nStatus: ok\n", _FRONTED),
        (
            "java.lang.SecurityException: Permission Denial: starting Intent\n",
            "java.lang.SecurityException: Permission Denial: starting Intent",
        ),
        (
            "Warning: Activity not started because the  current activity is being kept for the "
            "user.\nStatus: ok\n",
            "Warning: Activity not started because the  current activity is being kept for the user.",
        ),
        (
            "Warning: Activity not started because intent should be handled by the caller\n"
            "Status: ok\n",
            "Warning: Activity not started because intent should be handled by the caller",
        ),
        ("Starting: Intent {}\nStatus: timeout\n", "Starting: Intent {} / Status: timeout"),
        ("", "am start printed nothing"),
    ],
    ids=[
        "ok",
        "delivered-ok",
        "delivered",
        "error",
        "fronted",
        "exception",
        "switches-canceled",
        "returned-to-caller",
        "timeout",
        "empty",
    ],
)
def test_view_intent_rejection_reads_the_output_table(out: str, reason: str | None) -> None:
    assert adb.view_intent_rejection(out) == reason


def test_the_url_echo_is_not_read_as_an_exception() -> None:
    # The `Starting:` line echoes the URL, which may itself spell "Exception".
    out = "Starting: Intent { act=VIEW dat=myapp://docs/ExceptionHandling }\nStatus: ok\n"
    assert adb.view_intent_rejection(out) is None


def test_a_failed_exit_with_am_output_is_read_as_a_rejection() -> None:
    def run(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args, output=f"{_UNRESOLVED}\n", stderr="")

    assert adb.send_view_intent(run, ["adb"]) == _UNRESOLVED


def test_a_transport_fault_is_re_raised() -> None:
    def run(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args, output="", stderr="error: device offline")

    with pytest.raises(subprocess.CalledProcessError):
        adb.send_view_intent(run, ["adb"])


class _Device:
    """A scripted device: an accepted `VIEW` intent brings its package to the front."""

    def __init__(self, am_out: str = "Status: ok\n", *, moves_front: bool = True) -> None:
        self.front = _PKG
        self.am_out = am_out
        self.moves_front = moves_front
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if len(args) == 5 and "android.intent.action.VIEW" in args[4]:
            if self.moves_front and adb.view_intent_rejection(self.am_out) is None:
                self.front = shlex.split(args[4])[-2]
            return self.am_out
        if "resolve-activity" in args:
            return f"priority=0\n{args[-1]}/.Main\n"
        if args[4:6] == ["am", "start"]:
            self.front = args[args.index("-n") + 1].split("/")[0]
            return "Status: ok\n"
        if args[-2:] == ["activity", "activities"]:
            return (
                "ACTIVITY MANAGER ACTIVITIES\n"
                f"  topResumedActivity=ActivityRecord{{1a2b u0 {self.front}/.Main t42}}\n"
            )
        raise AssertionError(f"unexpected adb call: {args}")

    def intents(self) -> list[str]:
        return [shlex.split(a[4])[-2] for a in self.calls if len(a) == 5 and "VIEW" in a[4]]


@pytest.fixture
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_TIMEOUT", 0.3)
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_POLL", 0.001)
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_POLL_MAX", 0.001)


def test_adb_advertises_open_url() -> None:
    assert base.Capability.OPEN_URL in AdbDriver.CAPABILITIES


@pytest.mark.usefixtures("_fast_poll")
def test_adb_open_url_scopes_the_intent_to_the_app_under_test() -> None:
    device = _Device()
    driver = AdbDriver("emulator-5554", run=device.run, package=_PKG)
    driver.open_url("myapp://orders/42")
    assert device.intents() == [_PKG]
    assert "--activity-single-top" in device.calls[0][4]
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via) == ("openUrl", "url")


@pytest.mark.usefixtures("_fast_poll")
def test_adb_open_url_inside_an_app_block_goes_to_that_app() -> None:
    device = _Device()
    driver = AdbDriver("emulator-5554", run=device.run, package=_PKG)
    driver.enter_app(_OTHER)
    driver.open_url("https://example.com/promo")
    assert device.intents() == [_OTHER]


@pytest.mark.usefixtures("_fast_poll")
def test_adb_open_url_fails_the_step_with_am_starts_line() -> None:
    device = _Device(f"Starting: Intent {{}}\n{_UNRESOLVED}\n")
    driver = AdbDriver("emulator-5554", run=device.run, package=_PKG)
    with pytest.raises(base.SelectorError, match="unable to resolve Intent"):
        driver.open_url("myapp://nowhere")


@pytest.mark.usefixtures("_fast_poll")
def test_adb_open_url_waits_for_the_app_to_hold_the_foreground() -> None:
    device = _Device(moves_front=False)
    device.front = _OTHER
    driver = AdbDriver("emulator-5554", run=device.run, package=_PKG)
    with pytest.raises(base.ElementNotFound, match="did not reach the foreground"):
        driver.open_url("myapp://orders/42")


def test_adb_open_url_with_no_app_is_refused_before_am_start() -> None:
    device = _Device()
    with pytest.raises(base.UnsupportedAction, match="addresses no app"):
        AdbDriver("emulator-5554", run=device.run).open_url("myapp://a")
    assert device.calls == []


def test_adb_open_url_keeps_a_transport_fault_a_device_error() -> None:
    def run(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args, output="", stderr="error: device offline")

    with pytest.raises(adb.DeviceError, match="device offline"):
        AdbDriver("emulator-5554", run=run, package=_PKG).open_url("myapp://a")


def test_launch_deeplink_fails_when_android_did_not_deliver_it() -> None:
    env = adb.Env("S", run=lambda args: f"{_UNRESOLVED}\n")
    with pytest.raises(adb.DeviceError, match="launch deeplink did not open: Error"):
        env.open_url("myapp://nowhere", _PKG)


def test_launch_deeplink_turns_a_transport_fault_into_a_device_error() -> None:
    def run(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args, output="", stderr="error: device offline")

    with pytest.raises(adb.DeviceError, match="device offline"):
        adb.Env("S", run=run).open_url("myapp://a", _PKG)


def test_launch_deeplink_passes_on_status_ok() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return "Status: ok\n"

    adb.Env("S", run=run).open_url("myapp://a?b=1&c=2", _PKG)
    assert "'myapp://a?b=1&c=2'" in calls[0][4]


# --- iOS: the XCUITest driver's `/app/openUrl` call and its reply mapping ---


def _ios(reply: _Reply, sent: list[tuple[str, str, Any]]) -> XcuitestDriver:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return reply

    return XcuitestDriver(transport=transport, sleep=lambda _s: None)


def test_xcuitest_advertises_open_url() -> None:
    assert base.Capability.OPEN_URL in XcuitestDriver.CAPABILITIES


def test_xcuitest_open_url_posts_the_url_to_the_runner() -> None:
    sent: list[tuple[str, str, Any]] = []
    driver = _ios(_Reply(status="ok"), sent)
    driver.open_url("myapp://orders/42?a=1&b=2")
    assert sent == [("POST", "/app/openUrl", {"url": "myapp://orders/42?a=1&b=2"})]
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via) == ("openUrl", "url")


@pytest.mark.parametrize(
    ("reply", "error", "match"),
    [
        (
            _Reply(status="not-opened", reason="Failed to open URL"),
            base.SelectorError,
            "did not open the URL: Failed to open URL",
        ),
        (_Reply(status="not-foreground"), base.ElementNotFound, "did not reach the foreground"),
        (_Reply(status="unsupported"), base.UnsupportedAction, "iOS 16.4"),
        (_Reply(status="stale"), XcuitestChannelError, "status=stale"),
    ],
    ids=["not-opened", "not-foreground", "unsupported", "other"],
)
def test_xcuitest_open_url_maps_each_reply(
    reply: _Reply, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        _ios(reply, []).open_url("myapp://x")


def test_a_not_opened_reply_without_a_reason_still_names_one() -> None:
    with pytest.raises(base.SelectorError, match="the runner gave no reason"):
        _ios(_Reply(status="not-opened"), []).open_url("myapp://x")


def test_the_runner_reason_is_decoded() -> None:
    reply = _decode("/app/openUrl", 200, b'{"status": "not-opened", "reason": "boom"}')
    assert (reply.status, reply.reason) == ("not-opened", "boom")
    assert _decode("/tap", 200, b'{"status": "ok"}').reason is None


def test_a_component_cannot_splice_open_url_under_a_web_block() -> None:
    # The load-time check runs before `use:` expands, so `expand_components` re-runs it.
    component = load_component("steps:\n  - openUrl: 'myapp://a'\n")
    scenarios = load_scenarios(
        "- name: s\n  steps:\n    - web:\n        within: { id: host }\n"
        "        steps:\n          - use: { component: deep.yaml }\n"
    )
    with pytest.raises(ValueError, match="not allowed inside a web: block"):
        expand_components(scenarios, lambda _ref: component)


# --- iOS: the declared-scheme check ahead of the runner's route ---


def _schemes(
    declared: Mapping[str | None, frozenset[str] | None], sent: list[str]
) -> XcuitestDriver:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append(path)
        return _Reply(status="ok")

    return XcuitestDriver(transport=transport, url_schemes=lambda b: declared[b])


def test_an_undeclared_scheme_fails_before_the_url_is_sent() -> None:
    # `XCUIApplication.open(_:)` delivers it regardless, so the refusal has to come first.
    sent: list[str] = []
    driver = _schemes({None: frozenset({"myapp", "alt"})}, sent)
    with pytest.raises(
        base.SelectorError, match=r"the app under test .*'other'.*declared: alt, myapp"
    ):
        driver.open_url("other://x")
    assert sent == []
    assert not driver.drain_actuations().records


def test_a_build_that_declares_no_scheme_refuses_any_custom_one() -> None:
    with pytest.raises(base.SelectorError, match="declared: none"):
        _schemes({None: frozenset()}, []).open_url("myapp://x")


@pytest.mark.parametrize("url", ["myapp://x", "MyApp://x", "https://a.example/b", "HTTP://a.b"])
def test_a_declared_scheme_or_a_web_url_is_sent(url: str) -> None:
    sent: list[str] = []
    _schemes({None: frozenset({"myapp"})}, sent).open_url(url)
    assert sent == ["/app/openUrl"]


def test_an_unreadable_build_leaves_the_verdict_to_the_runner() -> None:
    sent: list[str] = []
    _schemes({None: None}, sent).open_url("anything://x")
    assert sent == ["/app/openUrl"]


def test_the_innermost_entered_app_is_the_one_checked() -> None:
    driver = _schemes(
        {None: frozenset({"base"}), "b.outer": frozenset({"outer"}), "b.inner": frozenset({"in"})},
        [],
    )
    driver.enter_app("b.outer")
    driver.enter_app("b.inner")
    driver.open_url("in://x")
    with pytest.raises(base.SelectorError, match=r"b\.inner does not declare"):
        driver.open_url("outer://x")
    driver.leave_app()
    driver.open_url("outer://x")
    driver.leave_app()
    driver.open_url("base://x")


def test_a_group_member_checks_its_own_app() -> None:
    driver = _schemes({"com.example.auth": frozenset({"auth"})}, [])
    driver.bind_to_member("com.example.auth", RunnerTarget(current="com.example.auth"))
    driver.open_url("auth://x")
    with pytest.raises(base.SelectorError, match=r"com\.example\.auth does not declare"):
        driver.open_url("other://x")


# --- iOS: reading the declared schemes out of a build ---


def _bundle(tmp_path: Path, plist: Mapping[str, Any], name: str = "A.app") -> str:
    app = tmp_path / name
    app.mkdir()
    (app / "Info.plist").write_bytes(plistlib.dumps(dict(plist)))
    return str(app)


def test_declared_url_schemes_merges_every_url_type(tmp_path: Path) -> None:
    path = _bundle(
        tmp_path,
        {
            "CFBundleIdentifier": "com.example.a",
            "CFBundleURLTypes": [
                {"CFBundleURLSchemes": ["MyApp", "alt"]},
                {"CFBundleURLName": "no schemes"},
            ],
        },
    )
    assert _declared_url_schemes(path, "com.example.a") == frozenset({"myapp", "alt"})
    # Another app's build says nothing about this one.
    assert _declared_url_schemes(path, "com.example.b") is None


def test_declared_url_schemes_without_url_types_is_the_empty_set(tmp_path: Path) -> None:
    path = _bundle(tmp_path, {"CFBundleIdentifier": "com.example.a"})
    assert _declared_url_schemes(path, "com.example.a") == frozenset()


@pytest.mark.parametrize(
    "types", ["myapp", ["myapp"], [{"CFBundleURLSchemes": "myapp"}]], ids=["str", "item", "schemes"]
)
def test_declared_url_schemes_of_an_unexpected_shape_is_unknown(
    tmp_path: Path, types: object
) -> None:
    path = _bundle(tmp_path, {"CFBundleIdentifier": "com.example.a", "CFBundleURLTypes": types})
    assert _declared_url_schemes(path, "com.example.a") is None


@pytest.mark.parametrize("raw", [None, b"garbage", b"<?xml version='1.0'?><plist><dict>"])
def test_declared_url_schemes_of_an_unreadable_build_is_unknown(
    tmp_path: Path, raw: bytes | None
) -> None:
    app = tmp_path / "A.app"
    app.mkdir()
    if raw is not None:
        (app / "Info.plist").write_bytes(raw)
    assert _declared_url_schemes(str(app), "com.example.a") is None


def _env_with(
    own: tuple[str, str | None] | None, installed: Mapping[str, str], *, device: bool = False
) -> XcuitestEnvironment:
    def run(args: list[str], _env: Mapping[str, str] | None = None) -> str:
        assert args[:3] == ["xcrun", "simctl", "get_app_container"]
        assert not device  # simctl cannot reach a real device
        if args[4] not in installed:
            raise subprocess.CalledProcessError(2, args)
        return installed[args[4]] + "\n"

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env._own_app = own
    env._is_real_device = device
    return env


def test_the_simulator_reads_any_installed_build(tmp_path: Path) -> None:
    def build(name: str, bundle_id: str) -> str:
        types = [{"CFBundleURLSchemes": [name]}]
        return _bundle(
            tmp_path, {"CFBundleIdentifier": bundle_id, "CFBundleURLTypes": types}, f"{name}.app"
        )

    env = _env_with(
        ("com.a", "/configured/elsewhere.app"),
        {"com.a": build("a", "com.a"), "com.b": build("b", "com.b")},
    )
    assert env._url_schemes(None) == frozenset(
        {"a"}
    )  # the installed build, not the configured path
    assert env._url_schemes("com.b") == frozenset({"b"})
    assert env._url_schemes("com.absent") is None
    assert _env_with(None, {})._url_schemes(None) is None  # nothing started yet


def test_a_real_device_reads_the_configured_build_of_its_own_app_alone(tmp_path: Path) -> None:
    path = _bundle(
        tmp_path,
        {"CFBundleIdentifier": "com.a", "CFBundleURLTypes": [{"CFBundleURLSchemes": ["a"]}]},
    )
    env = _env_with(("com.a", path), {}, device=True)
    assert env._url_schemes(None) == frozenset({"a"})
    assert env._url_schemes("com.a") == frozenset({"a"})
    assert env._url_schemes("com.b") is None
    assert _env_with(("com.a", None), {}, device=True)._url_schemes(None) is None

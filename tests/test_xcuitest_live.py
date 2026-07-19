"""Tests for the live-route WebDriver transport (BE-0238, live route Slice A).

The batch route packages an app for AWS Device Farm; the *live* route drives a reserved iOS device
that a self-hosted Appium grid exposes over a W3C WebDriver endpoint. Unit 4 shipped the `appium`
`DeviceProvider` (the seam that hands a run that endpoint as its udid spec) but not the transport that
drives it. This slice adds that transport: a minimal in-house W3C WebDriver client, a live driver that
keeps element resolution Python-side (determinism first), and the `environment_for` routing that
recognizes an `http(s)://` udid spec and opens a WebDriver session instead of running simctl /
`xcodebuild`. The WebDriver wire is the sanctioned fake point — no grid, no device on the gate.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from typing import Any

import pytest

from bajutsu.config import load_config, resolve
from bajutsu.drivers import base
from bajutsu.drivers.xcuitest_live import (
    BACKSPACE_KEY,
    ELEMENT_KEY,
    WebDriverClient,
    WebDriverError,
    XcuitestLiveDriver,
)
from bajutsu.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.platform_lifecycle.environments.xcuitest_live import (
    XcuitestLiveEnvironment,
    is_webdriver_endpoint,
)
from bajutsu.platform_lifecycle.factories import environment_for
from bajutsu.scenario import Preconditions
from bajutsu.scenario.models.scenario import Scenario

_ENDPOINT = "http://grid.local:4723"


class _FakeGrid:
    """A fake W3C WebDriver server: one open session over a fixed element table.

    Stands in for an Appium grid at the network boundary — the transport the client and driver drive
    against, so their request/response mapping is exercised with no real grid. Each element is a plain
    dict of the attributes Appium's XCUITest driver exposes (`name` / `label` / `value` / `type` /
    `enabled` / `selected` / `rect`); the fake serves them from the same table it lists.
    """

    def __init__(self, elements: list[dict[str, Any]], *, ready: bool = True) -> None:
        self._elements = elements
        self._ready = ready
        self._active_id = elements[0]["id"] if elements else "active-el"
        self.session: str | None = None
        self.deleted = False
        self.clicked: list[str] = []
        self.calls: list[tuple[str, str]] = []
        # Slice B records: each execute/sync gesture as (script, args), each send-keys as (id, text).
        self.executed: list[tuple[str, Any]] = []
        self.typed: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        self.calls.append((method, path))
        # The body-carrying Slice B endpoints are handled here (where the body is in scope); every
        # other route stays in `_route`, whose signature the readiness-failure fake below overrides.
        if method == "POST" and path == "/session/sess-1/execute/sync":
            assert body is not None
            self.executed.append((body["script"], body["args"]))
            return 200, {"value": None}
        if method == "GET" and path == "/session/sess-1/element/active":
            return 200, {"value": {ELEMENT_KEY: self._active_id}}
        if method == "POST" and (m := re.fullmatch(r"/session/sess-1/element/(.+)/value", path)):
            assert body is not None
            self.typed.append((m.group(1), body["text"]))
            return 200, {"value": None}
        return self._route(method, path)

    def _by_id(self, elid: str) -> dict[str, Any]:
        for el in self._elements:
            if el["id"] == elid:
                return el
        raise AssertionError(f"unknown element id: {elid}")

    def _route(self, method: str, path: str) -> tuple[int, Any]:
        if method == "POST" and path == "/session":
            self.session = "sess-1"
            return 200, {"value": {"sessionId": "sess-1", "capabilities": {}}}
        if method == "DELETE" and path == "/session/sess-1":
            self.deleted = True
            return 200, {"value": None}
        if method == "GET" and path == "/status":
            return 200, {"value": {"ready": self._ready}}
        if method == "POST" and path == "/session/sess-1/elements":
            return 200, {"value": [{ELEMENT_KEY: el["id"]} for el in self._elements]}
        if m := re.fullmatch(r"/session/sess-1/element/(.+)/attribute/(.+)", path):
            return 200, {"value": self._by_id(m.group(1)).get(m.group(2))}
        if m := re.fullmatch(r"/session/sess-1/element/(.+)/rect", path):
            return 200, {"value": self._by_id(m.group(1))["rect"]}
        if m := re.fullmatch(r"/session/sess-1/element/(.+)/click", path):
            self.clicked.append(m.group(1))
            return 200, {"value": None}
        if method == "GET" and path == "/session/sess-1/screenshot":
            return 200, {"value": base64.b64encode(b"PNGDATA").decode()}
        raise AssertionError(f"unexpected {method} {path}")


def _rect(x: float = 0, y: float = 0, w: float = 0, h: float = 0) -> dict[str, float]:
    return {"x": x, "y": y, "width": w, "height": h}


# --- the in-house W3C WebDriver client --- #


def test_client_new_session_returns_the_session_id() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    assert client.new_session({"platformName": "iOS"}) == "sess-1"
    # The capabilities are sent under the W3C `alwaysMatch` envelope.
    assert ("POST", "/session") in grid.calls


def test_client_delete_session_closes_the_open_session() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({"platformName": "iOS"})
    client.delete_session()
    assert grid.deleted is True


def test_client_delete_session_without_one_is_a_no_op() -> None:
    grid = _FakeGrid([])
    WebDriverClient(grid).delete_session()  # never opened a session
    assert grid.deleted is False


def test_client_raises_on_a_malformed_response() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        return 500, {"error": "boom"}  # no `value` envelope

    with pytest.raises(WebDriverError):
        WebDriverClient(transport).new_session({})


# --- the live driver: query keeps resolution Python-side --- #


def test_query_builds_elements_from_the_grid() -> None:
    grid = _FakeGrid(
        [
            {
                "id": "e1",
                "name": "stable.submit",
                "label": "Submit",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": _rect(1, 2, 3, 4),
            }
        ]
    )
    client = WebDriverClient(grid)
    client.new_session({})
    driver = XcuitestLiveDriver(client)

    (el,) = driver.query()
    assert el["identifier"] == "stable.submit"
    assert el["label"] == "Submit"
    assert el["value"] is None
    assert "button" in el["traits"]  # XCUIElementType prefix stripped, first letter lowered
    assert el["frame"] == (1.0, 2.0, 3.0, 4.0)


def test_query_normalizes_disabled_and_selected_traits() -> None:
    grid = _FakeGrid(
        [
            {
                "id": "e1",
                "name": None,
                "label": "Toggle",
                "value": None,
                "type": "XCUIElementTypeSwitch",
                "enabled": "false",
                "selected": "true",
                "rect": _rect(),
            }
        ]
    )
    client = WebDriverClient(grid)
    client.new_session({})
    (el,) = XcuitestLiveDriver(client).query()
    assert base.Trait.NOT_ENABLED in el["traits"]
    assert base.Trait.SELECTED in el["traits"]


def test_tap_resolves_python_side_then_clicks_the_resolved_handle() -> None:
    grid = _FakeGrid(
        [
            {
                "id": "e1",
                "name": "stable.cancel",
                "label": "Cancel",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": _rect(),
            },
            {
                "id": "e2",
                "name": "stable.ok",
                "label": "OK",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": _rect(),
            },
        ]
    )
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).tap({"id": "stable.ok"})
    assert grid.clicked == ["e2"]  # the element the selector resolved to, by its WebDriver handle


def test_tap_on_an_ambiguous_selector_fails_before_any_click() -> None:
    grid = _FakeGrid(
        [
            {
                "id": "e1",
                "name": "dup",
                "label": "A",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": _rect(),
            },
            {
                "id": "e2",
                "name": "dup",
                "label": "B",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": _rect(),
            },
        ]
    )
    client = WebDriverClient(grid)
    client.new_session({})
    with pytest.raises(base.AmbiguousSelector):
        XcuitestLiveDriver(client).tap({"id": "dup"})
    assert grid.clicked == []  # an ambiguous selector never actuates (determinism first)


def test_tap_on_a_missing_selector_raises_element_not_found() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    with pytest.raises(base.ElementNotFound):
        XcuitestLiveDriver(client).tap({"id": "nope"})


def test_screenshot_decodes_the_base64_png(tmp_path: Any) -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    out = tmp_path / "shot.png"
    XcuitestLiveDriver(client).screenshot(str(out))
    assert out.read_bytes() == b"PNGDATA"


def test_await_ready_returns_once_the_grid_reports_ready() -> None:
    grid = _FakeGrid([], ready=True)
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).await_ready(timeout=1.0, poll=0.01)  # returns without raising


def test_await_ready_times_out_when_the_grid_never_readies() -> None:
    grid = _FakeGrid([], ready=False)
    client = WebDriverClient(grid)
    client.new_session({})
    with pytest.raises(WebDriverError):
        XcuitestLiveDriver(client).await_ready(timeout=0.05, poll=0.01)


def _button(elid: str, name: str) -> dict[str, Any]:
    return {
        "id": elid,
        "name": name,
        "label": name,
        "value": None,
        "type": "XCUIElementTypeButton",
        "enabled": "true",
        "selected": "false",
        "rect": _rect(),
    }


def test_tap_point_issues_a_coordinate_mobile_tap() -> None:
    # The one gesture with no element/handle (system alerts): a raw coordinate tap.
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).tap_point((10.0, 20.0))
    assert grid.executed == [("mobile: tap", [{"x": 10.0, "y": 20.0}])]


def test_double_tap_resolves_python_side_then_taps_the_handle() -> None:
    grid = _FakeGrid([_button("e1", "stable.ok")])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).double_tap({"id": "stable.ok"})
    assert grid.executed == [("mobile: doubleTap", [{"elementId": "e1"}])]


def test_long_press_passes_the_duration() -> None:
    grid = _FakeGrid([_button("e1", "stable.ok")])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).long_press({"id": "stable.ok"}, 1.5)
    assert grid.executed == [("mobile: touchAndHold", [{"elementId": "e1", "duration": 1.5}])]


def test_swipe_issues_a_drag_from_to() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).swipe((5.0, 10.0), (5.0, 200.0))
    (script, (arg,)) = grid.executed[0]
    assert script == "mobile: dragFromToForDuration"
    assert (arg["fromX"], arg["fromY"], arg["toX"], arg["toY"]) == (5.0, 10.0, 5.0, 200.0)
    assert arg["duration"] > 0  # a real XCUITest drag needs a duration


def test_scroll_is_a_swipe() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).scroll((5.0, 200.0), (5.0, 10.0))
    (script, (arg,)) = grid.executed[0]
    assert script == "mobile: dragFromToForDuration"
    assert (arg["fromX"], arg["fromY"], arg["toX"], arg["toY"]) == (5.0, 200.0, 5.0, 10.0)


def test_pinch_velocity_sign_follows_the_scale() -> None:
    # Appium's `mobile: pinch` requires velocity > 0 to zoom in (scale > 1) and < 0 to zoom out.
    grid = _FakeGrid([_button("e1", "map")])
    client = WebDriverClient(grid)
    client.new_session({})
    driver = XcuitestLiveDriver(client)
    driver.pinch({"id": "map"}, 2.0)
    driver.pinch({"id": "map"}, 0.5)
    (_, (zoom_in,)) = grid.executed[0]
    (_, (zoom_out,)) = grid.executed[1]
    assert grid.executed[0][0] == "mobile: pinch"
    assert zoom_in["scale"] == 2.0 and zoom_in["velocity"] > 0
    assert zoom_out["scale"] == 0.5 and zoom_out["velocity"] < 0


def test_rotate_passes_rotation_and_keeps_velocity_positive() -> None:
    # `XCUIElement.rotate(_:withVelocity:)` — which `mobile: rotateElement` wraps — takes `rotation`
    # as a signed direction and `velocity` as a rate/magnitude (always positive, like codegen's fixed
    # `withVelocity: 1.0`). Unlike `pinch`, the direction is carried by `rotation`, not `velocity`.
    grid = _FakeGrid([_button("e1", "dial")])
    client = WebDriverClient(grid)
    client.new_session({})
    driver = XcuitestLiveDriver(client)
    driver.rotate({"id": "dial"}, 1.57)
    driver.rotate({"id": "dial"}, -1.57)
    (_, (clockwise,)) = grid.executed[0]
    (_, (counter,)) = grid.executed[1]
    assert clockwise["rotation"] == 1.57 and clockwise["velocity"] > 0
    assert (
        counter["rotation"] == -1.57 and counter["velocity"] > 0
    )  # direction via rotation, not sign


def test_type_text_sends_keys_to_the_active_element() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).type_text("hello")
    assert grid.typed == [("active-el", "hello")]  # typed into the focused field


def test_delete_text_sends_backspaces_to_the_active_element() -> None:
    grid = _FakeGrid([])
    client = WebDriverClient(grid)
    client.new_session({})
    XcuitestLiveDriver(client).delete_text(3)
    assert grid.typed == [("active-el", BACKSPACE_KEY * 3)]  # the W3C backspace, once per count


def test_select_all_and_copy_are_unsupported_on_the_live_route() -> None:
    # Neither has a first-class Appium XCUITest command; the local runner does them natively. On the
    # live route they fail loudly (determinism first) rather than silently no-op'ing.
    driver = XcuitestLiveDriver(WebDriverClient(_FakeGrid([])))
    with pytest.raises(base.UnsupportedAction):
        driver.select_all()
    with pytest.raises(base.UnsupportedAction):
        driver.copy_selection()


def test_a_gesture_on_an_ambiguous_selector_fails_before_actuation() -> None:
    grid = _FakeGrid([_button("e1", "dup"), _button("e2", "dup")])
    client = WebDriverClient(grid)
    client.new_session({})
    with pytest.raises(base.AmbiguousSelector):
        XcuitestLiveDriver(client).double_tap({"id": "dup"})
    assert grid.executed == []  # resolution fails before any WebDriver actuation


def test_capabilities_include_multitouch_but_exclude_simctl_backed() -> None:
    # Slice B wires the two-finger pinch / rotate, so the live driver now advertises MULTI_TOUCH; the
    # simctl device-control family still never applies to a real cloud device.
    caps = XcuitestLiveDriver(WebDriverClient(_FakeGrid([]))).capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.MULTI_TOUCH in caps
    assert not (caps & base.DEVICE_CONTROL_ALL)


# --- routing: an http(s) endpoint takes the live path --- #


def test_is_webdriver_endpoint_recognizes_urls() -> None:
    assert is_webdriver_endpoint("http://grid.local:4723") is True
    assert is_webdriver_endpoint("https://grid.local/wd/hub") is True
    assert is_webdriver_endpoint("00008030-000A1B2C3D4E") is False
    assert is_webdriver_endpoint("booted") is False


def test_environment_for_routes_an_endpoint_to_the_live_environment() -> None:
    env = environment_for("xcuitest", _ENDPOINT)
    assert isinstance(env, XcuitestLiveEnvironment)


def test_environment_for_keeps_a_udid_on_the_simulator_path() -> None:
    env = environment_for("xcuitest", "00008030-000A1B2C3D4E")
    assert isinstance(env, XcuitestEnvironment)


# --- the live environment: open a session, skip simctl / xcodebuild --- #


def _live_eff() -> Any:
    return resolve(
        load_config(
            "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
            "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
        ),
        "s",
    )


def _live_eff_with(**kwargs: str) -> Any:
    """Return a live Effective with target-level launchArgs / launchEnv set."""
    extra = ""
    if "launch_args" in kwargs:
        extra += f'    launchArgs: ["{kwargs["launch_args"]}"]\n'
    if "launch_env" in kwargs:
        k, v = next(iter(kwargs["launch_env"].items()))
        extra += f"    launchEnv:\n      {k}: {v}\n"
    return resolve(
        load_config(
            "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
            f"{extra}"
            "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
        ),
        "s",
    )


def test_live_environment_start_opens_a_session_and_returns_a_live_driver() -> None:
    grid = _FakeGrid([])
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _endpoint: grid)
    driver = env.start(_live_eff(), Preconditions())
    assert isinstance(driver, XcuitestLiveDriver)
    assert grid.session == "sess-1"  # a WebDriver session was opened, no simctl / xcodebuild
    env.teardown(driver, _live_eff())
    assert grid.deleted is True  # the session is closed on teardown


def test_live_environment_passes_the_bundle_id_in_the_session_capabilities() -> None:
    captured: dict[str, Any] = {}

    class _CapturingGrid(_FakeGrid):
        def __call__(
            self, method: str, path: str, body: Mapping[str, Any] | None
        ) -> tuple[int, Any]:
            if method == "POST" and path == "/session":
                captured["body"] = body
            return super().__call__(method, path, body)

    cap_grid = _CapturingGrid([])
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: cap_grid)
    env.start(_live_eff(), Preconditions())
    always = captured["body"]["capabilities"]["alwaysMatch"]
    assert always["appium:bundleId"] == "com.x"


def test_live_environment_start_raises_on_erase() -> None:
    # `erase` is a simctl operation; the live route has no simctl, so it fails loudly rather than
    # silently no-op'ing (determinism first — the scenario author must know erase had no effect).
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(erase=True))


def test_live_environment_start_raises_on_permissions() -> None:
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(), permissions={"photos": "allow"})


def test_live_environment_start_raises_on_extra_env() -> None:
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(), extra_env={"FOO": "bar"})


def test_live_environment_crawl_reset_raises_unsupported_action() -> None:
    # `crawl_reset` would otherwise fall through to the base, which builds `simctl.Env(endpoint)`
    # — the URL is rejected there with a confusing DeviceError. The override raises clearly instead.
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    reset = env.crawl_reset(_live_eff())
    driver = XcuitestLiveDriver(WebDriverClient(_FakeGrid([])))
    with pytest.raises(base.UnsupportedAction):
        reset(driver)


def test_live_environment_relauncher_raises_unsupported_action() -> None:
    # `relauncher()` must not reach simctl (which rejects the URL endpoint). Mirrors the test for
    # `crawl_reset`, which has the same shape and the same reason for the override.
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    eff = _live_eff()
    driver = XcuitestLiveDriver(WebDriverClient(_FakeGrid([])))
    relaunch = env.relauncher(eff, Scenario(name="t", steps=[]), driver)
    with pytest.raises(base.UnsupportedAction):
        relaunch(None)


def test_find_elements_raises_on_non_list_response() -> None:
    # If the WebDriver server returns a non-list `value` for `findElements`, we should get a clear
    # WebDriverError rather than a bare TypeError/KeyError from indexing.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        if method == "POST" and path == "/session":
            return 200, {"value": {"sessionId": "s1", "capabilities": {}}}
        if method == "POST" and path == "/session/s1/elements":
            return 200, {"value": "not-a-list"}  # malformed
        raise AssertionError(f"unexpected {method} {path}")

    client = WebDriverClient(transport)
    client.new_session({})
    with pytest.raises(WebDriverError, match="not a list"):
        client.find_elements("xpath", "//*")


def test_find_elements_raises_on_item_missing_element_key() -> None:
    # A list reply where an item lacks the W3C element-reference key raises WebDriverError, not
    # a raw KeyError — every malformed reply from this client is a WebDriverError.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        if method == "POST" and path == "/session":
            return 200, {"value": {"sessionId": "s1", "capabilities": {}}}
        if method == "POST" and path == "/session/s1/elements":
            return 200, {"value": [{"wrong-key": "e1"}]}  # missing ELEMENT_KEY
        raise AssertionError(f"unexpected {method} {path}")

    client = WebDriverClient(transport)
    client.new_session({})
    with pytest.raises(WebDriverError, match="missing"):
        client.find_elements("xpath", "//*")


def test_active_element_raises_when_the_reply_lacks_the_element_key() -> None:
    # A malformed active-element reply is an endpoint failure, surfaced as WebDriverError rather than
    # a bare KeyError — consistent with every other malformed reply from this client.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> tuple[int, Any]:
        if method == "POST" and path == "/session":
            return 200, {"value": {"sessionId": "s1", "capabilities": {}}}
        if method == "GET" and path == "/session/s1/element/active":
            return 200, {"value": {"wrong-key": "e1"}}  # missing ELEMENT_KEY
        raise AssertionError(f"unexpected {method} {path}")

    client = WebDriverClient(transport)
    client.new_session({})
    with pytest.raises(WebDriverError, match="missing"):
        client.active_element()


def test_driver_interval_returns_none_for_all_capture_kinds(tmp_path: Any) -> None:
    # `XcuitestLiveDriver` exposes `driver_interval` so the evidence `FileSink` routes through the
    # driver path rather than the simctl path (which would call `simctl.validated_udid(endpoint)`
    # and crash on the URL). Each kind returns None — no in-driver recording in Slice A.
    driver = XcuitestLiveDriver(WebDriverClient(_FakeGrid([])))
    assert driver.driver_interval("video", tmp_path / "out.mp4") is None
    assert driver.driver_interval("deviceLog", tmp_path / "out.log") is None


def test_live_environment_start_raises_on_deeplink() -> None:
    # `deeplink` is not wired on the live route; the run must fail loudly rather than silently no-op.
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(deeplink="myapp://home"))


def test_live_environment_start_raises_on_launch_args() -> None:
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(launch_args=["--reset"]))


def test_live_environment_start_raises_on_launch_env() -> None:
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(launch_env={"MODE": "test"}))


def test_live_environment_start_raises_on_eff_launch_args() -> None:
    # Target-level `launchArgs` in the effective config is silently dropped unless guarded — the same
    # loud-fail rule that pre.launch_args already applies, extended to the target-level counterpart.
    eff = resolve(
        load_config(
            "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
            '    launchArgs: ["--test-mode"]\n'
            "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
        ),
        "s",
    )
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(eff, Preconditions())


def test_live_environment_start_raises_on_eff_launch_env() -> None:
    eff = resolve(
        load_config(
            "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
            "    launchEnv:\n      MODE: test\n"
            "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
        ),
        "s",
    )
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(eff, Preconditions())


def test_live_environment_start_raises_on_locale() -> None:
    # `locale` is passed to simctl on the local route; the live route has no simctl, so it fails
    # loudly rather than silently no-op'ing (determinism first — the same guard as erase/deeplink/etc.)
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    with pytest.raises(base.UnsupportedAction):
        env.start(_live_eff(), Preconditions(locale="ja_JP"))


def test_live_environment_start_closes_session_on_await_ready_failure() -> None:
    # If `await_ready()` raises, the already-open WebDriver session must be closed — `launch_driver`
    # has no try/finally, and `pool.py` only wires teardown after `start()` succeeds, so a readiness
    # failure leaks the reserved cloud device's session without the fix.
    class _FailReadyGrid(_FakeGrid):
        def _route(self, method: str, path: str) -> tuple[int, Any]:
            if method == "GET" and path == "/status":
                return 200, {
                    "error": "not ready"
                }  # missing "value" key → WebDriverError immediately
            return super()._route(method, path)

    grid = _FailReadyGrid([])
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: grid)
    with pytest.raises(WebDriverError):
        env.start(_live_eff(), Preconditions())
    assert grid.deleted is True  # session must be released even on await_ready failure


def test_live_environment_captures_video_is_false() -> None:
    # The live route has no simctl interval; `XcuitestLiveDriver.driver_interval` returns `None` for
    # every kind including "video". Inheriting `_DeviceEnvironment.captures_video() -> True` would
    # have `record` tag a scenario with `capture: [video]` that silently records nothing on replay.
    env = XcuitestLiveEnvironment("xcuitest", _ENDPOINT, transport_factory=lambda _e: _FakeGrid([]))
    assert env.captures_video() is False


def test_query_raises_webdriver_error_on_null_rect_coordinate() -> None:
    # If a grid returns `null` for a rect coordinate, `float(None)` in `_snapshot` would raise a
    # bare TypeError; the driver should surface it as WebDriverError (an infra failure, not a test
    # outcome), consistent with every other malformed-reply case in this module.
    grid = _FakeGrid(
        [
            {
                "id": "e1",
                "name": "btn",
                "label": "OK",
                "value": None,
                "type": "XCUIElementTypeButton",
                "enabled": "true",
                "selected": "false",
                "rect": {"x": None, "y": 0, "width": 10, "height": 10},
            }
        ]
    )
    client = WebDriverClient(grid)
    client.new_session({})
    with pytest.raises(WebDriverError):
        XcuitestLiveDriver(client).query()

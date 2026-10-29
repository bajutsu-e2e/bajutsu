"""Route tokens: what a job needs and a worker advertises, per environment route (BE-0467)."""

from __future__ import annotations

from pathlib import Path

from bajutsu.common.capability.route_tokens import (
    UNCHECKED_NOTICE,
    advertised_route_tokens,
    cap_token,
    report_notices,
    route_tokens,
    setting_token,
    token_route,
    worker_routes,
)
from bajutsu.common.capability.worker_capability import (
    parse_worker_capability,
    reserved_routing_tokens,
)
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.target_lint import required_route_tokens
from bajutsu.serve.capabilities import (
    can_serve,
    checks_routes,
    route_refusal,
    unchecked_lease,
    worker_capabilities,
)

_CONFIG = """\
targets:
  sim:
    platform: ios
    app: {id: com.x}
  phone:
    platform: ios
    app: {id: com.x}
    runsOn: {ios: {kind: device}}
  phone-ok:
    platform: ios
    app: {id: com.x}
    runsOn: {ios: {kind: device}}
    run:
      ignoredSettings:
        erase: "this lane accepts state from earlier runs"
  site:
    platform: web
    app: {url: "http://x/"}
"""

_LOCATE = """\
- name: locate
  tags: [geo]
  preconditions: {run: {erase: true}}
  steps:
    - wait: { for: { id: a }, timeout: 1 }
    - setLocation: { lat: 1, lon: 2 }
- name: plain
  steps:
    - wait: { for: { id: a }, timeout: 1 }
"""


def _effective(name: str) -> Effective:
    return resolve(load_config(_CONFIG), name)


def _needs(
    tmp_path: Path,
    target: str,
    *,
    backend: str = "",
    exclude: tuple[str, ...] = (),
    grid: bool = False,
) -> dict[str, list[str]]:
    path = tmp_path / "s.yaml"
    path.write_text(_LOCATE, encoding="utf-8")
    return required_route_tokens(
        _LOCATE, path, _effective, target, backend=backend, exclude=exclude, grid=grid
    )


# --- the tokens --- #


def test_a_token_names_its_route() -> None:
    assert cap_token("ios-device", "query") == "cap:ios-device:query"
    assert setting_token("web", "erase") == "setting:web:erase"
    assert token_route("cap:ios-live:query") == "ios-live"
    assert token_route("platform:ios") is None


def test_each_route_advertises_its_own_capabilities_and_settings() -> None:
    sim, device = route_tokens("ios-simulator"), route_tokens("ios-device")
    assert cap_token("ios-simulator", "deviceControl.setLocation") in sim
    assert cap_token("ios-device", "deviceControl.setLocation") not in device
    assert setting_token("ios-simulator", "erase") in sim
    assert setting_token("ios-device", "erase") not in device
    # A web run's fresh context satisfies `erase`, so the web route advertises it.
    assert setting_token("web", "erase") in route_tokens("web")


def test_a_worker_drives_one_route_per_driver() -> None:
    assert worker_routes(["xcuitest", "playwright", "fake"]) == ["ios-simulator", "web"]
    assert worker_routes(["xcuitest"], real_devices=True) == ["ios-simulator", "ios-device"]
    assert worker_routes([], appium=True) == ["ios-live"]
    # The real-device route needs the driver that reaches it.
    assert worker_routes(["adb"], real_devices=True) == ["android"]


def test_a_mac_serving_two_routes_advertises_both_sets_unintersected() -> None:
    both = advertised_route_tokens(["ios-simulator", "ios-device"])
    assert cap_token("ios-simulator", "deviceControl.setLocation") in both
    assert cap_token("ios-device", "query") in both


def test_cap_and_setting_are_reserved_prefixes() -> None:
    assert reserved_routing_tokens(["cap:web:query", "setting:web:erase", "ios18"]) == [
        "cap:web:query",
        "setting:web:erase",
    ]


def test_worker_yaml_declares_real_devices() -> None:
    cap = parse_worker_capability("version: 1\ndrivers: [xcuitest]\nrealDevices: true\n")
    assert cap.real_devices is True
    assert parse_worker_capability("version: 1\ndrivers: [xcuitest]\n").real_devices is False


# --- what a job needs --- #


def test_a_job_needs_each_step_capability_and_setting_on_its_route(tmp_path: Path) -> None:
    needs = _needs(tmp_path, "sim")
    assert cap_token("ios-simulator", "deviceControl.setLocation") in needs
    assert cap_token("ios-simulator", "query") in needs
    assert setting_token("ios-simulator", "erase") in needs
    assert any(
        "setLocation" in why
        for why in needs[cap_token("ios-simulator", "deviceControl.setLocation")]
    )


def test_an_ignored_setting_needs_no_token(tmp_path: Path) -> None:
    assert setting_token("ios-device", "erase") in _needs(tmp_path, "phone")
    assert setting_token("ios-device", "erase") not in _needs(tmp_path, "phone-ok")


def test_tag_selection_narrows_what_a_job_needs(tmp_path: Path) -> None:
    needs = _needs(tmp_path, "sim", exclude=("geo",))
    assert cap_token("ios-simulator", "deviceControl.setLocation") not in needs


def test_a_fake_backend_needs_no_route_token(tmp_path: Path) -> None:
    assert _needs(tmp_path, "sim", backend="fake") == {}


def test_a_grid_job_puts_its_ios_target_on_the_live_route(tmp_path: Path) -> None:
    needs = _needs(tmp_path, "sim", grid=True)
    assert cap_token("ios-live", "deviceControl.setLocation") in needs
    assert not any(token.startswith("cap:ios-simulator:") for token in needs)


# --- matching --- #


def test_a_simulator_worker_serves_a_location_job_and_a_device_worker_does_not(
    tmp_path: Path,
) -> None:
    needs = sorted(_needs(tmp_path, "sim", exclude=("plain",)))
    sim = worker_capabilities(["ios"], routes=["ios-simulator"])
    device_only = worker_capabilities(["ios"], routes=["ios-device"])
    assert can_serve(["platform:ios", *needs], sim)
    assert not can_serve(["platform:ios", *needs], device_only)


def test_a_worker_from_before_this_item_matches_on_today_s_tokens() -> None:
    required = ["platform:ios", cap_token("ios-simulator", "query")]
    legacy = {"platform:ios"}
    assert not checks_routes(legacy)
    assert can_serve(required, legacy)
    assert unchecked_lease(required, legacy)
    assert not unchecked_lease(["platform:ios"], legacy)
    assert not unchecked_lease(required, {"platform:ios", *route_tokens("ios-simulator")})


def test_a_job_no_live_worker_drives_the_route_of_stays_queued() -> None:
    required = ["platform:ios", cap_token("ios-device", "query")]
    # No live worker at all (a cold start), or none driving the device route.
    assert route_refusal(required, []) is None
    assert route_refusal(required, [{"platform:web", *route_tokens("web")}]) is None


def test_a_job_is_refused_when_its_route_s_drivers_all_lack_a_token() -> None:
    required = ["platform:ios", cap_token("ios-device", "deviceControl.setLocation")]
    device = {"platform:ios", *route_tokens("ios-device")}
    assert route_refusal(required, [device]) == [
        cap_token("ios-device", "deviceControl.setLocation")
    ]


def test_a_legacy_worker_that_can_serve_the_job_keeps_it_from_refusal() -> None:
    required = ["platform:ios", cap_token("ios-device", "deviceControl.setLocation")]
    device = {"platform:ios", *route_tokens("ios-device")}
    assert route_refusal(required, [device, {"platform:ios"}]) is None


# --- the report --- #


def test_an_unchecked_lease_shows_a_notice_in_the_report() -> None:
    assert report_notices({"workerRouting": "unchecked"}) == [UNCHECKED_NOTICE]
    assert report_notices({}) == []
    assert report_notices(None) == []


def test_the_html_report_lists_run_notices_apart() -> None:
    from bajutsu.common.report.html import html_report

    assert "notices" not in html_report("r1", [])
    page = html_report("r1", [], notices=[UNCHECKED_NOTICE])
    assert "notices" in page
    assert "advertises no step capabilities or settings (BE-0467)" in page

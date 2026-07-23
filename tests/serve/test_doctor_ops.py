"""Tests for the doctor operations layer (BE-0024).

Operations-level tests for the doctor/preflight endpoint — no HTTP, no Simulator.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import project

from bajutsu import simctl
from bajutsu.drivers import base
from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState


def _state(tmp_path: Path, config_text: str | None = None) -> ServeState:
    """Build a ServeState with the given config text, or the default project config."""
    if config_text is None:
        _scn_dir, cfg, runs = project(tmp_path)
        return ServeState(runs_dir=runs, config=cfg, cwd=tmp_path)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(config_text, encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    return ServeState(runs_dir=runs, config=cfg, cwd=tmp_path)


def test_no_config_returns_400(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    state = ServeState(runs_dir=runs, config=None, cwd=tmp_path)
    payload, status = ops.doctor_check(state, {"target": "demo"})
    assert status == 400
    assert "error" in payload


def test_missing_target_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.doctor_check(state, {})
    assert status == 400
    assert "target" in payload["error"]


def test_unknown_target_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.doctor_check(state, {"target": "nonexistent"})
    assert status == 400
    assert "unknown target" in payload["error"]


def test_fake_backend_passes_all_checks(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        "defaults: { backend: [fake] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(state, {"target": "demo"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["target"] == "demo"
    assert payload["backend"] == "fake"
    # fake backend has no config checks or runnability checks
    assert isinstance(payload["checks"], list)


def test_ios_backend_reports_config_check(tmp_path: Path) -> None:
    # The iOS backend (XCUITest) needs a bundleId — with it set, that config check passes.
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    # Stub the live screen so the check assembly never spawns a real runner — otherwise a locally
    # staged bundled runner (`make runner-bundle`) would have the probe launch xcodebuild and hang
    # (the same reason the playwright config-check test stubs its screen).
    payload, status = ops.doctor_check(state, {"target": "demo"}, screen_query=lambda *a: [])
    assert status == 200
    assert payload["backend"] == "xcuitest"
    config_checks = [c for c in payload["checks"] if "bundleId" in c["name"]]
    assert config_checks
    assert all(c["ok"] for c in config_checks)


def test_playwright_backend_missing_base_url_fails_check(tmp_path: Path) -> None:
    # A web target without baseUrl should fail the config check.
    state = _state(
        tmp_path,
        "defaults: { backend: [playwright] }\ntargets:\n  webapp: { bundleId: com.example }\n",
    )
    payload, status = ops.doctor_check(state, {"target": "webapp"})
    assert status == 200
    assert payload["ok"] is False
    failed = [c for c in payload["checks"] if not c["ok"]]
    assert any("baseUrl" in c["name"] for c in failed)


def test_playwright_backend_with_base_url_passes_config_check(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        "defaults: { backend: [playwright] }\ntargets:\n  webapp: { baseUrl: 'http://localhost:3000' }\n",
    )
    # Stub the live screen so the config-check assertion never depends on a browser being installed
    # (with the `web` extra present the real probe would launch Chromium and navigate the baseUrl).
    payload, status = ops.doctor_check(state, {"target": "webapp"}, screen_query=lambda *a: [])
    assert status == 200
    assert payload["backend"] == "playwright"
    config_checks = [c for c in payload["checks"] if "baseUrl" in c["name"]]
    assert config_checks
    assert all(c["ok"] for c in config_checks)


def test_xcuitest_panel_reports_xcode_tools(tmp_path: Path) -> None:
    # BE-0199 reconciliation: the serve panel uses the shared check assembly, so an xcuitest target
    # reports the same set the CLI reports — XCUITest's own tools (xcrun + xcodebuild), iOS being a
    # single actuator (BE-0290).
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    # Stub the live screen (see test_ios_backend_reports_config_check) so a staged bundled runner
    # can't turn this tool-name check into a real xcodebuild spawn.
    payload, status = ops.doctor_check(state, {"target": "demo"}, screen_query=lambda *a: [])
    assert status == 200
    names = {c["name"] for c in payload["checks"]}
    assert "xcodebuild" in names


def test_current_screen_maps_probe_error_to_value_error(tmp_path: Path) -> None:
    # The serve adapter maps the probe's typed DoctorProbeError to its existing ValueError surface.
    # A baseUrl-less web target can't be resolved directly (the config gate rejects it), so resolve
    # a valid one and null the baseUrl to reach the probe's defensive backstop.
    import dataclasses

    from bajutsu.config import WebConfig, load_config, resolve
    from bajutsu.serve.operations.doctor import _current_screen

    eff = dataclasses.replace(
        resolve(load_config("targets: { web: { baseUrl: 'http://x' } }"), "web"),
        platform_config=WebConfig(base_url=None),
    )
    with pytest.raises(ValueError, match="baseUrl"):
        _current_screen(_state(tmp_path), "playwright", "booted", eff)


def test_screen_probe_failure_is_reported_not_raised(tmp_path: Path) -> None:
    # Runnability passes (tools present) but the live probe faults — e.g. a web target whose app
    # server is down, so navigating the baseUrl raises DeviceError. doctor must report it as a
    # failed check and null score, never let it crash the request (a 500 / stack trace). Driven on
    # the fake backend so runnability passes deterministically, with no web extra installed; the
    # caught branch is backend-agnostic.
    state = _state(
        tmp_path,
        "defaults: { backend: [fake] }\ntargets:\n  demo: { bundleId: com.example.demo }\n",
    )

    def screen_query(actuator: str, udid: str, eff: object) -> list[base.Element]:
        raise simctl.DeviceError("web browser fault (recoverable wedge): ERR_CONNECTION_REFUSED")

    payload, status = ops.doctor_check(state, {"target": "demo"}, screen_query=screen_query)
    assert status == 200
    assert payload["ok"] is False
    assert payload["score"] is None
    failed = [c for c in payload["checks"] if not c["ok"]]
    assert any("ERR_CONNECTION_REFUSED" in c["detail"] for c in failed)


def test_check_shape_has_name_ok_detail(tmp_path: Path) -> None:
    """Every check in the response has the expected keys."""
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    # Stub the live screen (see test_ios_backend_reports_config_check) so a staged bundled runner
    # can't turn this shape check into a real xcodebuild spawn.
    payload, status = ops.doctor_check(state, {"target": "demo"}, screen_query=lambda *a: [])
    assert status == 200
    for check in payload["checks"]:
        assert set(check.keys()) == {"name", "ok", "detail"}
        assert isinstance(check["name"], str)
        assert isinstance(check["ok"], bool)
        assert isinstance(check["detail"], str)


# --- BE-0148: {udid?, backend?}, the booted-Simulator check, and the convention score ---


def _element(
    identifier: str | None, traits: list[str], label: str = "", value: str | None = None
) -> base.Element:
    """A real screen element for the score path — test data, not a behavior mock."""
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_invalid_backend_rejected(tmp_path: Path) -> None:
    # A free-text backend must not reach argv / driver selection (BE-0051).
    state = _state(tmp_path)
    payload, status = ops.doctor_check(state, {"target": "demo", "backend": "rm -rf"})
    assert status == 400
    assert "backend" in payload["error"]


def test_invalid_udid_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.doctor_check(state, {"target": "demo", "udid": "; reboot"})
    assert status == 400
    assert "udid" in payload["error"]


def test_backend_override_selects_actuator(tmp_path: Path) -> None:
    # The config declares xcuitest, but the request overrides to fake.
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(
        state,
        {"target": "demo", "backend": "fake"},
        screen_query=lambda actuator, udid, eff: [],
    )
    assert status == 200
    assert payload["backend"] == "fake"


def test_comma_list_backend_resolves_first_implemented(tmp_path: Path) -> None:
    # A comma-list backend (like the CLI's --backend) is split, not treated as one token.
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(
        state,
        {"target": "demo", "backend": "fake,xcuitest"},
        screen_query=lambda actuator, udid, eff: [],
    )
    assert status == 200
    assert payload["backend"] == "fake"


def test_ios_reports_booted_simulator_check(tmp_path: Path) -> None:
    # doctor reports whether a Simulator is booted, reading it through state.simctl so the
    # Linux gate never shells out to a real xcrun.
    state = _state(
        tmp_path,
        "defaults: { backend: [xcuitest] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    state.simctl = lambda args, extra_env=None: '{"devices": {}}'  # no booted device
    payload, status = ops.doctor_check(state, {"target": "demo"})
    assert status == 200
    booted = [c for c in payload["checks"] if c["name"] == "Simulator booted"]
    assert booted and booted[0]["ok"] is False


def test_score_present_when_runnable(tmp_path: Path) -> None:
    # fake backend has no runnability gate, so the score is computed from the current screen.
    state = _state(
        tmp_path,
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.demo, idNamespaces: [auth] }\n",
    )
    screen = [
        _element("auth.email", ["textField"]),
        _element("auth.submit", ["button"]),
    ]
    payload, status = ops.doctor_check(
        state,
        {"target": "demo"},
        screen_query=lambda actuator, udid, eff: screen,
    )
    assert status == 200
    assert payload["score"] is not None
    score = payload["score"]
    assert score["grade"] == "Ready"
    assert score["actionable"] == 2
    assert score["withId"] == 2
    assert score["idCoverage"] == 1.0


def test_fake_score_does_not_shell_out_to_simctl(tmp_path: Path) -> None:
    # The fake backend needs no device, so scoring its (empty) screen must never call simctl —
    # a resolve_udid would shell out to xcrun and crash on a host without Xcode (the Linux gate).
    # No screen_query is injected here, so this exercises the real _current_screen fake path.
    state = _state(
        tmp_path,
        "defaults: { backend: [fake] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )

    def boom(args: list[str], extra_env: object = None) -> str:
        raise AssertionError("fake backend must not shell out to simctl")

    state.simctl = boom
    payload, status = ops.doctor_check(state, {"target": "demo"})
    assert status == 200
    assert payload["score"] is not None  # the empty fake screen is still scored


def test_score_reports_gaps(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.demo, idNamespaces: [auth] }\n",
    )
    screen = [
        _element("auth.email", ["textField"]),
        _element(None, ["button"], label="Submit"),  # missing id
    ]
    payload, _status = ops.doctor_check(
        state,
        {"target": "demo"},
        screen_query=lambda actuator, udid, eff: screen,
    )
    score = payload["score"]
    assert score["grade"] in {"Partial", "Blocked"}
    assert score["actionable"] == 2
    assert score["withId"] == 1
    assert len(score["missingId"]) == 1
    assert score["missingId"][0]["label"] == "Submit"


def test_score_null_when_runnability_fails(tmp_path: Path) -> None:
    # A web target without baseUrl fails the config check, so no screen is queried — the score
    # is null, mirroring the CLI which exits before scoring when the environment isn't runnable.
    state = _state(
        tmp_path,
        "defaults: { backend: [playwright] }\ntargets:\n  webapp: { bundleId: com.example }\n",
    )
    called = False

    def screen_query(actuator: str, udid: str, eff: object) -> list[base.Element]:
        nonlocal called
        called = True
        return []

    payload, status = ops.doctor_check(state, {"target": "webapp"}, screen_query=screen_query)
    assert status == 200
    assert payload["ok"] is False
    assert payload["score"] is None
    assert called is False  # never touch a device the runnability gate already failed

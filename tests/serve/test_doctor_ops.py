"""Tests for the doctor operations layer (BE-0024).

Operations-level tests for the doctor/preflight endpoint — no HTTP, no Simulator.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

from bajutsu.drivers import base
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState


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
    # idb backend needs a bundleId — with it set, that config check passes.
    state = _state(
        tmp_path,
        "defaults: { backend: [idb] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(state, {"target": "demo"})
    assert status == 200
    assert payload["backend"] == "idb"
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
    payload, status = ops.doctor_check(state, {"target": "webapp"})
    assert status == 200
    assert payload["backend"] == "playwright"
    config_checks = [c for c in payload["checks"] if "baseUrl" in c["name"]]
    assert config_checks
    assert all(c["ok"] for c in config_checks)


def test_check_shape_has_name_ok_detail(tmp_path: Path) -> None:
    """Every check in the response has the expected keys."""
    state = _state(
        tmp_path,
        "defaults: { backend: [idb] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(state, {"target": "demo"})
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
    # The config declares idb, but the request overrides to fake.
    state = _state(
        tmp_path,
        "defaults: { backend: [idb] }\ntargets:\n  demo: { bundleId: com.demo }\n",
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
        "defaults: { backend: [idb] }\ntargets:\n  demo: { bundleId: com.demo }\n",
    )
    payload, status = ops.doctor_check(
        state,
        {"target": "demo", "backend": "fake,idb"},
        screen_query=lambda actuator, udid, eff: [],
    )
    assert status == 200
    assert payload["backend"] == "fake"


def test_idb_reports_booted_simulator_check(tmp_path: Path) -> None:
    # doctor reports whether a Simulator is booted, reading it through state.simctl so the
    # Linux gate never shells out to a real xcrun.
    state = _state(
        tmp_path,
        "defaults: { backend: [idb] }\ntargets:\n  demo: { bundleId: com.demo }\n",
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

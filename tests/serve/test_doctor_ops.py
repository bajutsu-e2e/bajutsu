"""Tests for the doctor operations layer (BE-0024).

Operations-level tests for the doctor/preflight endpoint — no HTTP, no Simulator.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

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

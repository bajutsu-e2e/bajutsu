"""Tests for the /api/doctor endpoint — preflight environment checks via the web UI (BE-0024)."""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, project

from bajutsu import serve as srv


def test_doctor_returns_checks_for_target(tmp_path: Path) -> None:
    """POST /api/doctor with a valid target returns preflight checks.

    Uses the `fake` backend so the response shape is asserted without depending on a real device
    (an iOS target would probe a live screen where Xcode is present, hanging the gate on a Mac)."""
    _scn_dir, _cfg, runs = project(tmp_path)
    cfg = tmp_path / "fake.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n  demo: { bundleId: com.example.demo }\n",
        encoding="utf-8",
    )
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "demo"})
        assert status == 200
        assert "checks" in resp
        assert "ok" in resp
        assert "target" in resp
        assert resp["target"] == "demo"
        assert "backend" in resp
        assert isinstance(resp["checks"], list)
        # Every check has the right shape
        for check in resp["checks"]:
            assert "name" in check
            assert "ok" in check
            assert "detail" in check
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_requires_target(tmp_path: Path) -> None:
    """POST /api/doctor without a target returns 400."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_requires_config(tmp_path: Path) -> None:
    """POST /api/doctor without a bound config returns 400."""
    _scn_dir, _cfg, runs = project(tmp_path)
    state = srv.ServeState(config=None, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "demo"})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_unknown_target(tmp_path: Path) -> None:
    """POST /api/doctor with a target not in config returns 400."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "nonexistent"})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_fake_backend_returns_ok(tmp_path: Path) -> None:
    """A target with the 'fake' backend needs no tools, so all checks pass."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "demo"})
        assert status == 200
        assert resp["ok"] is True
        assert resp["backend"] == "fake"
        # fake backend needs no tools: config checks pass trivially, runnability returns []
        for check in resp["checks"]:
            assert check["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_web_target_missing_base_url(tmp_path: Path) -> None:
    """A web target without baseUrl fails the config check."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [playwright] }\ntargets:\n"
        f"  webapp: {{ bundleId: com.example, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "webapp"})
        assert status == 200
        assert resp["ok"] is False
        # At least one check failed (the baseUrl config check)
        failed = [c for c in resp["checks"] if not c["ok"]]
        assert len(failed) >= 1
        assert any("baseUrl" in c["name"] for c in failed)
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_score_present_for_fake_backend(tmp_path: Path) -> None:
    """A runnable backend also returns the convention score (BE-0148). The fake driver's screen is
    empty, so the score grades it Blocked — but the point is the score section is present."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "demo"})
        assert status == 200
        assert resp["score"] is not None
        assert resp["score"]["grade"] == "Blocked"  # empty fake screen: nothing actionable
        assert resp["score"]["noActionable"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_score_null_when_unrunnable(tmp_path: Path) -> None:
    """When the runnability gate fails there is no reachable screen, so the score is null."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [playwright] }\ntargets:\n"
        f"  webapp: {{ bundleId: com.example, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "webapp"})
        assert status == 200
        assert resp["ok"] is False
        assert resp["score"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_rejects_invalid_backend(tmp_path: Path) -> None:
    """A free-text backend is rejected before it can reach driver selection (BE-0051)."""
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "demo", "backend": "evil; rm"})
        assert status == 400
        assert "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_doctor_web_target_with_base_url(tmp_path: Path) -> None:
    """A web target with baseUrl passes the config check (runnability may still fail)."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [playwright] }\ntargets:\n"
        f"  webapp: {{ baseUrl: 'http://localhost:3000', scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/doctor", {"target": "webapp"})
        assert status == 200
        assert resp["backend"] == "playwright"
        # The config check for baseUrl passed
        config_checks = [c for c in resp["checks"] if "baseUrl" in c["name"]]
        assert config_checks
        assert all(c["ok"] for c in config_checks)
    finally:
        server.shutdown()
        server.server_close()

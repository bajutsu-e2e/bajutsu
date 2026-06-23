"""Tests for the bajutsu serve scenario save and config binding endpoints (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from _shared import (
    _get_json,
    _post,
    _serve,
    project,
)

from bajutsu import serve as srv


def test_http_scenario_save_validates_and_writes(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        target = scn_dir / "smoke.yaml"
        edited = "- name: edited\n  steps:\n    - tap: { id: y }\n"
        body = json.dumps({"path": str(target), "yaml": edited}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        assert target.read_text(encoding="utf-8") == edited  # the edit landed on disk

        bad = json.dumps({"path": str(target), "yaml": "steps: [not, a, scenario, list"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=bad,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
        assert target.read_text(encoding="utf-8") == edited  # rejected save left the file intact
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenario_save_reports_bad_path_before_bad_yaml(tmp_path: Path) -> None:
    # When both the path and the YAML are invalid, the path error wins (a non-saveable ref is
    # reported before the scenario is parsed), so the client learns where to save first.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port, "/api/scenario", {"path": "note.txt", "yaml": "steps: [not, a, list"}
        )
        assert status == 400 and "path must be" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_open_config_binds_and_lists_apps(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    # No config bound at startup; the browse root is the project dir.
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/config")["hasConfig"] is False
        assert _get_json(port, "/api/targets") == []  # nothing until a config is opened
        status, resp = _post(port, "/api/config", {"path": "bajutsu.config.yaml"})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["demo", "other"]
        assert _get_json(port, "/api/config")["hasConfig"] is True
        assert [a["name"] for a in _get_json(port, "/api/targets")] == ["demo", "other"]
        # A path outside the browse root is rejected.
        status, _ = _post(port, "/api/config", {"path": "/etc/hosts"})
        assert status == 400
        # A path inside root but not a config file → 404.
        status, _ = _post(port, "/api/config", {"path": "nope.yaml"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_rejects_absolute_traversal_outside_root(tmp_path: Path) -> None:
    # An absolute path with `..` that resolves outside the browse root must be rejected, not read
    # (CodeQL py/path-injection): the containment check resolves the path first, so the literal
    # parent of the unresolved path can't slip it through.
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.yaml"  # outside root, but inside tmp_path
    secret.write_text("targets: {evil: {bundleId: x}}\n", encoding="utf-8")
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=root, cwd=tmp_path))
    try:
        escape = str(root / ".." / "secret.yaml")  # absolute, resolves to tmp_path/secret.yaml
        status, resp = _post(port, "/api/config", {"path": escape})
        assert status == 400 and "outside the browse root" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenarios_by_app_from_config(tmp_path: Path) -> None:
    _, cfg, runs = project(tmp_path)
    # Config-driven (no --scenarios override): the dir comes from the selected app.
    server, port = _serve(srv.ServeState(runs_dir=runs, config=cfg, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/scenarios?target=demo")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/scenarios?target=other") == []  # app has no scenarios dir
        assert _get_json(port, "/api/scenarios") == []  # no app → nothing
    finally:
        server.shutdown()
        server.server_close()

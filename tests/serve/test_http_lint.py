"""HTTP-level tests for inline scenario validation routes (BE-0138)."""

from __future__ import annotations

from pathlib import Path

from _shared import _get_json, _post, _serve, project

from bajutsu import serve as srv


def test_http_lint_valid(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/lint", {"yaml": "- name: a\n  steps:\n    - tap: { id: ok }\n"}
        )
        assert status == 200
        assert body["ok"] is True
        assert body["diagnostics"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_http_lint_reports_line_anchored_error(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(port, "/api/lint", {"yaml": "- steps:\n    - tap: { id: ok }\n"})
        assert status == 200
        assert body["ok"] is False
        assert len(body["diagnostics"]) == 1
        assert body["diagnostics"][0]["line"] == 1
        assert body["diagnostics"][0]["severity"] == "error"
    finally:
        server.shutdown()
        server.server_close()


def test_http_schema_serves_the_scenario_json_schema(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        body = _get_json(port, "/api/schema")
        # The scenario schema covers both on-disk forms (a bare list or a mapping) via anyOf,
        # and exposes the reusable Scenario definition.
        assert "anyOf" in body
        assert "Scenario" in body["$defs"]
    finally:
        server.shutdown()
        server.server_close()

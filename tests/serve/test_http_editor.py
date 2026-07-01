"""HTTP-level tests for the scenario editor routes (BE-0013, Slice 1)."""

from __future__ import annotations

import json
from pathlib import Path

from _shared import _get_json, _post, _serve, project

from bajutsu import serve as srv


def _elements() -> list[dict[str, object]]:
    return [
        {
            "identifier": "btn.ok",
            "label": "OK",
            "traits": ["button"],
            "value": None,
            "frame": [50.0, 50.0, 100.0, 44.0],
        },
    ]


def test_http_resolve_pick(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    step_dir = runs / "run1" / "00-s" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")

    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/scenario/resolve",
            {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.8, 0.7]},
        )
        assert status == 200
        assert body["selector"]["id"] == "btn.ok"
        assert body["rung"] == "id"
    finally:
        server.shutdown()
        server.server_close()


def test_http_resolve_pick_missing_elements(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/scenario/resolve",
            {"target": "demo", "runId": "run1", "stepId": "00-s/step0", "point": [0.5, 0.5]},
        )
        assert status == 404
        assert "elements" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# GET /api/scenario with runId
# ---------------------------------------------------------------------------

_LOGIN_YAML = """\
- name: login
  steps:
    - tap: { id: auth.email }
    - tap: { id: auth.submit }
"""


def test_http_read_scenario_with_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    (scn_dir / "login.yaml").write_text(_LOGIN_YAML, encoding="utf-8")

    run_dir = runs / "run1"
    run_dir.mkdir()
    manifest = {
        "runId": "run1",
        "ok": True,
        "scenarios": [{"scenario": "login", "ok": True, "sid": "00-login", "steps": []}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for i in range(2):
        d = run_dir / f"00-login/step{i}"
        d.mkdir(parents=True)
        (d / "elements.json").write_text(json.dumps(_elements()), encoding="utf-8")
        (d / "after.png").write_bytes(b"PNG")

    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        path = str(scn_dir / "login.yaml")
        body = _get_json(
            port,
            f"/api/scenario?target=demo&path={path}&runId=run1&scenario=login",
        )
        assert "yaml" in body
        assert len(body["steps"]) == 2
        assert body["steps"][0]["stepId"] == "00-login/step0"
        assert body["steps"][0]["screenshotUrl"] is not None
        assert body["steps"][0]["elementsUrl"] is not None
    finally:
        server.shutdown()
        server.server_close()


def test_http_read_scenario_without_run(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    (scn_dir / "login.yaml").write_text(_LOGIN_YAML, encoding="utf-8")

    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        path = str(scn_dir / "login.yaml")
        body = _get_json(port, f"/api/scenario?target=demo&path={path}")
        assert "yaml" in body
        assert "steps" not in body
    finally:
        server.shutdown()
        server.server_close()

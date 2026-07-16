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
# POST /api/scenario/apply-selector and /api/scenario/enrich-apply (BE-0261)
# ---------------------------------------------------------------------------


def test_http_apply_selector(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/scenario/apply-selector",
            {
                "yaml": "- name: s\n  steps:\n    - tap: { id: a }   # keep\n    - tap: { id: old }\n",
                "scenario": "s",
                "stepIndex": 1,
                "selector": {"id": "new:val#x"},
            },
        )
        assert status == 200
        assert "id: new:val#x" in body["yaml"]
        assert "# keep" in body["yaml"]  # a comment on another step's line survives
    finally:
        server.shutdown()
        server.server_close()


def test_http_apply_selector_rejects_unsupported_action(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/scenario/apply-selector",
            {
                "yaml": "- name: s\n  steps:\n    - back: {}\n",
                "scenario": "s",
                "stepIndex": 0,
                "selector": {"id": "x"},
            },
        )
        assert status == 400
        assert "back" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_enrich_apply(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/scenario/enrich-apply",
            {
                "yaml": "- name: s\n  steps:\n    - tap: { id: a }\n",
                "scenario": "s",
                "expect": [{"exists": {"sel": {"id": "z"}}}],
                "settle": {"wait": {"for": {"id": "sp"}, "timeout": 5}},
            },
        )
        assert status == 200
        assert "expect:" in body["yaml"]
        assert "wait:" in body["yaml"]
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
        assert "scenarios" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_read_scenario_structure_opt_in(tmp_path: Path) -> None:
    """The Replay viewer (BE-0273) passes structure=1 to get the runner-parsed per-scenario steps
    without a run — the run-scoped step artifacts stay behind runId."""
    scn_dir, cfg, runs = project(tmp_path)
    (scn_dir / "login.yaml").write_text(_LOGIN_YAML, encoding="utf-8")

    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        path = str(scn_dir / "login.yaml")
        body = _get_json(port, f"/api/scenario?target=demo&path={path}&structure=1")
        assert "yaml" in body
        assert "steps" not in body
        scenarios = body["scenarios"]
        assert [s["name"] for s in scenarios] == ["login"]
        assert [st["action"] for st in scenarios[0]["steps"]] == ["tap", "tap"]
    finally:
        server.shutdown()
        server.server_close()

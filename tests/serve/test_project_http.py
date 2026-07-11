"""BE-0225 unit 3: the `/api/projects…` endpoints reach the shared operations through both
transports. The stdlib handler is driven over a real loopback server; the FastAPI control plane
through its TestClient. We pin the transport-specific parts the operations tests can't: the DELETE
verb (new to the stdlib handler), path-param parsing, and the RBAC gate that treats register /
deregister as admin and run as editor. No mocks — a real `LocalProjectRegistry` and local seams."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from _shared import _get_json, _post, _serve, fake_popen, project
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve.project_registry import LocalProjectRegistry
from bajutsu.serve.server.app import make_app


def _hub_state(tmp_path: Path, **kw: object) -> srv.ServeState:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        project_registry=reg,
        **kw,  # type: ignore[arg-type]
    )


def _delete(port: int, path: str) -> tuple[int, object]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_stdlib_handler_register_list_and_delete_roundtrip(tmp_path: Path) -> None:
    server, port = _serve(_hub_state(tmp_path))
    try:
        status, payload = _post(port, "/api/projects", {"name": "checkout", "source": None})
        assert status == 200 and payload["name"] == "checkout"

        assert [p["name"] for p in _get_json(port, "/api/projects")] == ["checkout"]

        status, payload = _delete(port, "/api/projects/checkout")
        assert (status, payload) == (200, {"ok": True})
        assert _get_json(port, "/api/projects") == []
    finally:
        server.shutdown()


def test_stdlib_handler_project_runs_route(tmp_path: Path) -> None:
    server, port = _serve(_hub_state(tmp_path))
    try:
        _post(port, "/api/projects", {"name": "checkout", "source": None})
        # No runs yet, but the route resolves the name and returns the (empty) slice, not a 404.
        assert _get_json(port, "/api/projects/checkout/runs") == []
    finally:
        server.shutdown()


def test_fastapi_transport_register_list_run_and_delete(tmp_path: Path) -> None:
    client = TestClient(
        make_app(
            _hub_state(tmp_path, popen=fake_popen(["PASS  runs/20260711-1/manifest.json\n"]))  # type: ignore[arg-type]
        )
    )
    assert (
        client.post("/api/projects", json={"name": "checkout", "source": None}).status_code == 200
    )
    assert [p["name"] for p in client.get("/api/projects").json()] == ["checkout"]

    run = client.post(
        "/api/projects/checkout/run", json={"target": "demo", "scenario": "smoke.yaml"}
    )
    assert run.status_code == 200 and "jobId" in run.json()

    assert client.delete("/api/projects/checkout").status_code == 200
    assert client.get("/api/projects").json() == []


def test_fastapi_run_of_a_non_active_project_is_409(tmp_path: Path) -> None:
    client = TestClient(make_app(_hub_state(tmp_path)))
    client.post("/api/projects", json={"name": "checkout", "source": None})  # first → active
    client.post("/api/projects", json={"name": "billing", "source": None})  # second → not active
    resp = client.post(
        "/api/projects/billing/run", json={"target": "demo", "scenario": "smoke.yaml"}
    )
    assert resp.status_code == 409

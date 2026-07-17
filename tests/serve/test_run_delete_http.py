"""BE-0239: the run delete / restore / bulk-delete routes reach the shared operations through both
transports. The stdlib handler is driven over a real loopback server; the FastAPI control plane
through its TestClient. Pins the transport-specific parts the operations tests can't: the DELETE
verb on ``/api/runs/{id}`` (and the crawl counterpart), the ``?purge=true`` query, path-param
parsing, and the restore / bulk-delete POST routes. Local seams, no mocks."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from _shared import _get_json, _post, _serve
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve.server.app import make_app


def _state(tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(runs_dir=tmp_path / "runs", root=tmp_path, cwd=tmp_path)


def _run(state: srv.ServeState, run_id: str) -> None:
    d = state.runs_dir / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"ok": true, "scenarios": []}')


def _crawl(state: srv.ServeState, run_id: str) -> None:
    d = state.runs_dir / run_id
    d.mkdir(parents=True)
    (d / "screenmap.json").write_text('{"nodes": [], "edges": [], "crashes": []}')


def _delete(port: int, path: str) -> tuple[int, object]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_stdlib_delete_restore_roundtrip(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _run(state, "20260101-000001")
    _run(state, "20260101-000002")
    server, port = _serve(state)
    try:
        status, payload = _delete(port, "/api/runs/20260101-000001")
        assert (status, payload) == (200, {"ok": True, "purged": False})
        assert [r["id"] for r in _get_json(port, "/api/runs")] == ["20260101-000002"]

        status, _ = _post(port, "/api/runs/20260101-000001/restore", {})
        assert status == 200
        assert {r["id"] for r in _get_json(port, "/api/runs")} == {
            "20260101-000001",
            "20260101-000002",
        }
    finally:
        server.shutdown()


def test_stdlib_purge_query_removes_the_bytes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _run(state, "20260101-000001")
    server, port = _serve(state)
    try:
        status, payload = _delete(port, "/api/runs/20260101-000001?purge=true")
        assert status == 200 and payload["purged"] is True
        assert not (state.runs_dir / "20260101-000001").exists()
    finally:
        server.shutdown()


def test_stdlib_delete_crawl_run(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _crawl(state, "20260101-000003")
    server, port = _serve(state)
    try:
        status, _ = _delete(port, "/api/crawl/runs/20260101-000003")
        assert status == 200
        assert _get_json(port, "/api/crawl/runs") == []
    finally:
        server.shutdown()


def test_stdlib_trash_lists_soft_deleted_runs(tmp_path: Path) -> None:
    # The Trash view's GET /api/runs/trash (BE-0239 unit 5): a soft-deleted run appears here, keyed
    # by id, while the live history hides it.
    state = _state(tmp_path)
    _run(state, "20260101-000001")
    _run(state, "20260101-000002")
    server, port = _serve(state)
    try:
        assert _get_json(port, "/api/runs/trash") == []
        _delete(port, "/api/runs/20260101-000001")
        trashed = _get_json(port, "/api/runs/trash")
        assert [r["id"] for r in trashed] == ["20260101-000001"]
        assert trashed[0]["deletedAt"]  # a deletion timestamp is carried for the view
        assert [r["id"] for r in _get_json(port, "/api/runs")] == ["20260101-000002"]
    finally:
        server.shutdown()


def test_fastapi_trash_lists_soft_deleted_runs(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _run(state, "20260101-000001")
    client = TestClient(make_app(state))
    assert client.get("/api/runs/trash").json() == []
    client.delete("/api/runs/20260101-000001")
    trashed = client.get("/api/runs/trash").json()
    assert [r["id"] for r in trashed] == ["20260101-000001"]


def test_fastapi_delete_restore_and_bulk(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for i in range(1, 4):
        _run(state, f"20260101-00000{i}")
    client = TestClient(make_app(state))

    deleted = client.delete("/api/runs/20260101-000001")
    assert deleted.json() == {"ok": True, "purged": False}
    assert client.post("/api/runs/20260101-000001/restore").status_code == 200

    bulk = client.post("/api/runs/bulk-delete", json={"ids": ["20260101-000002", "ghost"]})
    assert bulk.status_code == 200
    assert bulk.json()["deleted"] == ["20260101-000002"]
    assert bulk.json()["notFound"] == ["ghost"]

    listed = {r["id"] for r in client.get("/api/runs").json()}
    assert listed == {"20260101-000001", "20260101-000003"}


def test_fastapi_purge_query(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _run(state, "20260101-000001")
    client = TestClient(make_app(state))
    resp = client.delete("/api/runs/20260101-000001", params={"purge": "true"})
    assert resp.status_code == 200 and resp.json()["purged"] is True
    assert not (state.runs_dir / "20260101-000001").exists()

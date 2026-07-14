"""Tests for the independent per-artifact upload routes (bajutsu serve, BE-0268).

`POST /api/artifacts/{config,scenarios,binary}` stores one artifact at a time, independent of the
combined `/api/upload` bundle (BE-0073, unchanged). `GET /api/artifacts/exists` lets a client skip
re-uploading bytes already stored. These exercise the wire path against a real ThreadingHTTPServer,
no Simulator needed."""

from __future__ import annotations

import hashlib
import io
import json
import socket
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeObjectStore, _get_json, _serve, fake_popen

from bajutsu import serve as srv


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _post_bytes(port: int, path: str, data: bytes) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(port: int, path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _state(
    tmp_path: Path, *, object_store: Any = None, object_store_prefix: str = ""
) -> srv.ServeState:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return srv.ServeState(
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        uploads_dir=tmp_path / "uploads",
        popen=fake_popen(["PASS  runs/up-1/manifest.json\n"]),
        object_store=object_store,
        object_store_prefix=object_store_prefix,
    )


def test_upload_config_artifact_returns_its_sha256(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        blob = b"targets: {}\n"
        status, resp = _post_bytes(port, "/api/artifacts/config", blob)
        assert status == 200 and resp["ok"] is True
        assert resp["kind"] == "config"
        assert resp["sha256"] == hashlib.sha256(blob).hexdigest()
        assert resp["size"] == len(blob)
    finally:
        server.shutdown()
        server.server_close()


def test_upload_scenarios_and_binary_artifacts_independently(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        scenarios_blob = _zip({"scenarios/smoke.yaml": b"- name: a\n"})
        binary_blob = b"\x7fELF fake binary"
        s_status, s_resp = _post_bytes(port, "/api/artifacts/scenarios", scenarios_blob)
        b_status, b_resp = _post_bytes(port, "/api/artifacts/binary", binary_blob)
        assert s_status == 200 and s_resp["kind"] == "scenarios"
        assert b_status == 200 and b_resp["kind"] == "binary"
        assert s_resp["sha256"] != b_resp["sha256"]
    finally:
        server.shutdown()
        server.server_close()


def test_exists_reports_false_before_upload_and_true_after(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        blob = b"targets: {}\n"
        sha = hashlib.sha256(blob).hexdigest()
        before = _get_json(port, f"/api/artifacts/exists?kind=config&sha256={sha}")
        assert before == {"exists": False}
        _post_bytes(port, "/api/artifacts/config", blob)
        after = _get_json(port, f"/api/artifacts/exists?kind=config&sha256={sha}")
        assert after == {"exists": True}
    finally:
        server.shutdown()
        server.server_close()


def test_exists_rejects_unknown_kind(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        sha = hashlib.sha256(b"x").hexdigest()
        status, body = _get(port, f"/api/artifacts/exists?kind=bogus&sha256={sha}")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_exists_rejects_a_malformed_sha256(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, body = _get(port, "/api/artifacts/exists?kind=config&sha256=not-hex")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_reuploading_identical_bytes_does_not_duplicate_the_object_store_write(
    tmp_path: Path,
) -> None:
    store = FakeObjectStore()
    server, port = _serve(_state(tmp_path, object_store=store))
    try:
        blob = b"targets: {}\n"
        _post_bytes(port, "/api/artifacts/config", blob)
        _post_bytes(port, "/api/artifacts/config", blob)
        assert len(store.put_calls) == 1  # the second upload's key already existed — skipped
    finally:
        server.shutdown()
        server.server_close()


def test_artifacts_persist_to_the_object_store_under_a_kind_sub_prefix(tmp_path: Path) -> None:
    store = FakeObjectStore()
    server, port = _serve(_state(tmp_path, object_store=store, object_store_prefix="tenant/"))
    try:
        blob = b"targets: {}\n"
        sha = hashlib.sha256(blob).hexdigest()
        _post_bytes(port, "/api/artifacts/config", blob)
        assert f"tenant/uploads/config/{sha}" in store.objects
    finally:
        server.shutdown()
        server.server_close()


def test_config_and_binary_uploads_of_identical_bytes_do_not_collide(tmp_path: Path) -> None:
    # Two different kinds' artifacts with the same content sha256 must land at distinct keys/paths.
    server, port = _serve(_state(tmp_path))
    try:
        blob = b"same bytes either way"
        sha = hashlib.sha256(blob).hexdigest()
        _post_bytes(port, "/api/artifacts/config", blob)
        config_exists = _get_json(port, f"/api/artifacts/exists?kind=config&sha256={sha}")
        binary_exists = _get_json(port, f"/api/artifacts/exists?kind=binary&sha256={sha}")
        assert config_exists == {"exists": True}
        assert binary_exists == {"exists": False}
    finally:
        server.shutdown()
        server.server_close()


def _post_truncated(port: int, path: str, declared_length: int, sent: bytes) -> tuple[int, Any]:
    """POST *sent* bytes while declaring a larger `Content-Length` (*declared_length*), simulating a
    connection that drops mid-upload — regression coverage for the temp-file cleanup on that path
    (`_stream_bounded_body`'s "body ended early" branch)."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/octet-stream\r\n"
            f"Content-Length: {declared_length}\r\n"
            f"Connection: close\r\n\r\n".encode()
            + sent
        )
        sock.shutdown(socket.SHUT_WR)  # signal EOF so the server's read loop doesn't block forever
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    status_line, _, rest = response.partition(b"\r\n")
    status = int(status_line.split(b" ")[1])
    _, _, body = rest.partition(b"\r\n\r\n")
    return status, json.loads(body) if body else None


def test_truncated_artifact_upload_cleans_up_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the shared `_stream_bounded_body` streaming helper's "body ended early" branch
    # must still call `receiver.cleanup()` before returning — a prior version returned straight from
    # inside the `try` block, skipping cleanup and leaking the temp file on every truncated upload.
    from bajutsu.serve import uploads as uploads_mod

    created: list[Path] = []
    real_init = uploads_mod.BoundedZipReceiver.__init__

    def spy_init(self: Any, *a: Any, **kw: Any) -> None:
        real_init(self, *a, **kw)
        created.append(self.path)

    monkeypatch.setattr(uploads_mod.BoundedZipReceiver, "__init__", spy_init)
    server, port = _serve(_state(tmp_path))
    try:
        status, body = _post_truncated(port, "/api/artifacts/config", 1000, b"short body")
        assert status == 400
        assert body is not None and "incomplete" in body["error"]
        assert len(created) == 1
        assert not created[0].exists()  # cleaned up, not left behind
    finally:
        server.shutdown()
        server.server_close()

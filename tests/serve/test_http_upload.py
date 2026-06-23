"""Tests for the bundle upload + run endpoints (bajutsu serve, BE-0073).

A `.zip` of config + scenarios + app binary is POSTed as a raw body to `/api/upload`, extracted into
a serve-owned sandbox, and run via `/api/upload/run` — reusing the normal job machinery, only the
source differs. These exercise the wire path against a real ThreadingHTTPServer with a fake `popen`,
so no Simulator is needed.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from _shared import FakeProc, _get_json, _post, _serve, fake_popen

from bajutsu import serve as srv
from bajutsu.serve import handler as handler_mod

_CONFIG = (
    "defaults: { backend: [idb] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
)
_SCENARIO = "- name: alpha\n  steps:\n    - tap: { id: home.title }\n"


def _bundle_zip(config: str = _CONFIG, scenario: str = _SCENARIO) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bajutsu.config.yaml", config)
        zf.writestr("scenarios/smoke.yaml", scenario)
    return buf.getvalue()


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _post_bytes(
    port: int,
    path: str,
    data: bytes,
    *,
    ctype: str = "application/zip",
    headers: dict | None = None,
) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": ctype, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _state(tmp_path: Path, *, popen: Any = None, token: str | None = None) -> srv.ServeState:
    """A config-less serve state — the whole point is a browser user with nothing on the host. Runs
    land under tmp/runs; bundles extract under tmp/uploads."""
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return srv.ServeState(
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        uploads_dir=tmp_path / "uploads",
        popen=popen or fake_popen(["PASS  runs/up-1/manifest.json\n"]),
        token=token,
    )


def _wait_done(port: int, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        j = _get_json(port, "/api/jobs/" + job_id)
        if j["status"] == "done":
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _eventually(predicate: Any) -> bool:
    """Poll *predicate* until it holds — provenance recording and sandbox cleanup run in the job's
    `finally` (just before the live stream closes), so they land a hair after `status: done`. A
    predicate that raises (e.g. reading a file mid-write) counts as "not yet", not a failure."""
    for _ in range(200):
        try:
            if predicate():
                return True
        except Exception:
            pass  # a transient read of a file mid-write (etc.) — treat as "not ready", poll again
        time.sleep(0.02)
    return False


def test_upload_lists_apps_and_scenarios(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        assert status == 200
        assert resp["filename"] == "suite.zip" and len(resp["sha256"]) == 64
        targets = {t["name"]: t for t in resp["targets"]}
        assert "demo" in targets
        assert [s["path"] for s in targets["demo"]["scenarios"]] == ["smoke.yaml"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_run_spawns_from_bundle_dir_into_serve_runs(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def popen(cmd: list[str], **kw: Any) -> FakeProc:
        calls.append((cmd, kw))
        return FakeProc(["PASS  runs/up-1/manifest.json\n"])

    server, port = _serve(_state(tmp_path, popen=popen))
    try:
        _, up = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        status, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "smoke.yaml"},
        )
        assert status == 200 and "jobId" in resp
        _wait_done(port, resp["jobId"])
        cmd, kw = calls[0]
        # Runs from the extracted bundle dir, so the config's relative paths resolve against it…
        bundle_root = Path(kw["cwd"])
        assert (
            bundle_root.parent == (tmp_path / "uploads").resolve()
            or (tmp_path / "uploads") in bundle_root.parents
        )
        # …but the run is written into serve's runs store (an absolute --runs-dir), not the sandbox.
        assert cmd[cmd.index("--runs-dir") + 1] == str((tmp_path / "runs").resolve())
        assert cmd[cmd.index("--config") + 1].endswith("bajutsu.config.yaml")
        assert cmd[cmd.index("--scenario") + 1].endswith("smoke.yaml")
        assert "--baselines" not in cmd  # the bundle config drives baselines, resolved against cwd
    finally:
        server.shutdown()
        server.server_close()


def test_upload_run_records_provenance_in_manifest(tmp_path: Path) -> None:
    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        # Emulate the run subprocess writing its manifest into the --runs-dir we passed.
        runs_dir = Path(cmd[cmd.index("--runs-dir") + 1])
        (runs_dir / "up-1").mkdir(parents=True, exist_ok=True)
        (runs_dir / "up-1" / "manifest.json").write_text(
            json.dumps({"runId": "up-1", "ok": True, "scenarios": []}), encoding="utf-8"
        )
        return FakeProc(["PASS  runs/up-1/manifest.json\n"])

    server, port = _serve(_state(tmp_path, popen=popen))
    try:
        _, up = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        _, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "smoke.yaml"},
        )
        _wait_done(port, resp["jobId"])
        manifest_path = tmp_path / "runs" / "up-1" / "manifest.json"
        assert _eventually(
            lambda: "provenance" in json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        prov = json.loads(manifest_path.read_text(encoding="utf-8"))["provenance"]
        assert prov["source"] == "upload" and prov["filename"] == "suite.zip"
        assert prov["sha256"] == up["sha256"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_extraction_is_cleaned_up_after_the_run(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        _, up = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        _, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "smoke.yaml"},
        )
        _wait_done(port, resp["jobId"])
        # Ephemeral by design: no extracted bundle tree lingers after the run.
        assert _eventually(
            lambda: [d for d in (tmp_path / "uploads").iterdir() if d.is_dir()] == []
        )
    finally:
        server.shutdown()
        server.server_close()


def test_upload_run_consumes_the_bundle(tmp_path: Path) -> None:
    # One upload → one run: the bundle is consumed at dispatch, so re-running the same id 404s
    # (the extracted tree is now owned by the job and deleted afterward).
    server, port = _serve(_state(tmp_path))
    try:
        _, up = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        first, _ = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "smoke.yaml"},
        )
        assert first == 200
        again, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "smoke.yaml"},
        )
        assert again == 404 and "no such upload" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_pending_cap_rejects_and_cleans_up(tmp_path: Path, monkeypatch: Any) -> None:
    # The pending-upload cap bounds disk use: past it, a new upload is rejected and its extracted
    # tree is removed at once (the already-pending one is kept).
    monkeypatch.setattr("bajutsu.serve.jobs._MAX_UPLOADS", 1)
    server, port = _serve(_state(tmp_path))
    try:
        first, _ = _post_bytes(port, "/api/upload?name=a.zip", _bundle_zip())
        assert first == 200
        over, resp = _post_bytes(port, "/api/upload?name=b.zip", _bundle_zip())
        assert over == 429 and "too many pending uploads" in resp["error"]
        # Only the first (pending) extraction remains; the rejected one left nothing behind.
        assert len([d for d in (tmp_path / "uploads").iterdir() if d.is_dir()]) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_upload_run_rejects_unknown_upload(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": "deadbeef", "target": "demo", "scenario": "smoke.yaml"},
        )
        assert status == 404 and "no such upload" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_run_rejects_scenario_outside_bundle(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        _, up = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        status, resp = _post(
            port,
            "/api/upload/run",
            {"uploadId": up["uploadId"], "target": "demo", "scenario": "../secret.yaml"},
        )
        assert status == 400 and "inside the bundle's scenarios dir" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_zip_slip_bundle(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        slip = _zip_with({"bajutsu.config.yaml": _CONFIG.encode(), "../escape.txt": b"x"})
        status, resp = _post_bytes(port, "/api/upload?name=evil.zip", slip)
        assert status == 400 and "invalid bundle" in resp["error"]
        assert not (tmp_path / "escape.txt").exists()
        # A rejected upload leaves nothing extracted behind.
        uploads = tmp_path / "uploads"
        assert not uploads.exists() or [d for d in uploads.iterdir() if d.is_dir()] == []
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_config_path_outside_bundle(tmp_path: Path) -> None:
    # An uploaded config is untrusted: a target whose scenarios/appPath/baselines points outside the
    # bundle (absolute or `..`) must be rejected, or the run would reach host files (BE-0073).
    server, port = _serve(_state(tmp_path))
    try:
        for cfg in (
            "targets:\n  demo: { bundleId: com.example.demo, scenarios: /etc }\n",
            "targets:\n  demo: { bundleId: com.example.demo, appPath: ../../../etc/passwd }\n",
        ):
            status, resp = _post_bytes(
                port, "/api/upload?name=evil.zip", _zip_with({"bajutsu.config.yaml": cfg.encode()})
            )
            assert status == 400 and "outside the bundle" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_bundle_without_config(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post_bytes(
            port, "/api/upload?name=nope.zip", _zip_with({"readme.txt": b"hi"})
        )
        assert status == 400 and "no bajutsu.config.yaml" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_too_large_is_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(handler_mod, "MAX_UPLOAD_BYTES", 4)
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post_bytes(port, "/api/upload?name=big.zip", _bundle_zip())
        assert status == 413 and "too large" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_upload_requires_auth_when_token_set(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, token="s3cret"))
    try:
        status, _ = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        assert status == 401  # behind BE-0051 token auth like every other mutating endpoint
    finally:
        server.shutdown()
        server.server_close()

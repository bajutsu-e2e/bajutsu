"""Tests for binding an uploaded bundle as the active config (bajutsu serve, BE-0073).

A `.zip` of config + scenarios + app binary is POSTed as a raw body to `/api/upload`, extracted into
a serve-owned sandbox, and **bound as the active config** — a third source alongside the file browser
and the Git picker. After binding, the normal Replay / Record / Crawl flow runs from the extracted
tree, exactly like a config opened from disk. These exercise the wire path against a real
ThreadingHTTPServer with a fake `popen`, so no Simulator is needed.
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

from _shared import FakeObjectStore, FakeProc, _get_json, _post, _serve, fake_popen

from bajutsu import serve as srv
from bajutsu.serve import handler as handler_mod

_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
)
_SCENARIO = "- name: alpha\n  steps:\n    - tap: { id: home.title }\n"


# A fixed entry timestamp keeps repeated builds of the same logical bundle byte-identical, so the
# content-addressed cache (BE-0243) sees them as the same upload. A plain string name lets zipfile
# stamp each entry with the current time, which straddles a 2-second boundary between two calls and
# desyncs their sha256 — a flake on the required `check` gate.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _write_entry(zf: zipfile.ZipFile, name: str, content: str | bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, content)


def _bundle_zip(config: str = _CONFIG, scenario: str = _SCENARIO) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_entry(zf, "bajutsu.config.yaml", config)
        _write_entry(zf, "scenarios/smoke.yaml", scenario)
    return buf.getvalue()


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            _write_entry(zf, name, content)
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


def _state(
    tmp_path: Path,
    *,
    popen: Any = None,
    token: str | None = None,
    object_store: Any = None,
    object_store_prefix: str = "",
) -> srv.ServeState:
    """A config-less serve state — the whole point is a browser user with nothing on the host. Runs
    land under tmp/runs; bundles extract under tmp/uploads. *object_store* is None (BE-0073's
    original ephemeral behavior) unless a test opts into the BE-0243 durable-persistence path."""
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return srv.ServeState(
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        uploads_dir=tmp_path / "uploads",
        popen=popen or fake_popen(["PASS  runs/up-1/manifest.json\n"]),
        auth=srv.SessionManager(token=token),
        object_store=object_store,
        object_store_prefix=object_store_prefix,
    )


def _extracted_dirs(tmp_path: Path) -> list[Path]:
    uploads = tmp_path / "uploads"
    return [d for d in uploads.iterdir() if d.is_dir()] if uploads.exists() else []


def _wait_done(port: int, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        j = _get_json(port, "/api/jobs/" + job_id)
        if j["status"] == "done":
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _eventually(predicate: Any) -> bool:
    """Poll *predicate* until it holds — provenance recording runs in the job's `finally` (just before
    the live stream closes), so it lands a hair after `status: done`. A predicate that raises (e.g.
    reading a file mid-write) counts as "not yet", not a failure."""
    for _ in range(200):
        try:
            if predicate():
                return True
        except Exception:
            pass  # a transient read of a file mid-write (etc.) — treat as "not ready", poll again
        time.sleep(0.02)
    return False


def test_upload_binds_as_active_config(tmp_path: Path) -> None:
    # POSTing a bundle binds it as the active config: the response carries the bound config path, its
    # targets, and the upload's provenance — and the normal config/target/scenario reads now see it.
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        assert status == 200 and resp["ok"] is True
        assert resp["config"].endswith("bajutsu.config.yaml")
        assert "demo" in resp["targets"]
        assert resp["source"]["kind"] == "upload" and resp["source"]["filename"] == "suite.zip"
        assert len(resp["source"]["sha256"]) == 64
        # The Replay / Record / Crawl tabs read the bound config through the normal endpoints.
        cfg = _get_json(port, "/api/config")
        assert cfg["hasConfig"] is True and cfg["config"] == resp["config"]
        assert [t["name"] for t in _get_json(port, "/api/targets")] == ["demo"]
        scns = _get_json(port, "/api/scenarios?target=demo")
        assert [s["file"] for s in scns] == ["smoke.yaml"]
    finally:
        server.shutdown()
        server.server_close()


def test_run_from_bound_bundle_into_serve_runs(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def popen(cmd: list[str], **kw: Any) -> FakeProc:
        calls.append((cmd, kw))
        return FakeProc(["PASS  runs/up-1/manifest.json\n"])

    server, port = _serve(_state(tmp_path, popen=popen))
    try:
        _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        status, resp = _post(port, "/api/run", {"target": "demo", "scenario": "smoke.yaml"})
        assert status == 200 and "jobId" in resp
        _wait_done(port, resp["jobId"])
        cmd, kw = calls[0]
        # Runs from the extracted bundle dir, so the config's relative paths resolve against it…
        bundle_root = Path(kw["cwd"])
        assert (tmp_path / "uploads").resolve() in bundle_root.resolve().parents
        # …but the run is written into serve's runs store (an absolute --runs-dir), not the sandbox.
        assert cmd[cmd.index("--runs-dir") + 1] == str((tmp_path / "runs").resolve())
        assert cmd[cmd.index("--config") + 1].endswith("bajutsu.config.yaml")
        assert cmd[cmd.index("--scenario") + 1].endswith("smoke.yaml")
        assert "--baselines" not in cmd  # the bundle config drives baselines, resolved against cwd
    finally:
        server.shutdown()
        server.server_close()


def test_run_off_bundle_records_provenance_in_manifest(tmp_path: Path) -> None:
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
        _, resp = _post(port, "/api/run", {"target": "demo", "scenario": "smoke.yaml"})
        _wait_done(port, resp["jobId"])
        manifest_path = tmp_path / "runs" / "up-1" / "manifest.json"
        assert _eventually(
            lambda: "provenance" in json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        prov = json.loads(manifest_path.read_text(encoding="utf-8"))["provenance"]
        assert prov["source"] == "upload" and prov["filename"] == "suite.zip"
        assert prov["sha256"] == up["source"]["sha256"]
    finally:
        server.shutdown()
        server.server_close()


def test_run_off_bundle_never_builds_from_uploaded_config(tmp_path: Path) -> None:
    # An uploaded config that declares a `build` must never have it executed on the host (DESIGN §1):
    # the bundle ships a prebuilt binary, so only one process — the run — is ever spawned.
    cfg = (
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        "  demo:\n"
        "    bundleId: com.example.demo\n"
        "    scenarios: ./scenarios\n"
        "    appPath: ./Missing.app\n"
        "    build: touch /tmp/bajutsu-pwned\n"
    )
    spawned: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        spawned.append(cmd)
        return FakeProc(["PASS  runs/up-1/manifest.json\n"])

    server, port = _serve(_state(tmp_path, popen=popen))
    try:
        _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip(config=cfg))
        _, resp = _post(port, "/api/run", {"target": "demo", "scenario": "smoke.yaml"})
        _wait_done(port, resp["jobId"])
        # Exactly one spawn — the run — and it is `bajutsu run`, never the config's `build` command.
        assert len(spawned) == 1 and "run" in spawned[0]
        # The build (`touch …`) must not have been spawned. Match on each spawned command's
        # executable, not a substring of its args: the run's interpreter path can legitimately
        # contain "touch" (e.g. a checkout under a "multitouch" directory), which a substring
        # test would misread as the build having run.
        assert not any(cmd and cmd[0] == "touch" for cmd in spawned)
    finally:
        server.shutdown()
        server.server_close()


def test_binding_another_bundle_keeps_both_cache_entries(tmp_path: Path) -> None:
    # Only one bundle is *bound* at a time, but its sha256-keyed extraction cache dir is no longer
    # removed on switch-away (BE-0243): both bundles' entries stay on disk, ready for reuse.
    server, port = _serve(_state(tmp_path))
    try:
        first, a = _post_bytes(port, "/api/upload?name=a.zip", _bundle_zip())
        assert first == 200
        second, b = _post_bytes(
            port, "/api/upload?name=b.zip", _bundle_zip(scenario=_SCENARIO + "\n")
        )
        assert second == 200 and b["source"]["filename"] == "b.zip"
        assert a["source"]["sha256"] != b["source"]["sha256"]
        assert len(_extracted_dirs(tmp_path)) == 2  # neither cache entry was removed
        assert _get_json(port, "/api/config")["config"] == b["config"]
    finally:
        server.shutdown()
        server.server_close()


def test_repeat_upload_of_same_bundle_reuses_the_cache_dir(tmp_path: Path) -> None:
    # Uploading identical content twice is content-addressed (BE-0243): the second upload cache-hits
    # the first's extraction dir instead of creating a second one.
    server, port = _serve(_state(tmp_path))
    try:
        first, a = _post_bytes(port, "/api/upload?name=a.zip", _bundle_zip())
        second, b = _post_bytes(port, "/api/upload?name=b.zip", _bundle_zip())
        assert (first, second) == (200, 200)
        assert a["source"]["sha256"] == b["source"]["sha256"]
        assert len(_extracted_dirs(tmp_path)) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_binding_filesystem_config_keeps_the_bundle_cache(tmp_path: Path) -> None:
    # Switching to another config source (the file browser) repoints the active config, but the
    # bundle's extraction cache dir persists (BE-0243) — proving the bundle is just one source among
    # three, and that unbinding is no longer what governs the cache's lifetime.
    local = tmp_path / "local.config.yaml"
    local.write_text(_CONFIG, encoding="utf-8")
    (tmp_path / "scenarios").mkdir(exist_ok=True)
    server, port = _serve(_state(tmp_path))
    try:
        _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        assert len(_extracted_dirs(tmp_path)) == 1
        status, resp = _post(port, "/api/config", {"path": str(local)})
        assert status == 200 and resp["config"] == str(local)
        assert len(_extracted_dirs(tmp_path)) == 1  # the cache entry is still there
        assert _get_json(port, "/api/config")["config"] == str(local)
    finally:
        server.shutdown()
        server.server_close()


def test_upload_persists_the_raw_zip_when_a_store_is_configured(tmp_path: Path) -> None:
    store = FakeObjectStore()
    server, port = _serve(_state(tmp_path, object_store=store, object_store_prefix="tenant/"))
    try:
        blob = _bundle_zip()
        status, resp = _post_bytes(port, "/api/upload?name=suite.zip", blob)
        assert status == 200
        key = f"tenant/uploads/{resp['source']['sha256']}.zip"
        assert store.objects[key] == blob
        assert store.put_calls == [key]
    finally:
        server.shutdown()
        server.server_close()


def test_repeat_upload_skips_the_store_write_when_the_key_already_exists(tmp_path: Path) -> None:
    store = FakeObjectStore()
    server, port = _serve(_state(tmp_path, object_store=store))
    try:
        _post_bytes(port, "/api/upload?name=a.zip", _bundle_zip())
        assert len(store.put_calls) == 1
        _post_bytes(port, "/api/upload?name=b.zip", _bundle_zip())
        assert len(store.put_calls) == 1  # the key already existed — no second write
    finally:
        server.shutdown()
        server.server_close()


def test_upload_store_write_failure_binds_nothing_but_keeps_the_local_cache(
    tmp_path: Path,
) -> None:
    # A store-write failure never binds — but the local extraction is left alone (BE-0243): it is
    # valid, reusable content regardless of whether the store write succeeded, and by the time this
    # failure is observed a concurrent bind of the same sha256 might already depend on it.
    store = FakeObjectStore()
    store.fail_with = ConnectionError("bucket unreachable")
    server, port = _serve(_state(tmp_path, object_store=store))
    try:
        status, resp = _post_bytes(port, "/api/upload?name=suite.zip", _bundle_zip())
        assert status == 400 and "could not persist" in resp["error"]
        assert _get_json(port, "/api/config")["hasConfig"] is False
        assert len(_extracted_dirs(tmp_path)) == 1  # the extraction itself is kept, only unbound
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
        assert _extracted_dirs(tmp_path) == []  # a rejected upload leaves nothing extracted behind
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_config_path_outside_bundle(tmp_path: Path) -> None:
    # An uploaded config is untrusted: a target whose scenarios/appPath/baselines points outside the
    # bundle (absolute or `..`) must be rejected at bind, or runs would reach host files (BE-0073).
    server, port = _serve(_state(tmp_path))
    try:
        for cfg in (
            "targets:\n  demo: { bundleId: com.example.demo, scenarios: /etc }\n",
            "targets:\n  demo: { bundleId: com.example.demo, appPath: ../../../etc/passwd }\n",
        ):
            status, resp = _post_bytes(
                port, "/api/upload?name=evil.zip", _zip_with({"bajutsu.config.yaml": cfg.encode()})
            )
            assert status == 400 and "invalid bundle" in resp["error"]
        assert _extracted_dirs(tmp_path) == []
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

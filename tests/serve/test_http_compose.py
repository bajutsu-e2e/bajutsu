"""Tests for composing a stored artifact triple into the active config (bajutsu serve, BE-0268).

`POST /api/compose` assembles a `(config, scenarios, binary)` triple from artifacts already uploaded
through `POST /api/artifacts/*` and binds the composed tree as the active config — the compose
picker's server side, and the combination matrix's cheap path (a new cell is a new triple over
stored artifacts, never a fresh upload). These exercise the wire path against a real
ThreadingHTTPServer plus one direct-call provenance check, no Simulator needed."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

from _shared import _post, _serve, fake_popen

from bajutsu import serve as srv
from bajutsu.serve import operations as ops

_FULL_CONFIG = (
    b"defaults: { backend: [ios] }\n"
    b"targets:\n"
    b"  demo: { bundleId: com.example.demo, scenarios: ./scenarios, appPath: ./build/Demo.app }\n"
)
_SCENARIOS_ONLY_CONFIG = b"defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _scenarios_zip() -> bytes:
    return _zip({"scenarios/smoke.yaml": b"- name: a\n  steps: []\n"})


def _app_zip() -> bytes:
    return _zip({"Info.plist": b"<plist/>", "Demo": b"\x7fELF"})


def _state(tmp_path: Path) -> srv.ServeState:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return srv.ServeState(
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        uploads_dir=tmp_path / "uploads",
        popen=fake_popen(["PASS  runs/up-1/manifest.json\n"]),
    )


def _post_bytes(port: int, path: str, data: bytes) -> Any:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def test_compose_binds_a_stored_triple_as_the_active_config(tmp_path: Path) -> None:
    # No object store: the artifacts land in the local cache on upload, and compose resolves them
    # straight from there — the plain `make serve` case.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _FULL_CONFIG)["sha256"]
        scenarios_sha = _post_bytes(port, "/api/artifacts/scenarios", _scenarios_zip())["sha256"]
        binary_sha = _post_bytes(port, "/api/artifacts/binary", _app_zip())["sha256"]
        status, resp = _post(
            port,
            "/api/compose",
            {"config": config_sha, "scenarios": scenarios_sha, "binary": binary_sha},
        )
        assert status == 200 and resp["ok"] is True
        assert resp["targets"] == ["demo"]
        assert resp["source"]["kind"] == "upload"
        assert resp["source"]["artifacts"] == {
            "config": config_sha,
            "scenarios": scenarios_sha,
            "binary": binary_sha,
        }
        # The bound config is the one written into the composed tree, not the raw artifact blob.
        assert Path(resp["config"]).name == "bajutsu.config.yaml"
        assert Path(resp["config"]).read_bytes() == _FULL_CONFIG
    finally:
        server.shutdown()
        server.server_close()


def test_compose_needs_only_the_legs_the_config_references(tmp_path: Path) -> None:
    # A scenarios-only config composes from config + scenarios alone; no binary leg required.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _SCENARIOS_ONLY_CONFIG)["sha256"]
        scenarios_sha = _post_bytes(port, "/api/artifacts/scenarios", _scenarios_zip())["sha256"]
        status, resp = _post(
            port, "/api/compose", {"config": config_sha, "scenarios": scenarios_sha}
        )
        assert status == 200 and resp["targets"] == ["demo"]
        assert "binary" not in resp["source"]["artifacts"]
    finally:
        server.shutdown()
        server.server_close()


def test_compose_accepts_a_single_yaml_scenarios_artifact(tmp_path: Path) -> None:
    # The scenarios drop zone takes a single .yaml, not only a .zip: the server writes it into the
    # config's scenarios dir (named from scenariosName, normalized to .yaml) and binds the triple.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _SCENARIOS_ONLY_CONFIG)["sha256"]
        yaml_blob = b"- name: smoke\n  steps: []\n"
        scenarios_sha = _post_bytes(port, "/api/artifacts/scenarios", yaml_blob)["sha256"]
        status, resp = _post(
            port,
            "/api/compose",
            {"config": config_sha, "scenarios": scenarios_sha, "scenariosName": "smoke.yml"},
        )
        assert status == 200 and resp["targets"] == ["demo"]
        assert (Path(resp["config"]).parent / "scenarios" / "smoke.yaml").is_file()
    finally:
        server.shutdown()
        server.server_close()


def test_compose_ignores_scenarios_name_for_a_zip_artifact(tmp_path: Path) -> None:
    # A zip's name is irrelevant to the composed tree (materialize_composition content-sniffs it), so
    # supplying scenariosName for a zip must NOT fragment the compose cache: the same triple with and
    # without the name composes to the same tree. Enforced server-side, not just by the UI's gate.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _SCENARIOS_ONLY_CONFIG)["sha256"]
        scenarios_sha = _post_bytes(port, "/api/artifacts/scenarios", _scenarios_zip())["sha256"]
        _, without = _post(port, "/api/compose", {"config": config_sha, "scenarios": scenarios_sha})
        _, with_name = _post(
            port,
            "/api/compose",
            {"config": config_sha, "scenarios": scenarios_sha, "scenariosName": "irrelevant.yml"},
        )
        assert without["config"] == with_name["config"]  # one composition, name ignored for a zip
    finally:
        server.shutdown()
        server.server_close()


def test_compose_swapping_one_leg_is_a_new_triple_not_a_new_upload(tmp_path: Path) -> None:
    # The combination matrix: two scenario sets against one config compose into two distinct trees,
    # each a fresh triple over already-stored artifacts — no re-upload of the shared config.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _SCENARIOS_ONLY_CONFIG)["sha256"]
        a_sha = _post_bytes(port, "/api/artifacts/scenarios", _scenarios_zip())["sha256"]
        b_sha = _post_bytes(
            port, "/api/artifacts/scenarios", _zip({"scenarios/other.yaml": b"- name: b\n"})
        )["sha256"]
        _, resp_a = _post(port, "/api/compose", {"config": config_sha, "scenarios": a_sha})
        _, resp_b = _post(port, "/api/compose", {"config": config_sha, "scenarios": b_sha})
        assert resp_a["config"] != resp_b["config"]  # distinct composed trees
    finally:
        server.shutdown()
        server.server_close()


def test_compose_rejects_a_missing_config_leg(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post(port, "/api/compose", {"scenarios": hashlib.sha256(b"x").hexdigest()})
        assert status == 400 and "config" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_compose_rejects_a_malformed_config_sha(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path))
    try:
        status, resp = _post(port, "/api/compose", {"config": "not-a-sha"})
        assert status == 400 and "error" in resp
    finally:
        server.shutdown()
        server.server_close()


def test_compose_404s_when_a_leg_was_never_stored(tmp_path: Path) -> None:
    # A well-formed sha for a leg that was never uploaded (and no object store to fetch it from) is
    # a clean 404, not a crash — the `_fetch_artifact` no-object-store miss path.
    server, port = _serve(_state(tmp_path))
    try:
        config_sha = _post_bytes(port, "/api/artifacts/config", _SCENARIOS_ONLY_CONFIG)["sha256"]
        absent = hashlib.sha256(b"never uploaded").hexdigest()
        status, resp = _post(port, "/api/compose", {"config": config_sha, "scenarios": absent})
        assert status == 404 and "not available" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def _store_artifact(state: srv.ServeState, tmp_path: Path, kind: str, blob: bytes) -> str:
    sha = hashlib.sha256(blob).hexdigest()
    src = tmp_path / f"{kind}.blob"
    src.write_bytes(blob)
    ops.bind_artifact(state, kind, src, sha256=sha)  # type: ignore[arg-type]
    return sha


def test_composed_bind_records_triple_sha_provenance(tmp_path: Path) -> None:
    # A composed bind's manifest provenance reports `compositionId` + one `<kind>Sha` per supplied
    # artifact, never a single top-level `sha256` that would falsely imply one verifiable hash.
    state = _state(tmp_path)
    config_sha = _store_artifact(state, tmp_path, "config", _FULL_CONFIG)
    scenarios_sha = _store_artifact(state, tmp_path, "scenarios", _scenarios_zip())
    binary_sha = _store_artifact(state, tmp_path, "binary", _app_zip())
    _, status = ops.bind_composition(
        state, {"config": config_sha, "scenarios": scenarios_sha, "binary": binary_sha}
    )
    assert status == 200
    assert state.upload is not None
    prov = state.upload.provenance
    assert prov["compositionId"] == state.upload.sha256
    assert prov["configSha"] == config_sha
    assert prov["scenariosSha"] == scenarios_sha
    assert prov["binarySha"] == binary_sha
    assert "sha256" not in prov

"""Tests for adding scenarios to a bound config's scope via a `.zip` (bajutsu serve, BE-0340).

`POST /api/scenarios/upload` resolves the same `ScenarioScope` seam `save_scenario` resolves, so it
adds a new way to *populate* an already-bound config's scope, never a new way to bind one. These
exercise the wire path against a real ThreadingHTTPServer, mirroring test_http_upload.py's fixture
style (a fixed zip entry timestamp keeps repeated builds byte-identical).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest
from _shared import _get_json, _serve, project

from bajutsu import serve as srv
from bajutsu.serve import handler as handler_mod

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _write_entry(zf: zipfile.ZipFile, name: str, content: str | bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, content)


def _zip_with(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            _write_entry(zf, name, content)
    return buf.getvalue()


def _post_bytes(port: int, path: str, data: bytes) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/zip"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


_ALPHA = "- name: alpha\n  steps:\n    - tap: { id: x }\n"
_BETA = "- name: beta\n  steps:\n    - tap: { id: y }\n"


def test_scenarios_upload_adds_new_files_and_lists_them(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        blob = _zip_with({"alpha.yaml": _ALPHA, "beta.yaml": _BETA})
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", blob)
        assert status == 200 and resp["ok"] is True
        assert {(s["name"], s["overwritten"]) for s in resp["scenarios"]} == {
            ("alpha.yaml", False),
            ("beta.yaml", False),
        }
        assert (scn_dir / "alpha.yaml").read_text(encoding="utf-8") == _ALPHA
        assert (scn_dir / "beta.yaml").read_text(encoding="utf-8") == _BETA
        files = {s["file"] for s in _get_json(port, "/api/scenarios?target=demo")}
        assert files == {"smoke.yaml", "alpha.yaml", "beta.yaml"}
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_reports_overwritten_for_an_existing_name(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)  # project() already writes scenarios/smoke.yaml
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        blob = _zip_with({"smoke.yaml": _ALPHA})
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", blob)
        assert status == 200
        assert resp["scenarios"] == [{"name": "smoke.yaml", "overwritten": True}]
        assert (scn_dir / "smoke.yaml").read_text(encoding="utf-8") == _ALPHA
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_writes_nothing_when_one_entry_fails_to_parse(tmp_path: Path) -> None:
    # All-or-nothing (BE-0340): every entry is parsed before anything is written, so a bad batch
    # never leaves a partial overwrite behind — the same guarantee start_run_set applies to a
    # scenario fan-out.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        blob = _zip_with({"alpha.yaml": _ALPHA, "bad.yaml": "just a bare string\n"})
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", blob)
        assert status == 400 and "invalid scenario" in resp["error"]
        assert not (scn_dir / "alpha.yaml").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_rejects_zip_slip(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        blob = _zip_with({"../escape.yaml": _ALPHA})
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", blob)
        assert status == 400 and "unsafe entry" in resp["error"]
        assert not (tmp_path / "escape.yaml").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_rejects_a_nested_yaml(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        blob = _zip_with({"nested/deeper.yaml": _ALPHA})
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", blob)
        assert status == 400 and "nested entry" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_without_a_bound_config_errors(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    server, port = _serve(srv.ServeState(runs_dir=runs, cwd=tmp_path, root=tmp_path))
    try:
        status, resp = _post_bytes(
            port, "/api/scenarios/upload?target=demo", _zip_with({"alpha.yaml": _ALPHA})
        )
        assert status == 400 and "path must be" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_rejects_a_non_zip_body(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post_bytes(port, "/api/scenarios/upload?target=demo", b"not a zip at all")
        assert status == 400 and "valid zip" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_scenarios_upload_wire_cap_is_smaller_than_the_bundle_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The scenario-zip route must not inherit /api/upload's 1 GiB wire cap: a scenario batch has no
    # reason to approach that size, and admitting one lets an editor-role client (or, with no token
    # configured, anyone on loopback) stream far more to a server temp file than the scenario-sized
    # bounds are meant to allow. Shrink the cap this route actually reads (handler.py's own imported
    # name — read_scenario_zip's separate, similarly-named bound is not what's under test here).
    assert (
        handler_mod.MAX_SCENARIO_ZIP_TOTAL_BYTES  # type: ignore[attr-defined]
        < handler_mod.MAX_UPLOAD_BYTES  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(handler_mod, "MAX_SCENARIO_ZIP_TOTAL_BYTES", 4)
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post_bytes(
            port, "/api/scenarios/upload?target=demo", _zip_with({"alpha.yaml": _ALPHA})
        )
        assert status == 413 and "too large" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()

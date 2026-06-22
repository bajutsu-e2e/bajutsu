"""Tests for serving run artifacts through the ArtifactStore over HTTP (BE-0015 PR3)."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import _serve, project, write_run

from bajutsu import serve as srv


def test_serve_run_file_serves_body_from_store(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/r1/report.html") as r:
            assert r.status == 200 and "text/html" in r.headers.get("Content-Type", "")
            assert r.read() == b"<html></html>"
    finally:
        server.shutdown()
        server.server_close()


def test_archive_endpoint_streams_a_zip_attachment(tmp_path: Path) -> None:
    import io
    import zipfile

    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/r1/archive.zip") as r:
            assert r.status == 200
            assert r.headers.get("Content-Type") == "application/zip"
            assert 'attachment; filename="r1.zip"' in r.headers.get("Content-Disposition", "")
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert "r1/report.html" in zf.namelist()  # resolves through ArtifactStore, rooted at id
    finally:
        server.shutdown()
        server.server_close()


def test_archive_endpoint_rejects_a_nested_run_id(tmp_path: Path) -> None:
    # /runs/<id>/demo/archive.zip would zip a subdir and put a `/` in the download filename
    # (HTTP response splitting); a non-segment id is rejected as 404.
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    (runs / "r1" / "demo").mkdir()
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/r1/demo/archive.zip")
        assert ei.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


class _RedirectStore:
    """A stand-in server-style store that hands back a signed-URL redirect instead of bytes."""

    def get(self, rel: str) -> srv.Artifact:
        return srv.Artifact(content_type="image/png", redirect=f"https://signed.example/{rel}")

    def open_bytes(self, rel: str) -> bytes | None:
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        return []

    def archive(self, run_id: str) -> srv.Artifact:
        return srv.Artifact(
            content_type="application/zip", redirect=f"https://signed.example/{run_id}.zip"
        )


def test_serve_run_file_emits_redirect_when_store_returns_one(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs)
    state.artifacts = _RedirectStore()  # inject the server-style store (the swap-in seam)
    server, port = _serve(state)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a: object, **_k: object) -> None:
            return None  # don't follow — assert on the 302 itself

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            opener.open(f"http://127.0.0.1:{port}/runs/r1/shot.png")
        assert ei.value.code == 302
        assert ei.value.headers["Location"] == "https://signed.example/r1/shot.png"
    finally:
        server.shutdown()
        server.server_close()

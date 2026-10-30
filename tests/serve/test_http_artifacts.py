"""Tests for serving run artifacts through the ArtifactStore over HTTP (BE-0015 PR3)."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import StubArtifactStore, _serve, project, write_run
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve.helpers import INLINE_ARTIFACT_CACHE_CONTROL, SIGNED_REDIRECT_CACHE_CONTROL
from bajutsu.serve.server.app import make_app


def test_serve_run_file_serves_body_from_store(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/r1/report.html") as r:
            assert r.status == 200 and "text/html" in r.headers.get("Content-Type", "")
            assert r.read() == b"<html></html>"
            # 200 is heuristically cacheable (unlike the redirect's 302); org scoping is by actor,
            # not by URL, so a shared cache storing this would replay it across orgs.
            assert r.headers.get("Cache-Control") == INLINE_ARTIFACT_CACHE_CONTROL
    finally:
        server.shutdown()
        server.server_close()


def test_serve_run_file_honors_a_range_request(tmp_path: Path) -> None:
    # A report's <video> needs 206/Content-Range to seek into an unbuffered part of the file —
    # a 200-only server makes every browser silently restart playback from 0 instead.
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/runs/r1/report.html", headers={"Range": "bytes=1-4"}
        )
        with urllib.request.urlopen(req) as r:
            assert r.status == 206
            assert r.headers.get("Accept-Ranges") == "bytes"
            assert r.headers.get("Content-Range") == "bytes 1-4/13"  # b"<html></html>" is 13 bytes
            # 206 is heuristically cacheable too (RFC 9110 §15.1), so the Range path needs the
            # same bound as the 200 above.
            assert r.headers.get("Cache-Control") == INLINE_ARTIFACT_CACHE_CONTROL
            assert r.read() == b"html"
    finally:
        server.shutdown()
        server.server_close()


def test_serve_run_file_rejects_an_unsatisfiable_range(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    server, port = _serve(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/runs/r1/report.html", headers={"Range": "bytes=999-1000"}
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
        assert ei.value.code == 416
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


class _RedirectStore(StubArtifactStore):
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
        # Without this a cache may invent its own freshness for the 302 and go on handing out a
        # signed URL that has already expired.
        assert ei.value.headers["Cache-Control"] == SIGNED_REDIRECT_CACHE_CONTROL
    finally:
        server.shutdown()
        server.server_close()


def test_fastapi_redirect_carries_the_same_cache_control(tmp_path: Path) -> None:
    # The hosted backend emits the same redirect, so it must bound it the same way.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs)
    state.artifacts = _RedirectStore()
    resp = TestClient(make_app(state)).get("/runs/r1/shot.png", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "https://signed.example/r1/shot.png"
    assert resp.headers["cache-control"] == SIGNED_REDIRECT_CACHE_CONTROL


def test_fastapi_inline_bytes_carry_the_same_cache_control(tmp_path: Path) -> None:
    # The local-store (inline-bytes) branch is the hosted twin of
    # test_serve_run_file_serves_body_from_store — same org-scoped-by-actor exposure, both backends.
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "r1", ok=True, scenarios=[("smoke", True)])
    resp = TestClient(
        make_app(srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs))
    ).get("/runs/r1/report.html")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == INLINE_ARTIFACT_CACHE_CONTROL


def test_signed_redirect_is_never_stored() -> None:
    # The regression to catch is a positively-cacheable directive creeping back in: the redirect
    # target is org-scoped via the actor, not the request URL, so a stored copy reaches the wrong
    # caller. Asserted as a property, so a harmless reformat of the constant doesn't fail here.
    directives = {d.strip() for d in SIGNED_REDIRECT_CACHE_CONTROL.split(",")}
    assert "no-store" in directives
    assert "public" not in directives
    # A *zero* freshness lifetime is the point (see the constant); a non-zero one is the regression.
    assert not [
        d for d in directives if d.startswith(("max-age", "s-maxage")) and not d.endswith("=0")
    ]
    assert "s-maxage=0" in directives  # the CDN that reads only a freshness lifetime


def test_inline_artifact_is_never_stored_by_a_shared_cache() -> None:
    # The regression to catch is a shared-cacheable directive creeping back in: 200/206 are
    # heuristically cacheable (RFC 9110 §15.1) and the org comes from the actor, not the URL, so a
    # shared cache may legally replay one org's bytes to another caller. `private` is what forbids a
    # shared cache storing it at all (RFC 9111 §3). The wire tests above compare against this same
    # constant, so this is the only assertion that would fail if its value went shared-cacheable.
    directives = {d.strip() for d in INLINE_ARTIFACT_CACHE_CONTROL.split(",")}
    assert "private" in directives
    assert "public" not in directives


def test_relative_runs_dir_is_anchored_at_launch_cwd(tmp_path: Path, monkeypatch: Any) -> None:
    # A subdir config repoints `cwd` to the config's dir (BE-0242), but a relative `runs_dir` (the
    # `--runs` default) must stay anchored at serve's launch cwd — the store, `jobs`, and `triage`
    # all read there. Otherwise a just-finished run's report.html reads back as not-found. Construct
    # from a launch cwd distinct from the config's `cwd`, then serve a run written under launch/runs.
    launch = tmp_path / "launch"
    launch.mkdir()
    cfgdir = tmp_path / "cfgdir"
    scn_dir = cfgdir / "scenarios"
    scn_dir.mkdir(parents=True)
    cfg = cfgdir / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: ios\n"
            f"    subject: {{id: com.example.demo}}\n"
            f"    paths: {{scenarios: {scn_dir}}}\n"
        ),
        encoding="utf-8",
    )
    write_run(launch / "runs", "r1", ok=True, scenarios=[("smoke", True)])
    monkeypatch.chdir(launch)  # ServeState resolves relative dirs against the launch cwd
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=Path("runs"), cwd=cfgdir)
    assert state.runs_dir == launch / "runs"  # anchored at launch, not the config's `cwd`
    assert (
        state.baselines_dir == launch / "baselines"
    )  # a relative baselines_dir anchors the same way
    server, port = _serve(state)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/r1/report.html") as r:
            assert r.status == 200
            assert r.read() == b"<html></html>"
    finally:
        server.shutdown()
        server.server_close()

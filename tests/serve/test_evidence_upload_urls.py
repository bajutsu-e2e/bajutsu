"""Tests for the presigned evidence-upload endpoint (BE-0110 serve path).

`generate_upload_urls` is the control-plane operation behind ``POST /api/runs/<run_id>/upload-urls``:
the server holds the evidence store's credentials and hands a worker one presigned PUT URL per file,
so the worker uploads a run's evidence over plain HTTP with no cloud credentials of its own. The
server owns the bucket/base-prefix and re-validates every client-supplied segment (run id, the
optional per-run prefix, each file path) so a worker can't escape the run's key namespace.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, project

from bajutsu import serve as srv
from bajutsu.object_store import EvidenceTarget
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState


class _FakeStore:
    """The slice of `ObjectStore` the endpoint uses: a signed PUT URL per key."""

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = 3600) -> str:
        return f"https://signed.example/{key}?ct={content_type}"


def _state(tmp_path: Path, *, evidence: EvidenceTarget | None) -> ServeState:
    state = ServeState(runs_dir=tmp_path / "runs")
    state.evidence = evidence
    return state


def test_no_evidence_store_configured_returns_no_urls(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=None)
    payload, status = ops.generate_upload_urls(
        state, "20260702-143000", {"files": ["00-login/after.png"]}
    )
    assert status == 200
    assert payload == {"urls": {}}


def test_signs_one_put_url_per_file_keyed_under_base_prefix_and_run_id(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    payload, status = ops.generate_upload_urls(
        state,
        "20260702-143000",
        {"evidence_prefix": "main/abc1234/", "files": ["00-login/after.png", "manifest.json"]},
    )
    assert status == 200
    assert payload["urls"] == {
        "00-login/after.png": (
            "https://signed.example/evidence/main/abc1234/20260702-143000/00-login/after.png"
            "?ct=image/png"
        ),
        "manifest.json": (
            "https://signed.example/evidence/main/abc1234/20260702-143000/manifest.json"
            "?ct=application/json"
        ),
    }


def test_missing_evidence_prefix_keys_directly_under_base(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    payload, status = ops.generate_upload_urls(
        state, "20260702-143000", {"files": ["manifest.json"]}
    )
    assert status == 200
    assert payload["urls"]["manifest.json"].startswith(
        "https://signed.example/evidence/20260702-143000/manifest.json"
    )


def test_invalid_run_id_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    _, status = ops.generate_upload_urls(state, "../etc", {"files": ["manifest.json"]})
    assert status == 400


def test_evidence_prefix_traversal_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    _, status = ops.generate_upload_urls(
        state,
        "20260702-143000",
        {"evidence_prefix": "../../secret/", "files": ["manifest.json"]},
    )
    assert status == 400


def test_file_path_traversal_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    _, status = ops.generate_upload_urls(state, "20260702-143000", {"files": ["../../etc/passwd"]})
    assert status == 400


def test_files_must_be_a_list(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    _, status = ops.generate_upload_urls(state, "20260702-143000", {"files": "manifest.json"})
    assert status == 400


def test_a_non_string_file_entry_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path, evidence=EvidenceTarget(store=_FakeStore(), base_prefix="evidence/"))
    _, status = ops.generate_upload_urls(
        state, "20260702-143000", {"files": ["manifest.json", 123]}
    )
    assert status == 400


def test_http_upload_urls_route_signs_urls(tmp_path: Path) -> None:
    # The route is reachable through the real stdlib handler, not just the operation (BE-0110).
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    state.evidence = EvidenceTarget(store=_FakeStore(), base_prefix="evidence/")
    server, port = _serve(state)
    try:
        status, resp = _post(
            port, "/api/runs/20260702-143000/upload-urls", {"files": ["manifest.json"]}
        )
        assert status == 200
        assert resp["urls"]["manifest.json"].startswith(
            "https://signed.example/evidence/20260702-143000/manifest.json"
        )
    finally:
        server.shutdown()
        server.server_close()


def test_start_run_rejects_an_unsafe_evidence_prefix(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port,
            "/api/run",
            {"scenario": "smoke.yaml", "target": "demo", "evidence_prefix": "../escape/"},
        )
        assert status == 400 and "evidence_prefix" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_build_state_holds_the_evidence_target(tmp_path: Path) -> None:
    # The serve wiring carries the CLI-resolved evidence target onto the state unchanged.
    target = EvidenceTarget(store=_FakeStore(), base_prefix="evidence/")
    state = srv._build_state(
        runs_dir=tmp_path / "runs",
        config=None,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        evidence=target,
    )
    assert state.evidence is target

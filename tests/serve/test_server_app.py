"""Tests for the hosted-backend FastAPI control plane (BE-0015 server phase, PR5).

`make_app(state)` is the FastAPI shell that serves the same SPA + API as the local stdlib handler,
delegating every request to the shared `bajutsu.serve.operations`. These tests drive it via
FastAPI's TestClient over a `ServeState` built with the local seams + tmp dirs, so the app's
routing / auth / CSRF / security headers are exercised without a Simulator. The request-handling
logic itself is covered once, through the stdlib suite, since both backends call the same ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import SCENARIO, project, write_run
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.anthropic_client import BEDROCK_MODEL_ENV, PROVIDER_ENV
from bajutsu.serve.server.app import make_app


def _client(state: srv.ServeState) -> TestClient:
    return TestClient(make_app(state))


def _state(tmp_path: Path, *, token: str | None = None) -> srv.ServeState:
    _scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(config=cfg, runs_dir=runs, root=tmp_path, cwd=tmp_path, token=token)


def test_serves_the_spa_at_root(tmp_path: Path) -> None:
    client = _client(_state(tmp_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" in resp.text.lower()


def test_get_reads_delegate_to_operations(tmp_path: Path) -> None:
    state = _state(tmp_path)
    write_run(tmp_path / "runs", "20260101-000000", ok=True, scenarios=[("alpha", True)])
    client = _client(state)
    assert client.get("/api/apps").json() == ["demo", "other"]
    assert client.get("/api/scenarios?app=demo").json()[0]["names"] == ["alpha", "beta"]
    assert client.get("/api/config").json()["hasConfig"] is True
    assert client.get("/api/runs").json()[0]["id"] == "20260101-000000"
    body = client.get("/api/scenario?app=demo&path=smoke.yaml").json()
    assert body["yaml"] == SCENARIO
    assert client.get("/api/scenario?app=demo&path=missing.yaml").status_code == 404
    assert client.get("/api/nope").status_code == 404


def test_security_headers_on_every_response(tmp_path: Path) -> None:
    resp = _client(_state(tmp_path)).get("/api/runs")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_post_scenario_save_writes_and_rejects(tmp_path: Path) -> None:
    scn_dir = tmp_path / "scenarios"
    client = _client(_state(tmp_path))
    target = scn_dir / "smoke.yaml"
    edited = "- name: edited\n  steps:\n    - tap: { id: y }\n"
    ok = client.post("/api/scenario", json={"app": "demo", "path": str(target), "yaml": edited})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert target.read_text(encoding="utf-8") == edited
    # A non-saveable path is reported (path error wins over the YAML parse).
    bad = client.post("/api/scenario", json={"app": "demo", "path": "note.txt", "yaml": "x: ["})
    assert bad.status_code == 400 and "path must be" in bad.json()["error"]


def test_post_validation_errors_delegate(tmp_path: Path) -> None:
    client = _client(_state(tmp_path))
    # start_run: scenario+app required
    assert client.post("/api/run", json={"app": "demo"}).status_code == 400
    # approve: runId/sid/baseline required
    assert client.post("/api/approve", json={}).status_code == 400
    # cancel of an unknown job
    assert client.post("/api/jobs/ghost/cancel", json={}).status_code == 404
    assert client.get("/api/jobs/ghost").status_code == 404


def test_more_delegations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider/apikey POSTs write os.environ directly (inherited by spawned jobs); clear the
    # vars first so monkeypatch restores the originals at teardown and no state leaks to later tests.
    for var in ("ANTHROPIC_API_KEY", PROVIDER_ENV, BEDROCK_MODEL_ENV, "AWS_REGION"):
        monkeypatch.delenv(var, raising=False)
    client = _client(_state(tmp_path))
    # GET delegations
    assert client.get("/api/fs").json()["cwd"] == str(tmp_path.resolve())
    assert client.get("/api/apikey").json()["set"] in (True, False)
    assert "provider" in client.get("/api/provider").json()
    assert isinstance(client.get("/api/simulators").json(), list)
    # POST delegations (validation paths — no device needed)
    assert client.post("/api/config", json={}).status_code == 400  # path required
    assert client.post("/api/record", json={"app": "demo"}).status_code == 400  # goal required
    assert client.post("/api/crawl", json={}).status_code == 400  # app required
    assert client.post("/api/provider", json={"provider": "anthropic"}).json()["ok"] is True
    set_key = client.post("/api/apikey", json={"value": "k key"})  # whitespace rejected
    assert set_key.status_code == 400


def test_job_events_streams_log_then_done(tmp_path: Path) -> None:
    # The buffered LogBus means a subscriber that attaches after the job finished still replays
    # every line and the terminal event, so reading to EOF is deterministic on the gate.
    state = _state(tmp_path)
    state.jobs["j1"] = srv.Job(id="j1", cmd=[])
    state.logbus.publish("j1", "step 0 ok\n")
    state.logbus.publish("j1", "PASS  runs/20260610-1/manifest.json\n")
    state.logbus.close("j1")
    resp = _client(state).get("/api/jobs/j1/events")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "event: log" in text and "data: step 0 ok" in text
    assert "event: done" in text and '"id": "j1"' in text


def test_job_events_unknown_is_404(tmp_path: Path) -> None:
    assert _client(_state(tmp_path)).get("/api/jobs/nope/events").status_code == 404


def test_csrf_blocks_cross_origin_post(tmp_path: Path) -> None:
    # With a token configured, a state-changing POST from a foreign Origin is blocked (BE-0051),
    # mirroring the stdlib handler's CSRF check.
    state = _state(tmp_path, token="s3cret")
    client = TestClient(make_app(state))
    headers = {"Authorization": "Bearer s3cret", "Origin": "http://evil.example"}
    resp = client.post("/api/apikey", json={"value": "x"}, headers=headers)
    assert resp.status_code == 403 and "cross-origin" in resp.json()["error"]


def test_auth_gate_mirrors_stdlib(tmp_path: Path) -> None:
    state = _state(tmp_path, token="s3cret")
    app = make_app(state)
    client = TestClient(app)
    # The index is open (so the login UI can load); the API is not.
    assert client.get("/").status_code == 200
    assert client.get("/api/runs").status_code == 401
    # A bad token is rejected; a good one mints a session cookie that then authorizes.
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    login = client.post("/api/login", json={"token": "s3cret"})
    assert login.status_code == 200
    # The TestClient keeps the cookie jar, so the follow-up carries the session cookie.
    assert client.get("/api/runs").status_code == 200
    # A Bearer token also authorizes (a fresh client over the same state — no session cookie).
    fresh = TestClient(app)
    assert fresh.get("/api/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200

"""Tests for the hosted-backend FastAPI control plane (BE-0015 server phase, PR5).

`make_app(state)` is the FastAPI shell that serves the same SPA + API as the local stdlib handler,
delegating every request to the shared `bajutsu.serve.operations`. These tests drive it via
FastAPI's TestClient over a `ServeState` built with the local seams + tmp dirs, so the app's
routing / auth / CSRF / security headers are exercised without a Simulator. The request-handling
logic itself is covered once, through the stdlib suite, since both backends call the same ops.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from _shared import SCENARIO, fake_popen, project, write_run
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.agents.ai_config import BEDROCK_MODEL_ENV, PROVIDER_ENV
from bajutsu.serve import operations as ops
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.oauth import Identity


def _client(state: srv.ServeState) -> TestClient:
    return TestClient(make_app(state))


def _state(tmp_path: Path, *, token: str | None = None) -> srv.ServeState:
    _scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        config=cfg, runs_dir=runs, root=tmp_path, cwd=tmp_path, auth=srv.SessionManager(token=token)
    )


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
    assert [a["name"] for a in client.get("/api/targets").json()] == ["demo", "other"]
    assert client.get("/api/scenarios?target=demo").json()[0]["names"] == ["alpha", "beta"]
    assert client.get("/api/config").json()["hasConfig"] is True
    assert client.get("/api/runs").json()[0]["id"] == "20260101-000000"
    # The crawl history is keyed on screenmap.json, so a manifest-only run never appears.
    crawl_dir = tmp_path / "runs" / "20260101-000001"
    crawl_dir.mkdir(parents=True)
    (crawl_dir / "screenmap.json").write_text(
        json.dumps({"nodes": [{}], "edges": [], "crashes": []}), encoding="utf-8"
    )
    assert [r["id"] for r in client.get("/api/crawl/runs").json()] == ["20260101-000001"]
    body = client.get("/api/scenario?target=demo&path=smoke.yaml").json()
    assert body["yaml"] == SCENARIO
    assert client.get("/api/scenario?target=demo&path=missing.yaml").status_code == 404
    assert client.get("/api/nope").status_code == 404


def test_scenario_secrets_delegate_to_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0274: the scenario-secrets pair works on the hosted backend too — GET returns the declared
    # list (a bare JSON array, exercising list serialization through JSONResponse), POST sets one.
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir}, secrets: [LOGIN_PASSWORD] }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    client = _client(srv.ServeState(config=cfg, runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        assert client.get("/api/secrets").json() == [
            {"name": "LOGIN_PASSWORD", "set": False, "masked": None}
        ]
        resp = client.post(
            "/api/secrets", json={"name": "LOGIN_PASSWORD", "value": "hunter2-secret"}
        )
        assert resp.status_code == 200 and resp.json()["masked"] == "hunt…cret"
        assert client.get("/api/secrets").json()[0]["set"] is True
    finally:
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)


def test_run_file_honors_a_range_request(tmp_path: Path) -> None:
    # /runs/<rel> has its own Range handling (not delegated to `ops`, unlike the routes above), so
    # it's covered here too, not just in the stdlib-handler suite (BE-0015 PR3).
    state = _state(tmp_path)
    write_run(tmp_path / "runs", "r1", ok=True, scenarios=[("smoke", True)])
    client = _client(state)
    resp = client.get("/runs/r1/report.html", headers={"Range": "bytes=1-4"})
    assert resp.status_code == 206
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.headers["Content-Range"] == "bytes 1-4/13"  # b"<html></html>" is 13 bytes
    assert resp.content == b"html"

    unsatisfiable = client.get("/runs/r1/report.html", headers={"Range": "bytes=999-1000"})
    assert unsatisfiable.status_code == 416


def test_lint_and_schema_routes_delegate_to_operations(tmp_path: Path) -> None:
    # The editor's inline validation (BE-0138) reaches the same ops as the stdlib handler.
    client = _client(_state(tmp_path))
    ok = client.post(
        "/api/lint", json={"yaml": "- name: a\n  steps:\n    - tap: { id: x }\n"}
    ).json()
    assert ok == {"ok": True, "diagnostics": []}
    bad = client.post("/api/lint", json={"yaml": "- steps:\n    - tap: { id: x }\n"}).json()
    assert bad["ok"] is False
    assert bad["diagnostics"][0]["line"] == 1
    assert "Scenario" in client.get("/api/schema").json()["$defs"]


def test_audit_route_delegates_to_operations(tmp_path: Path) -> None:
    # The determinism audit (BE-0145) reaches the same ops as the stdlib handler, on both the
    # inline-yaml (editor) and {target, path} (Replay) paths the hosted backend serves.
    client = _client(_state(tmp_path))
    inline = client.post(
        "/api/audit", json={"yaml": "- name: a\n  steps:\n    - tap: { label: OK }\n"}
    ).json()
    assert inline["ok"] is True
    assert inline["reports"][0]["grade"] == "Moderate"
    by_path = client.post("/api/audit", json={"target": "demo", "path": "smoke.yaml"}).json()
    assert {r["grade"] for r in by_path["reports"]} == {"Stable"}
    assert (
        client.post("/api/audit", json={"target": "demo", "path": "missing.yaml"}).status_code
        == 404
    )


def test_upload_route_binds_bundle_like_stdlib(tmp_path: Path) -> None:
    # The FastAPI shell reaches the same shared op as the stdlib handler (BE-0073); the bind logic
    # itself (extraction, path confinement, sandboxing) is covered once via the stdlib suite
    # (test_http_upload.py) — this only proves the ASGI route exists and delegates to it.
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "bajutsu.config.yaml",
            "defaults: { backend: [fake] }\n"
            "targets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n",
        )
        zf.writestr("scenarios/smoke.yaml", "- name: alpha\n  steps:\n    - tap: { id: x }\n")
    zip_bytes = buf.getvalue()

    state = srv.ServeState(
        runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path, uploads_dir=tmp_path / "uploads"
    )
    (tmp_path / "runs").mkdir()
    resp = _client(state).post(
        "/api/upload?name=suite.zip", content=zip_bytes, headers={"content-type": "application/zip"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and "demo" in body["targets"]
    assert body["source"]["kind"] == "upload" and body["source"]["filename"] == "suite.zip"
    assert len(body["source"]["sha256"]) == 64
    assert _client(state).get("/api/config").json()["hasConfig"] is True


def test_artifact_upload_routes_bind_like_stdlib(tmp_path: Path) -> None:
    # The FastAPI shell's per-artifact routes (BE-0268) reach the same shared `ops.bind_artifact`
    # as the stdlib handler; the storage/caching logic itself is covered once via the stdlib suite
    # (test_http_upload_artifacts.py) — this only proves the ASGI routes exist and delegate to it.
    import hashlib

    state = srv.ServeState(
        runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path, uploads_dir=tmp_path / "uploads"
    )
    (tmp_path / "runs").mkdir()
    client = _client(state)
    blob = b"targets: {}\n"
    resp = client.post(
        "/api/artifacts/config", content=blob, headers={"content-type": "application/octet-stream"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "kind": "config",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size": len(blob),
    }
    exists = client.get(f"/api/artifacts/exists?kind=config&sha256={body['sha256']}").json()
    assert exists == {"exists": True}


def test_compose_route_binds_like_stdlib(tmp_path: Path) -> None:
    # The FastAPI shell's `/api/compose` route (BE-0268) reaches the same shared `ops.bind_composition`
    # as the stdlib handler; the compose logic itself is covered once via the stdlib suite
    # (test_http_compose.py) — this only proves the ASGI route exists and delegates to it.
    import hashlib
    import io
    import zipfile

    def _zip(entries: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return buf.getvalue()

    state = srv.ServeState(
        runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path, uploads_dir=tmp_path / "uploads"
    )
    (tmp_path / "runs").mkdir()
    client = _client(state)
    config = b"defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
    scenarios = _zip({"scenarios/smoke.yaml": b"- name: a\n  steps: []\n"})
    config_sha = client.post(
        "/api/artifacts/config",
        content=config,
        headers={"content-type": "application/octet-stream"},
    ).json()["sha256"]
    scenarios_sha = client.post(
        "/api/artifacts/scenarios",
        content=scenarios,
        headers={"content-type": "application/octet-stream"},
    ).json()["sha256"]
    resp = client.post("/api/compose", json={"config": config_sha, "scenarios": scenarios_sha})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["targets"] == ["demo"]
    assert body["source"]["artifacts"] == {"config": config_sha, "scenarios": scenarios_sha}
    assert hashlib.sha256(config).hexdigest() == config_sha  # sha the client computes matches


def test_upload_route_requires_auth_when_token_set(tmp_path: Path) -> None:
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        root=tmp_path,
        auth=srv.SessionManager(token="s3cret"),
    )
    (tmp_path / "runs").mkdir()
    resp = _client(state).post("/api/upload?name=x.zip", content=b"x")
    assert resp.status_code == 401  # behind BE-0051 token auth like every other mutating endpoint


def test_upload_route_rejects_body_exceeding_cap_mid_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A body under the declared Content-Length passes the upfront check but must still be rejected
    # once the loop-internal cap (BoundedZipReceiver, shared with the stdlib handler) is crossed —
    # a distinct guard from the upfront Content-Length check exercised by the stdlib suite.
    from bajutsu.serve import uploads as uploads_mod

    monkeypatch.setattr(uploads_mod, "MAX_UPLOAD_BYTES", 4)
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path)
    (tmp_path / "runs").mkdir()
    resp = _client(state).post("/api/upload?name=x.zip", content=b"more than four bytes")
    assert resp.status_code == 413 and "too large" in resp.json()["error"]


def test_upload_route_rejects_truncated_body(tmp_path: Path) -> None:
    # A body shorter than its declared Content-Length (a connection that dropped mid-upload) is an
    # explicit 400, not a partial bundle handed downstream as "invalid".
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path)
    (tmp_path / "runs").mkdir()
    resp = _client(state).post(
        "/api/upload?name=x.zip", content=b"short", headers={"content-length": "1000"}
    )
    assert resp.status_code == 400 and "incomplete" in resp.json()["error"]


def test_upload_route_returns_400_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A disk-write failure (e.g. ENOSPC) while streaming must get the same graceful 400 the stdlib
    # handler's `_handle_upload` gives for an `OSError`, not an unhandled 500.
    from bajutsu.serve import uploads as uploads_mod

    def _boom(self: object, chunk: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(uploads_mod.BoundedZipReceiver, "write", _boom)
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, root=tmp_path)
    (tmp_path / "runs").mkdir()
    resp = _client(state).post("/api/upload?name=x.zip", content=b"zip bytes here")
    assert resp.status_code == 400 and "interrupted" in resp.json()["error"]


def test_upload_urls_route_signs_put_urls(tmp_path: Path) -> None:
    # The FastAPI shell reaches the same evidence operation as the stdlib handler (BE-0110).
    class _FakeStore:
        def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = 3600) -> str:
            return f"https://signed.example/{key}"

    from bajutsu.object_store import EvidenceTarget

    state = _state(tmp_path)
    state.evidence = EvidenceTarget(store=_FakeStore(), base_prefix="evidence/")
    resp = _client(state).post(
        "/api/runs/20260101-000000/upload-urls", json={"files": ["manifest.json"]}
    )
    assert resp.status_code == 200
    assert resp.json()["urls"]["manifest.json"] == (
        "https://signed.example/evidence/20260101-000000/manifest.json"
    )


def test_legacy_apps_grammar_is_rejected(tmp_path: Path) -> None:
    # Hard cutover (BE-0057): the old `/api/apps` route and the `{"app": ...}` wire key are gone, so
    # a stale client fails loudly (404 / 400) rather than silently hitting a compatibility alias.
    client = _client(_state(tmp_path))
    assert client.get("/api/apps").status_code == 404
    stale = client.post("/api/run", json={"app": "demo", "scenario": "smoke.yaml"})
    assert stale.status_code == 400


def test_theme_contract_and_upload_routes_match_stdlib(tmp_path: Path) -> None:
    # The FastAPI shell must expose the theme editor endpoints in lockstep with the stdlib handler
    # (BE-0191 unit 6): GET the contract, POST an edited theme into --themes.
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    _scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        config=cfg, runs_dir=runs, root=tmp_path, cwd=tmp_path, themes_dir=themes_dir
    )
    client = _client(state)
    contract = client.get("/api/themecontract").json()
    assert "--bg" in contract["colors"]
    resp = client.post(
        "/api/theme", json={"name": "App Theme", "kind": "light", "tokens": {"--bg": "#020"}}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "app-theme"
    assert (themes_dir / "app-theme.css").exists()


def test_theme_upload_requires_themes_dir(tmp_path: Path) -> None:
    # Without --themes there is nowhere to persist a theme; the shared op returns a 400 either backend.
    client = _client(_state(tmp_path))
    resp = client.post("/api/theme", json={"name": "x", "tokens": {"--bg": "#000"}})
    assert resp.status_code == 400
    assert "--themes" in resp.json()["error"]


class _FakeOAuth:
    """Stand-in for the GitHub OAuth client — no network. `fetch_identity` fails for code ``"bad"``."""

    def __init__(self, login: str | None = "alice") -> None:
        self._login = login

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        if code == "bad" or not self._login:
            return None
        return Identity(login=self._login, orgs=[])


def _oauth_state(
    tmp_path: Path, *, login: str | None = "alice", allowed: frozenset[str] = frozenset({"alice"})
) -> srv.ServeState:
    _scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        auth=srv.SessionManager(oauth=_FakeOAuth(login), oauth_allowed_users=allowed),
    )


def _csrf_from_redirect(resp: object) -> str:
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]  # type: ignore[attr-defined]


def test_oauth_login_redirects_and_sets_a_state_cookie(tmp_path: Path) -> None:
    resp = _client(_oauth_state(tmp_path)).get("/api/oauth/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "github.test" in resp.headers["location"]
    assert "bajutsu_oauth_state" in resp.headers.get("set-cookie", "")


def test_oauth_callback_logs_in_an_allowlisted_user(tmp_path: Path) -> None:
    client = _client(_oauth_state(tmp_path))
    started = client.get(
        "/api/oauth/login", follow_redirects=False
    )  # jar now holds the state cookie
    csrf = _csrf_from_redirect(started)
    resp = client.get(f"/api/oauth/callback?code=ok&state={csrf}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "bajutsu_session" in resp.headers.get("set-cookie", "")


def test_oauth_callback_rejects_a_state_mismatch(tmp_path: Path) -> None:
    client = _client(_oauth_state(tmp_path))
    client.get("/api/oauth/login", follow_redirects=False)  # sets a state cookie
    resp = client.get("/api/oauth/callback?code=ok&state=wrong", follow_redirects=False)
    assert resp.status_code == 403


def test_oauth_callback_rejects_a_user_not_on_the_allowlist(tmp_path: Path) -> None:
    client = _client(_oauth_state(tmp_path, login="mallory"))
    csrf = _csrf_from_redirect(client.get("/api/oauth/login", follow_redirects=False))
    resp = client.get(f"/api/oauth/callback?code=ok&state={csrf}", follow_redirects=False)
    assert resp.status_code == 403


def test_run_audits_the_logged_in_user(tmp_path: Path) -> None:
    # End-to-end through the transport: a logged-in OAuth session attributes the run to that user in
    # the audit log (BE-0015 7c-1). A fake popen stands in for the runner so no Simulator is needed.
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import AuditLog, Base

    _scn_dir, cfg, runs = project(tmp_path)
    # A file DB (not in-memory) so the callback's threadpool worker and the run handler share it.
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    state = srv.ServeState(
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        auth=srv.SessionManager(
            oauth=_FakeOAuth("alice"), oauth_allowed_users=frozenset({"alice"})
        ),
        repository=SqlRepository(engine),
        popen=fake_popen([]),
    )
    client = TestClient(make_app(state))
    csrf = _csrf_from_redirect(client.get("/api/oauth/login", follow_redirects=False))
    assert (
        client.get(f"/api/oauth/callback?code=ok&state={csrf}", follow_redirects=False).status_code
        == 302
    )
    resp = client.post("/api/run", json={"scenario": "smoke.yaml", "target": "demo"})
    assert resp.status_code == 200
    with Session(engine) as s:
        rows = list(s.scalars(select(AuditLog)))
    assert len(rows) == 1
    assert rows[0].action == "run"
    assert rows[0].actor_id == "alice"  # the run is attributed to the logged-in GitHub user


def _rbac_state(
    tmp_path: Path,
    *,
    login: str,
    admins: frozenset[str] = frozenset(),
    viewers: frozenset[str] = frozenset(),
) -> srv.ServeState:
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    _scn_dir, cfg, runs = project(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'rbac.db'}")
    Base.metadata.create_all(engine)
    return srv.ServeState(
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        auth=srv.SessionManager(
            token="t",  # a token makes the gate enforce auth, so the OAuth session's role applies
            oauth=_FakeOAuth(login),
            oauth_allowed_users=frozenset({login}),
            oauth_admins=admins,
            oauth_viewers=viewers,
        ),
        repository=SqlRepository(engine),
        popen=fake_popen([]),
    )


def _oauth_signin(client: TestClient) -> None:
    csrf = _csrf_from_redirect(client.get("/api/oauth/login", follow_redirects=False))
    assert (
        client.get(f"/api/oauth/callback?code=ok&state={csrf}", follow_redirects=False).status_code
        == 302
    )


def test_rbac_viewer_cannot_run(tmp_path: Path) -> None:
    client = TestClient(make_app(_rbac_state(tmp_path, login="v", viewers=frozenset({"v"}))))
    _oauth_signin(client)
    assert (
        client.post("/api/run", json={"scenario": "smoke.yaml", "target": "demo"}).status_code
        == 403
    )


def test_rbac_viewer_gets_masked_key_never_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The vulnerability BE-0136 closes: a read-only viewer could previously read back the
    # admin-configured key in plaintext via GET /api/apikey?reveal=1. Now a viewer's GET returns
    # only a masked preview, never a `value`, for any query.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-admin-secret-12345")
    client = TestClient(make_app(_rbac_state(tmp_path, login="v", viewers=frozenset({"v"}))))
    _oauth_signin(client)
    body = client.get("/api/apikey").json()
    assert body == {"set": True, "masked": "sk-a…2345"}
    assert "value" not in client.get("/api/apikey?reveal=1").json()


def test_rbac_editor_can_run_but_not_change_settings(tmp_path: Path) -> None:
    client = TestClient(make_app(_rbac_state(tmp_path, login="e")))  # default role = editor
    _oauth_signin(client)
    assert (
        client.post("/api/run", json={"scenario": "smoke.yaml", "target": "demo"}).status_code
        == 200
    )
    assert client.post("/api/apikey", json={"value": "k"}).status_code == 403  # admin-only


def test_rbac_admin_can_change_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # so monkeypatch restores it after set_api_key runs
    client = TestClient(make_app(_rbac_state(tmp_path, login="root", admins=frozenset({"root"}))))
    _oauth_signin(client)
    assert client.post("/api/apikey", json={"value": "sk-admin"}).status_code == 200


def test_security_headers_on_every_response(tmp_path: Path) -> None:
    resp = _client(_state(tmp_path)).get("/api/runs")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert (
        resp.headers["x-frame-options"] == "SAMEORIGIN"
    )  # same-origin so Replay can frame the report
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_post_scenario_save_writes_and_rejects(tmp_path: Path) -> None:
    scn_dir = tmp_path / "scenarios"
    client = _client(_state(tmp_path))
    target = scn_dir / "smoke.yaml"
    edited = "- name: edited\n  steps:\n    - tap: { id: y }\n"
    ok = client.post("/api/scenario", json={"target": "demo", "path": str(target), "yaml": edited})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert target.read_text(encoding="utf-8") == edited
    # A non-saveable path is reported (path error wins over the YAML parse).
    bad = client.post("/api/scenario", json={"target": "demo", "path": "note.txt", "yaml": "x: ["})
    assert bad.status_code == 400 and "path must be" in bad.json()["error"]


def test_post_validation_errors_delegate(tmp_path: Path) -> None:
    client = _client(_state(tmp_path))
    # start_run: scenario+app required
    assert client.post("/api/run", json={"target": "demo"}).status_code == 400
    # approve: runId/sid/baseline required
    assert client.post("/api/approve", json={}).status_code == 400
    # cancel of an unknown job
    assert client.post("/api/jobs/ghost/cancel", json={}).status_code == 404
    assert client.get("/api/jobs/ghost").status_code == 404


def test_more_delegations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider/apikey POSTs write os.environ directly (inherited by spawned jobs); clear the
    # vars first so monkeypatch restores the originals at teardown and no state leaks to later tests.
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_GIT_CONFIG_TOKEN",
        PROVIDER_ENV,
        BEDROCK_MODEL_ENV,
        "AWS_REGION",
    ):
        monkeypatch.delenv(var, raising=False)
    client = _client(_state(tmp_path))
    # GET delegations
    assert client.get("/api/fs").json()["cwd"] == str(tmp_path.resolve())
    assert client.get("/api/apikey").json()["set"] in (True, False)
    assert client.get("/api/gitcredential").json()["set"] in (True, False)  # BE-0224, hosted parity
    assert "provider" in client.get("/api/provider").json()
    assert isinstance(client.get("/api/simulators").json(), list)
    # POST delegations (validation paths — no device needed)
    assert client.post("/api/config", json={}).status_code == 400  # path required
    assert client.post("/api/record", json={"target": "demo"}).status_code == 400  # goal required
    assert client.post("/api/crawl", json={}).status_code == 400  # app required
    assert client.post("/api/provider", json={"provider": "anthropic"}).json()["ok"] is True
    set_key = client.post("/api/apikey", json={"value": "k key"})  # whitespace rejected
    assert set_key.status_code == 400
    assert client.post("/api/gitcredential", json={"value": "t t"}).status_code == 400  # whitespace


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


def test_format_sse_splits_lines_and_blocks_injection() -> None:
    # A LogBus line carries a trailing newline; it must become exactly one data: line ended by a
    # single blank line (no stray blank line that would split the event).
    assert ops.format_sse("log", "step 0 ok\n") == "event: log\ndata: step 0 ok\n\n"
    # An embedded newline must be split across data: lines — never emitted raw, which a client would
    # parse as a separate (injected) SSE field.
    frame = ops.format_sse("log", "foo\nevent: hijack\ndata: x")
    assert frame == "event: log\ndata: foo\ndata: event: hijack\ndata: data: x\n\n"
    # Empty data still yields one (empty) data: line.
    assert ops.format_sse("done", "") == "event: done\ndata: \n\n"


def test_csrf_blocks_cross_origin_post(tmp_path: Path) -> None:
    # With a token configured, a state-changing POST from a foreign Origin is blocked (BE-0051),
    # mirroring the stdlib handler's CSRF check.
    state = _state(tmp_path, token="s3cret")
    client = TestClient(make_app(state))
    headers = {"Authorization": "Bearer s3cret", "Origin": "http://evil.example"}
    resp = client.post("/api/apikey", json={"value": "x"}, headers=headers)
    assert resp.status_code == 403 and "cross-origin" in resp.json()["error"]


def test_csrf_blocks_cross_origin_post_without_token(tmp_path: Path) -> None:
    # BE-0121: the CSRF check is unconditional on the ASGI transport too — a cross-origin POST is
    # blocked on the no-token default, matching the stdlib handler (not only when a token is set).
    client = TestClient(make_app(_state(tmp_path)))
    blocked = client.post(
        "/api/config",
        json={"git": "github:evil/repo@main"},
        headers={"Origin": "http://evil.example"},
    )
    assert blocked.status_code == 403 and "cross-origin" in blocked.json()["error"]
    # A non-browser client (no Origin) still reaches the operation.
    assert client.post("/api/config", json={"path": "/nonexistent"}).status_code != 403


def test_host_allowlist_rejects_mismatch(tmp_path: Path) -> None:
    # BE-0121: the ASGI gate enforces the same Host allowlist as the stdlib handler, so a rebound
    # hostname can't reach an endpoint like /api/apikey. `make_asgi_server` sets `allowed_hosts` from
    # the bound interface; here we set a named bind's allowlist directly and drive both hosts.
    state = _state(tmp_path)
    state.allowed_hosts = frozenset({"myhost.example"})
    app = make_app(state)
    assert TestClient(app, base_url="http://attacker.example").get("/api/apikey").status_code == 403
    assert TestClient(app, base_url="http://myhost.example").get("/api/apikey").status_code == 200


def test_make_asgi_server_sets_host_allowlist(tmp_path: Path) -> None:
    # The wiring that make_server does for the stdlib transport (BE-0121) also runs for --asgi.
    state = _state(tmp_path)
    srv.make_asgi_server(state, host="myhost.example", port=0)
    assert "myhost.example" in state.allowed_hosts


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


def test_metrics_route_serves_prometheus_text(tmp_path: Path) -> None:
    # Parity with the stdlib handler: the FastAPI shell serves the same rendered metrics behind the
    # same auth gate (BE-0169). A token makes /metrics require a credential like every other route.
    state = _state(tmp_path, token="s3cret")
    state.register(srv.Job(org="default"))
    client = TestClient(make_app(state))
    assert client.get("/metrics").status_code == 401
    resp = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "bajutsu_in_flight_jobs" in resp.text


def test_done_event_and_poll_use_the_bus_final_status(tmp_path: Path) -> None:
    # On the server backend the control-plane Job stays "running" (the worker ran it elsewhere);
    # the terminal status comes from the bus. The done event and the poll must report that, not the
    # local Job's "running".
    state = _state(tmp_path)
    state.jobs["k"] = srv.Job(id="k", cmd=[])  # control-plane handle: never leaves "running"
    state.logbus.publish("k", "step ok\n")
    state.logbus.close("k", json.dumps({"id": "k", "status": "done", "ok": True, "runId": "R1"}))

    events = ops.job_log_events(state, "k")
    assert events is not None
    pairs = list(events)
    assert ("log", "step ok\n") in pairs
    kind, data = pairs[-1]
    assert kind == "done" and json.loads(data)["status"] == "done" and json.loads(data)["ok"]

    payload, code = ops.job_view(state, "k")
    assert code == 200 and payload["status"] == "done" and payload["runId"] == "R1"


def test_job_sse_emits_keepalive_then_log_and_done(tmp_path: Path) -> None:
    # job_sse maps an idle stream's heartbeats to SSE keepalive comments, real lines to `log`
    # frames, and the end to a `done` frame (B). Unknown job -> None.
    import threading
    import time

    state = _state(tmp_path)
    assert ops.job_sse(state, "nope", keepalive=1.0) is None
    state.jobs["s"] = srv.Job(id="s", cmd=[])
    frames = ops.job_sse(state, "s", keepalive=0.02)
    assert frames is not None
    out: list[str] = []

    def consume() -> None:
        out.extend(frames)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(0.08)  # idle -> keepalive(s)
    state.logbus.publish("s", "hi\n")
    state.logbus.close("s", '{"status": "done", "ok": true, "id": "s"}')
    t.join(timeout=2)
    assert not t.is_alive()
    assert ":keepalive\n\n" in out
    assert "event: log\ndata: hi\n\n" in out
    assert out[-1].startswith("event: done") and '"ok": true' in out[-1]


def test_doctor_endpoint_returns_checks(tmp_path: Path) -> None:
    """POST /api/doctor via FastAPI returns the same shape as the stdlib handler (BE-0024)."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(config=cfg, runs_dir=runs, root=tmp_path, cwd=tmp_path)
    client = _client(state)
    resp = client.post("/api/doctor", json={"target": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["target"] == "demo"
    assert body["backend"] == "fake"
    assert isinstance(body["checks"], list)

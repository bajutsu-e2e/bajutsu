"""The `/metrics` observability endpoint (BE-0169).

Renders Prometheus-format metrics from state the control plane already tracks: in-flight jobs (the
local `state.jobs`), and — when a database is wired (the server backend) — queue depth, leased jobs,
and worker heartbeat freshness from the jobs table. The endpoint respects serve's exposure rules
(BE-0051) and never leaks a secret. All server-backend cases run against in-memory SQLite — no Mac,
no Postgres.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from _shared import _get, _serve, project
from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.state import Job


def _repo() -> SqlRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def test_in_flight_jobs_counted_per_org(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    state.register(Job(org="acme"))
    state.register(Job(org="acme"))
    state.register(Job(org="beta"))
    finished = state.register(Job(org="acme"))
    finished.status = "done"  # a finished job is not in flight

    text, code = ops.render_metrics(state)

    assert code == 200
    assert "# TYPE bajutsu_in_flight_jobs gauge" in text
    assert 'bajutsu_in_flight_jobs{org="acme"} 2' in text
    assert 'bajutsu_in_flight_jobs{org="beta"} 1' in text


def test_max_concurrent_is_exposed(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs", max_concurrent=7)

    text, _ = ops.render_metrics(state)

    assert "bajutsu_max_concurrent 7" in text


def test_queue_and_lease_counts_from_repository(tmp_path: Path) -> None:
    repo = _repo()
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": []})  # oldest -> leased below
    repo.enqueue_job("j2", org_id="acme", spec={"cmd": []})
    repo.enqueue_job("j3", org_id="beta", spec={"cmd": []})
    leased = repo.lease_job("w1")
    assert leased is not None and leased.id == "j1"
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)

    text, _ = ops.render_metrics(state)

    assert 'bajutsu_queue_depth{org="acme"} 1' in text  # j2 still queued
    assert 'bajutsu_queue_depth{org="beta"} 1' in text  # j3 still queued
    assert 'bajutsu_leased_jobs{org="acme"} 1' in text  # j1 leased to w1
    assert 'bajutsu_worker_heartbeat_age_seconds{worker="w1"}' in text


def test_unroutable_jobs_render_through_the_endpoint(tmp_path: Path) -> None:
    # BE-0166: a queued job no live worker can serve is counted and rendered as bajutsu_unroutable_jobs.
    repo = _repo()
    repo.enqueue_job("routable", org_id="acme", spec={"cmd": []}, capabilities=["platform:ios"])
    repo.enqueue_job("stuck", org_id="acme", spec={"cmd": []}, capabilities=["platform:android"])
    repo.register_worker("w1", ["platform:ios"])  # only an iOS worker is live
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)

    text, _ = ops.render_metrics(state)

    assert "# TYPE bajutsu_unroutable_jobs gauge" in text
    assert "bajutsu_unroutable_jobs 1" in text  # the android job matches no live worker


def test_no_repository_omits_queue_metrics(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")

    text, _ = ops.render_metrics(state)

    # Local serve has no queue and no workers; those series are simply absent, not zero-filled noise.
    assert "bajutsu_queue_depth" not in text
    assert "bajutsu_worker_heartbeat_age_seconds" not in text


def test_metrics_never_leak_secrets(tmp_path: Path) -> None:
    repo = _repo()
    # A job spec can carry secrets (a token flag, an API key in the env) — the renderer must never
    # touch it. The operator token is likewise never emitted.
    repo.enqueue_job(
        "j1",
        org_id="acme",
        spec={
            "cmd": ["run", "--token", "SUPERSECRET"],
            "env": {"ANTHROPIC_API_KEY": "sk-TOPSECRET"},
        },
    )
    repo.lease_job("w1")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        repository=repo,
        auth=srv.SessionManager(token="operator-token-XYZ"),
    )

    text, _ = ops.render_metrics(state)

    assert "SUPERSECRET" not in text
    assert "sk-TOPSECRET" not in text
    assert "operator-token-XYZ" not in text


def test_label_values_are_escaped(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    state.register(Job(org='ac"me\\x'))

    text, _ = ops.render_metrics(state)

    # A double-quote and a backslash in a label value must be escaped so the line stays parseable.
    assert 'bajutsu_in_flight_jobs{org="ac\\"me\\\\x"} 1' in text


def test_metrics_endpoint_serves_prometheus_text(tmp_path: Path) -> None:
    _, cfg, runs = project(tmp_path)
    state = srv.ServeState(runs_dir=runs, config=cfg)
    state.register(Job(org="default"))
    server, port = _serve(state)
    try:
        status, body, ctype = _get(port, "/metrics")
        assert status == 200
        assert ctype.startswith("text/plain")
        assert "bajutsu_in_flight_jobs" in body.decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def _request(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_metrics_requires_auth_when_token_set(tmp_path: Path) -> None:
    _, cfg, runs = project(tmp_path)
    state = srv.ServeState(runs_dir=runs, config=cfg, auth=srv.SessionManager(token="s3cret"))
    server, port = _serve(state)
    try:
        unauth, _ = _request(port, "/metrics")
        assert unauth == 401  # /metrics is not a public surface (BE-0051)
        authed, body = _request(port, "/metrics", headers={"Authorization": "Bearer s3cret"})
        assert authed == 200
        assert "bajutsu_in_flight_jobs" in body.decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

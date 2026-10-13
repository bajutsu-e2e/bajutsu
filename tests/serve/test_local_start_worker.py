"""A local start queues `run` for a worker (BE-0449 unit 5, part 2).

The local backend is now the server shape: a run goes through the queue to a `bajutsu worker`, and
every other job kind still runs in the server's own process. These tests lock the move end to end
on the fake backend, the stored secrets the lease relays to the worker (and nowhere else), the
server's own private-repository fetch reading a credential kept in the database, and the notice a
queued job carries while no worker can take it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from _shared import FakeProc
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.common.config_source import GitConfigSpec, resolve_github_credential
from bajutsu.serve import jobs
from bajutsu.serve import operations as ops
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.operations._common import NO_WORKER_NOTICE, no_worker_notice
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.worker_job import _spec_cwd, execute_job_spec, job_spec
from bajutsu.serve.state import Job

# Every variable that would steer the start away from its local defaults, plus the credentials a
# test sets through the API and must not find already in the environment.
_ENV = (
    "BAJUTSU_SERVER_STORE",
    "BAJUTSU_DATABASE_URL",
    "BAJUTSU_SECRETS_KEY",
    "BAJUTSU_HOSTED",
    "BAJUTSU_REDIS_URL",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_ID",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET",
    "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI",
    "BAJUTSU_OIDC_AUDIENCE",
    "ANTHROPIC_API_KEY",
    "BAJUTSU_GIT_CONFIG_TOKEN",
    "LOGIN_PASSWORD",
    "BAJUTSU_EVIDENCE_STORE",
    "GITHUB_ACTIONS",
)

# The fake app never renders a screen, so its readiness wait would run to its 10-second timeout.
# This shortens that wait, and nothing else, in the run the worker spawns (as `test_run_wrapper`).
_SITECUSTOMIZE = """\
from bajutsu.common.platform_lifecycle import readiness
from bajutsu.common.runner import launch

_real = readiness.await_ready


def _quick(driver, timeout=10.0, *args, **kwargs):
    return _real(driver, 0.05, *args, **kwargs)


launch.await_ready = _quick
"""

_API_KEY = "sk-ant-relay-0001"  # a fake credential shape, never a real key
_PASSWORD = "pw-relay-0002"


@pytest.fixture(autouse=True)
def _no_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _fake_project(tmp_path: Path, identifier: str = "bundleId: com.example.demo") -> Path:
    """A fake-backend project whose `demo` target declares a scenario secret; returns its config.

    *identifier* picks the target's platform: the default is iOS, and `package: ...` makes it an
    Android target that a worker on any host may serve (BE-0450).
    """
    scn = tmp_path / "scenarios"
    scn.mkdir()
    (scn / "pass.yaml").write_text("- name: ok\n  steps: []\n", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ {identifier}, scenarios: {scn}, secrets: [LOGIN_PASSWORD] }}\n",
        encoding="utf-8",
    )
    (tmp_path / "runs").mkdir()
    return cfg


def _local_start(tmp_path: Path, cfg: Path, *, token: str | None = None) -> srv.ServeState:
    return srv._build_state(
        runs_dir=tmp_path / "runs",
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=tmp_path / "baselines",
        max_concurrent=4,
        token=token,
        cwd=cfg.parent,
    )


def _no_spawn_here(_cmd: list[str], **_kw: Any) -> FakeProc:
    raise AssertionError("the server must not spawn a run itself")


class _StopLoop(Exception):
    """Ends the worker's poll loop once the queue is drained."""


def _worker_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, token: str | None = None
) -> None:
    """Run the real `bajutsu worker` loop against *client* until a lease comes back empty."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def post(url: str, body: dict[str, Any], **_kw: Any) -> tuple[int, Any]:
        path = url.removeprefix("http://cp")
        resp = client.post(path, json=body, headers=headers)
        if path == "/api/worker/lease" and resp.status_code == 204:
            raise _StopLoop
        return resp.status_code, (resp.json() if resp.content else {})

    monkeypatch.setattr(worker_mod, "_post_json", post)
    with pytest.raises(_StopLoop):
        worker_mod.worker(
            server_url="http://cp",
            poll_interval=0.01,
            heartbeat_interval=0.5,
            worker_id="w1",
            platform="ios",
            capabilities="",
            token="",
            worker_config=None,
            once=None,
        )


# --- end to end: a run on a local start goes through a worker -----------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned run")
def test_a_local_start_queues_a_run_and_a_worker_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _fake_project(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(p for p in (str(site), os.environ.get("PYTHONPATH")) if p)
    )
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])
    # The worker's own spawn records the environment its run gets, then runs it for real.
    seen: dict[str, Any] = {}
    real_execute = execute_job_spec

    def execute(spec: dict[str, Any], **kwargs: Any) -> Job:
        seen["secret_env"] = kwargs.get("secret_env")
        return real_execute(spec, **kwargs)

    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    state = _local_start(tmp_path, cfg)
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    assert client.post("/api/apikey", json={"value": _API_KEY}).status_code == 200
    assert client.post("/api/secrets", json={"name": "LOGIN_PASSWORD", "value": _PASSWORD}).json()[
        "ok"
    ]
    started = client.post(
        "/api/run", json={"target": "demo", "scenario": "pass.yaml", "backend": "fake"}
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["jobId"]
    assert state.repository is not None
    queued = state.repository.get_job(job_id)
    assert queued is not None and queued["status"] == "queued"
    # No worker has polled yet, so the job says why it is waiting.
    assert client.get(f"/api/jobs/{job_id}").json()["notice"] == NO_WORKER_NOTICE

    _worker_through(client, monkeypatch)

    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done"
    run_id = finished["result"]["runId"]
    # The worker on this machine wrote the run into the server's own runs directory.
    manifest = json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text("utf-8"))
    assert manifest["ok"] is True
    assert (tmp_path / "runs" / run_id / "console.log").is_file()
    assert state.repository.get_run(run_id) is not None
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["ok"] is True and "notice" not in view
    # The stored secrets reached the run's environment through the lease alone.
    assert seen["secret_env"] == {"ANTHROPIC_API_KEY": _API_KEY, "LOGIN_PASSWORD": _PASSWORD}
    assert _PASSWORD not in json.dumps(finished)


# --- the lease relays the stored secrets, and nothing else does ---------------------------------


def _queued_run(tmp_path: Path, *, token: str | None = None) -> tuple[srv.ServeState, TestClient]:
    state = _local_start(tmp_path, _fake_project(tmp_path), token=token)
    client = TestClient(make_app(state))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client.post("/api/apikey", json={"value": _API_KEY}, headers=headers)
    client.post(
        "/api/secrets", json={"name": "LOGIN_PASSWORD", "value": _PASSWORD}, headers=headers
    )
    started = client.post(
        "/api/run",
        json={"target": "demo", "scenario": "pass.yaml", "backend": "fake"},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    return state, client


def test_the_lease_relays_the_orgs_secrets_and_nothing_stores_them(tmp_path: Path) -> None:
    state, client = _queued_run(tmp_path, token="tok")
    lease = client.post(
        "/api/worker/lease",
        json={"worker_id": "w1", "capabilities": ["platform:ios"]},
        headers={"Authorization": "Bearer tok"},
    )
    assert lease.status_code == 200
    body = lease.json()
    assert body["secret_env"] == {"ANTHROPIC_API_KEY": _API_KEY, "LOGIN_PASSWORD": _PASSWORD}
    assert _PASSWORD not in json.dumps(body["spec"])
    # Nothing at rest holds the plaintext: not the job row, not the database file.
    assert state.repository is not None
    assert _PASSWORD not in json.dumps(state.repository.get_job(body["job_id"]))
    with sqlite3.connect(tmp_path / ".bajutsu" / "queue.db") as db:
        dump = "\n".join(db.iterdump())
    assert _PASSWORD not in dump and _API_KEY not in dump
    # No other response a reader sees carries a value.
    auth = {"Authorization": "Bearer tok"}
    for path in (f"/api/jobs/{body['job_id']}", "/api/apikey", "/api/secrets", "/api/config"):
        text = client.get(path, headers=auth).text
        assert _PASSWORD not in text and _API_KEY not in text, path
    heartbeat = client.post(
        "/api/worker/heartbeat", json={"worker_id": "w1", "job_id": body["job_id"]}, headers=auth
    )
    assert "secret_env" not in heartbeat.json()


def test_a_session_caller_leases_without_the_secrets(tmp_path: Path) -> None:
    # A browser (or a machine) session may reach the worker route; only the shared token, the
    # credential a worker holds, gets the values.
    state, client = _queued_run(tmp_path, token="tok")
    sid = state.auth.issue_session("alice")
    client.cookies.set("bajutsu_session", sid)
    lease = client.post(
        "/api/worker/lease", json={"worker_id": "w1", "capabilities": ["platform:ios"]}
    )
    assert lease.status_code == 200
    assert "secret_env" not in lease.json()


def test_the_lease_relays_only_the_leased_jobs_org(tmp_path: Path) -> None:
    state = _local_start(tmp_path, _fake_project(tmp_path))
    assert state.repository is not None
    state.repository.ensure_org("acme", slug="acme", name="acme")
    state.for_org("acme").secrets.set("LOGIN_PASSWORD", "acme-only")
    state.for_org("default").secrets.set("LOGIN_PASSWORD", "default-only")
    state.repository.enqueue_job("j-acme", org_id="acme", spec={"cmd": []}, capabilities=[])
    leased, code = ops.worker_lease(state, "w1", [])
    assert code == 200
    assert leased["secret_env"] == {"LOGIN_PASSWORD": "acme-only"}


def test_a_lease_without_relay_carries_no_secrets(tmp_path: Path) -> None:
    state, _client = _queued_run(tmp_path)
    leased, code = ops.worker_lease(state, "w1", ["platform:ios"], relay_secrets=False)
    assert code == 200
    assert "secret_env" not in leased


def test_a_store_failure_at_lease_fails_the_job_instead_of_running_it_bare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, _client = _queued_run(tmp_path)

    def broken(_state: srv.ServeState, _org: str) -> dict[str, str]:
        raise RuntimeError("database is down")

    import bajutsu.serve.operations.worker as worker_ops

    monkeypatch.setattr(worker_ops, "lease_secret_env", broken)
    leased, code = ops.worker_lease(state, "w1", ["platform:ios"])
    assert (leased, code) == ({}, 204)
    assert state.repository is not None
    row = state.repository.get_job(next(iter(state.jobs)))
    assert row is not None and row["status"] == "failed"
    assert "stored secrets" in row["result"]["error"]
    assert "database is down" not in json.dumps(row)


def test_the_worker_keeps_only_string_secrets_from_a_lease() -> None:
    lease = {"secret_env": {"A": "1", "B": 2, "": "x", "C": None}}
    assert worker_mod._lease_secret_env(lease) == {"A": "1"}
    assert worker_mod._lease_secret_env({"secret_env": ["A"]}) == {}
    assert worker_mod._lease_secret_env({}) == {}


def test_the_worker_lays_the_relayed_secrets_over_its_childs_environment(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def popen(cmd: list[str], **kw: Any) -> FakeProc:
        seen["env"] = kw["env"]
        seen["cmd"] = cmd
        return FakeProc([])

    job = execute_job_spec(
        {"job_id": "1", "cmd": ["bajutsu", "run"]},
        popen=popen,
        cwd=tmp_path,
        secret_env={"LOGIN_PASSWORD": "pw"},
    )
    assert seen["env"]["LOGIN_PASSWORD"] == "pw"
    assert "pw" not in seen["cmd"]  # the environment, never the argv
    assert "pw" not in repr(job)


def test_only_the_job_execution_path_reads_a_secrets_plaintext() -> None:
    # The lease and the server's Git fetch are the two new readers (BE-0449). Each reaches the
    # plaintext through its own named function in `serve.jobs`, called from exactly one place.
    from test_lossless_local_start import _calls_of

    assert _calls_of("values_for_job") == {"bajutsu/serve/jobs.py"}
    assert _calls_of("lease_secret_env") == {"bajutsu/serve/operations/worker.py"}
    assert _calls_of("stored_git_credential") == {"bajutsu/serve/operations/config.py"}


# --- a local-path job runs where its config lives ------------------------------------------------


def test_a_job_that_ships_no_files_names_its_config_directory(tmp_path: Path) -> None:
    job = Job(cmd=["x"], cwd=tmp_path)
    assert job_spec(job)["cwd"] == str(tmp_path)
    assert job_spec(Job(cmd=["x"], cwd=tmp_path, materials={"a": "b"}))["cwd"] is None
    assert job_spec(Job(cmd=["x"]))["cwd"] is None


def test_the_worker_honours_a_spec_cwd_only_for_a_job_that_ships_no_files(tmp_path: Path) -> None:
    assert _spec_cwd({"cwd": str(tmp_path)}) == tmp_path
    assert _spec_cwd({"cwd": str(tmp_path), "materials": {"a": "b"}}) is None
    assert _spec_cwd({"cwd": str(tmp_path), "bundle": {"id": "x"}}) is None
    with pytest.raises(ValueError, match="needs a worker on the server's machine"):
        _spec_cwd({"cwd": str(tmp_path / "missing")})
    assert _spec_cwd({"cwd": str(tmp_path / "missing"), "materials": {"a": "b"}}) is None
    assert _spec_cwd({"cwd": "relative"}) is None
    assert _spec_cwd({"cwd": 3}) is None
    assert _spec_cwd({}) is None


def test_a_leased_local_path_job_builds_and_runs_from_its_config_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    seen: dict[str, Any] = {}

    def popen(cmd: list[str], **kw: Any) -> FakeProc:
        seen["cwd"] = kw["cwd"]
        return FakeProc([])

    execute_job_spec(
        {"job_id": "1", "cmd": ["bajutsu", "run"], "cwd": str(project_dir)},
        popen=popen,
        cwd=tmp_path / "work",
    )
    assert seen["cwd"] == str(project_dir)


# --- the server's own Git fetch reads a credential kept in the database --------------------------


def test_a_stored_git_credential_reaches_the_servers_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path, _fake_project(tmp_path))
    client = TestClient(make_app(state))
    assert client.post("/api/gitcredential", json={"value": "ghp_stored0003"}).json()["set"]
    # The database store keeps it out of the process environment...
    assert "BAJUTSU_GIT_CONFIG_TOKEN" not in os.environ
    assert jobs.stored_git_credential(state, "default") == "ghp_stored0003"
    # ...and the bind hands it to the fetch.
    seen: dict[str, Any] = {}

    def materialize(_spec: GitConfigSpec, **kw: Any) -> Any:
        seen.update(kw)
        raise ValueError("stop here")

    import bajutsu.serve.operations.config as config_ops

    monkeypatch.setattr(config_ops, "materialize", materialize)
    resp = client.post("/api/config", json={"git": "github:acme/private@main"})
    assert resp.status_code == 400
    assert seen["credential"] == "ghp_stored0003"
    assert "ghp_stored0003" not in resp.text


def test_a_failing_store_leaves_the_fetch_on_its_other_credentials(tmp_path: Path) -> None:
    class _Broken:
        def values_for_job(self) -> dict[str, str]:
            raise RuntimeError("database is down")

    state = srv.ServeState(runs_dir=tmp_path / "runs")
    state.secrets = _Broken()  # type: ignore[assignment]
    assert jobs.stored_git_credential(state, "default") is None


def test_the_stored_credential_sits_between_the_app_and_the_ambient_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = GitConfigSpec(host="github.com", owner="acme", repo="app", ref="main", path=None)
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_ID", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient")
    assert resolve_github_credential(spec, "stored") == "stored"
    assert resolve_github_credential(spec) == "ambient"


# --- the no-worker notice --------------------------------------------------------------------------


def test_the_notice_appears_with_no_live_worker_and_clears_when_one_polls(tmp_path: Path) -> None:
    state, client = _queued_run(tmp_path)
    job_id = next(iter(state.jobs))
    assert client.get(f"/api/jobs/{job_id}").json()["notice"] == NO_WORKER_NOTICE
    # A live worker that cannot serve the job does not clear it: the job still cannot run.
    assert state.repository is not None
    state.repository.register_worker("web-only", ["platform:web"])
    assert client.get(f"/api/jobs/{job_id}").json()["notice"] == NO_WORKER_NOTICE
    # One that can serve it does, before it even leases.
    state.repository.register_worker("ios", ["platform:ios"])
    assert "notice" not in client.get(f"/api/jobs/{job_id}").json()


def test_the_notice_clears_once_the_job_is_leased(tmp_path: Path) -> None:
    state, client = _queued_run(tmp_path)
    job_id = next(iter(state.jobs))
    assert state.repository is not None
    assert state.repository.lease_job("w1", ["platform:ios"]) is not None
    assert not state.repository.lacks_live_worker(job_id)
    assert "notice" not in client.get(f"/api/jobs/{job_id}").json()


def test_a_stale_worker_does_not_count_as_live(tmp_path: Path) -> None:
    from datetime import timedelta

    from bajutsu.serve.server.db import SqlRepository, engine_from_url
    from bajutsu.serve.server.models import Base

    engine = engine_from_url(f"sqlite:///{tmp_path / 'q.db'}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine, lease_timeout=timedelta(seconds=-1))
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []}, capabilities=[])
    repo.register_worker("w1", [])  # seen now, but outside a window that ended before now
    assert repo.lacks_live_worker("j1")
    assert not repo.lacks_live_worker("missing")


def test_an_in_process_job_never_carries_the_notice(tmp_path: Path) -> None:
    state = _local_start(tmp_path, _fake_project(tmp_path))
    job = Job(id="r1", cmd=[sys.executable, "-m", "bajutsu", "record"])
    assert no_worker_notice(state, job) is None


def test_the_event_stream_sends_the_notice_and_its_clearing(tmp_path: Path) -> None:
    state, _client = _queued_run(tmp_path)
    job_id = next(iter(state.jobs))
    frames = ops.job_sse(state, job_id, keepalive=0.05)
    assert frames is not None
    first = next(frames)
    assert first.startswith("event: notice\n")
    assert json.loads(first.split("data: ", 1)[1]) == {"notice": NO_WORKER_NOTICE}
    assert state.repository is not None
    state.repository.register_worker("ios", ["platform:ios"])
    cleared = next(f for f in frames if f.startswith("event: notice"))
    assert json.loads(cleared.split("data: ", 1)[1]) == {"notice": None}
    frames.close()


def test_a_log_line_clears_the_notice_in_the_event_stream(tmp_path: Path) -> None:
    # A line reaches the stream only from a worker running the job, so it ends the wait too.
    state, _client = _queued_run(tmp_path)
    job_id = next(iter(state.jobs))
    frames = ops.job_sse(state, job_id, keepalive=5.0)
    assert frames is not None
    assert next(frames).startswith("event: notice\n")
    state.logbus.publish(job_id, "step ok")
    cleared = next(frames)
    assert cleared.startswith("event: notice\n")
    assert json.loads(cleared.split("data: ", 1)[1]) == {"notice": None}
    assert next(frames) == "event: log\ndata: step ok\n\n"
    frames.close()


# --- a stopped worker cancels its job instead of orphaning it -------------------------------------


def _processes_naming(text: str) -> list[str]:
    out = subprocess.run(["ps", "-ax", "-o", "command"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if text in line and "ps -ax" not in line]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and sessions")
def test_sigterm_to_a_worker_cancels_its_leased_run_and_ends_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import threading
    import time

    # An Android fake target: a real worker refuses `--platform ios` off macOS (BE-0450), and this
    # test spawns one on whatever host runs the suite.
    cfg = _fake_project(tmp_path, "package: com.example.demo")
    slow = tmp_path / "scenarios" / "slow-signal.yaml"
    slow.write_text(
        "- name: slow\n  steps:\n"
        "    - sleep: { seconds: 30, reason: hold the run open for the signal under test }\n",
        encoding="utf-8",
    )
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    state = srv._build_state(
        runs_dir=tmp_path / "runs",
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=tmp_path / "baselines",
        max_concurrent=4,
        token=None,
        cwd=cfg.parent,
        port=port,
    )
    server = srv.make_asgi_server(state, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    worker = None
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        started = httpx.post(
            f"http://127.0.0.1:{port}/api/run",
            json={"target": "demo", "scenario": slow.name, "backend": "fake"},
            timeout=10,
        )
        assert started.status_code == 200, started.text
        job_id = started.json()["jobId"]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(site), env.get("PYTHONPATH")) if p)
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bajutsu",
                "worker",
                "--server-url",
                f"http://127.0.0.1:{port}",
                "--platform",
                "android",
                "--poll-interval",
                "0.2",
                "--heartbeat-interval",
                "1",
                "--workdir",
                str(tmp_path / "work"),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert state.repository is not None
        deadline = time.time() + 60
        while time.time() < deadline and not _processes_naming(str(slow)):
            time.sleep(0.2)
        assert _processes_naming(str(slow)), "the worker never started the run"
        worker.send_signal(signal.SIGTERM)
        assert worker.wait(timeout=60) == 128 + signal.SIGTERM
        row = state.repository.get_job(job_id)
        assert row is not None and row["status"] == "cancelled"
        deadline = time.time() + 10
        while time.time() < deadline and _processes_naming(str(slow)):
            time.sleep(0.2)
        assert not _processes_naming(str(slow)), "the run outlived its worker"
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
        server.should_exit = True
        thread.join(timeout=5)


# --- review follow-ups -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONSTARTUP",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "BASH_ENV",
        "ENV",
        "BAJUTSU_SERVER_URL",
        "PATH",
    ],
)
def test_a_secret_cannot_set_a_variable_that_changes_how_a_job_runs(name: str) -> None:
    from bajutsu.serve.helpers import unsafe_env_name
    from bajutsu.serve.operations.config import _valid_key_env_name

    assert unsafe_env_name(name)
    assert not _valid_key_env_name(name)
    assert worker_mod._lease_secret_env({"secret_env": {name: "x", "LOGIN_PASSWORD": "pw"}}) == {
        "LOGIN_PASSWORD": "pw"
    }


def test_ordinary_secret_names_stay_allowed() -> None:
    from bajutsu.serve.helpers import unsafe_env_name

    for name in ("ANTHROPIC_API_KEY", "LOGIN_PASSWORD", "CLAUDE_CODE_OAUTH_TOKEN", "MY_LD_KEY"):
        assert not unsafe_env_name(name)


def test_a_stored_row_under_a_denied_name_never_reaches_a_job(tmp_path: Path) -> None:
    # A row written before its name joined the deny list is dropped at read time, so neither the
    # in-process job nor the lease hands it on.
    state = _local_start(tmp_path, _fake_project(tmp_path))
    state.for_org("default").secrets.set("LD_PRELOAD", "/tmp/evil.so")
    state.for_org("default").secrets.set("LOGIN_PASSWORD", "pw")
    assert jobs.lease_secret_env(state, "default") == {"LOGIN_PASSWORD": "pw"}
    assert jobs.job_secret_env(state, "default") == {"LOGIN_PASSWORD": "pw"}


def test_port_zero_on_a_local_start_exits_cleanly(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from bajutsu.cli import app

    cfg = _fake_project(tmp_path)
    result = CliRunner().invoke(
        app, ["serve", "--port", "0", "--config", str(cfg), "--runs", str(tmp_path / "runs")]
    )
    assert result.exit_code == 2
    assert "fixed --port (not 0)" in result.output
    assert "--backend server" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_the_startup_note_names_the_platforms_the_config_needs(tmp_path: Path) -> None:
    cfg = _fake_project(tmp_path)
    assert srv.worker_platforms(cfg, "darwin") == "ios"
    assert srv.worker_platforms(None, "darwin") == "android,ios,web"
    assert srv.worker_platforms(None, "linux") == "android,web"


def test_the_startup_note_leaves_out_a_platform_the_host_cannot_drive(tmp_path: Path) -> None:
    # A local worker refuses `--platform ios` on Linux (BE-0450), so `make serve` never starts one
    # that way: the iOS runs wait with the no-worker notice, and the worker serves what it can.
    cfg = _fake_project(tmp_path)
    assert srv.worker_platforms(cfg, "linux") == "android,web"


def test_a_worker_without_the_servers_files_fails_the_job_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "_post_json", lambda *_a, **_k: (200, {}))
    result, abandoned, _work = worker_mod._run_with_heartbeat(
        {"job_id": "1", "cmd": ["bajutsu", "run"], "cwd": str(tmp_path / "not-on-this-machine")},
        job_id="1",
        work=tmp_path,
        bundle_urls=None,
        bus=srv.InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=5.0,
    )
    assert not abandoned
    assert result is not None and result["ok"] is False
    assert "needs a worker on the server's machine" in result["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned run")
def test_an_uploaded_bundle_runs_on_a_same_machine_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import zipfile

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(p for p in (str(site), os.environ.get("PYTHONPATH")) if p)
    )
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "bajutsu.config.yaml",
            "defaults: { backend: [fake] }\n"
            "targets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n",
        )
        zf.writestr("scenarios/pass.yaml", "- name: ok\n  steps: []\n")
    state = _local_start(tmp_path, _fake_project(tmp_path))
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    bound = client.post(
        "/api/upload", content=buf.getvalue(), headers={"Content-Type": "application/zip"}
    )
    assert bound.status_code == 200, bound.text
    started = client.post(
        "/api/run", json={"target": "demo", "scenario": "pass.yaml", "backend": "fake"}
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["jobId"]

    # The worker fetches the bundle over the server's signed URL; the test client stands in for the
    # loopback socket that URL names.
    def get_file(url: str, dest: Path, *, timeout: float | None = None) -> None:
        resp = client.get(urlsplit(url).path + "?" + urlsplit(url).query)
        assert resp.status_code == 200, resp.text
        dest.write_bytes(resp.content)

    monkeypatch.setattr(worker_mod, "_get_file", get_file)
    _worker_through(client, monkeypatch)

    assert state.repository is not None
    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done", finished
    run_id = finished["result"]["runId"]
    assert (tmp_path / "runs" / run_id / "manifest.json").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_signal_while_idle_ends_the_poll_loop_with_the_signals_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import typer

    def post(url: str, body: dict[str, Any], **_kw: Any) -> tuple[int, Any]:
        os.kill(os.getpid(), signal.SIGTERM)  # what `serve.sh` sends when the server stops
        return 204, {}

    monkeypatch.setattr(worker_mod, "_post_json", post)
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])
    workdir = tmp_path / "work"
    with pytest.raises(typer.Exit) as stopped:
        worker_mod.worker(
            server_url="http://cp",
            poll_interval=0.01,
            heartbeat_interval=0.5,
            worker_id="w1",
            platform="ios",
            capabilities="",
            token="",
            worker_config=None,
            workdir=workdir,
            once=None,
        )
    assert stopped.value.exit_code == 128 + signal.SIGTERM
    assert workdir.is_dir()  # a workspace the caller named is the caller's to keep


def test_the_default_workspace_is_temporary_and_a_named_one_is_kept(tmp_path: Path) -> None:
    work, temporary = worker_mod._workspace(None)
    try:
        assert temporary and work.is_dir() and work != Path.cwd()
    finally:
        shutil.rmtree(work)
    named, temporary = worker_mod._workspace(tmp_path / "w")
    assert (named, temporary) == ((tmp_path / "w").resolve(), False)


class _Relay:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self) -> None:
        self.calls.append("request")

    def kill(self) -> None:
        self.calls.append("kill")

    def pgid(self) -> int | None:
        return 42


def test_the_current_job_relays_a_stop_to_the_job_leased_now_or_next() -> None:
    current = worker_mod._CurrentJob()
    assert current.pgid() is None
    current.kill()  # nothing leased: nothing to end
    first = _Relay()
    current.attach(first)  # type: ignore[arg-type]
    assert current.pgid() == 42
    current.request()
    current.kill()
    assert first.calls == ["request", "kill"]
    current.detach()
    later = _Relay()
    current.attach(later)  # type: ignore[arg-type]
    assert later.calls == ["request"]  # leased after the stop: cancelled at once


def test_the_deny_list_compares_names_as_the_platform_does(monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.serve import helpers

    # POSIX names are case-sensitive, and no loader reads `ld_preload`; Windows folds the case.
    monkeypatch.setattr(os, "name", "posix")
    assert not helpers.unsafe_env_name("ld_preload")
    monkeypatch.setattr(os, "name", "nt")
    assert helpers.unsafe_env_name("ld_preload")


def test_a_dropped_name_is_logged_by_name_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve import helpers

    monkeypatch.setattr(helpers, "_warned_env_names", set())  # each name warns once per process
    state = _local_start(tmp_path, _fake_project(tmp_path))
    state.for_org("default").secrets.set("DYLD_INSERT_LIBRARIES", "/tmp/never-logged.dylib")
    with caplog.at_level("WARNING"):
        assert jobs.job_secret_env(state, "default") == {}
        worker_mod._lease_secret_env({"secret_env": {"PYTHONSTARTUP": "never-logged-either"}})
    assert "DYLD_INSERT_LIBRARIES" in caplog.text and "PYTHONSTARTUP" in caplog.text
    assert "never-logged" not in caplog.text


def test_an_unsafe_ai_key_env_warns_that_the_server_reads_another(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve import helpers
    from bajutsu.serve.operations.config import active_key_env

    monkeypatch.setattr(helpers, "_warned_env_names", set())

    cfg = _fake_project(tmp_path)
    cfg.write_text(
        "defaults: { backend: [fake], ai: { keyEnv: BAJUTSU_AI_KEY_X } }\n"
        "targets:\n  demo: { bundleId: com.example.demo, secrets: [LD_AUDIT_X] }\n",
        encoding="utf-8",
    )
    state = _local_start(tmp_path, cfg)
    with caplog.at_level("WARNING"):
        assert active_key_env(state) == "ANTHROPIC_API_KEY"
        assert ops.declared_secret_names(state) == []
    assert "BAJUTSU_AI_KEY_X" in caplog.text and "LD_AUDIT_X" in caplog.text


def test_only_a_startup_check_becomes_a_clean_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _fake_project(tmp_path)
    with pytest.raises(srv.ServeStartupError, match="fixed --port"):
        srv._build_state(
            runs_dir=tmp_path / "runs",
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            port=0,
        )

    def broken(**_kw: Any) -> srv.ServeState:
        raise ValueError("a bug, not a setting")

    monkeypatch.setattr(srv, "_build_server_state", broken)
    with pytest.raises(ValueError, match="a bug") as raised:
        srv._build_state(
            runs_dir=tmp_path / "runs",
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
        )
    assert not isinstance(raised.value, srv.ServeStartupError)


def test_a_worker_sweeps_workspaces_whose_owner_is_gone(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    dead_pid = int(done.stdout)
    stale = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}stale"
    live = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}live"
    unowned = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}unowned"
    other = tmp_path / "something-else"
    for path, pid in ((stale, dead_pid), (live, os.getpid()), (unowned, None), (other, dead_pid)):
        path.mkdir()
        if pid is not None:
            (path / worker_mod.OWNER_PID_FILE).write_text(f"{pid}\n", encoding="utf-8")
    worker_mod._sweep_stale_workspaces(tmp_path)
    assert not stale.exists()
    assert live.is_dir() and unowned.is_dir() and other.is_dir()


def test_the_default_workspace_names_its_owner(tmp_path: Path) -> None:
    work, temporary = worker_mod._workspace(None)
    try:
        assert temporary
        assert (work / worker_mod.OWNER_PID_FILE).read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
    finally:
        shutil.rmtree(work)


def test_the_current_job_records_its_process_group_for_a_supervisor(tmp_path: Path) -> None:
    import time

    pgid_file = tmp_path / worker_mod.JOB_PGID_FILE
    current = worker_mod._CurrentJob(pgid_file=pgid_file)
    current.attach(_Relay())  # type: ignore[arg-type]
    deadline = time.time() + 5
    while time.time() < deadline and not pgid_file.exists():
        time.sleep(0.05)
    assert pgid_file.read_text(encoding="utf-8").strip() == "42"
    current.detach()
    assert not pgid_file.exists()


def _dead_pid() -> int:
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(done.stdout)


def test_a_workspace_stays_while_any_recorded_owner_lives(tmp_path: Path) -> None:
    # `serve.sh` records itself and then its worker: a SIGKILLed script alone frees nothing.
    dead = _dead_pid()
    shared = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}shared"
    gone = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}gone"
    for path, pids in ((shared, [dead, os.getpid()]), (gone, [dead, dead])):
        path.mkdir()
        (path / worker_mod.OWNER_PID_FILE).write_text(
            "".join(f"{pid}\n" for pid in pids), encoding="utf-8"
        )
    worker_mod._sweep_stale_workspaces(tmp_path)
    assert shared.is_dir()
    assert not gone.exists()


def test_windows_skips_the_sweep_and_never_probes_a_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}stale"
    stale.mkdir()
    (stale / worker_mod.OWNER_PID_FILE).write_text(f"{_dead_pid()}\n", encoding="utf-8")

    def no_kill(*_a: Any) -> None:
        raise AssertionError("os.kill(pid, 0) sends a console event on Windows")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "kill", no_kill)
    worker_mod._sweep_stale_workspaces(tmp_path)
    assert stale.is_dir()


class _ExitedRelay(_Relay):
    """A relay whose child ran in a group that has since emptied."""

    def __init__(self, pgid: int) -> None:
        super().__init__()
        self.group: int | None = pgid

    def pgid(self) -> int | None:
        return self.group


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_the_recorded_group_is_dropped_once_it_is_empty(tmp_path: Path) -> None:
    import time

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    pgid_file = tmp_path / worker_mod.JOB_PGID_FILE
    current = worker_mod._CurrentJob(pgid_file=pgid_file)
    relay = _ExitedRelay(child.pid)
    try:
        current.attach(relay)  # type: ignore[arg-type]
        deadline = time.time() + 5
        while time.time() < deadline and not pgid_file.exists():
            time.sleep(0.05)
        assert pgid_file.read_text(encoding="utf-8").strip() == str(child.pid)
        relay.group = None  # the job has no live child now
        child.kill()
        child.wait()
        deadline = time.time() + 5
        while time.time() < deadline and pgid_file.exists():
            time.sleep(0.05)
        assert not pgid_file.exists()
    finally:
        current.detach()
        if child.poll() is None:
            child.kill()


def test_the_liveness_probes_treat_an_unreadable_answer_as_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*_a: Any) -> None:
        raise PermissionError("another account's process")

    assert worker_mod._process_alive(0)  # never a real owner: kept, not swept
    monkeypatch.setattr(os, "kill", denied)
    monkeypatch.setattr(os, "killpg", denied)
    assert worker_mod._process_alive(12345)
    assert worker_mod._group_alive(12345)
    monkeypatch.setattr(os, "name", "nt")
    assert worker_mod._group_alive(12345)  # no process groups to probe there


def test_the_sweep_leaves_another_accounts_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = tmp_path / f"{worker_mod.WORKSPACE_PREFIX}foreign"
    foreign.mkdir()
    (foreign / worker_mod.OWNER_PID_FILE).write_text(f"{_dead_pid()}\n", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: -1)
    worker_mod._sweep_stale_workspaces(tmp_path)
    assert foreign.is_dir()

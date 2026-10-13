"""`--backend local` queues `triage` for a worker (BE-0449 unit 7).

Triage is an investigator, never a judge: it reads a finished run and explains it, and the run's
verdict stays the one its deterministic assertions wrote. Moving it to a worker changes where the
investigator runs and nothing it decides. These tests lock the move end to end on a local start:
the server spawns nothing, a worker on the same machine runs the job and writes `triage.json` into
the server's own run directory, the stored AI key reaches the `--ai` investigator through the lease
alone (a stub agent stands in for the model, so no API call is ever made), and a cancel reaches the
job in the queue and on the worker.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc, fake_popen
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve import jobs
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.operations._common import NO_WORKER_NOTICE
from bajutsu.serve.operations.dispatch import _register_and_dispatch
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.worker_job import execute_job_spec
from bajutsu.serve.state import Job, ServeState

_ENV = (
    "BAJUTSU_SERVER_STORE",
    "BAJUTSU_DATABASE_URL",
    "BAJUTSU_SECRETS_KEY",
    "BAJUTSU_HOSTED",
    "BAJUTSU_REDIS_URL",
    "BAJUTSU_AI_PROVIDER",
    "BAJUTSU_EVIDENCE_STORE",
    "ANTHROPIC_API_KEY",
    "GITHUB_ACTIONS",
)

_API_KEY = "sk-ant-triage-0001"  # a fake credential shape, never a real key
_RUN_ID = "20260101-000000"

# Stands in for the Claude investigator in the triage the worker spawns: it records whether the
# relayed key reached its environment, then answers with the rule-based diagnosis. No model is
# called, and the key itself is never written anywhere.
_STUB_AGENT = """\
import os
from pathlib import Path

import bajutsu.common.agents.claude_triage as _claude_triage
from bajutsu.triage.heuristic import HeuristicTriageAgent


class _StubClaudeTriageAgent:
    def __init__(self, **_kwargs):
        pass

    def triage(self, context):
        seen = os.environ.get("ANTHROPIC_API_KEY") == {key!r}
        Path({marker!r}).write_text("key-seen" if seen else "key-missing", encoding="utf-8")
        return HeuristicTriageAgent().triage(context)


_claude_triage.ClaudeTriageAgent = _StubClaudeTriageAgent
"""


@pytest.fixture(autouse=True)
def _no_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _project(tmp_path: Path) -> Path:
    """A fake-backend project whose scenario carries the typo the stored run failed on."""
    scn = tmp_path / "scenarios"
    scn.mkdir()
    (scn / "smoke.yaml").write_text(
        "- name: alpha\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn} }}\n",
        encoding="utf-8",
    )
    _write_failed_run(tmp_path / "runs")
    return cfg


def _write_failed_run(runs: Path) -> Path:
    """A failed run: `alpha` tapped `home.titel`, but the screen only has `home.title`."""
    run = runs / _RUN_ID
    (run / "00-alpha" / "step0").mkdir(parents=True)
    step = {
        "index": 0,
        "action": "tap",
        "ok": False,
        "reason": "no match: {'id': 'home.titel'}",
        "artifacts": [{"name": "00-alpha/step0/elements.json", "kind": "elements"}],
    }
    manifest = {
        "runId": _RUN_ID,
        "ok": False,
        "scenarios": [
            {
                "scenario": "alpha",
                "ok": False,
                "steps": [step],
                "expect_results": [],
                "failure": "step0 tap: no match",
            }
        ],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "scenario.yaml").write_text(
        "- name: alpha\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8"
    )
    element = {
        "identifier": "home.title",
        "label": "Home",
        "traits": ["button"],
        "value": None,
        "frame": [0, 0, 10, 10],
        "nativeZ": None,
    }
    (run / "00-alpha" / "step0" / "elements.json").write_text(
        json.dumps([element]), encoding="utf-8"
    )
    return run


def _local_start(tmp_path: Path, cfg: Path) -> srv.ServeState:
    return srv._build_state(
        runs_dir=tmp_path / "runs",
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=tmp_path / "baselines",
        max_concurrent=4,
        token=None,
        cwd=cfg.parent,
    )


def _no_spawn_here(_cmd: list[str], **_kw: Any) -> FakeProc:
    raise AssertionError("the server must not spawn a triage itself")


class _StopLoop(Exception):
    """Ends the worker's poll loop once the queue is drained."""


def _worker_through(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the real `bajutsu worker` loop against *client* until a lease comes back empty."""

    def post(url: str, body: dict[str, Any], **_kw: Any) -> tuple[int, Any]:
        path = url.removeprefix("http://cp")
        resp = client.post(path, json=body)
        if path == "/api/worker/lease" and resp.status_code == 204:
            raise _StopLoop
        return resp.status_code, (resp.json() if resp.content else {})

    monkeypatch.setattr(worker_mod, "_post_json", post)
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])
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


def _start_triage(client: TestClient, *, ai: bool = False) -> Any:
    body: dict[str, Any] = {"runId": _RUN_ID, "target": "demo", "scenario": "smoke.yaml"}
    if ai:
        body["ai"] = True
    return client.post("/api/triage", json=body)


# --- routing -----------------------------------------------------------------------------------


def test_a_local_start_queues_triage(tmp_path: Path) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    assert isinstance(state.executor, srv.RoutingExecutor)
    assert "triage" in srv.LOCAL_QUEUED_KINDS
    assert not state.executor.routes_locally("triage")


# --- end to end: a triage on a local start goes through a worker --------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned triage")
def test_a_worker_runs_a_queued_triage_and_the_server_serves_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    started = _start_triage(client)
    assert started.status_code == 200, started.text
    job_id = started.json()["jobId"]
    assert state.repository is not None
    queued = state.repository.get_job(job_id)
    assert queued is not None and queued["status"] == "queued"
    # The job names the server's files by path and ships nothing, so a same-machine worker runs it.
    spec = queued["spec"]
    assert spec["cwd"] == str(tmp_path) and spec["bundle"] is None and not spec["materials"]
    assert spec["cmd"][1:4] == ["-m", "bajutsu", "triage"]
    assert client.get(f"/api/jobs/{job_id}").json()["notice"] == NO_WORKER_NOTICE

    _worker_through(client, monkeypatch)

    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done"
    # Triage reads a run and produces none, so the job reports no run of its own.
    assert finished["result"].get("runId") is None
    run = tmp_path / "runs" / _RUN_ID
    # The worker wrote the result into the server's run directory, and the UI's read finds it.
    served = client.get(f"/runs/{_RUN_ID}/triage.json")
    assert served.status_code == 200
    data = served.json()
    assert data["category"] == "selector"
    assert (data["fix"]["find"], data["fix"]["replace"]) == ("home.titel", "home.title")
    assert data["apply"]["count"] == 1
    # The worker left the triaged run as it was: no console of its own over the run's, and the
    # verdict the run recorded is untouched.
    assert not (run / "console.log").exists()
    assert json.loads((run / "manifest.json").read_text("utf-8"))["ok"] is False
    # Dry run alone: the scenario source keeps its typo until a person applies the fix.
    assert "home.titel" in (tmp_path / "scenarios" / "smoke.yaml").read_text("utf-8")
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["ok"] is True and "notice" not in view


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned triage")
def test_ai_triage_gets_the_stored_key_through_the_lease_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "agent-saw-key"
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        _STUB_AGENT.format(key=_API_KEY, marker=str(marker)), encoding="utf-8"
    )
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(p for p in (str(site), os.environ.get("PYTHONPATH")) if p)
    )
    seen: dict[str, Any] = {}
    real_execute = execute_job_spec

    def execute(spec: dict[str, Any], **kwargs: Any) -> Job:
        seen["secret_env"] = kwargs.get("secret_env")
        return real_execute(spec, **kwargs)

    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)
    state = _local_start(tmp_path, _project(tmp_path))
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert client.post("/api/apikey", json={"value": _API_KEY}).status_code == 200
    # The database store keeps the key out of the server's environment.
    assert "ANTHROPIC_API_KEY" not in os.environ

    # The stored key satisfies the server's credential check, since the lease will relay it.
    started = _start_triage(client, ai=True)
    assert started.status_code == 200, started.text
    job_id = started.json()["jobId"]
    assert state.repository is not None
    queued = state.repository.get_job(job_id)
    assert queued is not None and "--ai" in queued["spec"]["cmd"]
    assert _API_KEY not in json.dumps(queued)

    _worker_through(client, monkeypatch)

    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done", finished
    assert seen["secret_env"] == {"ANTHROPIC_API_KEY": _API_KEY}
    assert marker.read_text("utf-8") == "key-seen"
    assert client.get(f"/runs/{_RUN_ID}/triage.json").json()["category"] == "selector"
    assert _API_KEY not in json.dumps(finished)


def test_ai_triage_without_a_key_anywhere_is_refused_before_it_queues(tmp_path: Path) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    client = TestClient(make_app(state))
    refused = _start_triage(client, ai=True)
    assert refused.status_code == 400
    assert "AI triage cannot run" in refused.json()["error"]


def test_a_stored_key_under_another_variable_does_not_count(tmp_path: Path) -> None:
    # The lease relays the key under the bound config's `ai.keyEnv`. A target reading another
    # variable would not get it, so the check still refuses the job.
    cfg = _project(tmp_path)
    cfg.write_text(
        cfg.read_text("utf-8").replace("bundleId:", "ai: { keyEnv: OTHER_AI_KEY }, bundleId:"),
        encoding="utf-8",
    )
    state = _local_start(tmp_path, cfg)
    client = TestClient(make_app(state))
    assert client.post("/api/apikey", json={"value": _API_KEY}).status_code == 200
    assert _start_triage(client, ai=True).status_code == 400


def test_a_store_that_cannot_be_read_counts_as_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    client = TestClient(make_app(state))
    assert client.post("/api/apikey", json={"value": _API_KEY}).status_code == 200
    # `for_org` builds the store per call, so the failure goes on its class.
    store_type = type(state.for_org("default").secrets)

    def broken(_self: object, _name: str) -> str | None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(store_type, "describe", broken)
    refused = _start_triage(client, ai=True)
    assert refused.status_code == 400
    assert "AI triage cannot run" in refused.json()["error"]
    assert "could not read the stored AI key" in caplog.text
    assert _API_KEY not in caplog.text


# --- cancel ------------------------------------------------------------------------------------


def test_cancelling_a_queued_triage_keeps_it_from_any_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    job_id = _start_triage(client).json()["jobId"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert state.repository is not None
    row = state.repository.get_job(job_id)
    assert row is not None and row["status"] == "cancelled"

    _worker_through(client, monkeypatch)

    assert not (tmp_path / "runs" / _RUN_ID / "triage.json").exists()


def test_cancelling_a_leased_triage_reaches_its_worker(tmp_path: Path) -> None:
    state = _local_start(tmp_path, _project(tmp_path))
    client = TestClient(make_app(state))
    job_id = _start_triage(client).json()["jobId"]
    lease = client.post("/api/worker/lease", json={"worker_id": "w1", "capabilities": []})
    assert lease.status_code == 200 and lease.json()["job_id"] == job_id
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    beat = client.post("/api/worker/heartbeat", json={"worker_id": "w1", "job_id": job_id})
    assert beat.json() == {"ok": True, "cancel": True}
    result = client.post(
        "/api/worker/result",
        json={"job_id": job_id, "worker_id": "w1", "result": {"cancelled": True, "ok": False}},
    )
    assert result.status_code == 200
    assert state.repository is not None
    row = state.repository.get_job(job_id)
    assert row is not None and row["status"] == "cancelled"


# --- the job carries the server's paths, never a bundle -----------------------------------------


class _Upload:
    def __init__(self) -> None:
        self.worker_ref = {"id": "a" * 64}


class _Binding:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.upload = _Upload()


class _Recording:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, _state: ServeState, job: Job) -> None:
        self.jobs.append(job)


def test_a_job_that_ships_no_bundle_keeps_the_binding_directory(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    recorder = _Recording()
    state.executor = recorder
    binding: Any = _Binding(tmp_path)
    shipped, _ = _register_and_dispatch(state, Job(cmd=["x"]), binding=binding)
    kept, _ = _register_and_dispatch(state, Job(cmd=["x"]), binding=binding, ship_bundle=False)
    assert shipped is not None and shipped.bundle == {"id": "a" * 64}
    assert kept is not None and kept.bundle is None and kept.cwd == tmp_path


# --- a triage never claims the run it read ------------------------------------------------------


def test_a_run_path_in_triage_output_is_not_taken_as_its_run(tmp_path: Path) -> None:
    line = f"see runs/{_RUN_ID}/manifest.json"
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=fake_popen([line]))
    triage = Job(cmd=[sys.executable, "-m", "bajutsu", "triage", str(tmp_path)])
    jobs.run_job(state, triage)
    assert triage.run_id is None and triage.lines == [line]
    run = Job(cmd=[sys.executable, "-m", "bajutsu.run.internal"])
    jobs.run_job(state, run)
    assert run.run_id == _RUN_ID

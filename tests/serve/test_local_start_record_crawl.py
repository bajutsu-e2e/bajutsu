"""`--backend local` queues `record` and `crawl` for a worker (BE-0449 unit 8).

Both are AI authoring and exploration paths, never a verdict: moving them to a worker changes where
the agent runs and nothing the deterministic `run` decides. These tests lock the move end to end on
a local start with the real worker loop. A stub stands in for the `bajutsu record` / `bajutsu crawl`
child the worker spawns, so no model and no device is ever reached: the crawl's growing screen map
reaches an event-stream client through the artifact relay, and a paused record's request reaches
the page while the person's answer reaches the record's standard input through the handoff relay.
The relay routes admit only the lease holder and only a worker, and a cancel reaches a record that
waits on a person.
"""

from __future__ import annotations

import contextlib
import functools
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.common.handoff import REQUEST_LINE_PREFIX
from bajutsu.serve import operations as ops
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.cli.worker_relay import ArtifactStreamer, HandoffRelay
from bajutsu.serve.logbus import ARTIFACT_LINE_PREFIX, InMemoryLogBus
from bajutsu.serve.operations.sse import _classify
from bajutsu.serve.operations.worker import HANDOFF_ANSWER_TTL_SECONDS, WORKER_ARTIFACT_MAX_BYTES
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.worker_job import CancelRelay, execute_job_spec
from bajutsu.serve.state import Job

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

_OTP = "otp-relay-424242"  # the value a person types into the handoff, never a real credential

# Stands in for `bajutsu crawl`: two snapshots of a growing screen map, written whole each time, as
# the crawl's own writer does, then a clean exit. No device and no model.
_STUB_CRAWL = """\
import json, os, sys, time
args = sys.argv[1:]
out = args[args.index("--out") + 1]
os.makedirs(out, exist_ok=True)
def write(n):
    data = {"nodes": [{"fingerprint": f"fp{i}", "ids": [f"s{i}"]} for i in range(n)], "edges": []}
    tmp = os.path.join(out, "screenmap.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, os.path.join(out, "screenmap.json"))
    print(f"screens: {n}", flush=True)
write(1)
time.sleep(0.6)
write(2)
time.sleep(0.3)
"""

# Stands in for `bajutsu record --handoff stream`: it pauses for a person the way `StreamHandoff`
# does, then writes the scenario from the answer, or exits 1 on a cancel or no answer.
_STUB_RECORD = """\
import json, os, select, sys
args = sys.argv[1:]
out = args[args.index("--out") + 1]
request = {"reason": "enter the one-time password", "screen": "login", "target": "", "screenshot": None}
print("recording", flush=True)
sys.stdout.write("@PREFIX@" + json.dumps(request) + "\\n")
sys.stdout.flush()
ready, _, _ = select.select([sys.stdin], [], [], 20)
line = sys.stdin.readline() if ready else ""
answer = json.loads(line) if line.strip() else {"cancelled": True}
if answer.get("cancelled") or not answer.get("values"):
    print("handoff cancelled", flush=True)
    sys.exit(1)
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    f.write("- name: login\\n  steps:\\n    - type: { id: otp, text: '${secrets.OTP}' }\\n")
print("wrote " + out, flush=True)
""".replace("@PREFIX@", REQUEST_LINE_PREFIX.encode("unicode_escape").decode("ascii"))


@pytest.fixture(autouse=True)
def _no_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _project(tmp_path: Path) -> Path:
    scn = tmp_path / "scenarios"
    scn.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn} }}\n",
        encoding="utf-8",
    )
    (tmp_path / "runs").mkdir()
    return cfg


def _local_start(tmp_path: Path, *, token: str | None = None) -> srv.ServeState:
    cfg = _project(tmp_path)
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
    raise AssertionError("the server must not spawn a record or a crawl itself")


def _stub_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Make the worker spawn the stubs in place of `bajutsu record` / `bajutsu crawl`.

    Returns the argv the worker asked to spawn, one per job, so a test can check what it received.
    """
    import subprocess

    stubs = {"crawl": tmp_path / "stub_crawl.py", "record": tmp_path / "stub_record.py"}
    stubs["crawl"].write_text(_STUB_CRAWL, encoding="utf-8")
    stubs["record"].write_text(_STUB_RECORD, encoding="utf-8")
    spawned: list[list[str]] = []

    def popen(cmd: list[str], **kwargs: Any) -> Any:
        spawned.append(list(cmd))
        kind = cmd[3]
        return subprocess.Popen([sys.executable, str(stubs[kind]), *cmd[4:]], **kwargs)

    def execute(spec: dict[str, Any], **kwargs: Any) -> Job:
        kwargs["popen"] = popen
        return execute_job_spec(spec, **kwargs)

    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)
    # A short flush interval, so the screen map is checked while the stub crawl still runs.
    monkeypatch.setattr(
        worker_mod,
        "_run_with_heartbeat",
        functools.partial(worker_mod._run_with_heartbeat, log_flush_interval=0.1),
    )
    return spawned


class _StopLoop(Exception):
    """Ends the worker's poll loop once the queue is drained."""


def _worker_thread(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, heartbeat: float = 0.5
) -> threading.Thread:
    """The real `bajutsu worker` loop against *client*, on a thread, until a lease comes back empty."""

    def post(url: str, body: dict[str, Any], **_kw: Any) -> tuple[int, Any]:
        path = url.removeprefix("http://cp")
        resp = client.post(path, json=body)
        if path == "/api/worker/lease" and resp.status_code == 204:
            raise _StopLoop
        return resp.status_code, (resp.json() if resp.content else {})

    monkeypatch.setattr(worker_mod, "_post_json", post)
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *_a: ["platform:ios"])

    def loop() -> None:
        with contextlib.suppress(_StopLoop):
            worker_mod.worker(
                server_url="http://cp",
                poll_interval=0.01,
                heartbeat_interval=heartbeat,
                worker_id="w1",
                platform="ios",
                capabilities="",
                token="",
                worker_config=None,
                once=None,
            )

    return threading.Thread(target=loop, daemon=True)


def _events(client: TestClient, job_id: str, into: list[tuple[str, str]]) -> threading.Thread:
    """Read the job's event stream on a thread, as the page does, into ``(event, data)`` pairs."""

    def read() -> None:
        body = client.get(f"/api/jobs/{job_id}/events").text
        for frame in body.split("\n\n"):
            event = ""
            data: list[str] = []
            for line in frame.splitlines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data.append(line.removeprefix("data: "))
            if event:
                into.append((event, "\n".join(data)))

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def _wait_for(check: Any, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def _awaiting_human(state: srv.ServeState, job_id: str) -> bool:
    assert state.repository is not None
    row = state.repository.get_job(job_id)
    return bool(row and row.get("awaiting_human"))


# --- routing -----------------------------------------------------------------------------------


def test_a_local_start_queues_record_and_crawl(tmp_path: Path) -> None:
    state = _local_start(tmp_path)
    assert isinstance(state.executor, srv.RoutingExecutor)
    for kind in ("record", "crawl"):
        assert kind in srv.LOCAL_QUEUED_KINDS
        assert not state.executor.routes_locally(kind)


# --- crawl: the live screen map reaches the page through the artifact stream --------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned child")
def test_a_worker_crawls_and_the_growing_map_reaches_an_event_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path)
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    spawned = _stub_children(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    started = client.post("/api/crawl", json={"target": "demo", "backend": "fake"})
    assert started.status_code == 200, started.text
    job_id, run_id = started.json()["jobId"], started.json()["runId"]
    assert state.repository is not None
    queued = state.repository.get_job(job_id)
    assert queued is not None and queued["status"] == "queued"
    assert queued["spec"]["cmd"][1:4] == ["-m", "bajutsu", "crawl"]

    events: list[tuple[str, str]] = []
    reader = _events(client, job_id, events)
    worker = _worker_thread(client, monkeypatch)
    worker.start()
    worker.join(timeout=60)
    reader.join(timeout=30)
    assert not worker.is_alive() and not reader.is_alive()

    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done", finished
    # The worker spawned the crawl the server queued, writing into the server's own run directory.
    assert spawned and spawned[0][3] == "crawl"
    out = spawned[0][spawned[0].index("--out") + 1]
    assert Path(out) == tmp_path / "runs" / run_id
    # The graph reached the stream as artifact events, in the order the map grew, ending complete.
    maps = [json.loads(data) for event, data in events if event == "artifact"]
    assert maps, events
    assert all(m["name"] == "screenmap.json" for m in maps)
    sizes = [len(m["data"]["nodes"]) for m in maps]
    assert sizes == sorted(sizes) and sizes[-1] == 2
    assert ("log", "screens: 2") in events
    assert events[-1][0] == "done"
    # The durable copy is where the page reads it once the job ends.
    served = client.get(f"/runs/{run_id}/screenmap.json")
    assert served.status_code == 200 and len(served.json()["nodes"]) == 2


# --- record: the handoff relay ------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned child")
def test_a_worker_records_and_the_handoff_goes_page_to_worker_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path)
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    _stub_children(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    started = client.post("/api/record", json={"target": "demo", "goal": "log in", "name": "login"})
    assert started.status_code == 200, started.text
    job_id = started.json()["jobId"]
    events: list[tuple[str, str]] = []
    reader = _events(client, job_id, events)
    worker = _worker_thread(client, monkeypatch)
    worker.start()

    # The paused record's request reached the server, which now waits on a person.
    _wait_for(lambda: _awaiting_human(state, job_id))
    assert client.get(f"/api/jobs/{job_id}").json()["awaitingHuman"] is True
    answered = client.post(f"/api/jobs/{job_id}/respond-human", json={"values": [_OTP]})
    assert answered.status_code == 200 and answered.json() == {"resumed": True}
    assert state.repository is not None
    # The answer waits encrypted, never as the value the person typed.
    with state.repository._engine.connect() as conn:  # type: ignore[attr-defined]
        from sqlalchemy import text

        stored = conn.execute(
            text("SELECT handoff_response FROM jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
    assert stored is None or _OTP not in stored

    worker.join(timeout=60)
    reader.join(timeout=30)
    assert not worker.is_alive() and not reader.is_alive()
    finished = state.repository.get_job(job_id)
    assert finished is not None and finished["status"] == "done", finished
    assert finished["awaiting_human"] is False
    # The request reached the page as a `human-request` event, not as a log line.
    requests = [json.loads(data) for event, data in events if event == "human-request"]
    assert requests == [
        {
            "reason": "enter the one-time password",
            "screen": "login",
            "target": "",
            "screenshot": None,
        }
    ]
    assert not any(REQUEST_LINE_PREFIX in data for _event, data in events)
    # The answer reached the record, and the scenario it wrote landed in the target's scenarios.
    authored = list((tmp_path / "scenarios").glob("login*.yaml"))
    assert len(authored) == 1, list((tmp_path / "scenarios").iterdir())
    assert "${secrets.OTP}" in authored[0].read_text("utf-8")
    # Nothing the worker reported, and nothing the jobs table keeps, holds the value.
    assert _OTP not in json.dumps(finished)
    with state.repository._engine.connect() as conn:  # type: ignore[attr-defined]
        from sqlalchemy import text

        assert (
            conn.execute(
                text("SELECT handoff_response FROM jobs WHERE id = :id"), {"id": job_id}
            ).scalar()
            is None
        )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions for the spawned child")
def test_cancelling_a_record_that_waits_on_a_person_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _local_start(tmp_path)
    state.popen = _no_spawn_here
    client = TestClient(make_app(state))
    _stub_children(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    job_id = client.post(
        "/api/record", json={"target": "demo", "goal": "log in", "name": "login"}
    ).json()["jobId"]
    # A heartbeat far longer than the test: the cancel must reach the worker through the handoff
    # poll, not wait for the next beat.
    worker = _worker_thread(client, monkeypatch, heartbeat=120.0)
    worker.start()
    _wait_for(lambda: _awaiting_human(state, job_id))

    cancelled = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
    # An answer after the cancel no longer reaches the record.
    late = client.post(f"/api/jobs/{job_id}/respond-human", json={"values": [_OTP]})
    assert late.json() == {"resumed": False}
    worker.join(timeout=30)
    assert not worker.is_alive()

    assert state.repository is not None
    row = state.repository.get_job(job_id)
    assert row is not None and row["status"] == "cancelled", row
    assert row["awaiting_human"] is False
    assert not list((tmp_path / "scenarios").glob("login*.yaml"))


# --- the relay routes admit the lease holder alone, and only a worker ---------------------------


def _leased_record(tmp_path: Path, *, token: str | None = None) -> tuple[srv.ServeState, str]:
    state = _local_start(tmp_path, token=token)
    client = TestClient(make_app(state))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    job_id = client.post(
        "/api/record", json={"target": "demo", "goal": "log in"}, headers=headers
    ).json()["jobId"]
    leased, code = ops.worker_lease(state, "w1", ["platform:ios"])
    assert code == 200 and leased["job_id"] == job_id
    return state, job_id


_REQUEST = json.dumps({"reason": "otp", "screen": "", "target": "", "screenshot": None})
_RECORD_CMD = [sys.executable, "-m", "bajutsu", "record"]


def test_only_the_lease_holder_may_relay(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    other = {"worker_id": "w2", "job_id": job_id}
    assert ops.worker_handoff(state, {**other, "request": _REQUEST})[1] == 409
    assert ops.worker_handoff_response(state, other)[1] == 409
    content = json.dumps({"nodes": []})
    assert (
        ops.worker_artifact(state, {**other, "name": "screenmap.json", "content": content})[1]
        == 409
    )
    assert not _awaiting_human(state, job_id)


def test_a_session_caller_is_refused_on_every_relay_route(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path, token="tok")
    client = TestClient(make_app(state))
    sid = state.auth.issue_session("alice")
    client.cookies.set("bajutsu_session", sid)
    holder = {"worker_id": "w1", "job_id": job_id}
    for path, body in (
        ("/api/worker/handoff", {**holder, "request": _REQUEST}),
        ("/api/worker/handoff-response", holder),
        ("/api/worker/artifact", {**holder, "name": "screenmap.json", "content": "{}"}),
    ):
        assert client.post(path, json=body).status_code == 403, path
    # The shared token, which a worker holds, is admitted.
    client.cookies.clear()
    worker = {"Authorization": "Bearer tok"}
    opened = client.post(
        "/api/worker/handoff", json={**holder, "request": _REQUEST}, headers=worker
    )
    assert opened.status_code == 200
    assert _awaiting_human(state, job_id)


def test_an_answer_reaches_only_its_own_org(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    assert (
        ops.worker_handoff(state, {"worker_id": "w1", "job_id": job_id, "request": _REQUEST})[1]
        == 200
    )
    refused, code = ops.respond_human(state, job_id, {"values": [_OTP]}, machine_org="acme")
    assert code == 404 and "resumed" not in refused
    assert _awaiting_human(state, job_id)
    answered, code = ops.respond_human(state, job_id, {"values": [_OTP]})
    assert code == 200 and answered == {"resumed": True}
    taken, code = ops.worker_handoff_response(state, {"worker_id": "w1", "job_id": job_id})
    assert code == 200 and json.loads(taken["response"])["values"] == [_OTP]
    # Taken once: a second poll finds nothing.
    again, _ = ops.worker_handoff_response(state, {"worker_id": "w1", "job_id": job_id})
    assert "response" not in again


def test_an_answer_older_than_the_records_wait_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cryptography.fernet as fernet_mod

    state, job_id = _leased_record(tmp_path)
    ops.worker_handoff(state, {"worker_id": "w1", "job_id": job_id, "request": _REQUEST})
    assert ops.respond_human(state, job_id, {"values": [_OTP]})[0] == {"resumed": True}
    written = time.time()

    class _Later:
        """The clock Fernet reads, once the record's whole wait has passed."""

        @staticmethod
        def time() -> float:
            return written + HANDOFF_ANSWER_TTL_SECONDS + 5

    monkeypatch.setattr(fernet_mod, "time", _Later)
    body, code = ops.worker_handoff_response(state, {"worker_id": "w1", "job_id": job_id})
    assert code == 200 and "response" not in body


def test_no_answer_is_stored_for_a_job_that_waits_on_nobody(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    assert ops.respond_human(state, job_id, {"values": [_OTP]})[0] == {"resumed": False}


def _published(state: srv.ServeState, job_id: str) -> list[str]:
    """The lines published so far for *job_id*, from the in-memory bus the test installed."""
    lines: list[str] = []
    for line in state.logbus.stream(job_id, timeout=0.0):
        if line is None:
            break
        lines.append(line)
    return lines


def test_a_log_line_cannot_forge_a_relay_event(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    state.logbus = InMemoryLogBus()
    forged = [
        REQUEST_LINE_PREFIX + _REQUEST,
        ARTIFACT_LINE_PREFIX + json.dumps({"name": "screenmap.json", "data": {}}),
        "plain",
    ]
    assert ops.worker_log(state, {"worker_id": "w1", "job_id": job_id, "lines": forged})[1] == 200
    assert _published(state, job_id) == ["plain"]
    assert not _awaiting_human(state, job_id)


def test_an_artifact_must_be_a_named_json_object_within_its_bound(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    state.logbus = InMemoryLogBus()
    holder = {"worker_id": "w1", "job_id": job_id}
    assert ops.worker_artifact(state, {**holder, "name": "other.json", "content": "{}"})[1] == 400
    assert (
        ops.worker_artifact(state, {**holder, "name": "screenmap.json", "content": "[]"})[1] == 400
    )
    assert (
        ops.worker_artifact(state, {**holder, "name": "screenmap.json", "content": "{"})[1] == 400
    )
    big = json.dumps({"x": "a" * WORKER_ARTIFACT_MAX_BYTES})
    assert (
        ops.worker_artifact(state, {**holder, "name": "screenmap.json", "content": big})[1] == 413
    )
    ok, code = ops.worker_artifact(state, {**holder, "name": "screenmap.json", "content": "{}"})
    assert code == 200 and ok == {"ok": True}
    [line] = _published(state, job_id)
    assert _classify(line) == ("artifact", '{"name":"screenmap.json","data":{}}')


def test_past_the_live_budget_one_truncation_notice_goes_out(tmp_path: Path) -> None:
    from bajutsu.serve.logbus import LiveLineBudget

    state, job_id = _leased_record(tmp_path)
    state.logbus = InMemoryLogBus()
    state.live_artifact_budget = LiveLineBudget(per_job=1)
    holder = {"worker_id": "w1", "job_id": job_id, "name": "screenmap.json", "content": "{}"}
    assert ops.worker_artifact(state, holder)[0] == {"ok": True}
    assert ops.worker_artifact(state, holder)[0] == {"ok": True, "truncated": True}
    assert ops.worker_artifact(state, holder)[0] == {"ok": True, "truncated": True}
    events = [_classify(line) for line in _published(state, job_id)]
    assert events == [
        ("artifact", '{"name":"screenmap.json","data":{}}'),
        ("artifact", '{"name": "screenmap.json", "truncated": true}'),
    ]


# --- the worker's relays on their own -----------------------------------------------------------


class _Recorder:
    def __init__(self, answers: list[tuple[int | None, Any]]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._answers = answers

    def __call__(self, path: str, body: dict[str, Any]) -> tuple[int | None, Any]:
        self.calls.append((path, body))
        return self._answers.pop(0) if self._answers else (200, {})


def test_the_streamer_posts_a_changed_map_once_and_skips_a_partial_write(tmp_path: Path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    post = _Recorder([])
    streamer = ArtifactStreamer(post)
    streamer.bind(Job(cmd=[sys.executable, "-m", "bajutsu", "crawl", "--out", str(out)]), tmp_path)
    streamer.check()
    assert post.calls == []  # nothing written yet
    (out / "screenmap.json").write_text('{"nodes": [', encoding="utf-8")
    streamer.check()
    assert post.calls == []  # read mid-write: tried again on the next check
    (out / "screenmap.json").write_text('{"nodes": []}', encoding="utf-8")
    streamer.check()
    streamer.check()
    assert [body["content"] for _path, body in post.calls] == ['{"nodes": []}']


def test_the_streamer_ignores_other_kinds_and_stops_on_a_lost_lease(tmp_path: Path) -> None:
    (tmp_path / "screenmap.json").write_text("{}", encoding="utf-8")
    post = _Recorder([(409, {})])
    idle = ArtifactStreamer(post)
    idle.bind(Job(cmd=[sys.executable, "-m", "bajutsu", "record", "--out", "x.yaml"]), tmp_path)
    idle.check()
    assert post.calls == []
    streamer = ArtifactStreamer(post)
    streamer.bind(Job(cmd=[sys.executable, "-m", "bajutsu", "crawl", "--out=."]), tmp_path)
    streamer.check()
    (tmp_path / "screenmap.json").write_text('{"a": 1}', encoding="utf-8")
    streamer.check()
    assert len(post.calls) == 1


def test_the_handoff_relay_hands_a_cancel_to_the_job(tmp_path: Path) -> None:
    post = _Recorder([(200, {}), (200, {}), (200, {"ok": True, "cancel": True})])
    cancel = CancelRelay()
    relay = HandoffRelay(post, cancel=cancel, poll_interval=0.01)
    relay.bind(Job(cmd=_RECORD_CMD))
    relay.offer(REQUEST_LINE_PREFIX + _REQUEST)
    _wait_for(lambda: cancel.requested, timeout=5)
    relay.stop()
    assert [path for path, _body in post.calls] == [
        "/api/worker/handoff",
        "/api/worker/handoff-response",
        "/api/worker/handoff-response",
    ]


def test_the_handoff_relay_gives_up_at_its_deadline_and_on_a_refused_request() -> None:
    clock = [0.0]
    post = _Recorder([(200, {})])
    relay = HandoffRelay(
        post, cancel=CancelRelay(), poll_interval=0.0, deadline=1.0, clock=lambda: clock[0]
    )

    def tick(path: str, body: dict[str, Any]) -> tuple[int | None, Any]:
        clock[0] += 0.4
        return post(path, body)

    relay._post = tick
    relay.bind(Job(cmd=_RECORD_CMD))
    relay.offer(REQUEST_LINE_PREFIX + _REQUEST)
    _wait_for(lambda: len(post.calls) >= 3, timeout=5)
    relay.stop()
    polls = [path for path, _body in post.calls if path.endswith("-response")]
    assert 1 <= len(polls) <= 3
    refused = _Recorder([(409, {})])
    relay = HandoffRelay(refused, cancel=CancelRelay(), poll_interval=0.0)
    relay.bind(Job(cmd=_RECORD_CMD))
    relay.offer(REQUEST_LINE_PREFIX + _REQUEST)
    relay.stop()
    assert [path for path, _body in refused.calls] == ["/api/worker/handoff"]


# --- malformed relay posts ----------------------------------------------------------------------


def test_a_relay_post_missing_its_owner_or_payload_is_refused(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    holder = {"worker_id": "w1", "job_id": job_id}
    for body in ({"job_id": job_id}, {"worker_id": "w1"}):
        assert ops.worker_artifact(state, body)[1] == 400
        assert ops.worker_handoff(state, body)[1] == 400
        assert ops.worker_handoff_response(state, body)[1] == 400
    assert ops.worker_artifact(state, {**holder, "name": "screenmap.json", "content": 1})[1] == 400
    assert ops.worker_handoff(state, {**holder, "request": 1})[1] == 400
    assert ops.worker_handoff(state, {**holder, "request": "[]"})[1] == 400
    assert ops.worker_handoff(state, {**holder, "request": "{"})[1] == 400
    big = json.dumps({"reason": "a" * (8 * 1024 * 1024)})
    assert ops.worker_handoff(state, {**holder, "request": big})[1] == 413
    for relay in (ops.worker_artifact, ops.worker_handoff, ops.worker_handoff_response):
        assert relay(state, holder, from_worker=False)[1] == 403
    # The result route, whose result clears the handoff, needs the job's id as well.
    assert ops.worker_result(state, {"worker_id": "w1", "result": {}})[1] == 400
    state.repository = None
    for relay in (ops.worker_artifact, ops.worker_handoff, ops.worker_handoff_response):
        assert relay(state, holder)[1] == 503


def test_a_handoff_on_a_lease_lost_between_checks_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, job_id = _leased_record(tmp_path)
    assert state.repository is not None
    monkeypatch.setattr(state.repository, "open_handoff", lambda *_a: False)
    body = {"worker_id": "w1", "job_id": job_id, "request": _REQUEST}
    assert ops.worker_handoff(state, body)[1] == 409


def test_the_job_view_reads_the_handoff_from_the_row(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    holder = {"worker_id": "w1", "job_id": job_id}
    assert ops.job_view(state, job_id)[0]["awaitingHuman"] is False
    assert ops.worker_handoff(state, {**holder, "request": _REQUEST})[1] == 200
    # The row is what every replica reads, so the view follows it, not this process's `Job`.
    assert ops.job_view(state, job_id)[0]["awaitingHuman"] is True
    # One request at a time: a second one while the first waits is refused.
    assert ops.worker_handoff(state, {**holder, "request": _REQUEST})[1] == 409
    assert ops.respond_human(state, job_id, {"values": [_OTP]})[0] == {"resumed": True}
    assert ops.job_view(state, job_id)[0]["awaitingHuman"] is False
    assert ops.worker_handoff(state, {**holder, "request": _REQUEST})[1] == 200


def test_only_a_record_hands_off(tmp_path: Path) -> None:
    from bajutsu.serve import jobs

    state = _local_start(tmp_path)
    client = TestClient(make_app(state))
    job_id = client.post("/api/crawl", json={"target": "demo", "backend": "fake"}).json()["jobId"]
    assert ops.worker_lease(state, "w1", ["platform:ios"])[0]["job_id"] == job_id
    body = {"worker_id": "w1", "job_id": job_id, "request": _REQUEST}
    assert ops.worker_handoff(state, body)[1] == 409
    assert not _awaiting_human(state, job_id)
    # The worker's relay ignores a crawl's line, and so does the server's own process.
    post = _Recorder([])
    relay = HandoffRelay(post, cancel=CancelRelay())
    relay.bind(Job(cmd=[sys.executable, "-m", "bajutsu", "crawl"]))
    relay.offer(REQUEST_LINE_PREFIX + _REQUEST)
    relay.stop()
    assert post.calls == []
    crawl = Job(id="c1", cmd=[sys.executable, "-m", "bajutsu", "crawl"], bus=InMemoryLogBus())
    jobs._take_line(crawl, REQUEST_LINE_PREFIX + _REQUEST, produces_run=True)
    assert crawl.awaiting_human is False and crawl.lines == []
    record = Job(id="r1", cmd=_RECORD_CMD)
    jobs._take_line(record, REQUEST_LINE_PREFIX + _REQUEST, produces_run=True)
    assert record.awaiting_human is True


def test_handoff_requests_past_the_jobs_budget_are_refused(tmp_path: Path) -> None:
    from bajutsu.serve.logbus import LiveLineBudget

    state, job_id = _leased_record(tmp_path)
    state.live_handoff_budget = LiveLineBudget(bytes_per_job=len(_REQUEST))
    body = {"worker_id": "w1", "job_id": job_id, "request": _REQUEST}
    assert ops.worker_handoff(state, body)[1] == 413
    assert not _awaiting_human(state, job_id)


def test_a_reconnecting_page_gets_no_request_the_job_stopped_waiting_on(tmp_path: Path) -> None:
    from bajutsu.serve.operations.sse import job_log_events

    state, job_id = _leased_record(tmp_path)
    state.logbus = InMemoryLogBus()
    ops.worker_handoff(state, {"worker_id": "w1", "job_id": job_id, "request": _REQUEST})
    state.logbus.publish(job_id, "after")
    state.logbus.close(job_id)
    waiting = job_log_events(state, job_id)
    assert waiting is not None
    assert [event for event, _data in waiting][:2] == ["human-request", "log"]
    ops.respond_human(state, job_id, {"values": [_OTP]})
    replay = job_log_events(state, job_id)
    assert replay is not None
    assert [event for event, _data in replay] == ["log", "done"]
    # The page's own stream, the one it reconnects to, skips it the same way.
    frames = ops.job_sse(state, job_id, keepalive=0.01)
    assert frames is not None
    body = "".join(frames)
    assert "event: human-request" not in body and "data: after" in body


def test_a_server_without_a_cipher_holds_no_answer(tmp_path: Path) -> None:
    state, job_id = _leased_record(tmp_path)
    ops.worker_handoff(state, {"worker_id": "w1", "job_id": job_id, "request": _REQUEST})
    state.handoff_cipher = None
    assert ops.respond_human(state, job_id, {"values": [_OTP]})[1] == 503
    body, code = ops.worker_handoff_response(state, {"worker_id": "w1", "job_id": job_id})
    assert code == 200 and "response" not in body


def test_a_job_the_server_runs_itself_carries_no_worker_notice(tmp_path: Path) -> None:
    from bajutsu.serve.operations._common import no_worker_notice
    from bajutsu.serve.server.db_executor import DbQueueExecutor

    state = _local_start(tmp_path)
    assert state.repository is not None
    state.executor = srv.RoutingExecutor(
        DbQueueExecutor(state.repository), srv.LocalExecutor(), queued_kinds={"run"}
    )
    record = Job(cmd=[sys.executable, "-m", "bajutsu", "record"])
    assert no_worker_notice(state, record) is None


def test_the_handoff_relay_writes_the_answer_to_the_record(tmp_path: Path) -> None:
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; print(sys.stdin.readline().strip())"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    job = Job(cmd=_RECORD_CMD, proc=proc)
    post = _Recorder([(200, {}), (200, {"ok": True, "response": '{"values": ["v"]}'})])
    relay = HandoffRelay(post, cancel=CancelRelay(), poll_interval=0.01)
    relay.bind(job)
    relay.offer("not a request")  # a line without the prefix is no request
    relay.offer(REQUEST_LINE_PREFIX + _REQUEST)
    out, _ = proc.communicate(timeout=10)
    relay.stop()
    assert out.strip() == '{"values": ["v"]}'
    # An answer for a record that already stopped reading is logged and dropped.
    late = HandoffRelay(
        _Recorder([(200, {}), (200, {"response": "{}"})]), cancel=CancelRelay(), poll_interval=0.01
    )
    late.bind(Job(cmd=_RECORD_CMD))
    late.offer(REQUEST_LINE_PREFIX + _REQUEST)
    late.stop()
    after_stop = HandoffRelay(_Recorder([]), cancel=CancelRelay())
    after_stop.stop()
    after_stop.offer(REQUEST_LINE_PREFIX + _REQUEST)


def test_the_streamer_skips_an_oversized_map_and_stops_on_truncation(tmp_path: Path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    crawl = Job(cmd=[sys.executable, "-m", "bajutsu", "crawl", "--out", "run"])
    post = _Recorder([(200, {"ok": True, "truncated": True})])
    streamer = ArtifactStreamer(post)
    streamer.bind(crawl, tmp_path)
    (out / "screenmap.json").write_text("x" * (WORKER_ARTIFACT_MAX_BYTES + 1), encoding="utf-8")
    streamer.check()
    assert post.calls == []
    (out / "screenmap.json").write_text("{}", encoding="utf-8")
    streamer.start(0.01)
    _wait_for(lambda: len(post.calls) == 1, timeout=5)
    (out / "screenmap.json").write_text('{"a": 2}', encoding="utf-8")
    streamer.stop(final=True)
    assert len(post.calls) == 1  # the truncated answer ended the posts
    no_out = ArtifactStreamer(post)
    no_out.bind(Job(cmd=[sys.executable, "-m", "bajutsu", "crawl"]), tmp_path)
    no_out.check()
    assert len(post.calls) == 1

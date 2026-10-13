"""A queued job's log reaches the browser live across the server/worker split (BE-0449 unit 4).

The worker posts its log lines to ``/api/worker/log`` while the job runs, the server publishes them
to a bus that accepts writes, and the result route closes the stream. The uploaded ``console.log``
stays the fallback. The executor that routes each job kind to the queue or the in-process thread is
covered here too, since it lands in the same unit. Every server-side case runs on a file-backed
SQLite, the store a local start uses, so FastAPI's threadpool sees the same database.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from conftest import StubArtifactStore
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.commands import crawl_command, record_command, run_command, triage_command
from bajutsu.serve.operations.worker import WORKER_LOG_MAX_LINE_CHARS, WORKER_LOG_MAX_LINES
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db._functions import engine_from_url
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.logbus import RedisLogBus
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.queue_logbus import QueueLogBus, live_log_bus, live_log_bus_from_env

_RUN_CMD = [sys.executable, "-m", "bajutsu", "run", "--scenario", "s.yaml"]


class _Artifacts(StubArtifactStore):
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def open_bytes(self, rel: str) -> bytes | None:
        return self._files.get(rel)


def _repo(tmp_path: Path) -> SqlRepository:
    engine = engine_from_url(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _state(
    tmp_path: Path, repo: SqlRepository, files: dict[str, bytes] | None = None
) -> srv.ServeState:
    artifacts = _Artifacts(files or {})
    return srv.ServeState(
        runs_dir=tmp_path / "runs",
        executor=srv.RoutingExecutor(
            DbQueueExecutor(repo), srv.LocalExecutor(), queued_kinds=srv.JOB_KINDS
        ),
        logbus=QueueLogBus(repo, srv.InMemoryLogBus(), lambda _org: artifacts, poll_interval=0.01),
        repository=repo,
    )


def _queued(state: srv.ServeState, job_id: str = "j1") -> srv.Job:
    """Register and dispatch a `run` job, as `_register_and_dispatch` does."""
    job = srv.Job(id=job_id, cmd=list(_RUN_CMD), bus=state.logbus)
    state.jobs[job_id] = job
    state.executor.dispatch(state, job)
    return job


def _log(state: srv.ServeState, worker: str, lines: Any, job_id: str = "j1") -> tuple[Any, int]:
    return ops.worker_log(state, {"worker_id": worker, "job_id": job_id, "lines": lines})


# --- the log route --------------------------------------------------------------------------------


def test_the_lease_holder_publishes_its_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["one\n", "two\n"]) == ({"ok": True}, 200)
    stream = state.logbus.stream("j1", timeout=0.05)
    assert [next(stream), next(stream)] == ["one\n", "two\n"]


def test_a_worker_without_the_lease_gets_409(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    # Not yet leased, then leased by someone else: neither may write into the stream.
    assert _log(state, "w1", ["x\n"])[1] == 409
    assert repo.lease_job("w1") is not None
    assert _log(state, "w2", ["x\n"])[1] == 409
    assert _log(state, "w1", ["x\n"], job_id="missing")[1] == 409
    # A finished job's lease is gone too.
    assert repo.complete_job("j1", {"ok": True}, worker_id="w1")
    assert _log(state, "w1", ["x\n"])[1] == 409


def test_the_log_route_holds_its_caps(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    assert repo.lease_job("w1") is not None
    too_many = ["x\n"] * (WORKER_LOG_MAX_LINES + 1)
    assert _log(state, "w1", too_many)[1] == 413
    assert _log(state, "w1", ["y" * (WORKER_LOG_MAX_LINE_CHARS + 50)])[1] == 200
    line = next(state.logbus.stream("j1", timeout=0.05))
    assert line == "y" * WORKER_LOG_MAX_LINE_CHARS  # the over-long line is cut, the batch kept


def test_the_log_route_masks_a_token_straddling_the_line_cut(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    assert repo.lease_job("w1") is not None
    # Joined at runtime so `make lint-secrets` does not flag the synthetic token in this source.
    token = "ghp_" + "a" * 30
    straddling = "y" * (WORKER_LOG_MAX_LINE_CHARS - 11) + " " + token
    assert _log(state, "w1", [straddling])[1] == 200
    line = next(state.logbus.stream("j1", timeout=0.05))
    assert line is not None and "ghp_" not in line  # cut first, its 6-char prefix would go out


def test_the_log_route_validates_its_body(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    assert ops.worker_log(state, {"job_id": "j1", "lines": []})[1] == 400
    assert ops.worker_log(state, {"worker_id": "w1", "lines": []})[1] == 400
    assert _log(state, "w1", "not a list")[1] == 400
    assert _log(state, "w1", ["ok", 3])[1] == 400
    assert ops.worker_log(srv.ServeState(runs_dir=tmp_path), {"worker_id": "w1"})[1] == 503


def test_holds_lease_reads_without_renewing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.holds_lease("j1", "w1") is False
    assert repo.lease_job("w1") is not None
    assert repo.holds_lease("j1", "w1") is True
    assert repo.holds_lease("j1", "w2") is False


# --- end to end: lines reach the SSE stream live, the result closes it ----------------------------


def test_posted_lines_stream_live_and_the_result_closes_the_stream(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    app = make_app(state)
    worker = TestClient(app)

    # A browser already watching the job before any line exists.
    seen: list[str] = []
    browser = threading.Thread(
        target=lambda: seen.append(TestClient(app).get("/api/jobs/j1/events").text), daemon=True
    )
    browser.start()

    leased = worker.post("/api/worker/lease", json={"worker_id": "w1"})
    assert leased.status_code == 200 and leased.json()["job_id"] == "j1"
    posted = worker.post(
        "/api/worker/log",
        json={"worker_id": "w1", "job_id": "j1", "lines": ["step 1 ok\n", "step 2 ok\n"]},
    )
    assert posted.status_code == 200

    # Live: the lines are out on the stream while the job is still running, before any result.
    frames = ops.job_sse(state, "j1", keepalive=5.0)
    assert frames is not None
    assert next(frames) == "event: log\ndata: step 1 ok\n\n"
    assert next(frames) == "event: log\ndata: step 2 ok\n\n"
    assert repo.get_job("j1")["status"] == "leased"  # type: ignore[index]

    result = {"ok": True, "runId": "20260101-000000"}
    done = worker.post(
        "/api/worker/result", json={"worker_id": "w1", "job_id": "j1", "result": result}
    )
    assert done.status_code == 200

    # The result closed the stream: the generator ends with the worker's terminal view.
    rest = list(frames)
    assert rest[-1].startswith("event: done\n")
    assert '"runId": "20260101-000000"' in rest[-1]
    browser.join(timeout=5.0)
    assert not browser.is_alive()
    assert "data: step 1 ok" in seen[0] and "data: step 2 ok" in seen[0]
    assert "event: done" in seen[0]


def test_a_stale_worker_cannot_post_into_the_next_attempts_stream(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    client = TestClient(make_app(state))
    assert client.post("/api/worker/lease", json={"worker_id": "w1"}).status_code == 200
    stale = client.post(
        "/api/worker/log", json={"worker_id": "w9", "job_id": "j1", "lines": ["x\n"]}
    )
    assert stale.status_code == 409


# --- the bus: fallback to the uploaded console.log ------------------------------------------------


def test_with_no_live_line_the_stream_replays_the_uploaded_log(tmp_path: Path) -> None:
    # An older worker posts no lines, so the stream serves the durable copy as before (BE-0106).
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo, {"r1/console.log": b"line 1\nline 2\n"})
    _queued(state)
    assert repo.lease_job("w1") is not None
    result = {"ok": True, "runId": "r1"}
    assert ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result})[1] == 200
    assert list(state.logbus.stream("j1")) == ["line 1\n", "line 2\n"]


def test_a_job_that_ends_with_no_close_drains_and_ends(tmp_path: Path) -> None:
    # A result posted to another replica (or a reclaim at the attempt cap) closes nothing here; the
    # jobs table ends the stream of a viewer already watching, and the live lines that viewer saw
    # are not repeated from console.log.
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo, {"r1/console.log": b"live\nmore\n"})
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["live\n"])[1] == 200
    stream = state.logbus.stream("j1", timeout=1.0)
    assert next(stream) == "live\n"
    assert _log(state, "w1", ["tail\n"])[1] == 200
    assert repo.complete_job("j1", {"ok": True, "runId": "r1"}, worker_id="w1")
    assert list(stream) == ["tail\n"]
    final = state.logbus.final("j1")
    assert final is not None and json.loads(final)["runId"] == "r1"


def test_an_idle_stream_heartbeats_while_the_job_runs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    stream = state.logbus.stream("j1", timeout=0.02)
    assert next(stream) is None
    stream.close()  # type: ignore[attr-defined]


def test_final_prefers_the_closing_payload_then_the_jobs_table(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    bus = QueueLogBus(repo, srv.InMemoryLogBus(), poll_interval=0.01)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert bus.final("j1") is None
    bus.close("j1", '{"from": "live"}')
    assert bus.final("j1") == '{"from": "live"}'
    repo.enqueue_job("j2", org_id="o", spec={"cmd": []})
    assert repo.cancel_job("j2") == ("cancelled", True)
    final = bus.final("j2")
    assert final is not None and json.loads(final)["cancelled"] is True


# --- which live bus ---------------------------------------------------------------------------------


class _FakeRedis:
    def rpush(self, key: str, *values: str) -> object:
        return 1

    def lrange(self, key: str, start: int, end: int) -> list[object]:
        return []

    def set(self, key: str, value: str) -> object:
        return True

    def get(self, key: str) -> object:
        return None

    def expire(self, key: str, seconds: int) -> object:
        return True


def test_the_live_bus_is_in_memory_without_a_redis_url() -> None:
    assert isinstance(live_log_bus_from_env(), srv.InMemoryLogBus)
    assert isinstance(live_log_bus(None), srv.InMemoryLogBus)


def test_a_redis_url_selects_the_redis_bus() -> None:
    urls: list[str] = []

    def connect(url: str) -> _FakeRedis:
        urls.append(url)
        return _FakeRedis()

    assert isinstance(live_log_bus("redis://cache:6379/0", connect=connect), RedisLogBus)
    assert urls == ["redis://cache:6379/0"]


def test_a_redis_url_without_the_redis_package_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)  # makes `import redis` raise ImportError
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://cache:6379/0")
    with pytest.raises(srv.MissingServerExtra, match="BAJUTSU_REDIS_URL"):
        live_log_bus_from_env()


# --- the bucket-only shape keeps its in-process seams ----------------------------------------------


def test_the_bucket_only_shape_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    # Even a Redis URL does not change it: with no queue there is no worker to post lines.
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://cache:6379/0")
    (tmp_path / "runs").mkdir()
    state = srv._build_state(
        runs_dir=tmp_path / "runs",
        config=None,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.repository is None
    assert type(state.executor) is srv.LocalExecutor
    assert type(state.logbus) is srv.InMemoryLogBus


# --- job kind and the routing executor --------------------------------------------------------------


def test_every_dispatched_command_has_a_kind() -> None:
    # A guard for the routing executor: a builder whose argv stops naming its subcommand the way
    # `Job.kind` reads it (unit 3 changes the `run` argv) would yield None and silently queue.
    from bajutsu.serve.batch_provider import BatchRequest

    commands = [
        run_command("s.yaml", "t"),
        record_command("o.yaml", "t", "goal"),
        crawl_command("t", out="runs/crawl"),
        triage_command("runs/r1"),
    ]
    assert all(srv.Job(cmd=cmd).kind is not None for cmd in commands)
    assert srv.Job(batch=BatchRequest.__new__(BatchRequest)).kind is not None


def test_a_job_kind_follows_its_command() -> None:
    assert srv.Job(cmd=run_command("s.yaml", "t")).kind == "run"
    assert srv.Job(cmd=record_command("o.yaml", "t", "goal")).kind == "record"
    assert srv.Job(cmd=crawl_command("t", out="runs/crawl")).kind == "crawl"
    assert srv.Job(cmd=triage_command("runs/r1")).kind == "triage"
    assert srv.Job(cmd=["echo", "hi"]).kind is None
    assert srv.Job(cmd=[sys.executable, "-m", "bajutsu", "doctor"]).kind is None


def test_a_batch_request_is_a_run_set() -> None:
    from bajutsu.serve.batch_provider import BatchRequest

    request = BatchRequest.__new__(BatchRequest)  # the kind reads only that a request is present
    assert srv.Job(batch=request).kind == "run-set"


class _Recorder:
    def __init__(self) -> None:
        self.jobs: list[srv.Job] = []

    def dispatch(self, state: srv.ServeState, job: srv.Job) -> None:
        self.jobs.append(job)


def test_the_routing_executor_routes_by_kind(tmp_path: Path) -> None:
    queue, local = _Recorder(), _Recorder()
    executor = srv.RoutingExecutor(queue, local, queued_kinds={"run", "triage"})
    state = srv.ServeState(runs_dir=tmp_path)
    jobs = {
        kind: srv.Job(id=kind, cmd=[sys.executable, "-m", "bajutsu", kind])
        for kind in ("run", "record", "crawl", "triage")
    }
    for job in jobs.values():
        executor.dispatch(state, job)
    unknown = srv.Job(id="other", cmd=["echo"])
    executor.dispatch(state, unknown)
    # An unrecognized command fails closed: it is queued, never run in the server's process.
    assert [j.id for j in queue.jobs] == ["run", "triage", "other"]
    assert [j.id for j in local.jobs] == ["record", "crawl"]
    assert executor.queued_kinds == frozenset({"run", "triage"})


def test_runs_in_process_reads_the_route_not_the_type() -> None:
    from bajutsu.serve.executor import runs_in_process

    routed = srv.RoutingExecutor(_Recorder(), srv.LocalExecutor(), queued_kinds={"record"})
    assert runs_in_process(routed, "run") is True
    assert runs_in_process(routed, "record") is False
    assert runs_in_process(srv.LocalExecutor(), "run") is True
    assert runs_in_process(_Recorder(), "run") is False


# --- the worker side: batched, flushed at the end, best-effort ---------------------------------------


class _Posts:
    """Records each batch the streamer posts and answers with the next scripted status."""

    def __init__(self, *codes: int | None) -> None:
        self.batches: list[list[str]] = []
        self._codes = list(codes)

    def __call__(self, lines: list[str]) -> int | None:
        self.batches.append(list(lines))
        return self._codes.pop(0) if self._codes else 200


def test_the_streamer_batches_within_the_server_cap() -> None:
    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    for i in range(WORKER_LOG_MAX_LINES * 2 + 3):
        streamer.add(f"line {i}\n")
    streamer.flush()
    assert [len(b) for b in posts.batches] == [WORKER_LOG_MAX_LINES, WORKER_LOG_MAX_LINES, 3]
    assert posts.batches[0][0] == "line 0\n" and posts.batches[-1][-1] == "line 1002\n"
    streamer.flush()  # nothing new: no empty post
    assert len(posts.batches) == 3


def test_the_streamer_masks_and_cuts_lines_like_the_stored_log() -> None:
    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    streamer.add("key sk-ant-abcdefghijklmnopqrstuv\n")
    streamer.add("z" * (WORKER_LOG_MAX_LINE_CHARS + 10))
    streamer.flush()
    assert "sk-ant-abcdefghijklmnopqrstuv" not in posts.batches[0][0]
    assert len(posts.batches[0][1]) == WORKER_LOG_MAX_LINE_CHARS


def test_a_409_marks_the_lease_lost_and_stops_posting() -> None:
    posts = _Posts(409)
    streamer = worker_mod._LogStreamer(posts)
    for i in range(WORKER_LOG_MAX_LINES + 1):
        streamer.add(f"{i}\n")
    streamer.flush()
    assert streamer.lost is True
    assert len(posts.batches) == 1  # the second batch is never sent
    streamer.add("later\n")
    streamer.flush()
    assert len(posts.batches) == 1


def test_a_failed_post_drops_its_lines_and_the_next_flush_carries_on() -> None:
    posts = _Posts(None)
    streamer = worker_mod._LogStreamer(posts)
    streamer.add("lost\n")
    streamer.flush()
    streamer.add("kept\n")
    streamer.flush()
    assert posts.batches == [["lost\n"], ["kept\n"]]
    assert streamer.lost is False


def test_the_background_flusher_posts_on_its_interval() -> None:
    posted = threading.Event()

    def post(lines: list[str]) -> int:
        posted.set()
        return 200

    streamer = worker_mod._LogStreamer(post)
    streamer.add("tick\n")
    streamer.start(0.01)
    assert posted.wait(5.0)
    streamer.stop(flush=False)


def test_post_log_reports_the_status_and_survives_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, body: dict[str, Any], **_kw: Any) -> tuple[int, Any]:
        calls.append((url, body))
        return 503, {}

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    assert worker_mod._post_log("http://cp", "w1", "j1", None, ["a\n"]) == 503
    assert calls == [
        ("http://cp/api/worker/log", {"worker_id": "w1", "job_id": "j1", "lines": ["a\n"]})
    ]

    def broken(*_a: Any, **_k: Any) -> tuple[int, Any]:
        raise URLError("down")

    monkeypatch.setattr(worker_mod, "_post_json", broken)
    assert worker_mod._post_log("http://cp", "w1", "j1", None, ["a\n"]) is None


class _PrintingJob:
    def view(self) -> dict[str, Any]:
        return {"ok": True, "runId": "r1", "lines": []}


def _run_streaming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, posts: _Posts
) -> tuple[tuple[dict[str, Any] | None, bool, Path], worker_mod._TeeLogBus]:
    streamer = worker_mod._LogStreamer(posts)
    bus = worker_mod._TeeLogBus(streamer.add)

    def execute(*_a: Any, **_k: Any) -> _PrintingJob:
        bus.publish("j1", "step 1\n")
        bus.publish("j1", "step 2\n")
        bus.close("j1")
        return _PrintingJob()

    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)
    monkeypatch.setattr(worker_mod, "_post_json", lambda *a, **k: (200, {}))  # keep hermetic
    out = worker_mod._run_with_heartbeat(
        {"cmd": "run"},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=bus,
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=5.0,
        streamer=streamer,
        log_flush_interval=60.0,  # no timed flush in the test: only the final one posts
    )
    return out, bus


def test_the_final_flush_posts_the_tail_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posts = _Posts()
    (result, abandoned, _work), bus = _run_streaming(monkeypatch, tmp_path, posts)
    assert abandoned is False
    assert result == {"ok": True, "runId": "r1"}
    assert posts.batches == [["step 1\n", "step 2\n"]]
    # The local buffer that becomes console.log keeps every line too.
    assert list(bus.stream("j1", timeout=0.0)) == ["step 1\n", "step 2\n"]


def test_a_failing_log_post_never_fails_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posts = _Posts(None)
    (result, abandoned, _work), _ = _run_streaming(monkeypatch, tmp_path, posts)
    assert abandoned is False
    assert result == {"ok": True, "runId": "r1"}


def test_a_409_on_the_log_route_abandons_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posts = _Posts(409)
    (_result, abandoned, _work), _ = _run_streaming(monkeypatch, tmp_path, posts)
    assert abandoned is True


def test_a_lost_log_lease_stops_the_heartbeat_loop(tmp_path: Path) -> None:
    release = threading.Event()
    runner = threading.Thread(target=release.wait, args=(5.0,), daemon=True)
    runner.start()
    streamer = worker_mod._LogStreamer(_Posts(409))
    streamer.add("x\n")
    streamer.flush()
    lost = threading.Event()

    def beat() -> int:
        release.set()
        return 200

    assert worker_mod._beat_until_done(
        runner, beat=beat, interval=0.01, lost=lost, streamer=streamer
    )
    assert lost.is_set()


# --- review follow-ups: masking across lines, bounded storage, fallbacks ---------------------------

# The armour is joined at runtime so the source never holds a contiguous PEM block: gitleaks'
# private-key rule (`make lint-secrets`) would otherwise flag this synthetic fixture.
_KEY = "RSA PRIVATE " + "KEY-----\n"
_PEM = [
    "-----BEGIN " + _KEY,
    "MIIEowIBAAKCAQEAbody1\n",
    "c2VjcmV0Ym9keTI=\n",
    "c2VjcmV0Ym9keTM=\n",
    "-----END " + _KEY,
]


def test_a_private_key_streamed_across_two_batches_never_posts_its_body() -> None:
    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    streamer.add("before\n")
    for line in _PEM[:3]:
        streamer.add(line)
    streamer.flush()
    for line in _PEM[3:]:
        streamer.add(line)
    streamer.add("after\n")
    streamer.flush()
    sent = "".join(line for batch in posts.batches for line in batch)
    for secret in ("MIIEowIBAAKCAQEAbody1", "c2VjcmV0Ym9keTI=", "c2VjcmV0Ym9keTM=", "BEGIN RSA"):
        assert secret not in sent
    assert posts.batches[0][0] == "before\n" and posts.batches[-1][-1] == "after\n"
    assert all(line.endswith("\n") for batch in posts.batches for line in batch)


def test_the_line_masker_bounds_an_unterminated_block() -> None:
    from bajutsu.common.evidence.redaction import PEM_MAX_LINES, PLACEHOLDER, LineCredentialMasker

    masker = LineCredentialMasker()
    masker.mask(_PEM[0])
    hidden = [masker.mask("body\n") for _ in range(PEM_MAX_LINES - 1)]
    assert set(hidden) == {PLACEHOLDER + "\n"}
    assert masker.mask("plain log line\n") == "plain log line\n"  # past the bound: not a key
    # A key whose BEGIN and END share one line is masked by the plain backstop, with no state left.
    one_line = "-----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----\n"
    assert "abc" not in masker.mask(one_line)
    assert masker.mask("next\n") == "next\n"


def test_the_server_masks_posted_lines_too(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["token ghp_abcdefghijklmnopqrstuvwxyz0123\n"])[1] == 200
    line = next(state.logbus.stream("j1", timeout=0.05))
    assert line is not None and "ghp_abcdefghijklmnopqrstuvwxyz0123" not in line


def test_a_job_past_its_live_budget_publishes_one_notice(tmp_path: Path) -> None:
    from bajutsu.serve.operations.worker import LIVE_LOG_TRUNCATED

    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    state.live_log_budget = srv.LiveLineBudget(per_job=3)
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["1\n", "2\n"])[1] == 200
    assert _log(state, "w1", ["3\n", "4\n"])[1] == 200
    assert _log(state, "w1", ["5\n"])[1] == 200
    state.logbus.close("j1")
    assert list(state.logbus.stream("j1")) == ["1\n", "2\n", "3\n", LIVE_LOG_TRUNCATED]


def test_the_live_budget_forgets_a_finished_job() -> None:
    budget = srv.LiveLineBudget(per_job=2)
    assert budget.admit("j1", ["a", "b", "c"]) == (2, True)
    assert budget.admit("j1", ["d"]) == (0, False)  # the notice goes out once
    budget.forget("j1")
    assert budget.admit("j1", ["e"]) == (1, False)


def test_the_live_budget_caps_bytes_too() -> None:
    budget = srv.LiveLineBudget(bytes_per_job=10)
    assert budget.admit("j1", ["12345", "1234"]) == (2, False)
    assert budget.admit("j1", ["12", "x"]) == (0, True)  # 9 + 2 would pass 10 bytes
    assert budget.admit("j2", ["é" * 6]) == (0, True)  # bytes, not characters: 12 > 10


def test_a_job_past_its_byte_budget_publishes_the_notice(tmp_path: Path) -> None:
    from bajutsu.serve.operations.worker import LIVE_LOG_TRUNCATED

    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    state.live_log_budget = srv.LiveLineBudget(bytes_per_job=8)
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["abc\n", "defg\n"])[1] == 200
    state.logbus.close("j1")
    assert list(state.logbus.stream("j1")) == ["abc\n", LIVE_LOG_TRUNCATED]


def test_a_channel_that_never_closes_is_still_evicted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    live = srv.InMemoryLogBus()
    bus = QueueLogBus(repo, live, poll_interval=0.01, keep_closed=1)
    bus.publish_many("orphan", ["lost worker\n"])  # its result never reaches this process
    bus.publish_many("next", ["x\n"])
    live.close("orphan")  # a fresh channel: the old one, with its line, is gone
    assert list(live.stream("orphan", timeout=0.0)) == []
    bus.close("next", "{}")
    assert live.final("next") == "{}"


def test_a_running_channel_outlives_a_finished_one_past_the_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    live = srv.InMemoryLogBus()
    bus = QueueLogBus(repo, live, poll_interval=0.01, keep_closed=2)
    bus.publish_many("running", ["still going\n"])  # oldest in the order, and not finished
    for job in ("done", "later"):
        bus.publish_many(job, ["x\n"])
        bus.close(job, "{}")
    # Over the bound at "later": the oldest *finished* job goes, not the older running one.
    assert live.final("done") is None
    assert live.final("later") == "{}"
    live.close("running")
    assert list(live.stream("running", timeout=0.0)) == ["still going\n"]


def test_finished_channels_are_discarded_past_the_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    live = srv.InMemoryLogBus()
    bus = QueueLogBus(repo, live, poll_interval=0.01, keep_closed=1)
    bus.publish("j1", "one\n")
    bus.close("j1", "{}")
    assert live.final("j1") == "{}"
    bus.publish("j2", "two\n")
    bus.close("j2", "{}")
    assert live.final("j1") is None  # j1's channel went when j2 finished
    assert live.final("j2") == "{}"


def test_in_memory_discard_forgets_a_channel() -> None:
    bus = srv.InMemoryLogBus()
    bus.publish_many("j1", ["a\n", "b\n"])
    bus.close("j1", "done")
    bus.discard("j1")
    assert bus.final("j1") is None
    bus.discard("missing")  # no error


def test_a_late_viewer_reads_console_log_not_the_live_backlog(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = _state(tmp_path, repo, {"r1/console.log": b"full 1\nfull 2\nfull 3\n"})
    _queued(state)
    assert repo.lease_job("w1") is not None
    assert _log(state, "w1", ["full 1\n", "full 3\n"])[1] == 200  # a batch went missing
    result = {"ok": True, "runId": "r1"}
    assert ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result})[1] == 200
    assert list(state.logbus.stream("j1")) == ["full 1\n", "full 2\n", "full 3\n"]


class _BrokenLive(srv.InMemoryLogBus):
    def stream(self, job_id: str, *, timeout: float | None = None) -> Any:
        raise ConnectionError("redis down")

    def final(self, job_id: str) -> str | None:
        raise ConnectionError("redis down")

    def close(self, job_id: str, final: str | None = None) -> None:
        raise ConnectionError("redis down")


def test_a_failing_live_bus_falls_back_to_the_jobs_table(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    files = {"r1/console.log": b"stored\n"}
    bus = QueueLogBus(repo, _BrokenLive(), lambda _org: _Artifacts(files), poll_interval=0.01)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    stream = bus.stream("j1", timeout=0.02)
    assert next(stream) is None  # the post-completion heartbeat while the job runs
    assert repo.complete_job("j1", {"ok": True, "runId": "r1"}, worker_id="w1")
    assert list(stream)[-1] == "stored\n"
    bus.close("j1", '{"ok": true}')  # does not raise
    final = bus.final("j1")
    assert final is not None and json.loads(final)["ok"] is True


def test_an_older_control_plane_turns_the_streamer_off(caplog: pytest.LogCaptureFixture) -> None:
    posts = _Posts(404)
    streamer = worker_mod._LogStreamer(posts)
    streamer.add("a\n")
    with caplog.at_level("WARNING", logger="bajutsu.worker"):
        streamer.flush()
    streamer.add("b\n")
    streamer.flush()
    assert posts.batches == [["a\n"]]
    assert streamer.lost is False
    assert sum("no worker log route" in r.getMessage() for r in caplog.records) == 1


def test_the_pending_queue_keeps_the_newest_lines() -> None:
    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts, max_pending=3)
    for i in range(5):
        streamer.add(f"{i}\n")
    assert streamer.dropped == 2
    streamer.flush()
    assert posts.batches == [["2\n", "3\n", "4\n"]]


def test_the_final_flush_stops_at_its_deadline() -> None:
    now = [0.0]
    sent: list[int] = []

    def post(lines: list[str]) -> int:
        sent.append(len(lines))
        now[0] += 3.0  # each batch takes three seconds on this clock
        return 200

    streamer = worker_mod._LogStreamer(post, clock=lambda: now[0])
    for i in range(WORKER_LOG_MAX_LINES * 3):
        streamer.add(f"{i}\n")
    streamer.stop(flush=True, budget=5.0)
    assert sent == [WORKER_LOG_MAX_LINES, WORKER_LOG_MAX_LINES]  # the third batch is past 5 s


def test_post_log_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> tuple[int, Any]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worker_mod, "_post_json", boom)
    assert worker_mod._post_log("http://cp", "w1", "j1", None, ["a\n"]) is None


def test_the_streamer_stops_even_when_the_heartbeat_loop_raises() -> None:
    release = threading.Event()
    runner = threading.Thread(target=release.wait, args=(5.0,), daemon=True)
    runner.start()
    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    streamer.add("unsent\n")

    def beat() -> int:
        release.set()
        raise RuntimeError("beat blew up")

    with pytest.raises(RuntimeError):
        worker_mod._beat_and_stream(
            runner,
            beat=beat,
            interval=0.01,
            lost=threading.Event(),
            streamer=streamer,
            log_flush_interval=60.0,
        )
    assert streamer._thread is not None and not streamer._thread.is_alive()
    assert posts.batches == []  # no tail is posted for a loop that failed


def test_handoff_requests_stay_out_of_the_live_stream() -> None:
    from bajutsu.common.handoff import REQUEST_LINE_PREFIX

    queued: list[str] = []
    bus = worker_mod._TeeLogBus(queued.append)
    bus.publish("j1", "step\n")
    bus.publish("j1", REQUEST_LINE_PREFIX + '{"prompt": "otp?"}\n')
    bus.close("j1")
    assert queued == ["step\n"]
    assert len(list(bus.stream("j1", timeout=0.0))) == 2  # console.log still records it


def test_a_batched_publish_reaches_the_live_stream_too() -> None:
    from bajutsu.common.handoff import REQUEST_LINE_PREFIX

    queued: list[str] = []
    bus = worker_mod._TeeLogBus(queued.append)
    bus.publish_many("j1", ["a\n", REQUEST_LINE_PREFIX + "{}\n", "b\n"])
    bus.close("j1")
    assert queued == ["a\n", "b\n"]
    assert len(list(bus.stream("j1", timeout=0.0))) == 3


def test_a_background_flush_yields_its_backlog_to_stop() -> None:
    # `stop` must not wait on a long backlog in the background flush; the rest goes back to the
    # queue and the final flush posts it within its budget.
    streamer: worker_mod._LogStreamer
    posts: list[list[str]] = []

    def post(lines: list[str]) -> int:
        posts.append(list(lines))
        streamer._stop.set()  # `stop` arrives while the first batch is in flight
        return 200

    streamer = worker_mod._LogStreamer(post)
    for i in range(WORKER_LOG_MAX_LINES * 2 + 1):
        streamer.add(f"{i}\n")
    streamer.flush()  # a background flush: no deadline
    assert [len(b) for b in posts] == [WORKER_LOG_MAX_LINES]
    streamer.stop(flush=True)
    assert [len(b) for b in posts] == [WORKER_LOG_MAX_LINES, WORKER_LOG_MAX_LINES, 1]
    assert posts[1][0] == f"{WORKER_LOG_MAX_LINES}\n"  # order kept across the hand-back


class _UploadIO:
    """A `WorkerIO` whose upload checks that console.log is already in the run tree."""

    def __init__(self) -> None:
        self.saw_console_log: bool | None = None

    def download_baselines(self, work: Path) -> None:
        pass

    def upload_run(self, work: Path, run_id: str) -> None:
        self.saw_console_log = (work / "runs" / run_id / "console.log").is_file()

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        pass


def test_console_log_is_written_before_the_run_tree_uploads(tmp_path: Path) -> None:
    import functools

    from _shared import FakeProc

    from bajutsu.serve.server.worker_job import execute_job_spec

    def popen_writing_run(_cmd: list[str], **_kw: object) -> FakeProc:
        (tmp_path / "runs" / "20260610-1").mkdir(parents=True, exist_ok=True)
        return FakeProc(["step ok\n", "PASS  runs/20260610-1/manifest.json\n"])

    bus = srv.InMemoryLogBus()
    io = _UploadIO()
    spec = {"job_id": "1", "cmd": ["bajutsu", "run"], "udids": [], "app_path": None, "build": None}
    execute_job_spec(
        spec,
        popen=popen_writing_run,
        cwd=tmp_path,
        bus=bus,
        io=io,
        before_upload=functools.partial(worker_mod._write_job_console_log, tmp_path, bus),
    )
    assert io.saw_console_log is True
    log = (tmp_path / "runs" / "20260610-1" / "console.log").read_text(encoding="utf-8")
    assert "step ok" in log


def test_the_worker_hands_execute_job_spec_its_console_log_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    bus = srv.InMemoryLogBus()

    def execute(*_a: Any, **kwargs: Any) -> _PrintingJob:
        bus.publish("j1", "line\n")
        bus.close("j1")
        kwargs["before_upload"](srv.Job(id="j1", run_id="r1"))
        kwargs["before_upload"](srv.Job(id="j1"))  # no run: nothing to write
        return _PrintingJob()

    monkeypatch.setattr(worker_mod, "execute_job_spec", execute)
    monkeypatch.setattr(worker_mod, "_post_json", lambda *a, **k: (200, {}))
    worker_mod._run_with_heartbeat(
        {"cmd": "run"},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=bus,
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=5.0,
    )
    assert (tmp_path / "runs" / "r1" / "console.log").read_text(encoding="utf-8") == "line\n"


# --- review follow-ups: body caps, a closed budget, a live bus failing mid-stream -----------------


def test_the_live_budget_stays_closed_once_the_byte_cap_truncates() -> None:
    budget = srv.LiveLineBudget(bytes_per_job=10)
    assert budget.admit("j1", ["123456789"]) == (1, False)
    assert budget.admit("j1", ["12"]) == (0, True)  # 9 + 2 > 10: the notice goes out
    assert budget.admit("j1", ["1"]) == (0, False)  # would fit, but follows the notice


class _FailingMidStream(srv.InMemoryLogBus):
    def stream(self, job_id: str, *, timeout: float | None = None) -> Any:
        yield "live 1\n"
        raise ConnectionError("redis down")


def test_a_live_bus_failing_mid_stream_does_not_replay_console_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    files = {"r1/console.log": b"live 1\nstored 2\n"}
    bus = QueueLogBus(repo, _FailingMidStream(), lambda _org: _Artifacts(files), poll_interval=0.01)
    repo.enqueue_job("j1", org_id="o", spec={"cmd": []})
    assert repo.lease_job("w1") is not None
    stream = bus.stream("j1", timeout=0.02)
    assert next(stream) == "live 1\n"
    assert next(stream) is None  # waiting for the job to finish, heartbeating
    assert repo.complete_job("j1", {"ok": True, "runId": "r1"}, worker_id="w1")
    assert [line for line in stream if line is not None] == []  # no duplicate of "live 1"


def test_the_streamer_splits_a_batch_by_its_encoded_size() -> None:
    from bajutsu.serve.operations.worker import WORKER_LOG_MAX_BODY_BYTES

    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    # One emoji escapes to 12 bytes of JSON, so 500 such lines would be ~49 MiB in one post.
    lines = [f"{i:04d}" + "\U0001f600" * (WORKER_LOG_MAX_LINE_CHARS - 4) for i in range(200)]
    for line in lines:
        streamer.add(line)
    streamer.flush()
    assert len(posts.batches) > 1
    assert [line for batch in posts.batches for line in batch] == lines
    for batch in posts.batches:
        body = json.dumps({"worker_id": "w" * 64, "job_id": "j" * 64, "lines": batch})
        assert len(body) <= WORKER_LOG_MAX_BODY_BYTES


def test_a_full_batch_of_maximal_plain_lines_fits_one_post() -> None:
    from bajutsu.serve.operations.worker import WORKER_LOG_MAX_BODY_BYTES

    posts = _Posts()
    streamer = worker_mod._LogStreamer(posts)
    for _ in range(WORKER_LOG_MAX_LINES):
        streamer.add("x" * WORKER_LOG_MAX_LINE_CHARS)
    streamer.flush()
    assert [len(b) for b in posts.batches] == [WORKER_LOG_MAX_LINES]
    body = json.dumps({"worker_id": "w1", "job_id": "j1", "lines": posts.batches[0]})
    assert len(body) <= WORKER_LOG_MAX_BODY_BYTES


def test_the_routes_declare_their_body_caps() -> None:
    from bajutsu.serve.operations.worker import WORKER_LOG_MAX_BODY_BYTES
    from bajutsu.serve.routes import MAX_JSON_BODY_BYTES, ROUTES, json_body_cap

    assert json_body_cap(ROUTES, "/api/worker/log") == WORKER_LOG_MAX_BODY_BYTES
    assert json_body_cap(ROUTES, "/api/worker/result") == MAX_JSON_BODY_BYTES
    assert json_body_cap(ROUTES, "/api/login") == MAX_JSON_BODY_BYTES  # bespoke: the global cap
    assert WORKER_LOG_MAX_BODY_BYTES < MAX_JSON_BODY_BYTES


def test_fastapi_refuses_an_oversized_log_body_by_length_and_while_streaming(
    tmp_path: Path,
) -> None:
    from bajutsu.serve.operations.worker import WORKER_LOG_MAX_BODY_BYTES

    repo = _repo(tmp_path)
    state = _state(tmp_path, repo)
    _queued(state)
    client = TestClient(make_app(state))
    assert client.post("/api/worker/lease", json={"worker_id": "w1"}).status_code == 200
    huge = json.dumps(
        {"worker_id": "w1", "job_id": "j1", "lines": ["x" * (WORKER_LOG_MAX_BODY_BYTES + 1)]}
    ).encode()
    assert client.post("/api/worker/log", content=huge).status_code == 413

    def chunked() -> Any:  # no Content-Length: only the streaming count can stop it
        for start in range(0, len(huge), 65536):
            yield huge[start : start + 65536]

    assert client.post("/api/worker/log", content=chunked()).status_code == 413
    ok = client.post("/api/worker/log", json={"worker_id": "w1", "job_id": "j1", "lines": ["a\n"]})
    assert ok.status_code == 200


def test_fastapi_login_body_is_capped_and_parsed_like_stdlib(tmp_path: Path) -> None:
    from bajutsu.serve.routes import MAX_JSON_BODY_BYTES

    client = TestClient(make_app(srv.ServeState(runs_dir=tmp_path / "runs")))
    too_big = client.post("/api/login", content=b" " * (MAX_JSON_BODY_BYTES + 1))
    assert too_big.status_code == 413
    assert client.post("/api/login", content=b"{not json").status_code == 400
    assert client.post("/api/login", content=b"[]").status_code == 400


def test_stdlib_refuses_an_oversized_body_before_reading_it(tmp_path: Path) -> None:
    import http.client

    from _shared import _serve

    from bajutsu.serve.operations.worker import WORKER_LOG_MAX_BODY_BYTES
    from bajutsu.serve.routes import MAX_JSON_BODY_BYTES

    server, port = _serve(srv.ServeState(runs_dir=tmp_path / "runs"))
    try:
        for path, cap in (
            ("/api/worker/log", WORKER_LOG_MAX_BODY_BYTES),
            ("/api/login", MAX_JSON_BODY_BYTES),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            # Declare a body past the cap and send none of it: a server that read before checking
            # would block here until the timeout instead of answering.
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(cap + 1))
            conn.endheaders()
            resp = conn.getresponse()
            assert resp.status == 413
            conn.close()
    finally:
        server.shutdown()
        server.server_close()

"""Tests for `bajutsu worker`'s helpers and its polling loop (BE-0117, BE-0106).

`_post_json` is driven against a real loopback HTTP server rather than a mocked `urlopen`, so the
actual urllib request/response path is exercised. The `worker()` loop test substitutes only the
orchestration boundaries the checklist names (`_post_json` and `_run_with_heartbeat`) — the HTTP
client and the job executor — so one iteration runs without a live control plane or a real job.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
import typer

from bajutsu.cli.commands import worker as worker_mod
from bajutsu.cli.commands.worker import _object_store, _post_json, _write_console_log, worker
from bajutsu.serve import InMemoryLogBus


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # BaseHTTPRequestHandler's required method name
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        self.server.last_request = {  # type: ignore[attr-defined]
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(raw) if raw else None,
        }
        status, payload = self.server.routes[self.path]  # type: ignore[attr-defined]
        self.send_response(status)
        if payload is None:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps(payload).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:  # silence the default stderr access log
        pass


@contextmanager
def _server(routes: dict[str, tuple[int, Any]]) -> Iterator[tuple[ThreadingHTTPServer, str]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.routes = routes  # type: ignore[attr-defined]
    httpd.last_request = None  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _host, port = httpd.server_address
        yield httpd, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join()


# --- _post_json ---------------------------------------------------------------------------------


def test_post_json_success_with_body() -> None:
    with _server({"/x": (200, {"hello": "world"})}) as (httpd, base):
        code, body = _post_json(f"{base}/x", {"a": 1})
        assert code == 200
        assert body == {"hello": "world"}
        assert httpd.last_request["body"] == {"a": 1}  # type: ignore[attr-defined]


def test_post_json_success_empty_body() -> None:
    with _server({"/x": (200, None)}) as (_httpd, base):
        code, body = _post_json(f"{base}/x", {})
        assert code == 200
        assert body == {}


def test_post_json_sends_bearer_token() -> None:
    with _server({"/x": (200, {})}) as (httpd, base):
        _post_json(f"{base}/x", {}, token="secret")
        assert httpd.last_request["authorization"] == "Bearer secret"  # type: ignore[attr-defined]


def test_post_json_http_error_with_body() -> None:
    with _server({"/x": (400, {"error": "bad request"})}) as (_httpd, base):
        code, body = _post_json(f"{base}/x", {})
        assert code == 400
        assert body == {"error": "bad request"}


def test_post_json_http_error_empty_body() -> None:
    with _server({"/x": (503, None)}) as (_httpd, base):
        code, body = _post_json(f"{base}/x", {})
        assert code == 503
        assert body == {}


# --- _object_store ------------------------------------------------------------------------------


def test_object_store_none_without_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    # Import succeeds; with no BAJUTSU_S3_BUCKET configured the store is None.
    monkeypatch.delenv("BAJUTSU_S3_BUCKET", raising=False)
    assert _object_store() is None


def test_object_store_built_when_bucket_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    store = _object_store()
    assert store is not None


def test_object_store_import_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the optional object-store dependency being absent: importing the module raises
    # ImportError, and the guard must swallow it and return None.
    import sys

    monkeypatch.setitem(sys.modules, "bajutsu.serve.server.object_store", None)
    assert _object_store() is None


# --- _write_console_log -------------------------------------------------------------------------


def test_write_console_log_no_run_dir(tmp_path: Path) -> None:
    bus = InMemoryLogBus()
    bus.publish("j1", "line\n")
    bus.close("j1")
    _write_console_log(tmp_path, "missing-run", bus, "j1")  # run dir absent → no-op
    assert not (tmp_path / "runs" / "missing-run" / "console.log").exists()


def test_write_console_log_empty_lines(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    run_dir.mkdir(parents=True)
    bus = InMemoryLogBus()
    bus.close("j1")  # closed with no buffered lines → no file written
    _write_console_log(tmp_path, "run1", bus, "j1")
    assert not (run_dir / "console.log").exists()


def test_write_console_log_writes_joined_lines(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run1"
    run_dir.mkdir(parents=True)
    bus = InMemoryLogBus()
    bus.publish("j1", "first\n")
    bus.publish("j1", "second\n")
    bus.close("j1")  # close so stream() terminates
    _write_console_log(tmp_path, "run1", bus, "j1")
    assert (run_dir / "console.log").read_text(encoding="utf-8") == "first\nsecond\n"


# --- worker() polling loop ----------------------------------------------------------------------


class _StopLoop(Exception):
    """Sentinel raised to break out of worker()'s `while True` after the asserted step."""


class _FakeJob:
    """Stand-in for execute_job_spec's return: view() carries the result the worker posts back."""

    def view(self) -> dict[str, Any]:
        return {"ok": True, "runId": "r1", "lines": ["log\n"]}


def test_worker_rejects_non_positive_intervals() -> None:
    with pytest.raises(typer.BadParameter):
        worker(server_url="http://cp", poll_interval=0, heartbeat_interval=1)
    with pytest.raises(typer.BadParameter):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=0)


def test_worker_runs_one_iteration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # execute_job_spec is mocked (per the BE-0117 design), so _run_with_heartbeat runs for real;
    # the fast fake job finishes before the first heartbeat, so no heartbeat is sent.
    monkeypatch.chdir(tmp_path)  # worker uses cwd for the run tree; keep runs/ isolated
    calls: list[tuple[str, dict[str, Any]]] = []
    leases = 0

    def fake_post(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        nonlocal leases
        calls.append((url, body))
        if url.endswith("/lease"):
            leases += 1
            if leases == 1:
                return 200, {"job_id": "j1", "spec": {"cmd": "run"}}
            raise _StopLoop  # completed one job, polled again — stop
        return 200, {}

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    monkeypatch.setattr(worker_mod, "execute_job_spec", lambda *a, **k: _FakeJob())

    with pytest.raises(_StopLoop):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=5, worker_id="w1")

    lease = [c for c in calls if c[0].endswith("/lease")]
    result = [c for c in calls if c[0].endswith("/result")]
    assert lease[0][1] == {"worker_id": "w1"}
    assert leases == 2
    # The result posted back is the job view with its bulky `lines` stripped.
    assert result and result[0][1] == {
        "job_id": "j1",
        "result": {"ok": True, "runId": "r1"},
        "worker_id": "w1",
    }


def test_worker_logs_result_post_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    leases = 0

    def fake_post(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        nonlocal leases
        if url.endswith("/lease"):
            leases += 1
            if leases == 1:
                return 200, {"job_id": "j1", "spec": {"cmd": "run"}}
            raise _StopLoop
        if url.endswith("/result"):
            raise URLError("control plane down")  # swallowed; the worker keeps going
        return 200, {}

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    monkeypatch.setattr(worker_mod, "execute_job_spec", lambda *a, **k: _FakeJob())

    with pytest.raises(_StopLoop):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=5, worker_id="w1")
    assert leases == 2


def test_worker_idle_polls_when_no_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod, "_post_json", lambda *a, **k: (204, {}))

    def stop_sleep(_seconds: float) -> None:
        raise _StopLoop

    monkeypatch.setattr(worker_mod.time, "sleep", stop_sleep)
    with pytest.raises(_StopLoop):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=1)


def test_worker_retries_after_lease_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_post(*a: Any, **k: Any) -> tuple[int, Any]:
        raise URLError("connection refused")

    monkeypatch.setattr(worker_mod, "_post_json", failing_post)

    def stop_sleep(_seconds: float) -> None:
        raise _StopLoop

    monkeypatch.setattr(worker_mod.time, "sleep", stop_sleep)
    with pytest.raises(_StopLoop):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=1)


def test_worker_abandons_reclaimed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    leases = 0

    def fake_post(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        nonlocal leases
        if url.endswith("/lease"):
            leases += 1
            if leases == 1:
                return 200, {"job_id": "j1", "spec": {"cmd": "run"}}
            raise _StopLoop  # second poll — stop after the abandon path ran once
        return 200, {}

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    # abandoned=True → worker drops the result and continues to the next lease poll.
    monkeypatch.setattr(worker_mod, "_run_with_heartbeat", lambda *a, **k: ({}, True))

    with pytest.raises(_StopLoop):
        worker(server_url="http://cp", poll_interval=1, heartbeat_interval=1)
    assert leases == 2


# --- _run_with_heartbeat ------------------------------------------------------------------------


def _run_hb(tmp_path: Path) -> tuple[dict[str, Any], bool]:
    return worker_mod._run_with_heartbeat(
        {"cmd": "run"},
        job_id="j1",
        work=tmp_path,
        bus=InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=0.02,
    )


def test_run_with_heartbeat_normal_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(worker_mod, "execute_job_spec", lambda *a, **k: _FakeJob())
    result, abandoned = _run_hb(tmp_path)
    assert abandoned is False
    assert result == {"ok": True, "runId": "r1"}  # `lines` popped before return


def test_run_with_heartbeat_job_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("job blew up")

    monkeypatch.setattr(worker_mod, "execute_job_spec", boom)
    result, abandoned = _run_hb(tmp_path)
    assert abandoned is False
    assert result["ok"] is False
    assert "job blew up" in result["error"]


def test_run_with_heartbeat_reclaimed_on_409(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = threading.Event()

    def slow_exec(*a: Any, **k: Any) -> _FakeJob:
        release.wait(2.0)  # stay alive until the heartbeat fires
        return _FakeJob()

    monkeypatch.setattr(worker_mod, "execute_job_spec", slow_exec)

    def hb_409(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        release.set()  # let the job finish so the follow-up join() returns
        return 409, {}  # the control plane reclaimed the lease

    monkeypatch.setattr(worker_mod, "_post_json", hb_409)
    _result, abandoned = _run_hb(tmp_path)
    assert abandoned is True


def test_run_with_heartbeat_survives_heartbeat_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = threading.Event()

    def slow_exec(*a: Any, **k: Any) -> _FakeJob:
        release.wait(2.0)
        return _FakeJob()

    monkeypatch.setattr(worker_mod, "execute_job_spec", slow_exec)

    def hb_error(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        release.set()
        raise URLError("heartbeat dropped")  # logged and retried, not fatal

    monkeypatch.setattr(worker_mod, "_post_json", hb_error)
    _result, abandoned = _run_hb(tmp_path)
    assert abandoned is False

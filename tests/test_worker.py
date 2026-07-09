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
from urllib.error import HTTPError, URLError

import pytest
import typer

from bajutsu.cli.commands import worker as worker_mod
from bajutsu.cli.commands.worker import (
    PresignedWorkerIO,
    _advertised_capabilities,
    _download_baselines,
    _evidence_files,
    _post_json,
    _put_tree_files,
    _upload_evidence,
    _write_console_log,
    worker,
)
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.capabilities import WORKER_CAPABILITIES_ENV


@pytest.fixture(autouse=True)
def _stub_advertised_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the worker's startup capability derivation hermetic (BE-0166): stub it to a fixed set so
    no test shells out to real `simctl`. The derivation itself is covered by test_capabilities.py; a
    test that cares about flag wiring overrides this with its own recorder."""
    monkeypatch.setattr(worker_mod, "_advertised_capabilities", lambda *a, **k: ["platform:ios"])


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
    assert lease[0][1] == {"worker_id": "w1", "capabilities": ["platform:ios"]}
    assert leases == 2
    # The result posted back is the job view with its bulky `lines` stripped.
    assert result and result[0][1] == {
        "job_id": "j1",
        "result": {"ok": True, "runId": "r1"},
        "worker_id": "w1",
    }


def test_advertised_capabilities_splits_platforms_and_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0166: the helper splits --platform into axes and, when --capabilities is empty, reads the
    # override from the env. worker_capabilities is patched so the test never shells out to simctl.
    seen: dict[str, Any] = {}

    def record(platforms: Any, *, override: Any = None, run: Any = None) -> set[str]:
        seen["platforms"] = list(platforms)
        seen["override"] = override
        return {"platform:ios", "platform:web"}

    monkeypatch.setattr(worker_mod, "worker_capabilities", record)
    monkeypatch.setenv(WORKER_CAPABILITIES_ENV, "ios18")
    caps = _advertised_capabilities("ios, web", "")  # empty flag → env override applies
    assert seen == {"platforms": ["ios", "web"], "override": "ios18"}
    assert caps == ["platform:ios", "platform:web"]  # sorted


def test_worker_advertises_capabilities_on_every_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0166: the advertised set (computed once at startup) is sent on every lease so the control
    # plane routes only servable jobs to this worker. The recorder overrides the autouse stub.
    monkeypatch.chdir(tmp_path)
    seen: dict[str, Any] = {}

    def record_caps(platform: str, capabilities: str) -> list[str]:
        seen["platform"] = platform
        seen["capabilities"] = capabilities
        return ["beta", "platform:web"]

    monkeypatch.setattr(worker_mod, "_advertised_capabilities", record_caps)

    lease_body: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
        if url.endswith("/lease"):
            lease_body.update(body)
            raise _StopLoop  # asserted the first poll's body — stop
        return 200, {}

    monkeypatch.setattr(worker_mod, "_post_json", fake_post)
    with pytest.raises(_StopLoop):
        worker(
            server_url="http://cp",
            poll_interval=1,
            heartbeat_interval=5,
            worker_id="w1",
            platform="web",
            capabilities="beta",
        )

    assert seen == {"platform": "web", "capabilities": "beta"}  # flags flow into the derivation
    assert lease_body == {"worker_id": "w1", "capabilities": ["beta", "platform:web"]}


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
        io=None,
    )


def test_run_with_heartbeat_normal_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Stub _post_json so that if the fast job somehow outlives the short join timeout, the stray
    # heartbeat stays in-process rather than reaching out to http://cp/… — keeps the test hermetic.
    monkeypatch.setattr(worker_mod, "_post_json", lambda *a, **k: (200, {}))
    monkeypatch.setattr(worker_mod, "execute_job_spec", lambda *a, **k: _FakeJob())
    result, abandoned = _run_hb(tmp_path)
    assert abandoned is False
    assert result == {"ok": True, "runId": "r1"}  # `lines` popped before return


def test_run_with_heartbeat_job_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(worker_mod, "_post_json", lambda *a, **k: (200, {}))  # keep hermetic

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


# --- evidence upload (presigned PUT) ------------------------------------------------------------


class _EvidenceHandler(BaseHTTPRequestHandler):
    """A control plane + object store in one: POST upload-urls signs URLs pointing back here, and
    PUT stores the body. `put_fail` keys respond 500 so the best-effort path is exercised."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.url_requests.append(body)  # type: ignore[attr-defined]
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        override = self.server.url_override  # type: ignore[attr-defined]
        urls_response = self.server.urls_response  # type: ignore[attr-defined]
        if urls_response is not None:  # return a caller-fixed (possibly malformed) `urls` value
            urls: Any = urls_response
        else:
            urls = {rel: (override or f"{base}/put/{rel}") for rel in body.get("files", [])}
        data = json.dumps({"urls": urls}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        key = self.path[len("/put/") :]
        if key in self.server.put_fail:  # type: ignore[attr-defined]
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.server.puts[key] = {  # type: ignore[attr-defined]
            "body": data,
            "content_type": self.headers.get("Content-Type"),
        }
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


@contextmanager
def _evidence_server(
    put_fail: set[str] | None = None,
    url_override: str | None = None,
    urls_response: Any = None,
) -> Iterator[tuple[Any, str]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _EvidenceHandler)
    httpd.puts = {}  # type: ignore[attr-defined]
    httpd.url_requests = []  # type: ignore[attr-defined]
    httpd.put_fail = put_fail or set()  # type: ignore[attr-defined]
    httpd.url_override = url_override  # type: ignore[attr-defined]
    httpd.urls_response = urls_response  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _host, port = httpd.server_address
        yield httpd, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()  # close the listening socket so tests don't leak file descriptors
        thread.join()


def _run_tree(work: Path, run_id: str) -> Path:
    run = work / "runs" / run_id
    (run / "00-login").mkdir(parents=True)
    (run / "00-login" / "after.png").write_bytes(b"\x89PNG")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


def test_evidence_files_lists_relative_paths_and_skips_symlinks(tmp_path: Path) -> None:
    run = _run_tree(tmp_path, "r1")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (run / "link.txt").symlink_to(secret)
    assert _evidence_files(run) == ["00-login/after.png", "manifest.json"]


def test_upload_evidence_puts_each_file_with_its_content_type(tmp_path: Path) -> None:
    _run_tree(tmp_path, "r1")
    with _evidence_server() as (httpd, base):
        _upload_evidence(tmp_path, "r1", url=base, auth_token=None, evidence_prefix="main/")
    puts = httpd.puts  # type: ignore[attr-defined]
    assert set(puts) == {"00-login/after.png", "manifest.json"}
    assert puts["00-login/after.png"]["body"] == b"\x89PNG"
    assert puts["00-login/after.png"]["content_type"] == "image/png"
    assert puts["manifest.json"]["content_type"] == "application/json"
    # The worker relays the run's file list and the per-run prefix to the endpoint.
    req = httpd.url_requests[0]  # type: ignore[attr-defined]
    assert req["evidence_prefix"] == "main/"
    assert sorted(req["files"]) == ["00-login/after.png", "manifest.json"]


def test_upload_evidence_is_a_noop_without_a_run_dir(tmp_path: Path) -> None:
    with _evidence_server() as (httpd, base):
        _upload_evidence(tmp_path, "missing", url=base, auth_token=None, evidence_prefix="")
    assert httpd.url_requests == []  # type: ignore[attr-defined]


def test_upload_evidence_survives_a_per_file_put_failure(tmp_path: Path) -> None:
    # A failed PUT must warn and move on — the run's verdict is already final (BE-0110).
    _run_tree(tmp_path, "r1")
    with _evidence_server(put_fail={"00-login/after.png"}) as (httpd, base):
        _upload_evidence(tmp_path, "r1", url=base, auth_token=None, evidence_prefix="")
    puts = httpd.puts  # type: ignore[attr-defined]
    assert set(puts) == {"manifest.json"}  # the good file still uploaded


def test_upload_evidence_does_not_raise_on_a_malformed_signed_url(tmp_path: Path) -> None:
    # A malformed URL from the endpoint raises ValueError in urlopen; the upload runs before the
    # result post, so it must be caught, not crash the worker (BE-0110 best-effort).
    _run_tree(tmp_path, "r1")
    with _evidence_server(url_override="not a url") as (_httpd, base):
        _upload_evidence(tmp_path, "r1", url=base, auth_token=None, evidence_prefix="")  # no raise


def test_upload_evidence_ignores_a_non_dict_urls_response(tmp_path: Path) -> None:
    # A malformed `urls` (not a dict) must not crash the worker on `.items()`.
    _run_tree(tmp_path, "r1")
    with _evidence_server(urls_response=["nope"]) as (httpd, base):
        _upload_evidence(tmp_path, "r1", url=base, auth_token=None, evidence_prefix="")
    assert httpd.puts == {}  # type: ignore[attr-defined]


def test_upload_evidence_skips_a_key_that_escapes_the_run_dir(tmp_path: Path) -> None:
    # A returned key that resolves outside the run dir must be skipped, never PUT.
    _run_tree(tmp_path, "r1")
    with _evidence_server() as (httpd, base):
        httpd.urls_response = {  # type: ignore[attr-defined]
            "../../escape.txt": f"{base}/put/escape",
            "manifest.json": f"{base}/put/manifest.json",
        }
        _upload_evidence(tmp_path, "r1", url=base, auth_token=None, evidence_prefix="")
    puts = httpd.puts  # type: ignore[attr-defined]
    assert set(puts) == {"manifest.json"}  # the escaping key was skipped


# --- PresignedWorkerIO: artifact / scenario upload + baseline download (BE-0160) ----------------


class _WorkerIOHandler(BaseHTTPRequestHandler):
    """A control plane + object store in one for the worker-I/O paths: POST artifact-urls/scenario-url
    signs URLs pointing back here, PUT stores the body, GET serves a seeded baseline. `put_fail` keys
    respond 500 so the fail-loud artifact path is exercised."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append((self.path, body))  # type: ignore[attr-defined]
        status = self.server.post_status  # type: ignore[attr-defined] — force a non-200 to test fail-loud
        if status != 200:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path.endswith("/scenario-url"):
            # `scenario_url` None models a 200 response missing its `url` (a malformed control plane).
            payload: Any = {"url": self.server.scenario_url}  # type: ignore[attr-defined]
            if payload["url"] == "@self":
                payload["url"] = f"{base}/put/{body['app']}/{body['ref']}"
        else:  # artifact-urls
            payload = {"urls": {rel: f"{base}/put/{rel}" for rel in body.get("files", [])}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        key = self.path[len("/put/") :]
        if key in self.server.put_fail:  # type: ignore[attr-defined]
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.server.puts[key] = data  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        key = self.path[len("/get/") :]
        data = self.server.gets.get(key)  # type: ignore[attr-defined]
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@contextmanager
def _worker_io_server(
    put_fail: set[str] | None = None, *, post_status: int = 200, scenario_url: Any = "@self"
) -> Iterator[tuple[Any, str]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerIOHandler)
    httpd.requests = []  # type: ignore[attr-defined]
    httpd.puts = {}  # type: ignore[attr-defined]
    httpd.gets = {}  # type: ignore[attr-defined]
    httpd.put_fail = put_fail or set()  # type: ignore[attr-defined]
    httpd.post_status = post_status  # type: ignore[attr-defined]
    httpd.scenario_url = scenario_url  # type: ignore[attr-defined] — "@self" = a live PUT URL
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _host, port = httpd.server_address
        yield httpd, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def _io(base: str, *, baseline_urls: Any = None) -> PresignedWorkerIO:
    return PresignedWorkerIO(
        url=base, auth_token=None, job_id="j1", worker_id="w1", baseline_urls=baseline_urls
    )


def test_presigned_io_uploads_the_run_tree_over_signed_urls(tmp_path: Path) -> None:
    # The worker asks the control plane for artifact PUT URLs (relaying only its job + run ids and
    # relative file list) and uploads each file — with no cloud credentials of its own (BE-0160).
    _run_tree(tmp_path, "r1")
    with _worker_io_server() as (httpd, base):
        _io(base).upload_run(tmp_path, "r1")
    assert set(httpd.puts) == {"00-login/after.png", "manifest.json"}  # type: ignore[attr-defined]
    path, body = httpd.requests[0]  # type: ignore[attr-defined]
    assert path == "/api/worker/artifact-urls"
    assert body["job_id"] == "j1" and body["run_id"] == "r1"
    assert sorted(body["files"]) == ["00-login/after.png", "manifest.json"]


def test_presigned_io_upload_run_raises_on_a_put_failure(tmp_path: Path) -> None:
    # Artifact upload feeds the report, so a failed PUT must fail the job loudly, not be swallowed
    # (unlike the best-effort post-verdict evidence upload) — determinism-first (BE-0160). The failed
    # PUT surfaces as the underlying HTTP error, not a swallowed no-op.
    _run_tree(tmp_path, "r1")
    with _worker_io_server(put_fail={"manifest.json"}) as (_httpd, base), pytest.raises(HTTPError):
        _io(base).upload_run(tmp_path, "r1")


def test_presigned_io_upload_run_is_a_noop_without_a_run_dir(tmp_path: Path) -> None:
    with _worker_io_server() as (httpd, base):
        _io(base).upload_run(tmp_path, "missing")
    assert httpd.requests == []  # type: ignore[attr-defined]


def test_presigned_io_upload_run_raises_on_a_non_200_urls_response(tmp_path: Path) -> None:
    # A non-200 from the artifact-urls endpoint means the report can't be stored: fail loud.
    _run_tree(tmp_path, "r1")
    with _worker_io_server(post_status=500) as (_httpd, base), pytest.raises(RuntimeError):
        _io(base).upload_run(tmp_path, "r1")


def test_presigned_io_saves_the_authored_scenario(tmp_path: Path) -> None:
    out = tmp_path / "scenarios" / "login.yaml"
    out.parent.mkdir(parents=True)
    out.write_text("- name: login\n  steps: []\n", encoding="utf-8")
    with _worker_io_server() as (httpd, base):
        _io(base).save_scenario(tmp_path, "scenarios/login.yaml", "demo", "login.yaml")
    assert httpd.puts["demo/login.yaml"] == b"- name: login\n  steps: []\n"  # type: ignore[attr-defined]
    path, body = httpd.requests[0]  # type: ignore[attr-defined]
    assert path == "/api/worker/scenario-url"
    assert body == {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "login.yaml"}


def test_presigned_io_save_scenario_confines_to_the_workspace(tmp_path: Path) -> None:
    # A crafted out_path escaping the workspace must not read & upload a host file (defensive).
    secret = tmp_path / "secret.yaml"
    secret.write_text("secret", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    with _worker_io_server() as (httpd, base):
        _io(base).save_scenario(ws, "../secret.yaml", "demo", "stolen.yaml")
    assert httpd.requests == []  # type: ignore[attr-defined] — nothing requested or uploaded


def test_presigned_io_save_scenario_raises_when_the_authored_file_is_missing(
    tmp_path: Path,
) -> None:
    # A `record` that reached save with no output file must fail loudly, not report a phantom success.
    with (
        _worker_io_server() as (httpd, base),
        pytest.raises(RuntimeError, match="authored no scenario"),
    ):
        _io(base).save_scenario(tmp_path, "scenarios/login.yaml", "demo", "login.yaml")
    assert httpd.requests == []  # type: ignore[attr-defined] — no URL requested for a phantom file


def test_presigned_io_save_scenario_raises_on_a_non_200(tmp_path: Path) -> None:
    out = tmp_path / "scenarios" / "login.yaml"
    out.parent.mkdir(parents=True)
    out.write_text("- name: login\n", encoding="utf-8")
    with _worker_io_server(post_status=500) as (_httpd, base), pytest.raises(RuntimeError):
        _io(base).save_scenario(tmp_path, "scenarios/login.yaml", "demo", "login.yaml")


def test_presigned_io_save_scenario_raises_when_no_url_returned(tmp_path: Path) -> None:
    # A 200 that omits the signed URL is a malformed control plane: fail loud, don't skip the save.
    out = tmp_path / "scenarios" / "login.yaml"
    out.parent.mkdir(parents=True)
    out.write_text("- name: login\n", encoding="utf-8")
    with _worker_io_server(scenario_url=None) as (_httpd, base), pytest.raises(RuntimeError):
        _io(base).save_scenario(tmp_path, "scenarios/login.yaml", "demo", "login.yaml")


def test_presigned_io_downloads_baselines_and_clears_stale(tmp_path: Path) -> None:
    with _worker_io_server() as (httpd, base):
        httpd.gets["home.png"] = b"\x89PNG"  # type: ignore[attr-defined]
        # A stale baseline from a previous job in the reused workspace must be cleared first.
        (tmp_path / "baselines").mkdir()
        (tmp_path / "baselines" / "stale.png").write_bytes(b"old")
        _io(base, baseline_urls={"home.png": f"{base}/get/home.png"}).download_baselines(tmp_path)
    assert (tmp_path / "baselines" / "home.png").read_bytes() == b"\x89PNG"
    assert not (tmp_path / "baselines" / "stale.png").exists()  # cleared before re-materialize


def test_download_baselines_raises_on_a_name_that_escapes_the_dir(tmp_path: Path) -> None:
    # A hostile baseline name resolving outside work/baselines is a broken/hostile lease: fail loud
    # and never write outside the dir (the control plane only ever signs safe names).
    with _worker_io_server() as (httpd, base):
        httpd.gets["escape"] = b"nope"  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="escapes"):
            _download_baselines(tmp_path, {"../../escape.png": f"{base}/get/escape"})
    assert not (tmp_path.parent / "escape.png").exists()  # confinement held


def test_download_baselines_raises_on_a_non_string_url(tmp_path: Path) -> None:
    # A non-string URL for a named baseline is a malformed lease: fail loud rather than silently drop
    # a baseline and leave the run comparing against nothing (BE-0160 / determinism-first).
    with pytest.raises(RuntimeError, match="non-string"):
        _download_baselines(tmp_path, {"home.png": None})


def test_download_baselines_raises_on_a_non_string_name(tmp_path: Path) -> None:
    # A non-string baseline name must raise the fail-loud RuntimeError, not a TypeError on the join.
    with pytest.raises(RuntimeError, match="non-string"):
        _download_baselines(tmp_path, {123: "https://signed.example/get/x"})  # type: ignore[dict-item]


def test_download_baselines_raises_on_a_failed_get(tmp_path: Path) -> None:
    # A baseline whose signed GET fails must surface, not be swallowed into an empty baselines dir.
    with _worker_io_server() as (_httpd, base), pytest.raises(HTTPError):  # unseeded → 404
        _download_baselines(tmp_path, {"home.png": f"{base}/get/home.png"})


def test_put_tree_files_skips_a_non_string_key_when_best_effort(tmp_path: Path) -> None:
    # A malformed urls mapping with a non-string key must be skipped (best-effort), never crash the
    # loop on the path-join (evidence uploads past the verdict must not die on a bad response).
    run = _run_tree(tmp_path, "r1")
    assert _put_tree_files(run, {123: "https://x/put"}, best_effort=True) == 0  # type: ignore[dict-item]


def test_put_tree_files_raises_on_a_non_string_key_when_not_best_effort(tmp_path: Path) -> None:
    run = _run_tree(tmp_path, "r1")
    with pytest.raises(RuntimeError, match="unexpected upload entry"):
        _put_tree_files(run, {123: "https://x/put"}, best_effort=False)  # type: ignore[dict-item]

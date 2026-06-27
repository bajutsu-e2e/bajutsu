"""Tests for the target-server lifecycle (`targets.<name>.launchServer`)."""

from __future__ import annotations

import socket
import subprocess
import urllib.error
from typing import Any

import pytest

from bajutsu import config
from bajutsu.runner import launch_server as ls


def _eff(extra: str = "") -> config.Effective:
    """A resolved web Effective, optionally with a `launchServer:` block."""
    body = "baseUrl: 'http://127.0.0.1:8/'"
    if extra:
        body += ", " + extra
    return config.resolve(config.load_config(f"targets: {{ web: {{ {body} }} }}"), "web")


class _FakeProc:
    """Stand-in for subprocess.Popen: poll() returns `code` (None = still running)."""

    def __init__(self, code: int | None) -> None:
        self._code = code
        self.returncode = code or 0
        self.pid = 4321

    def poll(self) -> int | None:
        return self._code


def test_no_launch_server_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # No `launchServer` declared: never shells out, and the stop callable is a no-op.
    started: list[Any] = []
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: started.append(a))
    stop, decision = ls.start_launch_server(_eff())
    stop()
    assert started == []
    assert decision is None  # no launchServer → nothing governed


def test_reuses_already_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    # readyUrl already answers: reuse the externally-started server, never start or stop one.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: True)
    started: list[Any] = []
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: started.append(a))
    stop, decision = ls.start_launch_server(_eff("launchServer: { cmd: 'serve it' }"))
    stop()
    assert started == []
    assert decision is None  # ungoverned (local/Git) reuse records no policy decision


def test_starts_and_waits_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not yet serving → start the command, poll until ready, return a stopper that terminates it.
    probes = iter([False, True])  # reuse-check (down), then first readiness poll (up)
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: next(probes))
    fake = _FakeProc(code=None)
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: fake)
    terminated: list[Any] = []
    monkeypatch.setattr(ls, "_terminate", lambda proc, say: terminated.append(proc))
    stop, _decision = ls.start_launch_server(_eff("launchServer: { cmd: 'serve it' }"))
    assert terminated == []  # not torn down until we stop it
    stop()
    assert terminated == [fake]


def test_timeout_raises_and_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Server never becomes ready within readyTimeout → terminate what we started and raise.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    fake = _FakeProc(code=None)
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: fake)
    terminated: list[Any] = []
    monkeypatch.setattr(ls, "_terminate", lambda proc, say: terminated.append(proc))
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    times = iter([100.0, 100.0, 200.0])  # deadline=101; enter once, then past it
    monkeypatch.setattr(ls.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="not ready"):
        ls.start_launch_server(_eff("launchServer: { cmd: 'serve it', readyTimeout: 1 }"))
    assert terminated == [fake]


def test_command_exit_before_ready_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The command dies before serving → a clear error (not a readiness timeout).
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: _FakeProc(code=1))
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    monkeypatch.setattr(ls.time, "monotonic", iter([0.0, 0.0]).__next__)
    with pytest.raises(RuntimeError, match="exited"):
        ls.start_launch_server(_eff("launchServer: { cmd: 'serve it' }"))


def test_missing_ready_url_raises() -> None:
    # No baseUrl to fall back to and no readyUrl → can't probe, so refuse up front.
    eff = config.resolve(
        config.load_config("targets: { x: { bundleId: com.x, launchServer: { cmd: 'srv' } } }"), "x"
    )
    with pytest.raises(RuntimeError, match="readyUrl"):
        ls.start_launch_server(eff)


def test_probe_dead_port_is_false() -> None:
    # A port nobody is listening on → connection refused → not serving.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert ls._probe(f"http://127.0.0.1:{port}/", timeout=0.5) is False


def test_probe_status_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status = 200

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr(ls.urllib.request, "urlopen", lambda url, timeout=2.0: _Resp())
    assert ls._probe("http://x/") is True

    def _raise(url: str, timeout: float = 2.0) -> None:
        raise urllib.error.HTTPError("http://x/", 503, "err", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(ls.urllib.request, "urlopen", _raise)
    assert ls._probe("http://x/") is False  # a live 5xx is "up" but not "ready"


def test_upload_exec_reuse_accepts_an_external_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # reuse: never runs the uploaded cmd; an externally-answering readyUrl is accepted (decision reused).
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: True)
    started: list[Any] = []
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: started.append(a))
    stop, decision = ls.start_launch_server(
        _eff("launchServer: { cmd: 'serve it' }"), upload_exec="reuse"
    )
    stop()
    assert started == []  # uploaded cmd never runs
    assert decision == {
        "decision": "reused",
        "field": "launchServer",
        "source": None,
        "image": None,
    }


def test_upload_exec_deny_fails_loud_with_no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # deny + nothing answering readyUrl: the run that needs the server fails loud (no bare-host run).
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    started: list[Any] = []
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: started.append(a))
    with pytest.raises(RuntimeError, match="deny"):
        ls.start_launch_server(_eff("launchServer: { cmd: 'serve it' }"), upload_exec="deny")
    assert started == []  # never reaches the bare-host Popen


def test_upload_exec_unknown_mode_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="unknown --upload-exec"):
        ls.start_launch_server(_eff("launchServer: { cmd: 'serve it' }"), upload_exec="bogus")


def test_fail_loud_message_escapes_untrusted_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    # The cmd is untrusted (upload-sourced); a newline in it must be escaped in the error, not
    # injected raw into logs/CLI output.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    with pytest.raises(RuntimeError) as exc:
        ls.start_launch_server(
            _eff('launchServer: { cmd: "serve\\nINJECTED" }'), upload_exec="deny"
        )
    msg = str(exc.value)
    assert "\\nINJECTED" in msg  # repr-escaped
    assert "\nINJECTED" not in msg  # no raw newline reached the message


def test_upload_exec_sandbox_delegates_to_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    # sandbox mode hands off to the sandbox module rather than the bare-host Popen.
    from bajutsu.runner import sandbox

    called: list[Any] = []

    def _fake_sandbox(eff: Any, **_k: Any) -> Any:
        called.append(eff)
        return (lambda: None), {"decision": "sandboxed"}

    monkeypatch.setattr(sandbox, "start_sandboxed_server", _fake_sandbox)
    started: list[Any] = []
    monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: started.append(a))
    _stop, decision = ls.start_launch_server(
        _eff("launchServer: { cmd: 'serve it', port: 8080, dockerImage: 'img' }"),
        upload_exec="sandbox",
    )
    assert len(called) == 1 and started == []
    assert decision == {"decision": "sandboxed"}


def test_terminate_kills_a_real_process() -> None:
    # A real process group is signalled down (the SIGTERM path + wait).
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    ls._terminate(proc, lambda m: None)
    assert proc.poll() is not None

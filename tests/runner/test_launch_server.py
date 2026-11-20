"""Tests for the target-server lifecycle (`targets.<name>.services.server`)."""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from bajutsu.common import config
from bajutsu.common.capability.command_gate import CommandGate, CommandRefused
from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy
from bajutsu.common.runner import launch_server as ls


def _gate(mode: str) -> CommandGate:
    return CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded=mode))  # type: ignore[arg-type]
    )


def _eff(extra: str = "") -> config.Effective:
    """A resolved web Effective, optionally with a `services.server` block."""
    body = "platform: web, subject: { url: 'http://127.0.0.1:8/' }"
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
    # No `services.server` declared: never shells out, and the stop callable is a no-op.
    started: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    gate = _gate("deny")
    stop = ls.start_launch_server(_eff(), gate=gate)
    stop()
    assert started == []
    assert gate.records == []  # no services.server → nothing governed


def test_reuses_already_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    # readyUrl already answers: reuse the externally-started server, never start or stop one.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: True)
    started: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    gate = _gate("sandbox")
    stop = ls.start_launch_server(_eff("services: { server: { cmd: 'serve it' } }"), gate=gate)
    stop()
    assert started == []
    assert gate.records == []  # a reused server runs no command


def test_a_refused_command_fails_even_when_a_server_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0492: a config whose mode refuses its command cannot ride on a server started elsewhere,
    # and the run never probes the readiness URL that config chose.
    probed: list[str] = []
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: probed.append(url) or True)
    with pytest.raises(CommandRefused):
        ls.start_launch_server(
            _eff("services: { server: { cmd: 'serve it' } }"), gate=_gate("deny")
        )
    assert probed == []


def test_starts_and_waits_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not yet serving → start the command, poll until ready, return a stopper that terminates it.
    probes = iter([False, True])  # reuse-check (down), then first readiness poll (up)
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: next(probes))
    fake = _FakeProc(code=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    terminated: list[Any] = []
    monkeypatch.setattr(ls, "_terminate", lambda proc, say: terminated.append(proc))
    gate = CommandGate()
    stop = ls.start_launch_server(_eff("services: { server: { cmd: 'serve it' } }"), gate=gate)
    assert terminated == []  # not torn down until we stop it
    stop()
    assert terminated == [fake]
    [entry] = gate.records
    assert (entry["field"], entry["source"], entry["mode"], entry["decision"]) == (
        "services.server.cmd",
        "operator",
        "allow",
        "ran",
    )


def test_timeout_raises_and_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Server never becomes ready within readyTimeout → terminate what we started and raise.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    fake = _FakeProc(code=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    terminated: list[Any] = []
    monkeypatch.setattr(ls, "_terminate", lambda proc, say: terminated.append(proc))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    times = iter([100.0, 100.0, 200.0])  # deadline=101; enter once, then past it
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="not ready"):
        ls.start_launch_server(_eff("services: { server: { cmd: 'serve it', readyTimeout: 1 } }"))
    assert terminated == [fake]


def test_command_exit_before_ready_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The command dies before serving → a clear error (not a readiness timeout).
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc(code=1))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(time, "monotonic", iter([0.0, 0.0]).__next__)
    with pytest.raises(RuntimeError, match="exited"):
        ls.start_launch_server(_eff("services: { server: { cmd: 'serve it' } }"))


def test_missing_ready_url_raises() -> None:
    # No baseUrl to fall back to and no readyUrl → can't probe, so refuse up front.
    eff = config.resolve(
        config.load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    services:\n"
            "      server: {cmd: srv}\n"
        ),
        "x",
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

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=2.0: _Resp())
    assert ls._probe("http://x/") is True

    def _raise(url: str, timeout: float = 2.0) -> None:
        raise urllib.error.HTTPError("http://x/", 503, "err", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    assert ls._probe("http://x/") is False  # a live 5xx is "up" but not "ready"


@pytest.mark.parametrize("mode", ["named", "deny"])
def test_a_refused_command_never_runs(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # `named` and `deny` never run a command line the config spells out (BE-0492): the run fails
    # loud, naming the source and the mode, and the refusal is recorded.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    started: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    gate = _gate(mode)
    with pytest.raises(CommandRefused, match=f"uploaded: {mode}"):
        ls.start_launch_server(_eff("services: { server: { cmd: 'serve it' } }"), gate=gate)
    assert started == []  # never reaches the bare-host Popen
    assert [e["decision"] for e in gate.records] == ["refused"]


def test_fail_loud_message_escapes_untrusted_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    # The cmd is untrusted (upload-sourced); a newline in it must be escaped in the error, not
    # injected raw into logs/CLI output.
    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    with pytest.raises(RuntimeError) as exc:
        ls.start_launch_server(
            _eff('services: { server: { cmd: "serve\\nINJECTED" } }'), gate=_gate("deny")
        )
    msg = str(exc.value)
    assert "\\nINJECTED" in msg  # repr-escaped
    assert "\nINJECTED" not in msg  # no raw newline reached the message


def test_sandbox_mode_delegates_to_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    # sandbox mode hands off to the sandbox module rather than the bare-host Popen.
    from bajutsu.common.runner import sandbox

    called: list[Any] = []

    def _fake_sandbox(eff: Any, **_k: Any) -> Any:
        called.append(eff)
        return lambda: None

    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(sandbox, "start_sandboxed_server", _fake_sandbox)
    started: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    gate = _gate("sandbox")
    ls.start_launch_server(
        _eff("services: { server: { cmd: 'serve it', port: 8080, dockerImage: 'img' } }"),
        gate=gate,
    )
    assert len(called) == 1 and started == []
    assert [e["decision"] for e in gate.records] == ["sandboxed"]


def test_without_a_gate_the_installed_one_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.common.capability import command_gate

    monkeypatch.setattr(ls, "_probe", lambda url, timeout=2.0: False)
    command_gate.install(_gate("deny"))
    with pytest.raises(CommandRefused):
        ls.start_launch_server(_eff("services: { server: { cmd: 'serve it' } }"))


def test_terminate_kills_a_real_process() -> None:
    # A real process group is signalled down (the SIGTERM path + wait).
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    ls._terminate(proc, lambda m: None)
    assert proc.poll() is not None

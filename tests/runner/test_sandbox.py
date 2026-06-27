"""Tests for the sandboxed launchServer execution (BE-0090, `serve --upload-exec=sandbox`).

The Docker daemon is never touched: the container launch (`run_fn`) and the short docker commands
(`exec_fn`, for `build` / `rm`) are injected fakes, so the argv construction, the deny/reuse/sandbox
decision, the exactly-one-of-image enforcement, and the fail-loud paths are all covered here. Only
the real `docker run` / `docker build` is exercised outside this gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from bajutsu import config
from bajutsu.runner import sandbox


def _eff(ls_body: str, base: str = "http://127.0.0.1:3000/") -> config.Effective:
    """A resolved web Effective with a `launchServer:` block and a port-bearing baseUrl."""
    cfg = f"targets: {{ web: {{ baseUrl: '{base}', launchServer: {{ {ls_body} }} }} }}"
    return config.resolve(config.load_config(cfg), "web")


class _FakeProc:
    """Stand-in for the container subprocess: poll() returns `code` (None = still running)."""

    def __init__(self, code: int | None) -> None:
        self._code = code
        self.returncode = code or 0

    def poll(self) -> int | None:
        return self._code


class _Recorder:
    """Records the argv of each injected docker call so a test can assert on it."""

    def __init__(
        self, proc: _FakeProc | None = None, build_error: bool = False, rm_error: bool = False
    ) -> None:
        self.run_calls: list[list[str]] = []
        self.exec_calls: list[list[str]] = []
        self._proc = proc or _FakeProc(code=None)
        self._build_error = build_error
        self._rm_error = rm_error

    def run_fn(self, argv: list[str]) -> Any:
        self.run_calls.append(argv)
        return self._proc

    def exec_fn(self, argv: list[str]) -> None:
        self.exec_calls.append(argv)
        if self._build_error and argv[:2] == ["docker", "build"]:
            raise sandbox.SandboxError("docker build failed")
        if self._rm_error and argv[:3] == ["docker", "rm", "-f"]:
            raise sandbox.SandboxError("No such container")


def _ls(eff: config.Effective) -> config.LaunchServer:
    assert eff.launch_server is not None
    return eff.launch_server


# --- build_run_argv: the hardened container command ---


def test_build_run_argv_has_hardening_flags() -> None:
    ls = _ls(_eff("cmd: 'node server.js', port: 8080, dockerImage: 'node:20-slim'"))
    argv = sandbox.build_run_argv(
        ls,
        image="node:20-slim",
        container_name="bajutsu-sandbox-abc",
        host_port=3000,
        bundle_cwd="/b",
    )
    for flag in (
        "--rm",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--user",
        "--pids-limit",
    ):
        assert flag in argv, f"missing hardening flag {flag}"
    assert argv[:2] == ["docker", "run"]
    assert argv[-len(["node", "server.js"]) :] == ["node", "server.js"]  # split cmd, last
    assert "node:20-slim" in argv


def test_build_run_argv_publishes_loopback_only() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerImage: 'img'"))
    argv = sandbox.build_run_argv(
        ls, image="img", container_name="c", host_port=3000, bundle_cwd="/b"
    )
    pub = argv[argv.index("-p") + 1]
    assert pub == "127.0.0.1:3000:8080"  # loopback host port → in-container port, never 0.0.0.0
    assert not any(p.startswith("0.0.0.0") for p in argv)
    mount = argv[argv.index("-v") + 1]
    assert mount == "/b:/bundle:ro"  # bundle bind-mounted read-only


def test_build_run_argv_threads_env() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerImage: 'img', env: { API: 'k' }"))
    argv = sandbox.build_run_argv(
        ls, image="img", container_name="c", host_port=3000, bundle_cwd="/b"
    )
    assert "API=k" in argv


# --- resolve_image: exactly-one-of dockerImage / dockerfile ---


def test_resolve_image_uses_named_docker_image() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerImage: 'node:20-slim'"))
    rec = _Recorder()
    image, meta = sandbox.resolve_image(ls, exec_fn=rec.exec_fn, bundle_cwd="/b", tag="t")
    assert image == "node:20-slim"
    assert meta == {"source": "dockerImage", "image": "node:20-slim"}
    assert rec.exec_calls == []  # nothing built


def test_resolve_image_builds_dockerfile() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerfile: 'Dockerfile'"))
    rec = _Recorder()
    image, meta = sandbox.resolve_image(
        ls, exec_fn=rec.exec_fn, bundle_cwd="/b", tag="bajutsu-sandbox:c"
    )
    assert image == "bajutsu-sandbox:c"
    assert meta == {"source": "dockerfile", "image": "bajutsu-sandbox:c"}
    assert rec.exec_calls[0][:2] == ["docker", "build"]
    assert "bajutsu-sandbox:c" in rec.exec_calls[0]


def test_resolve_image_neither_field_fails_loud() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080"))
    with pytest.raises(sandbox.SandboxError, match="exactly one"):
        sandbox.resolve_image(ls, exec_fn=_Recorder().exec_fn, bundle_cwd="/b", tag="t")


def test_resolve_image_both_fields_fails_loud() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerImage: 'img', dockerfile: 'Dockerfile'"))
    with pytest.raises(sandbox.SandboxError, match="exactly one"):
        sandbox.resolve_image(ls, exec_fn=_Recorder().exec_fn, bundle_cwd="/b", tag="t")


def test_resolve_image_build_failure_fails_loud() -> None:
    ls = _ls(_eff("cmd: 'serve', port: 8080, dockerfile: 'Dockerfile'"))
    rec = _Recorder(build_error=True)
    with pytest.raises(sandbox.SandboxError):
        sandbox.resolve_image(ls, exec_fn=rec.exec_fn, bundle_cwd="/b", tag="t")


# --- start_sandboxed_server: the full decision + lifecycle ---


def test_reuse_short_circuits_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    # An externally-answering readyUrl reuses it: no container, no build.
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: True)
    rec = _Recorder()
    stop, decision = sandbox.start_sandboxed_server(
        _eff("cmd: 'serve', port: 8080, dockerImage: 'img'"), run_fn=rec.run_fn, exec_fn=rec.exec_fn
    )
    stop()
    assert rec.run_calls == [] and rec.exec_calls == []
    assert decision == {
        "decision": "reused",
        "field": "launchServer",
        "source": None,
        "image": None,
    }


def test_docker_image_runs_hardened_container(monkeypatch: pytest.MonkeyPatch) -> None:
    probes = iter([False, True])  # reuse-check down, then ready
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: next(probes))
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    rec = _Recorder(_FakeProc(code=None))
    stop, decision = sandbox.start_sandboxed_server(
        _eff("cmd: 'serve', port: 8080, dockerImage: 'img'"), run_fn=rec.run_fn, exec_fn=rec.exec_fn
    )
    assert len(rec.run_calls) == 1 and rec.run_calls[0][:2] == ["docker", "run"]
    assert rec.exec_calls == []  # nothing built for a named image
    assert decision["decision"] == "sandboxed" and decision["source"] == "dockerImage"
    stop()
    assert rec.exec_calls[-1][:3] == ["docker", "rm", "-f"]  # torn down


def test_dockerfile_builds_then_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    probes = iter([False, True])
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: next(probes))
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    rec = _Recorder(_FakeProc(code=None))
    _stop, decision = sandbox.start_sandboxed_server(
        _eff("cmd: 'serve', port: 8080, dockerfile: 'Dockerfile'"),
        run_fn=rec.run_fn,
        exec_fn=rec.exec_fn,
    )
    assert rec.exec_calls[0][:2] == ["docker", "build"]  # built before run
    assert len(rec.run_calls) == 1
    assert decision["source"] == "dockerfile"


def test_container_exits_before_ready_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    monkeypatch.setattr(sandbox.time, "monotonic", iter([0.0, 0.0]).__next__)
    rec = _Recorder(_FakeProc(code=1))  # container dies immediately
    with pytest.raises(sandbox.SandboxError, match="exited"):
        sandbox.start_sandboxed_server(
            _eff("cmd: 'serve', port: 8080, dockerImage: 'img'"),
            run_fn=rec.run_fn,
            exec_fn=rec.exec_fn,
        )
    assert rec.exec_calls[-1][:3] == ["docker", "rm", "-f"]  # torn down on failure


def test_readiness_timeout_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    times = iter([100.0, 100.0, 200.0])  # deadline ~101; enter once, then past it
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: next(times))
    rec = _Recorder(_FakeProc(code=None))
    with pytest.raises(sandbox.SandboxError, match="not ready"):
        sandbox.start_sandboxed_server(
            _eff("cmd: 'serve', port: 8080, dockerImage: 'img', readyTimeout: 1"),
            run_fn=rec.run_fn,
            exec_fn=rec.exec_fn,
        )
    assert rec.exec_calls[-1][:3] == ["docker", "rm", "-f"]


def test_teardown_tolerates_an_already_removed_container(monkeypatch: pytest.MonkeyPatch) -> None:
    # `docker run --rm` auto-removes a self-exited container, so teardown's `docker rm -f` may hit
    # "No such container" — that is success for teardown and must not raise.
    probes = iter([False, True])
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: next(probes))
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    rec = _Recorder(_FakeProc(code=None), rm_error=True)
    stop, _decision = sandbox.start_sandboxed_server(
        _eff("cmd: 'serve', port: 8080, dockerImage: 'img'"), run_fn=rec.run_fn, exec_fn=rec.exec_fn
    )
    stop()  # must not raise even though `docker rm -f` fails


def test_early_exit_error_is_not_masked_by_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    # The container exits early AND teardown's rm fails (already auto-removed): the actionable
    # "container exited" error must still surface, not the rm failure.
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: False)
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    monkeypatch.setattr(sandbox.time, "monotonic", iter([0.0, 0.0]).__next__)
    rec = _Recorder(_FakeProc(code=1), rm_error=True)
    with pytest.raises(sandbox.SandboxError, match="exited"):
        sandbox.start_sandboxed_server(
            _eff("cmd: 'serve', port: 8080, dockerImage: 'img'"),
            run_fn=rec.run_fn,
            exec_fn=rec.exec_fn,
        )


def test_malformed_port_in_ready_url_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo'd port (`:abc`) must fail loud as a SandboxError, not escape as a raw ValueError.
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: False)
    rec = _Recorder()
    with pytest.raises(sandbox.SandboxError, match="invalid port"):
        sandbox.start_sandboxed_server(
            _eff("cmd: 'serve', port: 8080, dockerImage: 'img'", base="http://127.0.0.1:abc/"),
            run_fn=rec.run_fn,
            exec_fn=rec.exec_fn,
        )


def test_no_port_in_ready_url_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # The container publishes to the readyUrl's port; without one there is nothing to publish to.
    monkeypatch.setattr(sandbox, "_probe", lambda url, timeout=2.0: False)
    rec = _Recorder()
    with pytest.raises(sandbox.SandboxError, match="port"):
        sandbox.start_sandboxed_server(
            _eff("cmd: 'serve', port: 8080, dockerImage: 'img'", base="http://127.0.0.1/"),
            run_fn=rec.run_fn,
            exec_fn=rec.exec_fn,
        )

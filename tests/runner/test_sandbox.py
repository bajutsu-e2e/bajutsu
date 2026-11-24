"""Tests for the per-run hook container (`hooks.sandbox`, BE-0491; the worker's mode, BE-0492).

The Docker daemon is never touched: the container launch (`run_fn`) and the short docker commands
(`exec_fn`, for `build` / `exec` / `rm`) are injected fakes, so the argv construction, the
exactly-one-of-image enforcement, the readiness wait, and the fail-loud paths are all covered here.
Only the real `docker run` / `docker build` is exercised outside this gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.config.schema.hook_command import HookSandbox
from bajutsu.common.runner import sandbox


def _sb(**kw: Any) -> HookSandbox:
    return HookSandbox.model_validate({"dockerImage": "node:20-slim", **kw})


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
        self,
        proc: _FakeProc | None = None,
        build_error: bool = False,
        rm_error: bool = False,
        ready_after: int = 0,
    ) -> None:
        self.run_calls: list[list[str]] = []
        self.exec_calls: list[list[str]] = []
        self._proc = proc or _FakeProc(code=None)
        self._build_error = build_error
        self._rm_error = rm_error
        self._ready_after = ready_after
        self._probes = 0

    def run_fn(self, argv: list[str]) -> Any:
        self.run_calls.append(argv)
        return self._proc

    def exec_fn(self, argv: list[str]) -> None:
        self.exec_calls.append(argv)
        if self._build_error and argv[:2] == ["docker", "build"]:
            raise sandbox.SandboxError("docker build failed")
        if self._rm_error and argv[:3] == ["docker", "rm", "-f"]:
            raise sandbox.SandboxError("No such container")
        if argv[-1] == "true":
            self._probes += 1
            if self._probes <= self._ready_after:
                raise sandbox.SandboxError("not up yet")


def _runtime(tmp_path: Path, rec: _Recorder, **kw: Any) -> sandbox.SandboxRuntime:
    return sandbox.SandboxRuntime(
        sandbox=kw.pop("sandbox", _sb()),
        bundle_cwd=tmp_path / "bundle",
        raw_dir=tmp_path / "raw",
        run_fn=rec.run_fn,
        exec_fn=rec.exec_fn,
        **kw,
    )


# --- build_run_argv: the hardened container command ---


def test_build_run_argv_has_hardening_flags() -> None:
    argv = sandbox.build_run_argv(
        _sb(), image="img", container_name="c1", bundle_cwd="/b", raw_dir="/r"
    )
    for flag in ("--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--rm"):
        assert flag in argv
    assert argv[argv.index("--user") + 1] == sandbox.container_user()
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--pids-limit") + 1] == "256"


def test_the_container_user_is_the_worker_never_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # The container has to write the entry's output file in the mounted raw area, which is the
    # worker's own, so it runs as that user; a root worker falls back to `nobody`.
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 1000)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 1000)
    assert sandbox.container_user() == "1000:1000"
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 0)
    assert sandbox.container_user() == "65534:65534"


def test_build_run_argv_replaces_the_image_default_with_an_idle_command() -> None:
    # The container exists to be `exec`-ed into for the whole run, not to run the image's entrypoint.
    argv = sandbox.build_run_argv(
        _sb(), image="img", container_name="c1", bundle_cwd="/b", raw_dir="/r"
    )
    assert argv[-len(sandbox.IDLE_COMMAND) :] == list(sandbox.IDLE_COMMAND)
    assert argv[-len(sandbox.IDLE_COMMAND) - 1] == "img"


def test_build_run_argv_publishes_every_declared_port_to_loopback_only() -> None:
    # The browser or the app on the worker reaches the server at 127.0.0.1:<port>, and nothing else
    # on the network does.
    argv = sandbox.build_run_argv(
        _sb(ports=[8787, 9000]), image="img", container_name="c1", bundle_cwd="/b", raw_dir="/r"
    )
    published = [argv[i + 1] for i, a in enumerate(argv) if a == "-p"]
    assert published == ["127.0.0.1:8787:8787", "127.0.0.1:9000:9000"]


def test_build_run_argv_mounts_the_config_read_only_and_the_raw_area_writable() -> None:
    # The config's directory is the worker's own preparation, read-only; the raw area must be
    # writable, since that is where $BAJUTSU_OUTPUT and the logs are written.
    argv = sandbox.build_run_argv(
        _sb(), image="img", container_name="c1", bundle_cwd="/b", raw_dir="/r"
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [f"/b:{sandbox.WORKDIR}:ro", f"/r:{sandbox.HOOK_DIR}"]
    assert argv[argv.index("-w") + 1] == sandbox.WORKDIR


# --- build_exec_argv and the path rewriting ---


def test_exec_argv_carries_the_entry_environment_and_container_cwd() -> None:
    argv = sandbox.build_exec_argv("c1", ["./seed.sh"], env={"B": "2", "A": "1"}, cwd="/bundle/sub")
    assert argv[:2] == ["docker", "exec"]
    assert argv[argv.index("-w") + 1] == "/bundle/sub"
    assert argv[2:4] == ["-e", "A=1"] and argv[4:6] == ["-e", "B=2"]  # sorted, so argv is stable
    assert argv[-2:] == ["c1", "./seed.sh"]


def test_wrap_rewrites_the_output_path_and_drops_the_run_directory(tmp_path: Path) -> None:
    # The run directory is not mounted, so BAJUTSU_RUN_DIR must not point at a path the container
    # cannot reach; $BAJUTSU_OUTPUT is rewritten to its path inside the mounted raw area.
    rec = _Recorder()
    runtime = _runtime(tmp_path, rec)
    out = tmp_path / "raw" / "beforeRun" / "web" / "0.output"
    argv, _ = runtime.wrap(
        ["./seed.sh"],
        {"BAJUTSU_OUTPUT": str(out), "BAJUTSU_RUN_DIR": str(tmp_path / "runs"), "SEED": "1"},
        None,
    )
    passed = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")
    assert passed["BAJUTSU_OUTPUT"] == f"{sandbox.HOOK_DIR}/beforeRun/web/0.output"
    assert "BAJUTSU_RUN_DIR" not in passed
    assert passed["SEED"] == "1"


def test_wrap_resolves_a_cwd_inside_the_mounted_config_directory(tmp_path: Path) -> None:
    rec = _Recorder()
    runtime = _runtime(tmp_path, rec)
    (tmp_path / "bundle" / "scripts").mkdir(parents=True)
    argv, _ = runtime.wrap(["./s.sh"], {}, tmp_path / "bundle" / "scripts")
    assert argv[argv.index("-w") + 1] == f"{sandbox.WORKDIR}/scripts"


def test_wrap_falls_back_to_the_workdir_for_a_cwd_outside_the_mount(tmp_path: Path) -> None:
    # A host path with no counterpart inside the container would make every command fail to start.
    rec = _Recorder()
    runtime = _runtime(tmp_path, rec)
    (tmp_path / "bundle").mkdir(parents=True)
    argv, _ = runtime.wrap(["./s.sh"], {}, tmp_path / "elsewhere")
    assert argv[argv.index("-w") + 1] == sandbox.WORKDIR


# --- resolve_image: exactly one of dockerImage / dockerfile ---


def test_resolve_image_uses_named_docker_image(tmp_path: Path) -> None:
    rec = _Recorder()
    image, record = sandbox.resolve_image(
        _sb(), exec_fn=rec.exec_fn, bundle_cwd=str(tmp_path), tag="t"
    )
    assert image == "node:20-slim" and record["source"] == "dockerImage"
    assert rec.exec_calls == []  # nothing is built


def test_resolve_image_builds_dockerfile(tmp_path: Path) -> None:
    rec = _Recorder()
    sb = HookSandbox.model_validate({"dockerfile": "docker/Dockerfile"})
    image, record = sandbox.resolve_image(
        sb, exec_fn=rec.exec_fn, bundle_cwd=str(tmp_path), tag="tag1"
    )
    assert image == "tag1" and record["source"] == "dockerfile"
    assert rec.exec_calls[0][:2] == ["docker", "build"]
    assert str(tmp_path / "docker/Dockerfile") in rec.exec_calls[0]


@pytest.mark.parametrize("body", [{}, {"dockerImage": "img", "dockerfile": "Dockerfile"}])
def test_the_schema_requires_exactly_one_image_source(body: dict[str, str]) -> None:
    # Caught when the config loads, so a run never reaches the container with an ambiguous image.
    with pytest.raises(ValueError, match="exactly one of"):
        HookSandbox.model_validate(body)


def test_resolve_image_rejects_a_dockerfile_escaping_the_config_directory(tmp_path: Path) -> None:
    rec = _Recorder()
    sb = HookSandbox.model_construct(docker_image=None, dockerfile="../../etc/Dockerfile", ports=[])
    with pytest.raises(sandbox.SandboxError, match=r"without '\.\.'"):
        sandbox.resolve_image(sb, exec_fn=rec.exec_fn, bundle_cwd=str(tmp_path), tag="t")


def test_resolve_image_build_failure_fails_loud(tmp_path: Path) -> None:
    rec = _Recorder(build_error=True)
    sb = HookSandbox.model_validate({"dockerfile": "Dockerfile"})
    with pytest.raises(sandbox.SandboxError, match="docker build failed"):
        sandbox.resolve_image(sb, exec_fn=rec.exec_fn, bundle_cwd=str(tmp_path), tag="t")


def test_real_exec_surfaces_docker_stderr() -> None:
    # Without docker's own diagnostic a failure is just "docker command failed" with no clue why.
    with pytest.raises(sandbox.SandboxError, match=r"No such file|docker command failed"):
        sandbox._real_exec(["/nonexistent/docker", "ps"])


# --- the container lifecycle ---


def test_start_runs_the_container_then_waits_until_it_accepts_a_command(tmp_path: Path) -> None:
    rec = _Recorder(ready_after=2)
    runtime = _runtime(tmp_path, rec)
    runtime.start()
    assert rec.run_calls and rec.run_calls[0][:2] == ["docker", "run"]
    probes = [c for c in rec.exec_calls if c[-1] == "true"]
    assert len(probes) == 3  # two refusals, then up — a condition wait, never a fixed sleep


def test_a_container_that_exits_before_it_is_ready_fails_loud(tmp_path: Path) -> None:
    rec = _Recorder(proc=_FakeProc(code=1))
    runtime = _runtime(tmp_path, rec)
    with pytest.raises(sandbox.SandboxError, match="exited"):
        runtime.start()


def test_a_container_that_never_becomes_ready_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "_READY_TIMEOUT", 0.05)
    monkeypatch.setattr(sandbox, "_POLL_INTERVAL", 0.01)
    rec = _Recorder(ready_after=10_000)
    runtime = _runtime(tmp_path, rec)
    with pytest.raises(sandbox.SandboxError, match="not ready"):
        runtime.start()
    assert ["docker", "rm", "-f", runtime.container] in rec.exec_calls  # torn down on the way out


def test_stop_tolerates_an_already_removed_container(tmp_path: Path) -> None:
    rec = _Recorder(rm_error=True)
    runtime = _runtime(tmp_path, rec)
    runtime.stop()  # a container that auto-removed itself is success for teardown


def test_one_container_serves_the_whole_run(tmp_path: Path) -> None:
    # A server a `beforeRun` script starts must keep serving until `afterRun`, which a per-command
    # container could not do.
    rec = _Recorder()
    runtime = _runtime(tmp_path, rec)
    runtime.start()
    for _ in range(3):
        runtime.wrap(["./s.sh"], {}, None)
    assert len(rec.run_calls) == 1


def test_a_root_worker_hands_the_output_half_to_the_container_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A root worker's container runs as `nobody`, which cannot open a root-owned owner-only file; the
    # owner is handed over rather than the mode widened, so the files stay owner-only.
    out = tmp_path / "out" / "beforeRun"
    out.mkdir(parents=True)
    (out / "0.output").write_text("", encoding="utf-8")
    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 0)
    monkeypatch.setattr(sandbox.os, "chown", lambda p, u, g: chowned.append((Path(p).name, u, g)))
    sandbox.grant_container_access(tmp_path / "out")
    assert ("0.output", 65534, 65534) in chowned
    assert ("out", 65534, 65534) in chowned


def test_a_non_root_worker_changes_no_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        sandbox.os, "chown", lambda *a: pytest.fail("a non-root worker changed an owner")
    )
    (tmp_path / "out").mkdir()
    sandbox.grant_container_access(tmp_path / "out")

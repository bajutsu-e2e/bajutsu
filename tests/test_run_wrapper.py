"""`bajutsu run` as a real process: the wrapper keeps the command line's behavior (BE-0449).

The rest of the suite invokes the run body in process (`run_cli.invoke`), which cannot see the
process the public wrapper adds. These run `python -m bajutsu run` itself, on the fake backend, and
check what a caller of the command line sees: the verdict line on standard output, the exit code,
the run tree, a cancel on SIGTERM, and an interactive `--step` prompt on a terminal.

The fake app never renders a screen, so its readiness wait would run to its 10-second timeout on
every launch. A `sitecustomize` on the children's `PYTHONPATH` shortens that wait, and nothing else,
so each run takes about a second.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and sessions")

_SITECUSTOMIZE = """\
from bajutsu.common.platform_lifecycle import readiness
from bajutsu.common.runner import launch

_real = readiness.await_ready


def _quick(driver, timeout=10.0, *args, **kwargs):
    return _real(driver, 0.05, *args, **kwargs)


launch.await_ready = _quick
"""

_CONFIG = (
    "targets:\n"
    "  demo:\n"
    "    platform: ios\n"
    "    app:\n"
    "      id: com.example.demo\n"
    "      idNamespaces: [home]\n"
)

_SCENARIOS = {
    "pass.yaml": "- name: ok\n  steps: []\n",
    # The fake screen is empty, so the tap fails at once: a deterministic FAIL.
    "fail.yaml": "- name: miss\n  steps:\n    - tap: { id: home.title }\n",
    # Long enough to signal mid-run; `sleep` answers a cancel at once (BE-0370).
    "slow.yaml": (
        "- name: slow\n  steps:\n"
        "    - sleep: { seconds: 20, reason: hold the run open for the signal under test }\n"
    ),
    "two.yaml": (
        "- name: two\n  steps:\n"
        "    - sleep: { seconds: 0.1, reason: a first step to stop before }\n"
        "    - sleep: { seconds: 0.1, reason: a second step }\n"
    ),
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A config, the scenarios above, and the readiness shortcut, all under *tmp_path*."""
    (tmp_path / "bajutsu.config.yaml").write_text(_CONFIG, encoding="utf-8")
    for name, body in _SCENARIOS.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    return tmp_path


def _env(workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(workspace / "site"), env.get("PYTHONPATH", "")) if p
    )
    for var in ("BAJUTSU_EVIDENCE_STORE", "GITHUB_ACTIONS", "BAJUTSU_LOG_LEVEL"):
        env.pop(var, None)
    return env


def _run_argv(scenario: str, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        scenario,
        "--target",
        "demo",
        "--backend",
        "fake",
        "--no-system-alert-handling",
        *extra,
    ]


def _run(workspace: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=workspace,
        env=_env(workspace),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _run_dirs(workspace: Path) -> list[Path]:
    runs = workspace / "runs"
    return sorted(p for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []


def test_a_passing_run_prints_pass_on_stdout_and_exits_zero(workspace: Path) -> None:
    result = _run(workspace, *_run_argv("pass.yaml", "--progress"))
    assert result.returncode == 0, result.stderr
    [run_dir] = _run_dirs(workspace)
    assert result.stdout == f"PASS  {run_dir.resolve() / 'manifest.json'}\n"
    assert (run_dir / "manifest.json").is_file()
    # Progress stays on standard error, never mixed into the verdict line.
    assert "scenario 1/1" in result.stderr


def test_a_failing_run_prints_fail_and_exits_one(workspace: Path) -> None:
    result = _run(workspace, *_run_argv("fail.yaml"))
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith("FAIL  ")
    assert len(_run_dirs(workspace)) == 1


def test_a_bad_flag_is_a_usage_error_in_the_callers_words(workspace: Path) -> None:
    result = _run(workspace, *_run_argv("pass.yaml", "--no-such-flag"))
    assert result.returncode == 2
    assert "bajutsu run" in result.stderr
    assert "no-such-flag" in result.stderr
    assert not _run_dirs(workspace)


def test_an_over_long_label_is_refused_before_anything_runs(workspace: Path) -> None:
    result = _run(workspace, *_run_argv("pass.yaml", "--label", "x" * 500))
    assert result.returncode == 2
    assert "--label" in result.stderr
    assert not _run_dirs(workspace)


def _wait_for(path: Path, text: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        if path.is_file() and text in path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.05)
    pytest.fail(f"{text!r} never appeared in {path}")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_cancels_the_run_which_still_writes_its_manifest(
    workspace: Path, sig: signal.Signals
) -> None:
    if signal.getsignal(sig) == signal.SIG_IGN:
        # An ignored signal is inherited as ignored, and the wrapper rightly leaves it so.
        pytest.skip(f"{sig.name} is ignored in this test process")
    out, err = workspace / "out.txt", workspace / "err.txt"
    with out.open("w") as stdout, err.open("w") as stderr:
        proc = subprocess.Popen(
            _run_argv("slow.yaml", "--progress"),
            cwd=workspace,
            env=_env(workspace),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            _wait_for(err, "step 1: sleep", time.monotonic() + 30)
            proc.send_signal(sig)
            code = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
    assert code == 128 + sig
    # BE-0370: the cancelled run still closes itself out, so the run tree and its verdict line exist.
    [run_dir] = _run_dirs(workspace)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenarios"][0]["ok"] is False
    assert out.read_text(encoding="utf-8").startswith("FAIL  ")


def test_a_spec_queued_before_the_internal_module_runs_once_under_worker_once(
    workspace: Path,
) -> None:
    # A spec queued before BE-0449 names the public command. `_local_interpreter` rewrites it to the
    # internal module, so it never enters the wrapper: one run tree, and no extra process layer.
    spec = {
        "job_id": "legacy",
        "cmd": [
            "python",  # rewritten to this interpreter, as a leased spec's is
            "-m",
            "bajutsu",
            "run",
            "--scenario",
            "pass.yaml",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--no-system-alert-handling",
        ],
        "udids": [],
        "graceful_cancel": True,
    }
    (workspace / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    result = _run(workspace, sys.executable, "-m", "bajutsu", "worker", "--once", "spec.json")
    assert result.returncode == 0, result.stderr
    [run_dir] = _run_dirs(workspace)
    assert result.stdout == f"PASS  {run_dir.resolve() / 'manifest.json'}\n"


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pseudo-terminal")
@pytest.mark.parametrize("answer", [b"n\n", b"\x03"])
def test_the_step_prompt_reads_the_callers_terminal(workspace: Path, answer: bytes) -> None:
    # `--step` needs a terminal on stdin and stderr. The internal run sits in a session of its own,
    # yet it still reads the terminal it inherited, and a Ctrl-C there (which reaches the wrapper)
    # ends the run at the prompt with its report written.
    import fcntl
    import select
    import termios

    master, slave = os.openpty()

    def _take_terminal() -> None:  # runs in the child after setsid: the pty becomes its terminal
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        _run_argv("two.yaml", "--step"),
        cwd=workspace,
        env=_env(workspace),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        preexec_fn=_take_terminal,  # noqa: PLW1509  # the one way to give the child a terminal
    )
    os.close(slave)
    buf = b""
    seen = 0

    def read_until(token: bytes, seconds: float = 30) -> bool:
        nonlocal buf, seen
        deadline = time.monotonic() + seconds
        while (at := buf.find(token, seen)) < 0:
            if time.monotonic() > deadline:
                return False
            if not select.select([master], [], [], 0.1)[0]:
                continue
            try:
                data = os.read(master, 4096)
            except OSError:
                return False
            if not data:
                return False
            buf += data
        seen = at + len(token)
        return True

    try:
        assert read_until(b"step> "), buf
        os.write(master, answer)
        if answer == b"n\n":
            assert read_until(b"before step 1"), buf
            assert read_until(b"step> "), buf
            os.write(master, b"c\n")
        read_until(b"manifest.json")
        code = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    if answer == b"n\n":
        assert code == 0, buf
        assert b"PASS  " in buf
    else:
        assert code == 128 + signal.SIGINT, buf
        assert b"run ended at the prompt" in buf
        assert b"FAIL  " in buf
    assert len(_run_dirs(workspace)) == 1


# --- the child is never left running on its own ---------------------------------------------------

_needs_pgrep = pytest.mark.skipif(
    subprocess.run(["which", "pgrep"], capture_output=True, check=False).returncode != 0,
    reason="needs pgrep to find the wrapper's child",
)


def _child_of(pid: int, deadline: float) -> int:
    while time.monotonic() < deadline:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False)
        if out.stdout.split():
            return int(out.stdout.split()[0])
        time.sleep(0.05)
    raise AssertionError(f"process {pid} never started a child")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return not stat.stdout.strip().startswith("Z")


def _state(pid: int) -> str:
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return out.stdout.strip()


def _wait_until(predicate: Any, seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@_needs_pgrep
def test_a_killed_wrapper_takes_the_run_down_with_it(workspace: Path) -> None:
    # SIGKILL leaves the wrapper no chance to relay a cancel. The run notices the end of the
    # liveness pipe and cancels itself gracefully, so it still writes its manifest.
    err = workspace / "err.txt"
    with err.open("w") as stderr:
        proc = subprocess.Popen(
            _run_argv("slow.yaml", "--progress"),
            cwd=workspace,
            env=_env(workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            child = _child_of(proc.pid, time.monotonic() + 30)
            _wait_for(err, "step 1: sleep", time.monotonic() + 30)
            proc.kill()
            proc.wait(timeout=10)
            assert _wait_until(lambda: not _alive(child)), "the run outlived its wrapper"
        finally:
            if proc.poll() is None:
                proc.kill()
    [run_dir] = _run_dirs(workspace)
    assert (run_dir / "manifest.json").is_file()


# A child that ignores the graceful SIGTERM, so only a hard kill ends it.
_STUBBORN = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)


def _once(workspace: Path) -> subprocess.Popen[bytes]:
    spec = {"cmd": [sys.executable, "-c", _STUBBORN], "graceful_cancel": True}
    (workspace / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "bajutsu", "worker", "--once", "spec.json"],
        cwd=workspace,
        env=_env(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        # A group of its own whose parent (this test) sits in another group of the same session, as
        # a shell job does: the kernel discards a SIGTSTP sent to an orphaned group.
        process_group=0,
    )


@_needs_pgrep
def test_a_second_ctrl_c_stops_the_child_at_once(workspace: Path) -> None:
    if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
        pytest.skip("SIGINT is ignored in this test process")
    proc = _once(workspace)
    try:
        assert proc.stdout is not None and proc.stdout.readline() == b"ready\n"
        child = _child_of(proc.pid, time.monotonic() + 30)
        proc.send_signal(signal.SIGINT)
        time.sleep(0.5)
        assert _alive(child)  # the graceful request alone does not end this child
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 128 + signal.SIGINT
        assert _wait_until(lambda: not _alive(child), 10)
    finally:
        if proc.poll() is None:
            proc.kill()


@_needs_pgrep
def test_ctrl_z_stops_and_continues_the_child_with_the_wrapper(workspace: Path) -> None:
    proc = _once(workspace)
    try:
        assert proc.stdout is not None and proc.stdout.readline() == b"ready\n"
        child = _child_of(proc.pid, time.monotonic() + 30)
        proc.send_signal(signal.SIGTSTP)
        assert _wait_until(
            lambda: _state(child).startswith("T") and _state(proc.pid).startswith("T")
        )
        proc.send_signal(signal.SIGCONT)
        assert _wait_until(lambda: not _state(child).startswith("T"))
        assert not _state(proc.pid).startswith("T")
    finally:
        proc.kill()
        proc.wait(timeout=10)
        with contextlib.suppress(ProcessLookupError, UnboundLocalError):
            os.kill(child, signal.SIGKILL)

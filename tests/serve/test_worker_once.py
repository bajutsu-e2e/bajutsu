"""`bajutsu worker --once` and the public `bajutsu run` wrapper on the one-shot path (BE-0449).

These run in process with injected processes; `tests/test_run_wrapper.py` runs the real commands.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from bajutsu import serve as srv
from bajutsu.cli import app
from bajutsu.common.cancellation import GRACE_ENV
from bajutsu.run import INTERNAL_MODULE, PARENT_FD_ENV
from bajutsu.run import cli as run_cli
from bajutsu.serve import _cli_flags as cf
from bajutsu.serve import jobs as srv_jobs
from bajutsu.serve.server import one_shot
from bajutsu.serve.server.worker_job import _local_interpreter

runner = CliRunner()

_INTERNAL = [sys.executable, "-m", INTERNAL_MODULE]


# --- what a one-shot job refuses before running ------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "words"),
    [
        ([], "JSON object"),
        ({"cmd": ["x"], "bundle": {"id": "a" * 64}}, "uploaded bundle"),
        ({"cmd": ["x"], "overrides": {"binary": "b"}}, "artifact overrides"),
        ({"cmd": ["x"], "materialize_baselines": True}, "visual baselines"),
        ({"batch": {"provider": "devicefarm"}}, "worker.yaml environment"),
        ({"cmd": []}, "non-empty list"),
        ({"cmd": ["x", 1]}, "non-empty list"),
        ({}, "non-empty list"),
    ],
)
def test_a_spec_a_server_must_serve_is_refused(spec: Any, words: str) -> None:
    reason = one_shot.refusal(spec)
    assert reason is not None and words in reason


@pytest.mark.parametrize(
    ("spec", "words"),
    [
        ({"cmd": ["x"], "udids": "U1"}, "`udids`"),
        ({"cmd": ["x"], "udids": [1]}, "`udids`"),
        ({"cmd": ["x"], "materials": ["s.yaml"]}, "`materials`"),
        ({"cmd": ["x"], "materials": {"s.yaml": 1}}, "`materials`"),
    ],
)
def test_a_spec_field_of_the_wrong_type_is_refused(spec: Any, words: str) -> None:
    reason = one_shot.refusal(spec)
    assert reason is not None and words in reason


def test_a_material_that_would_overwrite_a_different_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "same.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "other.yaml").write_text("mine\n", encoding="utf-8")
    (tmp_path / "dir.yaml").mkdir()
    spec = {"cmd": ["x"], "materials": {"same.yaml": "x: 1\n", "new.yaml": "y"}}
    assert one_shot.refusal(spec, tmp_path) is None
    for rel in ("other.yaml", "dir.yaml"):
        reason = one_shot.refusal({"cmd": ["x"], "materials": {rel: "theirs"}}, tmp_path)
        assert reason is not None and rel in reason
    assert (tmp_path / "other.yaml").read_text(encoding="utf-8") == "mine\n"


def test_worker_once_refuses_a_clashing_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bajutsu.config.yaml").write_text("mine\n", encoding="utf-8")
    spec = _spec_file(
        tmp_path, {"cmd": ["never-run"], "materials": {"bajutsu.config.yaml": "theirs"}}
    )
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 2
    assert "overwrite" in result.output


def test_a_plain_spec_is_accepted() -> None:
    assert (
        one_shot.refusal(
            {
                "cmd": ["x"],
                "bundle": None,
                "overrides": None,
                "batch": None,
                "materialize_baselines": False,
            }
        )
        is None
    )


def test_the_local_worker_io_touches_nothing(tmp_path: Path) -> None:
    io = one_shot.LocalWorkerIO()
    io.download_baselines(tmp_path)
    io.upload_run(tmp_path, "r1")
    io.save_scenario(tmp_path, "out.yaml", "app", "ref")
    assert list(tmp_path.iterdir()) == []


def test_the_wrappers_spec_leaves_devices_and_builds_to_the_run() -> None:
    spec = one_shot.local_run_spec(["--target=demo"])
    assert spec["cmd"] == [*_INTERNAL, "--target=demo"]
    assert spec["udids"] == [] and spec["build"] is None
    assert spec["graceful_cancel"] is True
    assert one_shot.refusal(spec) is None


# --- the exit code ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "signum", "expected"),
    [(0, None, 0), (1, None, 1), (2, None, 2), (-9, None, 137), (None, None, 1), (1, 15, 143)],
)
def test_the_exit_code_is_the_childs_or_128_plus_the_signal(
    code: int | None, signum: int | None, expected: int
) -> None:
    job = srv.Job(cmd=["x"])
    job.exit_code = code
    assert one_shot.exit_code(job, signum) == expected


# --- the signal mapping -------------------------------------------------------------------------


class _Child:
    """Records what the signal relay asks of the job's child."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cancelled = threading.Event()
        self.killed = threading.Event()

    def request(self) -> None:
        self.calls.append("cancel")
        self.cancelled.set()

    def pgid(self) -> int | None:
        return None

    def kill(self) -> None:
        self.calls.append("kill")
        self.killed.set()


def test_a_signal_becomes_one_cancel_and_the_handlers_come_back() -> None:
    before = signal.getsignal(signal.SIGTERM)
    child = _Child()
    with one_shot.cancel_on_signals(child) as signalled:
        assert signalled() is None
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)  # a repeat is not a second cancel
        assert child.cancelled.wait(5)
        assert signalled() == signal.SIGTERM
    assert child.calls == ["cancel"]
    assert signal.getsignal(signal.SIGTERM) == before


def test_a_second_ctrl_c_kills_the_child_at_once() -> None:
    child = _Child()
    with one_shot.cancel_on_signals(child) as signalled:
        os.kill(os.getpid(), signal.SIGINT)
        assert child.cancelled.wait(5)
        os.kill(os.getpid(), signal.SIGINT)
        assert child.killed.wait(5)
        assert signalled() == signal.SIGINT
    assert child.calls == ["cancel", "kill"]


def test_a_continue_reaches_the_childs_group() -> None:
    sent: list[tuple[int, int]] = []

    class _Grouped(_Child):
        def pgid(self) -> int | None:
            return 4242

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
        with one_shot.cancel_on_signals(_Grouped()):
            os.kill(os.getpid(), signal.SIGCONT)
    assert (4242, signal.SIGCONT) in sent


def test_a_signal_the_caller_ignores_stays_ignored() -> None:
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with one_shot.cancel_on_signals(_Child()):
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_off_the_main_thread_the_signals_keep_their_defaults() -> None:
    seen: list[int | None] = []

    def body() -> None:
        with one_shot.cancel_on_signals(_Child()) as signalled:
            seen.append(signalled())

    thread = threading.Thread(target=body)
    thread.start()
    thread.join(5)
    assert seen == [None]


# --- the inherited-stdio spawn and the run id from the run tree ---------------------------------


class _TreeProc:
    """A finished child that wrote a run tree under *runs*, as the internal run does."""

    def __init__(self, runs: Path | None, code: int) -> None:
        if runs is not None:
            (runs / "20260101-000000").mkdir(parents=True)
            (runs / "20260101-000000" / "manifest.json").write_text("{}", encoding="utf-8")
        self.pid = -1
        self.returncode = code

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode


def test_an_inherited_run_takes_its_run_id_from_the_tree(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    runs = tmp_path / "elsewhere"
    (runs / "older").mkdir(parents=True)

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        calls.append(kw)
        return _TreeProc(runs, 1)

    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=popen)
    job = srv.Job(cmd=[*_INTERNAL, "--runs-dir", str(runs)])
    srv.run_job(state, job, inherit_stdio=True)
    assert job.run_id == "20260101-000000"
    assert job.exit_code == 1 and job.status == "done"
    # The caller's own streams, and a session of its own for the child.
    assert "stdout" not in calls[0] and "stdin" not in calls[0]
    assert calls[0]["start_new_session"] is True
    # The liveness pipe: the child gets its read end, named in its environment, and nothing else.
    (fd,) = calls[0]["pass_fds"]
    assert calls[0]["env"][PARENT_FD_ENV] == str(fd)


@pytest.mark.parametrize(("keep", "expected"), [(True, "/only/this"), (False, None)])
def test_only_a_job_that_keeps_the_callers_path_skips_the_bin_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep: bool, expected: str | None
) -> None:
    monkeypatch.setenv("PATH", "/only/this")
    envs: list[dict[str, str]] = []

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        envs.append(kw["env"])
        return _TreeProc(None, 0)

    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=popen)
    job = srv.Job(cmd=["echo"], graceful_cancel=True, keep_caller_path=keep)
    srv.run_job(state, job, inherit_stdio=True)
    bindir = str(Path(sys.executable).parent)
    assert envs[0]["PATH"] == (expected or bindir + os.pathsep + "/only/this")
    assert GRACE_ENV in envs[0]


@pytest.mark.parametrize(("keep", "bindir_first"), [(True, False), (False, True)])
def test_run_spec_once_puts_the_bin_dir_first_unless_the_wrapper_keeps_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep: bool, bindir_first: bool
) -> None:
    # `worker --once` (keep False) resolves `idb` as a leased worker does; the public `bajutsu run`
    # (keep True) keeps the caller's shell PATH.
    monkeypatch.setenv("PATH", "/only/this")
    envs: list[dict[str, str]] = []

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        envs.append(kw["env"])
        return _TreeProc(None, 0)

    one_shot.run_spec_once(
        {"cmd": ["echo"]},
        worker_config=None,
        keep_caller_path=keep,
        cwd=tmp_path,
        popen=popen,
    )
    bindir = str(Path(sys.executable).parent)
    assert envs[0]["PATH"].startswith(bindir + os.pathsep) is bindir_first


def test_an_inherited_job_that_is_not_a_run_has_no_run_id(tmp_path: Path) -> None:
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        popen=lambda c, **k: _TreeProc(tmp_path / "runs", 0),
    )
    job = srv.Job(cmd=[sys.executable, "-m", "bajutsu", "record"])
    srv.run_job(state, job, inherit_stdio=True)
    assert job.run_id is None and job.exit_code == 0


def test_a_job_cancelled_before_its_spawn_never_runs(tmp_path: Path) -> None:
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", cwd=tmp_path, popen=lambda c, **k: _TreeProc(None, 0)
    )
    job = srv.Job(cmd=["echo"])
    job.cancelled = True
    srv.run_job(state, job, inherit_stdio=True)
    assert job.exit_code == 1 and job.status == "done"


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ([], "runs"),
        (["--runs-dir", "a"], "a"),
        (["--runs-dir=b"], "b"),
        (["--runs-dir", "a", "--runs-dir=c"], "c"),
        (["--runs-dir"], "runs"),
    ],
)
def test_the_runs_dir_follows_the_last_flag(tmp_path: Path, cmd: list[str], expected: str) -> None:
    assert srv_jobs._runs_dir_of(cmd, tmp_path) == tmp_path / expected


def test_a_tree_without_a_manifest_is_no_run(tmp_path: Path) -> None:
    (tmp_path / "20260101-000000").mkdir()
    assert srv_jobs._new_run_id_in(tmp_path, frozenset()) is None
    assert srv_jobs._new_run_id_in(tmp_path / "missing", frozenset()) is None
    assert srv_jobs._new_run_id_in(None, frozenset()) is None
    assert srv_jobs._run_dir_names(tmp_path / "missing") == frozenset()


# --- run_spec_once: a cancel on a signal --------------------------------------------------------


class _CancellableProc:
    """A running child that ends when it is sent SIGTERM, as a cancelled `bajutsu run` does."""

    def __init__(self) -> None:
        self.pid = -1
        self.returncode: int | None = None
        self.started = threading.Event()
        self._ended = threading.Event()
        self.signals: list[int] = []

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = 1
        self._ended.set()

    def wait(self, timeout: float | None = None) -> int | None:
        self.started.set()
        self._ended.wait(timeout)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def test_a_signal_cancels_a_one_shot_run_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GRACE_ENV, "0.1")  # the escalation timer finds the run already gone
    proc = _CancellableProc()

    def signal_once_running() -> None:
        assert proc.started.wait(5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=signal_once_running, daemon=True).start()
    code = one_shot.run_spec_once(
        {"cmd": [*_INTERNAL, "--target", "demo"], "graceful_cancel": True},
        worker_config=None,
        cwd=tmp_path,
        popen=lambda cmd, **kw: proc,
    )
    assert code == 128 + signal.SIGTERM
    # Graceful: the leader alone is asked to stop (BE-0370), not its group killed.
    assert proc.signals == [signal.SIGTERM]


def test_run_spec_once_refuses_a_spec_its_caller_did_not_vet(tmp_path: Path) -> None:
    def popen(cmd: list[str], **kw: Any) -> Any:
        raise AssertionError("nothing may run")

    with pytest.raises(ValueError, match="inside the workspace"):
        one_shot.run_spec_once(
            {"cmd": ["x"], "materials": {"../escape.yaml": "y"}},
            worker_config=None,
            cwd=tmp_path,
            popen=popen,
        )
    assert not (tmp_path.parent / "escape.yaml").exists()


def test_run_spec_once_hands_the_run_its_worker_file(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        seen.append(cmd)
        return _TreeProc(None, 0)

    path = tmp_path / "w.yaml"
    code = one_shot.run_spec_once(
        {"cmd": ["python", "-m", "bajutsu", "run", "--target", "demo"]},
        worker_config=path,
        cwd=tmp_path,
        popen=popen,
    )
    assert code == 0
    # A spec queued before BE-0449 runs the internal module directly: one process, no wrapper.
    assert seen == [[*_INTERNAL, "--worker-config", str(path.resolve()), "--target", "demo"]]


def test_the_wrapper_keeps_the_callers_worker_file_as_typed(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        seen.append(cmd)
        return _TreeProc(None, 0)

    spec = one_shot.local_run_spec(["--worker-config=w.yaml"])
    one_shot.run_spec_once(
        spec, worker_config=None, apply_worker_config=False, cwd=tmp_path, popen=popen
    )
    assert seen == [[*_INTERNAL, "--worker-config=w.yaml"]]


# --- `bajutsu worker --once` --------------------------------------------------------------------


def _spec_file(tmp_path: Path, spec: Any) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_worker_once_exits_with_the_jobs_code(tmp_path: Path) -> None:
    spec = _spec_file(tmp_path, {"cmd": [sys.executable, "-c", "raise SystemExit(3)"]})
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 3, result.output


def test_worker_once_refuses_a_bundle_before_running(tmp_path: Path) -> None:
    spec = _spec_file(tmp_path, {"cmd": ["never-run"], "bundle": {"id": "a" * 64}})
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 2
    assert "uploaded bundle" in result.output


def test_worker_once_refuses_an_unreadable_spec(tmp_path: Path) -> None:
    bad = tmp_path / "spec.json"
    bad.write_text("{not json", encoding="utf-8")
    for path in (bad, tmp_path / "missing.json"):
        result = runner.invoke(app, ["worker", "--once", str(path)])
        assert result.exit_code == 2
        assert "cannot read the job spec" in result.output


def test_worker_once_refuses_a_device_cloud_worker_file_without_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEVICEFARM_PROJECT_ARN", raising=False)
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text(
        "version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", encoding="utf-8"
    )
    spec = _spec_file(tmp_path, {"cmd": ["never-run"]})
    result = runner.invoke(
        app, ["worker", "--once", str(spec), "--worker-config", str(worker_file)]
    )
    assert result.exit_code == 2
    assert "registered no provider" in re.sub(
        r"[\s│]+", " ", re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    )


# --- the public `bajutsu run` wrapper -----------------------------------------------------------


def test_the_wrapper_passes_on_what_the_caller_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_once(spec: dict[str, Any], **kwargs: Any) -> int:
        captured.update(spec=spec, **kwargs)
        return 7

    monkeypatch.setattr(one_shot, "run_spec_once", fake_once)
    # An environment value is left for the run to read itself; a typed one is passed on, even when
    # it equals the default or clears the environment value.
    monkeypatch.setenv("BAJUTSU_EVIDENCE_STORE", "s3://bucket/prefix")
    argv = [
        "run",
        "--scenario",
        "a.yaml",
        "--scenario",
        "b.yaml",
        "--target",
        "demo",
        "--udid",
        "",
        "--workers",
        "1",
        "--erase",
        "--no-network",
        "--zip",
        "--break",
        "-1",
        "--label",
        "-x",
    ]
    result = runner.invoke(app, argv)
    assert result.exit_code == 7, result.output
    assert captured["spec"]["cmd"] == [
        *_INTERNAL,
        "--target=demo",
        "--scenario=a.yaml",
        "--scenario=b.yaml",
        "--udid=",
        "--workers=1",
        "--erase",
        "--no-network",
        "--break=-1",
        "--zip",
        "--label=-x",
    ]
    assert captured["worker_config"] is None
    assert captured["apply_worker_config"] is False

    result = runner.invoke(app, ["run", "--target", "demo", "--evidence-store", ""])
    assert result.exit_code == 7, result.output
    assert captured["spec"]["cmd"] == [*_INTERNAL, "--target=demo", "--evidence-store="]


def test_the_internal_module_is_a_run_job() -> None:
    assert srv.Job(cmd=[*_INTERNAL, "--target", "demo"]).kind == "run"
    assert srv.Job(cmd=[sys.executable, "-m", "bajutsu", "run"]).kind == "run"
    assert srv.Job(cmd=[sys.executable, "-m"]).kind is None
    assert srv.Job(cmd=[sys.executable, "-m", "bajutsu"]).kind is None
    assert srv.Job(cmd=[sys.executable, "-m", "other", "run"]).kind is None
    assert _local_interpreter(["/elsewhere/python", "-m", INTERNAL_MODULE]) == _INTERNAL


# --- option_args: every option survives the trip to the internal run -----------------------------


def _non_default(option: Any) -> Any:
    """A value for *option* that differs from its default and that its type accepts."""
    if option.is_bool_flag:
        # A flag pair whose default is unset or on is sent its `--no-` side, so both sides are tried.
        return not (option.secondary_opts and option.default in (None, True))
    kind = option.type.name
    if kind == "integer":
        return 3
    if kind == "float":
        return 0.25
    if kind == "path":
        return Path("w.yaml")
    if option.multiple:
        return ["a", "-b"]
    return "-value"


def test_every_option_round_trips_through_the_internal_run() -> None:
    options = cf._options("run")
    values = {name: _non_default(option) for name, option in options.items()}
    assert all(values[name] != option.default for name, option in options.items())
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    run_cli._share_signature(capture, run_cli.run_body)
    body = typer.Typer()
    body.command()(capture)
    result = runner.invoke(body, cf.option_args("run", values))
    assert result.exit_code == 0, result.output
    assert {name: captured[name] for name in values} == values


def test_option_args_refuses_an_unknown_option() -> None:
    with pytest.raises(ValueError, match="not an option"):
        cf.option_args("run", {"nope": 1})


# --- the `--step` prompt notices a cancel -------------------------------------------------------


@pytest.fixture
def piped_stdin(monkeypatch: pytest.MonkeyPatch) -> Any:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r")
    writer = os.fdopen(write_fd, "w")
    monkeypatch.setattr(sys, "stdin", reader)
    monkeypatch.setattr(run_cli, "_STEP_INPUT", bytearray())
    yield writer
    reader.close()
    if not writer.closed:
        writer.close()


def test_the_step_prompt_ends_on_a_cancel(piped_stdin: Any) -> None:
    with pytest.raises(EOFError):
        run_cli._read_step_line("step> ", lambda: True)


def test_the_step_prompt_reads_a_typed_line(piped_stdin: Any) -> None:
    piped_stdin.write("n\n")
    piped_stdin.flush()
    assert run_cli._read_step_line("step> ") == "n\n"
    piped_stdin.close()
    with pytest.raises(EOFError):
        run_cli._read_step_line("step> ")


def test_two_answers_sent_at_once_reach_two_prompts(piped_stdin: Any) -> None:
    # The writer stays open, so the second answer is never followed by end of file: the prompt must
    # serve it from what was already read rather than wait on the drained descriptor.
    piped_stdin.write("n\nc\n")
    piped_stdin.flush()
    deadline = time.monotonic() + 5
    gave_up = lambda: time.monotonic() > deadline  # noqa: E731
    assert run_cli._read_step_line("step> ", gave_up) == "n\n"
    assert run_cli._read_step_line("step> ", gave_up) == "c\n"
    assert not piped_stdin.closed


def test_a_last_answer_without_a_newline_is_still_read(piped_stdin: Any) -> None:
    piped_stdin.write("q")
    piped_stdin.close()
    assert run_cli._read_step_line("step> ") == "q"
    with pytest.raises(EOFError):
        run_cli._read_step_line("step> ")


def test_the_late_cancel_reads_the_source_bound_last() -> None:
    late = run_cli._LateCancel()
    assert late() is False
    late.source = lambda: True
    assert late() is True


def test_a_real_child_inherits_the_callers_streams(tmp_path: Path) -> None:
    # A real process, so the spawn's keyword set is one `subprocess.Popen` accepts.
    out = tmp_path / "out.txt"
    code = one_shot.run_spec_once(
        {"cmd": [sys.executable, "-c", f"open({str(out)!r}, 'w').write('ran')"]},
        worker_config=None,
        cwd=tmp_path,
        popen=subprocess.Popen,
    )
    assert code == 0
    assert out.read_text(encoding="utf-8") == "ran"


# --- what the relay knows of the bound job's child ---------------------------------------------


class _LiveProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_the_relay_reaches_the_bound_childs_group() -> None:
    from bajutsu.serve.server.worker_job import CancelRelay

    relay = CancelRelay()
    assert relay.pgid() is None
    relay.kill()  # nothing bound yet: a no-op
    job = srv.Job(cmd=["x"])
    relay.bind(job)
    assert relay.pgid() is None  # bound, but not spawned
    job.proc = _LiveProc(os.getpid())
    assert relay.pgid() == os.getpgid(os.getpid())
    gone = _LiveProc(-1)  # no such group, so the kill falls back to the process itself
    job.proc = gone
    relay.kill()
    assert gone.killed is True


# --- round 2: the liveness pipe is a `run`'s alone, and spec shapes ------------------------------


def test_only_a_run_gets_the_liveness_pipe(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def popen(cmd: list[str], **kw: Any) -> _TreeProc:
        calls.append(kw)
        return _TreeProc(None, 0)

    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=popen)
    srv.run_job(state, srv.Job(cmd=[sys.executable, "-m", "bajutsu", "triage"]), inherit_stdio=True)
    assert "pass_fds" not in calls[0]
    assert PARENT_FD_ENV not in calls[0]["env"]


def test_a_failing_spawn_closes_both_pipe_ends(tmp_path: Path) -> None:
    opened: list[int] = []
    real_pipe = os.pipe

    def tracking_pipe() -> tuple[int, int]:
        r, w = real_pipe()
        opened.extend((r, w))
        return r, w

    def popen(cmd: list[str], **kw: Any) -> Any:
        raise OSError("spawn failed")

    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=popen)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "pipe", tracking_pipe)
        with pytest.raises(OSError, match="spawn failed"):
            srv_jobs._run_inherited(state, srv.Job(cmd=[*_INTERNAL]))
    for fd in opened:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)


def test_the_internal_run_calls_the_apps_shared_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every `bajutsu run` executes in the internal module rather than under the app's callback, so
    # the module calls the same `bootstrap`: a setup step added there reaches the run by construction.
    from bajutsu.run import internal

    calls: list[bool] = []
    monkeypatch.setattr(
        "bajutsu.cli.bootstrap", lambda *, configure_logging: calls.append(configure_logging)
    )
    monkeypatch.setattr(internal, "build_app", lambda: lambda **kw: None)
    internal.main()
    assert calls == [True]


@pytest.mark.parametrize(("sub", "logging_on"), [("serve", False), ("lint", True)])
def test_the_app_callback_skips_logging_for_serve_alone(
    monkeypatch: pytest.MonkeyPatch, sub: str, logging_on: bool
) -> None:
    from bajutsu.cli import _bootstrap
    from bajutsu.common import diagnostics

    seen: list[bool] = []
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda: None)
    monkeypatch.setattr(diagnostics, "configure", lambda: seen.append(True))

    class _Ctx:
        invoked_subcommand = sub

    _bootstrap(_Ctx())  # type: ignore[arg-type]
    assert seen == ([True] if logging_on else [])


@pytest.mark.parametrize("raw", ["not-a-number", "999999"])
def test_the_run_ignores_a_parent_fd_it_cannot_watch(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.run import internal

    monkeypatch.setenv(PARENT_FD_ENV, raw)
    internal._watch_parent()
    assert PARENT_FD_ENV not in os.environ


def test_the_run_ignores_a_parent_fd_that_is_not_a_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.run import internal

    started: list[Any] = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: started.append(k))
    with (tmp_path / "f").open("w") as f:
        monkeypatch.setenv(PARENT_FD_ENV, str(f.fileno()))
        internal._watch_parent()
    assert started == []
    monkeypatch.delenv(PARENT_FD_ENV, raising=False)
    internal._watch_parent()  # no variable: nothing to watch
    assert started == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("build", {"cmd": "make"}),
        ("env_overlay", {"A": 1}),
        ("env_overlay", ["A"]),
        ("app_path", 3),
        ("graceful_cancel", "yes"),
        ("actor", 7),
    ],
)
def test_a_spec_field_of_the_wrong_shape_is_refused(field: str, value: Any) -> None:
    reason = one_shot.refusal({"cmd": ["x"], field: value})
    assert reason is not None and f"`{field}`" in reason


def test_well_shaped_optional_fields_are_accepted() -> None:
    spec = {
        "cmd": ["x"],
        "build": "make app",
        "env_overlay": {"A": "b"},
        "app_path": "App.app",
        "graceful_cancel": True,
        "actor": "me",
    }
    assert one_shot.refusal(spec) is None


def test_the_wrapper_passes_on_only_the_flag_the_caller_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        one_shot, "run_spec_once", lambda spec, **kw: captured.update(spec=spec, kw=kw) or 0
    )
    monkeypatch.setenv("BAJUTSU_EVIDENCE_STORE", "s3://bucket/prefix")
    result = runner.invoke(app, ["run", "--target", "demo"])
    assert result.exit_code == 0, result.output
    assert captured["spec"]["cmd"] == [*_INTERNAL, "--target=demo"]
    assert captured["kw"]["keep_caller_path"] is True


def test_a_queued_spec_with_a_build_command_is_accepted() -> None:
    from bajutsu.serve.server.worker_job import job_spec

    spec = job_spec(srv.Job(cmd=["x"], build="xcodebuild -scheme App", app_path="App.app"))
    assert spec["build"] == "xcodebuild -scheme App"
    assert one_shot.refusal(spec) is None


# --- materials must stay inside the workspace -----------------------------------------------------


@pytest.mark.parametrize("rel", ["../scenario.yaml", "/etc/scenario.yaml", "a/../../b.yaml", ""])
def test_a_material_that_escapes_the_workspace_is_refused(tmp_path: Path, rel: str) -> None:
    for work in (None, tmp_path):
        reason = one_shot.refusal({"cmd": ["x"], "materials": {rel: "y"}}, work)
        assert reason is not None and "inside the workspace" in reason


def test_a_material_reaching_out_through_a_link_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "link").symlink_to(outside, target_is_directory=True)
    reason = one_shot.refusal({"cmd": ["x"], "materials": {"link/s.yaml": "y"}}, work)
    assert reason is not None and "link/s.yaml" in reason
    assert one_shot.refusal({"cmd": ["x"], "materials": {"in/s.yaml": "y"}}, work) is None


def test_worker_once_refuses_an_escaping_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = _spec_file(tmp_path, {"cmd": ["never-run"], "materials": {"../s.yaml": "y"}})
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 2
    assert not (tmp_path.parent / "s.yaml").exists()


# --- a second Ctrl-C before the spawn is registered -----------------------------------------------


class _KillRecorder:
    pid = -1  # no such group: `_kill` / `_terminate` fall back to the methods below
    returncode = -9

    def __init__(self) -> None:
        self.calls: list[str] = []

    def kill(self) -> None:
        self.calls.append("kill")

    def terminate(self) -> None:
        self.calls.append("terminate")

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


@pytest.mark.parametrize("bind_first", [True, False])
def test_a_hard_kill_before_the_spawn_is_consumed_at_registration(bind_first: bool) -> None:
    from bajutsu.serve.server.worker_job import CancelRelay

    relay = CancelRelay()
    job = srv.Job(cmd=["x"], graceful_cancel=True)
    if bind_first:
        relay.bind(job)
    relay.request()
    relay.kill()  # the second Ctrl-C: no child yet, so it is latched
    if not bind_first:
        relay.bind(job)
    assert job.kill_requested and job.cancelled
    proc = _KillRecorder()
    assert srv_jobs._register_proc(job, proc, graceful=True) is False
    assert proc.calls == ["kill"]  # never the SIGTERM a child may ignore


def test_a_hard_kill_reaches_a_child_stopped_at_registration() -> None:
    from bajutsu.serve.server.worker_job import CancelRelay

    relay = CancelRelay()
    job = srv.Job(cmd=["x"], graceful_cancel=True)
    relay.bind(job)
    relay.request()
    proc = _KillRecorder()
    assert srv_jobs._register_proc(job, proc, graceful=True) is False
    assert proc.calls == ["terminate"]
    relay.kill()  # pressed while the caller waits for the terminated child to exit
    assert proc.calls == ["terminate", "kill"]


def test_a_hard_kill_past_a_finished_build_is_latched_for_the_run() -> None:
    from bajutsu.serve.server.worker_job import CancelRelay

    class _Exited(_KillRecorder):
        def poll(self) -> int:
            return 0

    relay = CancelRelay()
    job = srv.Job(cmd=["x"])
    relay.bind(job)
    built = _Exited()
    job.proc = built  # a build phase leaves its exited process until the run is spawned
    relay.kill()
    assert built.calls == [] and job.kill_requested


# --- Windows: no wrapper, and nothing POSIX-only at import -----------------------------------------


def test_on_windows_the_public_run_calls_the_body_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(run_cli, "run_body", lambda **kw: seen.append(kw))
    monkeypatch.setattr(
        one_shot, "run_spec_once", lambda *a, **k: pytest.fail("no wrapper on Windows")
    )
    monkeypatch.setattr(os, "name", "nt")
    run_cli.run(None, target_name="demo")  # type: ignore[arg-type]
    monkeypatch.undo()
    assert seen == [{"target_name": "demo"}]


def test_on_windows_worker_once_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.serve.cli import worker as worker_cli

    spec = _spec_file(tmp_path, {"cmd": ["never-run"]})
    monkeypatch.setattr(os, "name", "nt")
    with pytest.raises(typer.BadParameter) as caught:
        worker_cli._run_once(spec, None)
    monkeypatch.undo()
    assert "POSIX" in str(caught.value)


def test_on_windows_the_step_prompt_reads_without_select(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import select

    monkeypatch.setattr(select, "select", lambda *a: pytest.fail("select on Windows"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    monkeypatch.setattr(os, "name", "nt")
    line = run_cli._read_step_line("step> ")
    monkeypatch.undo()
    assert line == "n\n"


def test_the_one_shot_modules_import_without_posix_job_control_signals() -> None:
    # Windows has no SIGHUP, SIGTSTP, or SIGCONT; a fresh interpreter without them must still import
    # the wrapper's modules and install the cancel handlers it has.
    code = (
        "import signal\n"
        "for name in ('SIGHUP', 'SIGTSTP', 'SIGCONT'):\n"
        "    delattr(signal, name)\n"
        "import bajutsu.run.cli, bajutsu.serve.jobs\n"
        "from bajutsu.serve.server import one_shot\n"
        "from bajutsu.serve.server.worker_job import CancelRelay\n"
        "assert one_shot.CANCEL_SIGNALS == (signal.SIGINT, signal.SIGTERM), one_shot.CANCEL_SIGNALS\n"
        "with one_shot.cancel_on_signals(CancelRelay()) as signalled:\n"
        "    assert signalled() is None\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0, done.stderr


# --- a run argv the worker cannot place `--worker-config` in -------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        ["bajutsu", "run", "--target", "demo"],
        ["python", "bajutsu.run.internal", "run"],
        ["python", "-X", "bajutsu.run.internal"],
    ],
)
def test_an_unrecognised_run_argv_is_refused(cmd: list[str]) -> None:
    reason = one_shot.refusal({"cmd": cmd})
    assert reason is not None and "does not recognise" in reason


def test_worker_once_refuses_an_unrecognised_run_argv_with_exit_2(tmp_path: Path) -> None:
    spec = _spec_file(tmp_path, {"cmd": ["bajutsu", "run", "--target", "demo"]})
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 2, result.output
    assert "does not recognise" in result.output


def test_worker_once_turns_a_late_value_error_into_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(spec: Any, **kw: Any) -> int:
        raise ValueError("no slot for the flag")

    monkeypatch.setattr(one_shot, "run_spec_once", boom)
    spec = _spec_file(tmp_path, {"cmd": ["echo"]})
    result = runner.invoke(app, ["worker", "--once", str(spec)])
    assert result.exit_code == 2
    assert "no slot for the flag" in result.output


def test_a_hard_kill_latched_before_the_cancel_lands_still_kills_at_registration() -> None:
    # The cancel and the kill run on separate helper threads: here the kill's latch lands first and
    # the child registers before the cancel does. The latch alone must make registration a kill.
    from bajutsu.serve.server.worker_job import CancelRelay

    relay = CancelRelay()
    job = srv.Job(cmd=["x"], graceful_cancel=True)
    relay.bind(job)
    relay.kill()
    assert job.kill_requested and not job.cancelled
    proc = _KillRecorder()
    assert srv_jobs._register_proc(job, proc, graceful=True) is False
    assert proc.calls == ["kill"]
    relay.request()  # the late cancel finds nothing graceful left to ask
    assert proc.calls == ["kill", "terminate"]

"""The BE-0361 diagnostics action's shell, run the way GitHub Actions runs it.

Nothing in the gate reaches a composite action's `run:` block — `actionlint` reads
`.github/workflows/` only, and `make lint-sh` a named list of `.sh` files — so this shell shipped
unchecked and took five iOS jobs down with it: a `simctl` screenshot hung on a degraded Simulator, the
watchdog SIGKILLed it, `wait` returned the child's 137, and the errexit that Actions puts on the shell
line failed the whole step. The real test step was then skipped, so a lane whose *diagnostics* fell
over reported nothing about the software under test.

These tests close that hole for the property that matters: **a diagnostics step cannot fail a job.**
They run the action's own script under the exact interpreter and flags Actions uses, with `xcrun`
stubbed to behave like the wedged device the probes exist to document.

Because the script really runs, these tests are platform-sensitive in a way the rest of the suite is
not: the gate runs them on Linux while the action targets macOS, so a contributor on a Mac sees a
green `make check` for an assertion CI will fail. Assert only text the *script* writes, or text a
binary present on both platforms writes (`ps`); for a macOS-only collector like `vm_stat`, assert its
invocation against the script instead of its output.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest
import yaml

# Verbatim from a real job log's `shell:` line. Actions supplies `-e`, which is the whole reason the
# script must disable it: writing `set -uo pipefail` alone leaves errexit on.
_ACTIONS_SHELL = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"]

_ACTION = Path(__file__).resolve().parents[1] / ".github/actions/collect-ios-diagnostics/action.yml"


def _script(phase: str) -> str:
    """The `run:` block of the action's `start` or `collect` step.

    The watchdog limits are shortened here: what is under test is that a killed probe cannot fail the
    step, not how long the real limits wait.
    """
    steps = yaml.safe_load(_ACTION.read_text())["runs"]["steps"]
    wanted = "Start" if phase == "start" else "Collect"
    (step,) = (s for s in steps if s["name"].startswith(wanted))
    run: str = step["run"]
    for slow, quick in (
        ("reap $! 15", "reap $! 1"),
        ('reap "$video" 15', 'reap "$video" 1'),
        ("sleep 5", "sleep 1"),
        ("bounded 300", "bounded 1"),
        ("bounded 180", "bounded 1"),
        ("bounded 30", "bounded 1"),
        ("bounded 20", "bounded 1"),
        ("reap $! 20", "reap $! 1"),
    ):
        run = run.replace(slow, quick)
    return run


def _stub_xcrun(bin_dir: Path, *, hangs: bool) -> None:
    """Put an `xcrun` on PATH that either hangs forever or answers instantly.

    The hanging form `exec`s, so the process the watchdog kills IS the one that hangs — a wrapper
    would leave its child orphaned holding the step's stdout, which models neither a hanging binary
    nor anything the watchdog is meant to handle.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = "exec sleep 3600\n" if hangs else 'echo "{}"\n'
    (bin_dir / "xcrun").write_text(f"#!/bin/bash\n{body}")
    (bin_dir / "xcrun").chmod(0o755)


def _reap_sampler(dest: Path) -> None:
    """Kill the detached telemetry sampler the `start` phase leaves running.

    The sampler is deliberately `nohup`ed and loops forever — correct in a job, which ends and takes it
    with it, but a test process does not end. Left alone, every `start` case leaked a bash loop
    appending to a `tmp_path` that pytest had already deleted; one suite run accumulated 73 of them.
    """
    pidfile = dest / "host-telemetry.pid"
    # Suppressed, not handled: never started, already gone, and unparseable all mean the same thing
    # here — there is nothing to reap.
    with contextlib.suppress(OSError, ValueError):
        os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)


def _run(
    phase: str, tmp_path: Path, *, hangs: bool, **env: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    _stub_xcrun(bin_dir, hangs=hangs)
    dest = tmp_path / "runs" / "diagnostics"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        return _spawn(phase, tmp_path, bin_dir, env)
    finally:
        _reap_sampler(dest)


def _spawn(
    phase: str, tmp_path: Path, bin_dir: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_ACTIONS_SHELL, "-c", _script(phase)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "UDID": "STUB-UDID",
            "DEST": "runs/diagnostics",
            "RUNNER_TEMP": str(tmp_path),
            "FAILED": env.get("FAILED", "false"),
            "CORESIM_LOG_BYTES": "1024",
            "LOG_WINDOW": "1m",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_a_probe_that_has_to_be_killed_does_not_fail_the_step(tmp_path: Path) -> None:
    # The exact regression: the screenshot hangs, the watchdog SIGKILLs it, and `wait` reports 137.
    # Under the errexit Actions supplies, an unabsorbed status there ends the step — and the job.
    done = _run("start", tmp_path, hangs=True)
    assert done.returncode == 0, done.stderr
    # And the stall is recorded rather than swallowed: a probe that had to be killed IS the finding.
    report = (tmp_path / "runs/diagnostics/render-probe.txt").read_text()
    assert "STILL RUNNING" in report


def test_a_probe_that_produced_no_file_reports_zero_rather_than_a_shell_error(
    tmp_path: Path,
) -> None:
    # Observed in a real job: `wc -c <missing 2>/dev/null` does not suppress the redirect failure,
    # because the shell reports that before `wc` runs — so the byte count the report exists to carry
    # arrived under a line of shell diagnostics naming a temp script. The report is read by a human
    # deciding whether the pipeline was alive; noise in it is a defect in the evidence.
    _run("start", tmp_path, hangs=True)
    report = (tmp_path / "runs/diagnostics/render-probe.txt").read_text()
    assert "No such file or directory" not in report, report
    assert "recordVideo: exit 124, 0 bytes" in report, report


def test_the_start_phase_succeeds_against_a_healthy_device(tmp_path: Path) -> None:
    done = _run("start", tmp_path, hangs=False)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "runs/diagnostics/host-telemetry.pid").exists()


def test_the_start_phase_records_a_process_baseline(tmp_path: Path) -> None:
    # The gap the first real collection exposed: `ps` was snapshotted only at a stall, so the evidence
    # said what was resident when it broke and could not say whether that differed from normal.
    done = _run("start", tmp_path, hangs=False)
    assert done.returncode == 0, done.stderr
    baseline = (tmp_path / "runs/diagnostics/ps-baseline.txt").read_text()
    assert "before any scenario ran" in baseline
    # The reading itself, not just the header. `ps aux` is the one collector here that exists on the
    # Linux gate as well as the macOS host this action targets — `vm_stat` is macOS-only, so its
    # presence is asserted against the script below rather than against output this platform cannot
    # produce. Every other test in this file holds to the same line: assert text the script itself
    # writes, never text a macOS binary would have written.
    assert "PID" in baseline, baseline
    # Owner-only like every other artifact of this class (BE-0131).
    assert (tmp_path / "runs/diagnostics/ps-baseline.txt").stat().st_mode & 0o777 == 0o600
    assert "vm_stat" in _script("start")


def test_the_process_baseline_is_not_retaken_by_a_second_start(tmp_path: Path) -> None:
    # Same reason the render probe is guarded: its whole value is being a *pre-run* reading, so the
    # second `start` that `bundled-runner` makes must not overwrite it with a mid-job one.
    _run("start", tmp_path, hangs=False)
    first = (tmp_path / "runs/diagnostics/ps-baseline.txt").read_text()
    done = _run("start", tmp_path, hangs=False)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "runs/diagnostics/ps-baseline.txt").read_text() == first


def test_the_sampler_ranks_processes_by_memory_not_cpu(tmp_path: Path) -> None:
    # `top -l 1` differences nothing, so every process reports 0.0% CPU and `-o cpu` sorts on a
    # constant. Measured: across a whole failing job's telemetry, the rows it kept included the 1.2 GB
    # app zero times. Asserted against the emitted script because the sampler runs detached, so its own
    # output is not this step's to observe.
    _run("start", tmp_path, hangs=False)
    sampler = (tmp_path / "bajutsu-host-telemetry.sh").read_text()
    assert "-o mem" in sampler, sampler
    assert "-o cpu" not in sampler, sampler


def test_the_render_probe_is_not_retaken_by_a_second_start(tmp_path: Path) -> None:
    # `bundled-runner` calls this action twice. The probe's whole value is that it reads the pipeline
    # *before* any scenario ran, so a second start must not overwrite it with a mid-job reading.
    _run("start", tmp_path, hangs=False)
    first = (tmp_path / "runs/diagnostics/render-probe.txt").read_text()
    done = _run("start", tmp_path, hangs=False)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "runs/diagnostics/render-probe.txt").read_text() == first


@pytest.mark.parametrize("failed", ["false", "true"])
def test_the_collect_phase_never_fails_the_step(tmp_path: Path, failed: str) -> None:
    # Both tiers, on a host with no Simulator, no CoreSimulator log, and an `xcrun` that hangs: every
    # collector misses, and the step must still report success — the job's verdict is the test step's.
    done = _run("collect", tmp_path, hangs=True, FAILED=failed)
    assert done.returncode == 0, done.stderr


def test_a_missing_collector_warns_rather_than_passing_silently(tmp_path: Path) -> None:
    # The failure this action replaced was a sweep that collected nothing and said so with silence.
    done = _run("collect", tmp_path, hangs=False, FAILED="false")
    assert done.returncode == 0, done.stderr
    assert "::warning::" in done.stdout

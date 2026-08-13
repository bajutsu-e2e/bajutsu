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
"""

from __future__ import annotations

import os
import shutil
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


def _run(
    phase: str, tmp_path: Path, *, hangs: bool, **env: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    _stub_xcrun(bin_dir, hangs=hangs)
    dest = tmp_path / "runs" / "diagnostics"
    dest.mkdir(parents=True, exist_ok=True)
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


def test_the_start_phase_succeeds_against_a_healthy_device(tmp_path: Path) -> None:
    done = _run("start", tmp_path, hangs=False)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "runs/diagnostics/host-telemetry.pid").exists()


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

"""A run whose worker is gone cancels itself, on the piped path a worker takes (BE-0511)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bajutsu import serve as srv
from bajutsu.run import INTERNAL_MODULE, PARENT_FD_ENV
from bajutsu.serve import jobs as srv_jobs

_RUN = [sys.executable, "-m", INTERNAL_MODULE]


class _Done:
    pid = -1
    returncode = 0
    stdout: tuple[str, ...] = ()

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int:
        return 0


@pytest.mark.parametrize(
    ("cmd", "piped"), [(_RUN, True), ([sys.executable, "-m", "bajutsu", "triage"], False)]
)
def test_only_a_piped_run_watches_its_worker(tmp_path: Path, cmd: list[str], piped: bool) -> None:
    calls: list[dict[str, Any]] = []

    def popen(_cmd: list[str], **kwargs: Any) -> _Done:
        calls.append(kwargs)
        return _Done()

    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, popen=popen)
    srv.run_job(state, srv.Job(cmd=list(cmd)))
    assert (PARENT_FD_ENV in calls[0]["env"], "pass_fds" in calls[0]) == (piped, piped)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes and signals")
def test_closing_the_pipe_cancels_the_run_and_it_still_writes_its_manifest(tmp_path: Path) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo: { platform: android, subject: { id: com.example.demo } }\n",
        encoding="utf-8",
    )
    scenario = tmp_path / "slow.yaml"
    scenario.write_text(
        "- name: slow\n  steps:\n"
        "    - sleep: { seconds: 30, reason: hold the run open until its worker is gone }\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    cmd = [
        *_RUN,
        *("--config", str(config), "--target", "demo", "--scenario", str(scenario)),
        *("--backend", "fake", "--runs-dir", str(runs), "--progress"),
    ]
    state = srv.ServeState(runs_dir=runs, cwd=tmp_path, popen=subprocess.Popen)
    with srv_jobs._spawned(
        state,
        srv.Job(cmd=cmd),
        str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        seen: list[str] = []
        for line in proc.stdout:
            seen.append(line)
            if "sleep" in line:
                break
        assert seen and "sleep" in seen[-1], "".join(seen)
    # The block closed the write end, as a dead worker's operating system would.
    proc.communicate(timeout=60)
    (manifest,) = runs.glob("*/manifest.json")
    report = json.loads(manifest.read_text(encoding="utf-8"))
    assert [s["failure"] for s in report["scenarios"]] == ["cancelled"]

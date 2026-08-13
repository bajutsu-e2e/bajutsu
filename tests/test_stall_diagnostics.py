"""The BE-0361 stall capture: opt-in, bounded, and incapable of raising into a failure path.

Every probe shells out to a host tool, so the subprocess boundary is faked here — what these tests
pin is the *contract* around it: nothing runs unless the operator asked, nothing runs unbounded, and
nothing a probe does can escape as an exception into the crash the capture is documenting.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bajutsu import stall_diagnostics


def _capture_dir(root: Path, index: int, reason: str) -> Path:
    """The one capture directory for `index`/`reason` — the stem carries this process's pid too."""
    (found,) = root.glob(f"stall-{index:02d}-{reason}-*")
    return found


@pytest.fixture(autouse=True)
def _fresh_capture_count() -> Any:
    """The per-run cap is module state; a leaked count would make these tests order-dependent."""
    stall_diagnostics.reset()
    yield
    stall_diagnostics.reset()


class _FakeCompleted:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _record_runs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every subprocess the module would have spawned, answering each one successfully."""
    calls: list[dict[str, Any]] = []

    def _run(argv: list[str], **kw: Any) -> _FakeCompleted:
        calls.append({"argv": argv, "timeout": kw.get("timeout")})
        # `pgrep` drives the sample loop; one pid per process keeps the fan-out predictable.
        stdout = b"4242\n" if argv[0].endswith("pgrep") else b"out"
        return _FakeCompleted(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_capture_is_a_no_op_until_the_operator_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Unset is the default everywhere but the iOS CI lane, so an ordinary local run must not shell
    # out to `sample`/`ps` on a crash — nor create a directory to say it thought about it.
    monkeypatch.delenv(stall_diagnostics._DIAGNOSTICS_ENV, raising=False)
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", "UDID")
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_capture_writes_the_probes_the_hypotheses_need(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", "UDID")

    dest = _capture_dir(tmp_path, 1, "runner-crash")
    assert dest.is_dir()
    programs = [call["argv"][0] for call in calls]
    # The screenshot separates a wedged render service from a wedged runner; ps/vm_stat answer the
    # host-starvation hypothesis; `sample` says where the wedged processes are stuck.
    assert "xcrun" in programs
    assert "/bin/ps" in programs
    assert "/usr/bin/vm_stat" in programs
    assert "/usr/bin/sample" in programs
    assert (dest / "ps.txt").read_bytes() == b"out"
    assert "screenshot: exit 0" in (dest / stall_diagnostics._SUMMARY).read_text()


def test_every_probe_carries_a_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A capture runs on a path that is already failing; an unbounded probe there would trade a
    # diagnosable failure for a job cancelled at `timeout-minutes`.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", "UDID")
    assert calls  # the assertion below is vacuous otherwise
    assert all(
        call["timeout"] is not None and 0 < call["timeout"] <= stall_diagnostics._CAPTURE_BUDGET
        for call in calls
    )


def test_the_screenshot_is_skipped_without_a_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The video-no-bytes trigger knows its udid, but a future caller may not; the host-side probes
    # answer the starvation hypothesis on their own rather than the capture refusing to run.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("video-no-bytes")
    assert "xcrun" not in [call["argv"][0] for call in calls]
    assert (_capture_dir(tmp_path, 1, "video-no-bytes") / "vm_stat.txt").exists()


def test_a_run_takes_at_most_the_capture_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A crash-looping run must not fill the disk or the wall clock with repeats of the same evidence.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    for _ in range(stall_diagnostics._MAX_CAPTURES + 3):
        stall_diagnostics.capture("runner-crash", "UDID")
    assert len(list(tmp_path.iterdir())) == stall_diagnostics._MAX_CAPTURES


def test_a_probe_that_times_out_is_recorded_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A `simctl` screenshot that also stalls is the *finding*, not an error: it says the render
    # service is wedged rather than the runner. It must reach the summary, never the caller.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **kw: Any) -> _FakeCompleted:
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", "UDID")
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert "TIMED OUT" in summary


def test_a_missing_tool_never_escapes_into_the_failure_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **_kw: Any) -> _FakeCompleted:
        raise OSError("no such tool")

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", "UDID")  # must not raise
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert "OSError" in summary


def test_an_unwritable_destination_is_survived(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The directory the operator named may not be creatable (a stale path, a read-only mount). That
    # is a diagnostics failure, and a diagnostics failure must never replace the crash it documents.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(blocker))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", "UDID")  # must not raise
    assert calls == []  # and must not probe into a directory it could not create


def test_the_reason_cannot_escape_the_capture_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reason names a path. Today's two callers pass constants, but the sanitizer is what keeps
    # that a local convention rather than a traversal waiting for a third caller.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path / "root"))
    _record_runs(monkeypatch)
    stall_diagnostics.capture("../../escaped")
    assert [p.name for p in (tmp_path / "root").iterdir()] == [f"stall-01-escaped-{os.getpid()}"]

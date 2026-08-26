"""The stall capture: opt-in, bounded, and incapable of raising into a failure path.

Every probe shells out to a host tool, so the subprocess boundary is faked here — what these tests
pin is the *contract* around it: nothing runs unless the operator asked, nothing runs unbounded, and
nothing a probe does can escape as an exception into the crash the capture is documenting.

Both backends' probe sets are covered: the Simulator one (BE-0361) and the adb one (BE-0367). The
contract above belongs to `capture`, which owns it for whichever set a trigger hands it, so the
Android tests at the end assert that the adb set inherits it rather than re-implementing it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from bajutsu import artifact_perms, stall_diagnostics


def _capture_dir(root: Path, index: int, reason: str) -> Path:
    """The one capture directory for `index`/`reason` — the stem carries this process's pid too."""
    (found,) = root.glob(f"stall-{index:02d}-{reason}-*")
    return found


@pytest.fixture(autouse=True)
def _fresh_capture_count() -> Any:
    """The budget and the warn-once flag are module state; a leak would make these tests order-dependent."""
    stall_diagnostics.reset()
    yield
    stall_diagnostics.reset()


class _FakeCompleted:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _record_runs(
    monkeypatch: pytest.MonkeyPatch, *, pids: dict[str, bytes] | None = None
) -> list[dict[str, Any]]:
    """Capture every subprocess the module would have spawned, answering each one successfully.

    The fake also *writes* the files the real commands write for themselves — `simctl io screenshot`'s
    png and `sample -file`'s report — because `restrict_file` is a no-op on an absent file, so a fake
    that skipped them would leave the owner-only guarantee asserted by nothing. `pids` overrides what
    `pgrep` answers per process name, for the several-pids fan-out the sample cap exists to bound.
    """
    calls: list[dict[str, Any]] = []

    def _run(argv: list[str], **kw: Any) -> _FakeCompleted:
        calls.append({"argv": argv, "timeout": kw.get("timeout")})
        if argv[0].endswith("pgrep"):
            # `pgrep` drives the sample loop; one pid per name unless a test asks for a fan-out.
            return _FakeCompleted(stdout=(pids or {}).get(argv[-1], b"4242\n"))
        if argv[0].endswith("sample"):
            Path(argv[argv.index("-file") + 1]).write_bytes(b"stack")
        elif "screenshot" in argv:
            Path(argv[-1]).write_bytes(b"png")
        return _FakeCompleted(stdout=b"out")

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_capture_is_a_no_op_until_the_operator_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Unset is the default everywhere but the iOS CI lane, so an ordinary local run must not shell
    # out to `sample`/`ps` on a crash — nor create a directory to say it thought about it.
    monkeypatch.delenv(stall_diagnostics._DIAGNOSTICS_ENV, raising=False)
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_capture_writes_the_probes_the_hypotheses_need(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))

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
    summary = (dest / stall_diagnostics._SUMMARY).read_text()
    assert "screenshot: exit 0" in summary
    # An absolute stamp is what lets a capture be lined up against the host telemetry and the
    # unified-log extracts, which are the other two layers' wall-clock-stamped output.
    assert "captured at" in summary
    # The sub-second snapshots run before the screenshot, which can spend half the budget on the
    # interesting case: the host's load must already be on record by the time that probe runs.
    assert programs.index("/bin/ps") < programs.index("xcrun")


def test_every_probe_carries_a_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A capture runs on a path that is already failing; an unbounded probe there would trade a
    # diagnosable failure for a job cancelled at `timeout-minutes`.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
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
    stall_diagnostics.capture("video-no-bytes", stall_diagnostics.simulator_probes())
    assert "xcrun" not in [call["argv"][0] for call in calls]
    assert (_capture_dir(tmp_path, 1, "video-no-bytes") / "vm_stat.txt").exists()


def test_a_run_takes_at_most_the_budget_for_one_trigger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A crash-looping run must not fill the disk or the wall clock with repeats of the same evidence.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    for _ in range(stall_diagnostics._MAX_CAPTURES_PER_REASON + 3):
        stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    assert len(list(tmp_path.iterdir())) == stall_diagnostics._MAX_CAPTURES_PER_REASON


def test_a_frequent_trigger_cannot_starve_the_crash_trigger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The budget is per trigger for one reason: `recordVideo produced no new bytes` fires on every
    # scenario of these runners, green runs included, and a job runs a dozen scenarios in one process.
    # Under a single shared budget the video warnings would spend it before the runner crash — the very
    # failure the capture exists to explain — ever reached the collector.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    for _ in range(12):
        stall_diagnostics.capture("video-no-bytes", stall_diagnostics.simulator_probes("UDID"))
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))

    names = sorted(p.name for p in tmp_path.iterdir())
    assert sum("video-no-bytes" in n for n in names) == stall_diagnostics._MAX_CAPTURES_PER_REASON
    assert sum("runner-crash" in n for n in names) == 1


def test_a_declined_capture_is_logged_loudly_enough_to_be_seen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The iOS lane installs no log handler, so a `debug` refusal is no record at all — and the operator
    # reading the artifact needs to know a stall went uncaptured before concluding the evidence is silent.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    for _ in range(stall_diagnostics._MAX_CAPTURES_PER_REASON):
        stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    with caplog.at_level("WARNING", logger="bajutsu.stall_diagnostics"):
        stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    assert any("NOT capturing" in record.message for record in caplog.records)


def test_an_unexpected_error_inside_the_capture_never_escapes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `_probe` catches OSError and SubprocessError; the module's promise is broader than that, because
    # the video trigger calls `capture` bare from inside a live scenario's evidence setup. A malformed
    # argv raises ValueError from `subprocess.run`, and the next probe someone adds may raise something
    # else again — so the wall lives in `capture`, not in each probe.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **_kw: Any) -> _FakeCompleted:
        raise ValueError("malformed argv")

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture(
        "runner-crash", stall_diagnostics.simulator_probes("UDID")
    )  # must not raise


def test_a_probe_that_times_out_is_recorded_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A `simctl` screenshot that also stalls is the *finding*, not an error: it says the render
    # service is wedged rather than the runner. It must reach the summary, never the caller.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **kw: Any) -> _FakeCompleted:
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert "TIMED OUT" in summary


def test_a_missing_tool_never_escapes_into_the_failure_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **_kw: Any) -> _FakeCompleted:
        raise OSError("no such tool")

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture(
        "runner-crash", stall_diagnostics.simulator_probes("UDID")
    )  # must not raise
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
    stall_diagnostics.capture(
        "runner-crash", stall_diagnostics.simulator_probes("UDID")
    )  # must not raise
    assert calls == []  # and must not probe into a directory it could not create


def test_the_reason_cannot_escape_the_capture_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reason names a path. Today's two callers pass constants, but the sanitizer is what keeps
    # that a local convention rather than a traversal waiting for a third caller.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path / "root"))
    _record_runs(monkeypatch)
    stall_diagnostics.capture("../../escaped", stall_diagnostics.simulator_probes())
    assert [p.name for p in (tmp_path / "root").iterdir()] == [f"stall-01-escaped-{os.getpid()}"]


def test_the_capture_budget_clamps_and_then_skips_the_remaining_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The module's headline bound: a capture spends at most `_CAPTURE_BUDGET` across *all* its probes,
    # not per probe. Driven by a fake clock rather than real waiting, so the test stays deterministic —
    # each `monotonic()` reading advances a fixed step, which walks the budget down predictably.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    ticks = iter(range(10_000))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)) * 4.0)
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))

    timeouts = [call["timeout"] for call in calls]
    assert timeouts, "no probe ran, so the clamp below is vacuous"
    # No probe may be handed more than the budget has left, so the total never exceeds the budget's
    # own ceiling — the property an unclamped `timeout=` would break while every other test passed.
    assert all(0 < t <= stall_diagnostics._CAPTURE_BUDGET for t in timeouts)
    assert min(timeouts) < stall_diagnostics._SNAPSHOT_TIMEOUT  # a later probe was clamped down
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert "the capture budget is spent" in summary


def test_every_collected_file_is_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A device screenshot and a thread sample are exactly the artifacts BE-0131 locks to owner-only,
    # and the files the probes write for *themselves* are the ones a `restrict_file` slip would leave
    # world-readable — the capture directory's mode must not be the only guard.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))

    dest = _capture_dir(tmp_path, 1, "runner-crash")
    assert dest.stat().st_mode & 0o777 == artifact_perms.RUN_DIR_MODE
    written = sorted(p.name for p in dest.iterdir())
    assert "screenshot.png" in written and any(n.startswith("sample-") for n in written)
    for path in dest.iterdir():
        assert path.stat().st_mode & 0o777 == artifact_perms.ARTIFACT_FILE_MODE, path.name


def test_a_frame_written_before_a_probe_was_killed_is_still_locked_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The wedge case: `simctl` writes the png, then stops answering and the probe times out. The frame
    # exists and is just as sensitive, so the failure paths must secure it too.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **kw: Any) -> _FakeCompleted:
        if "screenshot" in argv:
            Path(argv[-1]).write_bytes(b"png")
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    png = _capture_dir(tmp_path, 1, "runner-crash") / "screenshot.png"
    assert png.stat().st_mode & 0o777 == artifact_perms.ARTIFACT_FILE_MODE


def test_one_process_fan_out_cannot_crowd_out_the_render_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Why the per-name slice exists: one name resolves to several pids whenever the host has more than
    # one Simulator booted (each device runs its own `backboardd`), and an unsliced walk would let that
    # one name spend the whole allowance — leaving `testmanagerd`, central to the XCTest-host wedge,
    # unsampled.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    crowded = stall_diagnostics._SAMPLE_PROCESSES[0]
    calls = _record_runs(monkeypatch, pids={crowded: b"1 2 3 4 5 6\n"})
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))

    sampled = [
        call["argv"][1] for call in calls if call["argv"][0].endswith("sample")
    ]  # the pid argument
    # The crowded name gets its slice and no more, and every other process still gets sampled.
    assert sum(pid in {"1", "2", "3", "4", "5", "6"} for pid in sampled) == (
        stall_diagnostics._MAX_PIDS_PER_PROCESS
    )
    others = len(stall_diagnostics._SAMPLE_PROCESSES) - 1
    assert sum(pid == "4242" for pid in sampled) == others


def test_a_non_simulator_device_id_is_refused_without_stopping_the_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `screenshot_cmd` validates the id before it reaches an argv. A caller that hands over an Android
    # serial should lose the screenshot only — the host-side probes still answer the starvation
    # hypothesis, and nothing may raise back into the failure path.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("-not-a-udid"))

    dest = _capture_dir(tmp_path, 1, "runner-crash")
    assert "refused the device id" in (dest / stall_diagnostics._SUMMARY).read_text()
    assert not (dest / "screenshot.png").exists()
    assert (dest / "vm_stat.txt").exists()
    assert "xcrun" not in [call["argv"][0] for call in calls]


def test_a_process_that_is_not_running_is_recorded_rather_than_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A wedged Simulator whose `backboardd` has died IS the diagnosis for the render-service
    # hypothesis. Omitting the line would leave "dead" indistinguishable from "never probed".
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    absent = stall_diagnostics._SAMPLE_PROCESSES[1]
    _record_runs(monkeypatch, pids={absent: b""})
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert f"sample:{absent}: NOT RUNNING" in summary


def test_a_failed_process_lookup_is_not_read_as_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `pgrep` exits 1 for "no match" but 2/3 for a usage or fatal error. Reading only stdout would make
    # a broken lookup indistinguishable from a process that simply is not there, and the wedge-central
    # CoreSimulator service would go unsampled with nothing saying so.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **_kw: Any) -> _FakeCompleted:
        if argv[0].endswith("pgrep"):
            return _FakeCompleted(returncode=2)
        return _FakeCompleted(stdout=b"out")

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    summary = (_capture_dir(tmp_path, 1, "runner-crash") / stall_diagnostics._SUMMARY).read_text()
    assert "exit 2 — the lookup failed" in summary
    assert "NOT RUNNING" not in summary  # a broken lookup is not an absent process


def test_an_unwritable_summary_warns_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An unwritable summary loses *every* probe outcome, so it cannot vanish at debug on a lane that
    # installs no log handler — and it must not flood the log either, since the same failure repeats
    # for every probe and would bury the crash underneath it.
    # The warn-once state is process-wide, so `reset()` — which the autouse fixture runs — is what keeps
    # an earlier test's warning from suppressing this one's.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)
    real_open = Path.open

    def _open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == stall_diagnostics._SUMMARY:
            raise OSError("no space left on device")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)
    with caplog.at_level("WARNING", logger="bajutsu.stall_diagnostics"):
        stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    unwritable = [r for r in caplog.records if "cannot write" in r.message]
    assert len(unwritable) == 1
    # And `reset()` must forget the warning too, or a long-lived process — the `bajutsu serve` case the
    # module's own budget comment calls out — goes permanently silent after its first unwritable summary.
    stall_diagnostics.reset()
    with caplog.at_level("WARNING", logger="bajutsu.stall_diagnostics"):
        stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    assert len([r for r in caplog.records if "cannot write" in r.message]) == 2


def test_a_sample_written_before_an_error_is_still_locked_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The OSError sibling of the timeout case: `sample` writes its report, then the call fails. The
    # report exists and is just as sensitive, so this failure path must secure it too.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def _run(argv: list[str], **_kw: Any) -> _FakeCompleted:
        if argv[0].endswith("pgrep"):
            return _FakeCompleted(stdout=b"4242\n")
        if argv[0].endswith("sample"):
            Path(argv[argv.index("-file") + 1]).write_bytes(b"stack")
            raise OSError("the sampler died writing")
        return _FakeCompleted(stdout=b"out")

    monkeypatch.setattr(subprocess, "run", _run)
    stall_diagnostics.capture("runner-crash", stall_diagnostics.simulator_probes("UDID"))
    reports = list(_capture_dir(tmp_path, 1, "runner-crash").glob("sample-*.txt"))
    assert reports
    for report in reports:
        assert report.stat().st_mode & 0o777 == artifact_perms.ARTIFACT_FILE_MODE, report.name


def test_the_capture_budget_stays_small_against_the_lane_recovery_budget() -> None:
    # A capture is charged to a clock it does not own. BE-0353's `CrashRecoveryBudget` is a wall-clock
    # deadline set at the *first* crash and re-checked at each later one, so every second spent
    # capturing is a second the recovery no longer has. A `visual` run on 2026-08-13 missed that
    # deadline by 75s with two captures of up to 30s inside the window — a diagnostic able to turn a
    # recoverable degradation into a failed run, which is precisely what an observer may not do.
    #
    # Pinned as a *relationship*, read from the lane that actually sets the recovery budget, so raising
    # the capture budget or lowering the lane's fails here rather than in a red iOS job.
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ios-e2e.yml").read_text()
    )
    recovery_budget = float(workflow["env"]["BAJUTSU_CRASH_RECOVERY_BUDGET"])
    worst_case = stall_diagnostics._CAPTURE_BUDGET * stall_diagnostics._MAX_CAPTURES_PER_REASON
    assert worst_case <= recovery_budget / 10


# --- the adb probe set (BE-0367): the Android half of the same seam ---


def test_the_device_probes_read_the_emulator_and_its_linux_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Android twin of the Simulator set: two host snapshots that answer the starvation
    # hypothesis, and two `adb` reads that answer the wedged-device one.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)

    stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("emulator-5554"))

    dest = _capture_dir(tmp_path, 1, "resident-read")
    assert {p.name for p in dest.iterdir()} == {
        stall_diagnostics._SUMMARY,
        "ps.txt",
        "top.txt",
        "surfaceflinger-latency.txt",
        "logcat-tail.txt",
    }
    # Addressed by serial, like every other adb argv: a parallel lane's second emulator must not be
    # the one this capture reads.
    device_reads = [c["argv"] for c in calls if c["argv"][0] == "adb"]
    assert device_reads and all(a[1:3] == ["-s", "emulator-5554"] for a in device_reads)


def test_the_host_snapshots_land_before_the_device_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Ordering by cost, the same reason the Simulator set snapshots first: the `adb` reads are the
    # ones a wedged emulator can hold to their whole ceiling, and the host's load — the answer to the
    # starvation hypothesis — must be on record before a probe outruns the shared budget.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)

    stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("SER"))

    order = [c["argv"][0] for c in calls]
    assert order.index("ps") < order.index("adb")
    assert order.index("top") < order.index("adb")


def test_every_device_probe_carries_a_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An unbounded probe on a wedged emulator would hang the very run the capture exists to leave
    # diagnosable (prime directive 2). Two `adb` reads at a snapshot's ceiling would also outrun the
    # shared budget, so they get their own smaller one.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)

    stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("SER"))

    assert all(c["timeout"] for c in calls)
    assert {c["timeout"] for c in calls if c["argv"][0] == "adb"} == {
        stall_diagnostics._ADB_TIMEOUT
    }
    assert (
        stall_diagnostics._ADB_TIMEOUT * 2 + stall_diagnostics._SNAPSHOT_TIMEOUT
        <= stall_diagnostics._CAPTURE_BUDGET * 2
    )


def test_a_refused_device_serial_leaves_the_host_snapshots_standing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A serial the adb builders refuse (one leading with `-`, which adb would read as an option)
    # must not cost the capture its host half — a summary silent about the device would otherwise
    # read as a capture that found the emulator healthy.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    calls = _record_runs(monkeypatch)

    stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("-not-a-serial"))

    dest = _capture_dir(tmp_path, 1, "resident-read")
    assert (dest / "ps.txt").exists() and (dest / "top.txt").exists()
    assert "refused the device serial" in (dest / stall_diagnostics._SUMMARY).read_text()
    assert not [c for c in calls if c["argv"][0] == "adb"]


def test_every_device_probe_file_is_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The adb captures land in the same operator-named directory as the Simulator ones, so they are
    # held to the same BE-0131 permissions — a logcat tail carries whatever the app logged.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)

    stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("SER"))

    for collected in _capture_dir(tmp_path, 1, "resident-read").iterdir():
        assert collected.stat().st_mode & 0o777 == artifact_perms.ARTIFACT_FILE_MODE, collected.name


def test_the_two_backends_share_the_per_trigger_budget_machinery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The point of the seam: a backend contributes probes, not its own opt-in gate, cap, or summary.
    # The adb triggers therefore inherit the per-trigger budget rather than re-implementing it.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))
    _record_runs(monkeypatch)

    for _ in range(stall_diagnostics._MAX_CAPTURES_PER_REASON + 1):
        stall_diagnostics.capture("resident-read", stall_diagnostics.device_probes("SER"))
    # A different trigger still has its own budget, so a frequent one cannot starve a rare one.
    stall_diagnostics.capture("screenrecord-no-growth", stall_diagnostics.device_probes("SER"))

    assert len(list(tmp_path.glob("stall-*-resident-read-*"))) == (
        stall_diagnostics._MAX_CAPTURES_PER_REASON
    )
    assert len(list(tmp_path.glob("stall-*-screenrecord-no-growth-*"))) == 1


def test_a_probe_set_that_raises_never_escapes_into_the_failure_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The never-raises promise covers the backend's half too, not only the machinery's: a caller is
    # a failure path, so a bug in a probe set must cost a log line and nothing else.
    monkeypatch.setenv(stall_diagnostics._DIAGNOSTICS_ENV, str(tmp_path))

    def exploding(_capture: stall_diagnostics.Capture) -> None:
        raise RuntimeError("a backend's probe set went wrong")

    stall_diagnostics.capture("resident-read", exploding)  # must not raise

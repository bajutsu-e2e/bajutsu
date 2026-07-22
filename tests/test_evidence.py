"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink, capture, write_elements, write_screenshot


class _StubInterval:
    """A finished recording, standing in for the subprocess-backed `Interval` (an external
    boundary): `finish_scenario_intervals` only needs `stop()` / `kind` / `provider`."""

    def __init__(self, path: Path, kind: str = "deviceLog", provider: str = "idb") -> None:
        self._path = path
        self.kind = kind
        self.provider = provider

    def stop(self) -> Path:
        return self._path


def _el(identifier: str, label: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_write_elements(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A"), _el("b", "B")])
    path = write_elements(driver, tmp_path / "step0")
    assert path.name == "elements.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [e["identifier"] for e in data] == ["a", "b"]


def test_write_elements_uses_provided_elements(tmp_path: Path) -> None:
    """When pre-queried elements are provided, write_elements uses them
    instead of calling driver.query()."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    path = write_elements(driver, tmp_path / "step0", elements=provided)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_uses_provided_elements(tmp_path: Path) -> None:
    """capture() passes provided elements through to write_elements."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    capture(driver, tmp_path / "step0", ["elements"], elements=provided)
    data = json.loads((tmp_path / "step0" / "elements.json").read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_elements_and_screenshot(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    written = capture(driver, tmp_path / "step0", ["elements", "screenshot.after"])
    assert [(a.name, a.kind, a.provider) for a in written] == [
        ("elements.json", "elements", "driver"),
        ("after.png", "screenshot", "driver"),
    ]
    assert (tmp_path / "step0" / "elements.json").exists()
    # FakeDriver records the screenshot call with the path it was given.
    assert ("screenshot", str(tmp_path / "step0" / "after.png")) in driver.actions


def test_file_sink_wait_diagnostic_writes_provenance_stamped_artifact(tmp_path: Path) -> None:
    # BE-0231 Unit 1: a first-wait timeout writes a self-contained diagnostic — the element tree at
    # timeout, the readiness signal, the poll trace, and the provenance stamp — so a rerun-to-green
    # never discards the evidence needed to decide which cause fired.
    from bajutsu.orchestrator.waits import WaitTrace
    from bajutsu.platform_lifecycle import ReadinessResult

    driver = FakeDriver([_el("a", "A"), _el("b", "B")])
    trace = WaitTrace(
        target="{'id': 'stable.row.1'}",
        timeout_s=30.0,
        polls=120,
        first_nonempty_s=0.3,
        elements_at_timeout=2,
    )
    sink = FileSink(
        tmp_path,
        readiness=ReadinessResult(True, "readyWhen", 2.1),
        provenance={
            "scenarioHash": "sha256:abc",
            "toolVersion": "9.9.9",
            "gitRevision": "deadbeef",
        },
    )
    art = sink.wait_diagnostic("00-x/step0", trace=trace, elements=driver.query())
    assert art is not None
    assert art.name == "00-x/step0/wait-timeout.json"
    doc = json.loads((tmp_path / "00-x/step0/wait-timeout.json").read_text(encoding="utf-8"))
    assert doc["target"] == "{'id': 'stable.row.1'}"
    assert doc["timeoutSeconds"] == 30.0
    assert doc["readiness"] == {"ready": True, "signal": "readyWhen", "elapsedSeconds": 2.1}
    assert doc["trace"]["polls"] == 120
    assert doc["trace"]["firstNonemptySeconds"] == 0.3
    assert doc["trace"]["elementsAtTimeout"] == 2
    assert doc["trace"]["awaitedEverQueryable"] is False
    assert doc["provenance"]["scenarioHash"] == "sha256:abc"
    assert doc["provenance"]["gitRevision"] == "deadbeef"
    assert [e["identifier"] for e in doc["elements"]] == ["a", "b"]


def test_file_sink_wait_diagnostic_survives_missing_readiness_and_provenance(
    tmp_path: Path,
) -> None:
    # A backend/lane that never carried a readiness result (or a run outside git) still gets a
    # diagnostic — the missing pieces are recorded as null, never a crash on the failure path.
    from bajutsu.orchestrator.waits import WaitTrace

    sink = FileSink(tmp_path)
    art = sink.wait_diagnostic(
        "00-x/step0", trace=WaitTrace(target="{'id': 'z'}", timeout_s=5.0), elements=[]
    )
    assert art is not None
    doc = json.loads((tmp_path / "00-x/step0/wait-timeout.json").read_text(encoding="utf-8"))
    assert doc["readiness"] is None
    assert doc["provenance"] is None
    assert doc["trace"]["firstNonemptySeconds"] is None


def test_file_sink_wait_diagnostic_redacts_and_locks_down_the_dump(tmp_path: Path) -> None:
    # The element tree at timeout can hold on-screen secrets: the diagnostic must scrub configured
    # secret values and leave the file owner-only, like the other sensitive dumps (BE-0131).
    from bajutsu.orchestrator.waits import WaitTrace

    sink = FileSink(tmp_path, secrets=["hunter2"])
    path = tmp_path / "00-x/step0/wait-timeout.json"
    sink.wait_diagnostic(
        "00-x/step0", trace=WaitTrace(target="{'id': 'z'}"), elements=[_el("f", "hunter2")]
    )
    text = path.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_null_sink_wait_diagnostic_is_a_noop() -> None:
    from bajutsu.evidence import NullSink
    from bajutsu.orchestrator.waits import WaitTrace

    assert NullSink().wait_diagnostic("s", trace=WaitTrace(), elements=[]) is None


class _WritingDriver(FakeDriver):
    """A driver whose `screenshot` actually writes bytes, unlike `FakeDriver` (which only records
    the call). Needed to observe the file mode `write_screenshot` leaves behind (BE-0131)."""

    def screenshot(self, path: str) -> None:
        super().screenshot(path)
        Path(path).write_bytes(b"\x89PNG\r\n")


def test_write_screenshot_is_owner_only(tmp_path: Path) -> None:
    # A screenshot can capture on-screen secrets, so it must land owner-only (0600), not
    # world-readable under the ambient umask (BE-0131).
    path = write_screenshot(_WritingDriver([_el("a", "A")]), tmp_path / "step0")
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_elements_is_owner_only(tmp_path: Path) -> None:
    # The element dump holds on-screen text (labels / values), redacted best-effort — owner-only,
    # like the other sensitive artifacts (BE-0131, issue #558's accessibility-dump scope).
    path = write_elements(FakeDriver([_el("a", "A")]), tmp_path / "step0")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_capture_no_writing_kinds_leaves_dir_uncreated(tmp_path: Path) -> None:
    """capture() creates the step dir only when it actually writes a file; a kind it
    does not handle here (e.g. an interval kind) must leave the dir untouched, as before."""
    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    assert capture(driver, step_dir, ["video"]) == []
    assert not step_dir.exists()


def test_capture_creates_dir_once_for_writing_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture of two writing kinds creates the step dir exactly once, not once per writer.

    Counts `Path.mkdir` calls on the step dir: a regression to per-writer `mkdir()` would make it
    fire two-plus times. Both files still land under the freshly created dir.
    """
    mkdirs: list[Path] = []
    real_mkdir = Path.mkdir

    def counting_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        mkdirs.append(self)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)

    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    capture(driver, step_dir, ["elements", "screenshot.after"])
    assert (step_dir / "elements.json").exists()
    assert ("screenshot", str(step_dir / "after.png")) in driver.actions
    assert mkdirs.count(step_dir) == 1  # the step dir is created once, not per writing kind


def test_filesink_dispatches_intervals_to_web_provider(tmp_path: Path) -> None:
    # When a web interval provider is injected, the sink uses it (Playwright-native) instead of
    # the simctl starters, even though the web lane carries a (synthetic) udid.
    from bajutsu.evidence import FileSink, intervals

    calls: list[tuple[str, str]] = []

    def driver_interval(kind: str, path: Path) -> intervals.Interval | None:
        calls.append((kind, path.name))
        if kind == "deviceLog":
            return intervals.Interval(kind="deviceLog", path=path, provider="playwright")
        return None  # video etc. not provided in this slice

    sink = FileSink(tmp_path, udid="web-0", driver_interval=driver_interval)
    started = sink.start_scenario_intervals("00-s", ["deviceLog", "video"])

    assert calls == [("deviceLog", "device.log"), ("video", "scenario.mp4")]
    # Only the provided (deviceLog) interval is started; the unsupported video is skipped.
    assert [iv.kind for iv in started] == ["deviceLog"]
    assert started[0].provider == "playwright"


def test_filesink_without_web_provider_uses_udid_gate(tmp_path: Path) -> None:
    from bajutsu.evidence import FileSink

    # No udid and no web provider: intervals are skipped (the fake/headless path).
    sink = FileSink(tmp_path, udid=None)
    assert sink.start_scenario_intervals("00-s", ["deviceLog"]) == []


def test_filesink_dispatches_adb_driver_intervals_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real seam: FileSink + AdbDriver.driver_interval start BOTH kinds via adb (not the simctl
    # path), even with a udid set. The subprocess spawn is faked so no adb process runs; the video's
    # pull/rm still go through the driver's injected run.
    from bajutsu.drivers.adb import AdbDriver
    from bajutsu.evidence import intervals

    class _FakeProc:
        def stop(self, sig: int, timeout: float) -> None:
            return None

    monkeypatch.setattr(intervals, "_SubprocessProc", lambda argv, stdout_path: _FakeProc())

    ran: list[list[str]] = []

    def run(argv: list[str]) -> str:
        ran.append(argv)
        return ""

    sink = FileSink(tmp_path, udid="SER", driver_interval=AdbDriver("SER", run=run).driver_interval)
    started = sink.start_scenario_intervals("00-s", ["video", "deviceLog"])
    assert {(iv.kind, iv.provider) for iv in started} == {("video", "adb"), ("deviceLog", "adb")}

    arts = sink.finish_scenario_intervals("00-s", started)
    assert {(a.kind, a.provider) for a in arts} == {("video", "adb"), ("deviceLog", "adb")}
    # The video's pull + rm rode the driver's injected run (device-side capture pulled to the host).
    assert any("pull" in c for c in ran) and any("rm" in c for c in ran)


def test_filesink_adopts_a_prestarted_video_instead_of_starting_one(tmp_path: Path) -> None:
    # A device backend starts its video before launch; the sink must adopt that running interval for
    # the "video" kind (relocating it to the artifact path on stop) rather than ask the driver /
    # simctl to start a fresh one after launch — the other kinds still start on demand.
    from bajutsu.evidence import FileSink, intervals

    temp = tmp_path / "_video_tmp" / "prestart-SER.mp4"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"clip")

    class _Proc:
        def stop(self, sig: int, timeout: float) -> None:
            pass

    prestarted = intervals.start_video("SER", temp, spawn=lambda argv, out: _Proc())

    asked: list[str] = []

    def driver_interval(kind: str, path: Path) -> intervals.Interval | None:
        asked.append(kind)  # a prestarted kind must never reach the on-demand provider
        return intervals.Interval(kind=kind, path=path, provider="adb")

    sink = FileSink(
        tmp_path, udid="SER", driver_interval=driver_interval, prestarted_intervals=[prestarted]
    )
    started = sink.start_scenario_intervals("00-s", ["video", "deviceLog"])

    assert asked == ["deviceLog"]  # video adopted; only deviceLog started on demand
    assert [iv.kind for iv in started] == ["video", "deviceLog"]
    assert started[0].stop() == tmp_path / "00-s" / "scenario.mp4"
    assert (tmp_path / "00-s" / "scenario.mp4").read_bytes() == b"clip" and not temp.exists()


def test_finish_scenario_intervals_drops_a_failed_stop_but_finishes_the_rest(
    tmp_path: Path,
) -> None:
    # A stop() that raises (e.g. the adb video pull failing) must not orphan the intervals started
    # after it: every interval is still stopped, the failed one is dropped (no phantom artifact), and
    # an evidence-I/O hiccup does not fail the scenario.
    stopped: list[str] = []

    class _Recording(_StubInterval):
        def __init__(self, path: Path, kind: str, *, fail: bool) -> None:
            super().__init__(path, kind=kind, provider="adb")
            self._fail = fail

        def stop(self) -> Path:
            stopped.append(self.kind)
            if self._fail:
                raise OSError("pull failed")
            return self._path

    good = tmp_path / "device.log"
    good.write_text("log", encoding="utf-8")
    started = [
        _Recording(tmp_path / "scenario.mp4", "video", fail=True),
        _Recording(good, "deviceLog", fail=False),
    ]
    out = FileSink(tmp_path).finish_scenario_intervals("s", started)
    assert stopped == ["video", "deviceLog"]  # both stopped despite the first raising
    assert [a.kind for a in out] == ["deviceLog"]  # the failed video is dropped, no phantom


def test_finish_scenario_intervals_redacts_then_emits_a_readable_file(tmp_path: Path) -> None:
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    f = tmp_path / "deviceLog.txt"
    f.write_text("auth token=topsecret here", encoding="utf-8")
    out = sink.finish_scenario_intervals("s", [_StubInterval(f)])
    assert [a.name for a in out] == ["deviceLog.txt"]
    assert "topsecret" not in f.read_text(encoding="utf-8")


def test_finish_scenario_intervals_drops_an_artifact_it_cannot_redact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Redaction is a security control: if the evidence file can't be read to scrub it, the artifact
    # must not ship (fail closed) rather than reach the report unredacted. A directory at the file
    # path makes read_text raise IsADirectoryError (a real OSError) without mocking the filesystem.
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    unreadable = tmp_path / "deviceLog.txt"
    unreadable.mkdir()
    with caplog.at_level("WARNING"):
        out = sink.finish_scenario_intervals("s", [_StubInterval(unreadable)])
    assert out == []
    assert any("redact" in r.message.lower() for r in caplog.records)


def test_finish_scenario_intervals_emits_unreadable_file_when_redactor_inactive(
    tmp_path: Path,
) -> None:
    # With no secrets the redactor is inactive: _redact_file returns safe before any read, so even an
    # unreadable file (a directory here) is emitted — the fail-closed guard is scoped to active
    # redaction and must not drop evidence when there is nothing to scrub.
    sink = FileSink(tmp_path, udid="u")
    unreadable = tmp_path / "deviceLog.txt"
    unreadable.mkdir()
    out = sink.finish_scenario_intervals("s", [_StubInterval(unreadable)])
    assert [a.name for a in out] == ["deviceLog.txt"]


def test_finish_scenario_intervals_drops_apptrace_when_only_the_raw_is_unredactable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # appTrace ships with a raw stream beside it; if the raw can't be scrubbed the artifact must be
    # dropped too, and the warning must name the raw file (not the main appTrace path).
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    main = tmp_path / "appTrace.json"
    main.write_text("clean", encoding="utf-8")
    (tmp_path / "appTrace.raw").mkdir()  # unreadable raw stream
    with caplog.at_level("WARNING"):
        out = sink.finish_scenario_intervals("s", [_StubInterval(main, kind="appTrace")])
    assert out == []
    assert any("appTrace.raw" in r.getMessage() for r in caplog.records)

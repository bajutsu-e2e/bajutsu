"""Tests for lightweight evidence capture."""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import (
    FileSink,
    capture,
    write_elements,
    write_raw_tree,
    write_screenshot,
)
from bajutsu.common.evidence.intervals import Interval
from bajutsu.common.evidence.redaction import PLACEHOLDER, Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.scenario import Redact


def _writer(run_dir: Path, redactor: Redactor | None = None) -> RunArtifactWriter:
    """The run's sink, defaulting to a redactor with nothing configured."""
    return RunArtifactWriter(run_dir, redactor if redactor is not None else Redactor(None))


class _StubInterval(Interval):
    """A finished recording, standing in for the subprocess-backed `Interval` (an external
    boundary): `finish_scenario_intervals` only needs `stop()` / `kind` / `provider`. It subclasses
    the real `Interval` so it fits the seam's own type, and overrides `stop()` to skip the
    subprocess."""

    def __init__(self, path: Path, kind: str = "deviceLog", provider: str = "xcuitest") -> None:
        super().__init__(kind=kind, path=path, provider=provider)
        self._path = path

    def stop(self) -> Path:
        return self._path


def _el(identifier: str, label: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


def test_write_elements(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A"), _el("b", "B")])
    name = write_elements(driver, _writer(tmp_path), "step0")
    assert name == "step0/elements.json"
    data = json.loads((tmp_path / name).read_text(encoding="utf-8"))
    assert [e["identifier"] for e in data] == ["a", "b"]


def test_write_elements_uses_provided_elements(tmp_path: Path) -> None:
    """When pre-queried elements are provided, write_elements uses them
    instead of calling driver.query()."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    name = write_elements(driver, _writer(tmp_path), "step0", elements=provided)
    data = json.loads((tmp_path / name).read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_uses_provided_elements(tmp_path: Path) -> None:
    """capture() passes provided elements through to write_elements."""
    driver = FakeDriver([_el("from_driver", "D")])
    provided = [_el("provided", "P")]
    capture(driver, _writer(tmp_path), "step0", ["elements"], elements=provided)
    data = json.loads((tmp_path / "step0" / "elements.json").read_text(encoding="utf-8"))
    assert data[0]["identifier"] == "provided"


def test_capture_elements_and_screenshot(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    written = capture(driver, _writer(tmp_path), "step0", ["elements", "screenshot.after"])
    assert [(a.name, a.kind, a.provider) for a in written] == [
        ("step0/elements.json", "elements", "driver"),
        ("step0/after.png", "screenshot", "driver"),
    ]
    assert (tmp_path / "step0" / "elements.json").exists()
    # FakeDriver records the screenshot call with the path it was given.
    assert ("screenshot", str(tmp_path / "step0" / "after.png")) in driver.actions


def test_capture_records_which_screen_each_artifact_depicts(tmp_path: Path) -> None:
    # The pre-step baseline's pair: both files show the screen the step is about to act on, so both
    # carry the same `depicts` and a viewer pairs them.
    driver = FakeDriver([_el("a", "A")])
    before = capture(driver, _writer(tmp_path), "step0", ["screenshot.before", "elements.before"])
    assert [a.depicts for a in before] == ["fake:before", "fake:before"]
    # The post-step pair, where a bare token means the post-action moment.
    after = capture(driver, _writer(tmp_path), "step0", ["screenshot.after", "elements"])
    assert [a.depicts for a in after] == ["fake:after", "fake:after"]


def test_capture_names_the_driver_the_tree_was_read_from(tmp_path: Path) -> None:
    """A `web` block screenshots the native driver while its tree comes from the WebView, so the two
    describe different screens — and each artifact says which one it is, rather than looking like an
    ordinary pair."""
    driver = FakeDriver([_el("a", "A")])
    written = capture(
        driver,
        _writer(tmp_path),
        "step0",
        ["screenshot.after", "elements"],
        elements=[_el("w", "W")],
        elements_source="webview",
    )
    assert [(a.kind, a.depicts) for a in written] == [
        ("screenshot", "fake:after"),
        ("elements", "webview:after"),
    ]


class _RawSourceStub(FakeDriver):
    """A driver whose only addition is `last_raw_source` — nothing else is needed for `isinstance`
    to recognize it as a `base.RawSourceProvider`, since the protocol is `@runtime_checkable` and
    structural, and `FakeDriver` itself implements no part of it."""

    def __init__(self, raw: base.RawSource | None) -> None:
        super().__init__([])
        self._raw = raw

    def last_raw_source(self) -> base.RawSource | None:
        return self._raw


def test_write_raw_tree_is_a_noop_for_a_backend_without_the_protocol(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])  # FakeDriver implements no RawSourceProvider
    assert write_raw_tree(driver, _writer(tmp_path), "step0") == []
    assert not (tmp_path / "step0").exists()  # never even created the dir


def test_write_raw_tree_is_a_noop_before_the_first_read(tmp_path: Path) -> None:
    driver = _RawSourceStub(None)
    assert write_raw_tree(driver, _writer(tmp_path), "step0") == []


def test_write_raw_tree_writes_the_raw_dump(tmp_path: Path) -> None:
    driver = _RawSourceStub(base.RawSource(text="<hierarchy>raw</hierarchy>", suffix=".xml"))
    names = write_raw_tree(driver, _writer(tmp_path), "step0")
    assert names == ["step0/hierarchy.raw.xml"]  # no parsed-input file: none given
    assert (tmp_path / names[0]).read_text(encoding="utf-8") == "<hierarchy>raw</hierarchy>"


def test_write_raw_tree_writes_the_parsed_input_body_when_present(tmp_path: Path) -> None:
    driver = _RawSourceStub(
        base.RawSource(
            text="<hierarchy>wide</hierarchy>",
            suffix=".xml",
            parsed_input="<hierarchy>narrowed</hierarchy>",
        )
    )
    names = write_raw_tree(driver, _writer(tmp_path), "step0")
    assert set(names) == {"step0/hierarchy.raw.xml", "step0/hierarchy.parsed-input.xml"}
    assert (tmp_path / "step0" / "hierarchy.raw.xml").read_text(
        encoding="utf-8"
    ) == "<hierarchy>wide</hierarchy>"
    assert (tmp_path / "step0" / "hierarchy.parsed-input.xml").read_text(
        encoding="utf-8"
    ) == "<hierarchy>narrowed</hierarchy>"


def test_write_raw_tree_redacts_a_configured_secret(tmp_path: Path) -> None:
    driver = _RawSourceStub(base.RawSource(text='<node text="s3kr3t" />', suffix=".xml"))
    redactor = Redactor(Redact(), values=["s3kr3t"])
    names = write_raw_tree(driver, _writer(tmp_path, redactor), "step0")
    assert "s3kr3t" not in (tmp_path / names[0]).read_text(encoding="utf-8")


def test_write_raw_tree_redacts_the_parsed_input_body_too(tmp_path: Path) -> None:
    # `parsed_input` goes through the same write loop as hierarchy.raw.xml, so pin that the loop
    # redacts every body it writes and not just the first. The stub is deliberately artificial:
    # narrowing only drops windows, so on a real adb read `parsed_input` is a subset of `text` and
    # unique content can only ever show up in the untouched reply.
    driver = _RawSourceStub(
        base.RawSource(
            text='<node text="clean" />', suffix=".xml", parsed_input='<node text="s3kr3t" />'
        )
    )
    redactor = Redactor(Redact(), values=["s3kr3t"])
    names = write_raw_tree(driver, _writer(tmp_path, redactor), "step0")
    parsed_input = next(n for n in names if n.endswith("hierarchy.parsed-input.xml"))
    assert "s3kr3t" not in (tmp_path / parsed_input).read_text(encoding="utf-8")


def test_write_raw_tree_refuses_when_a_label_rule_is_configured(tmp_path: Path) -> None:
    # `redact.labels` blanks a labeled element's value structurally in elements.json
    # (`redact_elements`, which has the parsed tree); `redact_text` over free text has no such
    # structure to match against, so writing the raw dump anyway would leak exactly what
    # elements.json just masked. Refuse the artifact instead of writing an unmasked superset.
    driver = _RawSourceStub(
        base.RawSource(text='<node label="Password" text="hunter2" />', suffix=".xml")
    )
    redactor = Redactor(Redact(labels=["Password"]))
    assert write_raw_tree(driver, _writer(tmp_path, redactor), "step0") == []
    assert not (tmp_path / "step0").exists()  # refused before ever creating the step dir


def test_write_raw_tree_still_writes_without_a_label_rule(tmp_path: Path) -> None:
    # Only `redact.labels` triggers the refusal above — a redactor active for other reasons
    # (header/field patterns, literal secret values) still lets `rawTree` through.
    driver = _RawSourceStub(base.RawSource(text="<node/>", suffix=".xml"))
    redactor = Redactor(Redact(), values=["s3kr3t"])
    assert write_raw_tree(driver, _writer(tmp_path, redactor), "step0") != []


def test_capture_raw_tree_kind_produces_artifacts(tmp_path: Path) -> None:
    driver = _RawSourceStub(
        base.RawSource(
            text="<hierarchy>wide</hierarchy>",
            suffix=".xml",
            parsed_input="<hierarchy>narrowed</hierarchy>",
        )
    )
    written = capture(driver, _writer(tmp_path), "step0", ["rawTree"])
    assert {(a.name, a.kind, a.provider) for a in written} == {
        ("step0/hierarchy.raw.xml", "rawTree", "driver"),
        ("step0/hierarchy.parsed-input.xml", "rawTree", "driver"),
    }


def test_capture_raw_tree_depicts_the_driver_whose_reply_it_holds(tmp_path: Path) -> None:
    """`write_raw_tree` reads the *capture* driver's own last reply, so the dump depicts that
    driver's screen even when the `elements` beside it were read from another one. Stating the real
    source keeps `depicts` correct on its own, rather than resting on the run loop dropping
    `rawTree` inside a `web` block (review follow-up)."""
    driver = _RawSourceStub(base.RawSource(text="<hierarchy/>", suffix=".xml"))
    written = capture(
        driver,
        _writer(tmp_path),
        "step0",
        ["rawTree"],
        elements=[_el("w", "W")],
        elements_source="webview",
    )
    assert [a.depicts for a in written] == ["fake:after"]


def test_capture_raw_tree_kind_on_an_unsupported_backend_produces_nothing(tmp_path: Path) -> None:
    driver = FakeDriver([_el("a", "A")])
    assert capture(driver, _writer(tmp_path), "step0", ["rawTree"]) == []
    assert not (tmp_path / "step0").exists()  # write_raw_tree's own no-op never mkdirs either


class _RawSourceQueryStub(FakeDriver):
    """A driver whose raw source updates on `query()`, mirroring how AdbDriver/XcuitestDriver's
    `last_raw_source()` always reflects whichever read happened most recently."""

    def __init__(self) -> None:
        super().__init__([])
        self.reads = 0

    def query(self) -> list[base.Element]:
        self.reads += 1
        return [_el(f"e{self.reads}", "L")]

    def last_raw_source(self) -> base.RawSource:
        return base.RawSource(text=f"read-{self.reads}", suffix=".xml")


def test_capture_pairs_raw_tree_with_the_read_elements_json_took_regardless_of_kinds_order(
    tmp_path: Path,
) -> None:
    # `write_elements` issues its own `query()` when no pre-fetched `elements` is passed (the
    # `elements=None` path `orchestrator/loop.py` takes for a step with no fresh tree). If a scenario
    # lists `capture: [rawTree, elements]` (rawTree first), `rawTree` must not run before that query
    # and capture a now-stale read — the two files must always describe the same one.
    driver = _RawSourceQueryStub()
    capture(driver, _writer(tmp_path), "step0", ["rawTree", "elements"])
    assert (tmp_path / "step0" / "hierarchy.raw.xml").read_text(encoding="utf-8") == "read-1"
    assert driver.reads == 1  # elements.json's own query() is the only read, and rawTree saw it


def test_file_sink_wait_diagnostic_writes_provenance_stamped_artifact(tmp_path: Path) -> None:
    # BE-0231 Unit 1: a first-wait timeout writes a self-contained diagnostic — the element tree at
    # timeout, the readiness signal, the poll trace, and the provenance stamp — so a rerun-to-green
    # never discards the evidence needed to decide which cause fired.
    from bajutsu.common.orchestrator.waits import WaitTrace
    from bajutsu.common.platform_lifecycle import ReadinessResult

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
    assert doc["readiness"] == {
        "ready": True,
        "signal": "readyWhen",
        "elapsedSeconds": 2.1,
        # Whether the screen had stopped moving when the gate returned: a dropped actuation
        # is what a `false` here points at when this very diagnostic is a wait timeout.
        "settled": True,
    }
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
    from bajutsu.common.orchestrator.waits import WaitTrace

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
    from bajutsu.common.orchestrator.waits import WaitTrace

    sink = FileSink(tmp_path, secrets=["hunter2"])
    path = tmp_path / "00-x/step0/wait-timeout.json"
    sink.wait_diagnostic(
        "00-x/step0", trace=WaitTrace(target="{'id': 'z'}"), elements=[_el("f", "hunter2")]
    )
    text = path.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_null_sink_wait_diagnostic_is_a_noop() -> None:
    from bajutsu.common.evidence import NullSink
    from bajutsu.common.orchestrator.waits import WaitTrace

    assert NullSink().wait_diagnostic("s", trace=WaitTrace(), elements=[]) is None


class _WritingDriver(FakeDriver):
    """A driver whose `screenshot` actually writes bytes, unlike `FakeDriver` (which only records
    the call). Needed to observe the file mode `write_screenshot` leaves behind (BE-0131)."""

    def screenshot(self, path: str) -> None:
        super().screenshot(path)
        Path(path).write_bytes(b"\x89PNG\r\n")


def test_write_screenshot_is_owner_only(tmp_path: Path) -> None:
    # A screenshot can capture on-screen secrets, so it must land owner-only (0600), not
    # world-readable under the ambient umask (BE-0131) — even though the driver, not the sink,
    # writes the bytes into the path the sink reserved.
    name = write_screenshot(_WritingDriver([_el("a", "A")]), _writer(tmp_path), "step0")
    assert name == "step0/after.png"
    assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_write_elements_is_owner_only(tmp_path: Path) -> None:
    # The element dump holds on-screen text (labels / values), redacted best-effort — owner-only,
    # like the other sensitive artifacts (BE-0131, issue #558's accessibility-dump scope).
    name = write_elements(FakeDriver([_el("a", "A")]), _writer(tmp_path), "step0")
    assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_capture_no_writing_kinds_leaves_dir_uncreated(tmp_path: Path) -> None:
    """capture() creates the step dir only when it actually writes a file; a kind it
    does not handle here (e.g. an interval kind) must leave the dir untouched, as before."""
    driver = FakeDriver([_el("a", "A")])
    assert capture(driver, _writer(tmp_path), "step0", ["video"]) == []
    assert not (tmp_path / "step0").exists()


def test_capture_writes_every_writing_kind_into_the_step_dir(tmp_path: Path) -> None:
    """Both writing kinds land under the step dir the sink creates on the way in."""
    driver = FakeDriver([_el("a", "A")])
    step_dir = tmp_path / "step0"
    capture(driver, _writer(tmp_path), "step0", ["elements", "screenshot.after"])
    assert (step_dir / "elements.json").exists()
    assert ("screenshot", str(step_dir / "after.png")) in driver.actions


def test_filesink_dispatches_intervals_to_web_provider(tmp_path: Path) -> None:
    # When a web interval provider is injected, the sink uses it (Playwright-native) instead of
    # the simctl starters, even though the web lane carries a (synthetic) udid.
    from bajutsu.common.evidence import FileSink, intervals

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
    from bajutsu.common.evidence import FileSink

    # No udid and no web provider: intervals are skipped (the fake/headless path).
    sink = FileSink(tmp_path, udid=None)
    assert sink.start_scenario_intervals("00-s", ["deviceLog"]) == []


def test_filesink_dispatches_adb_driver_intervals_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real seam: FileSink + AdbDriver.driver_interval start BOTH kinds via adb (not the simctl
    # path), even with a udid set. The subprocess spawn is faked so no adb process runs; the video's
    # pull/rm still go through the driver's injected run.
    from bajutsu.common.drivers.adb import AdbDriver
    from bajutsu.common.evidence import intervals

    class _FakeProc:
        def stop(self, sig: int, timeout: float) -> None:
            return None

    monkeypatch.setattr(intervals, "_SubprocessProc", lambda argv, stdout_path: _FakeProc())

    ran: list[list[str]] = []
    pgrep_calls = 0

    def run(argv: list[str]) -> str:
        nonlocal pgrep_calls
        ran.append(argv)
        if "pgrep" in " ".join(argv):
            pgrep_calls += 1
            # The video branch's start/stop confirmation probes share this one pgrep check: call 1
            # is the pre-spawn baseline (nothing running yet), call 2 is the confirm-started poll
            # (our own process appears — fast, no real wait), call 3+ is the stop-confirmation poll
            # (it has already exited, so the pull can proceed without waiting out the finalize
            # timeout). A response that stayed "running" past call 2 would otherwise be
            # indistinguishable from a real hang and burn `_VIDEO_FINALIZE_TIMEOUT` (120s).
            return "1234\n" if pgrep_calls == 2 else ""
        return ""

    sink = FileSink(tmp_path, udid="SER", driver_interval=AdbDriver("SER", run=run).driver_interval)
    started = sink.start_scenario_intervals("00-s", ["video", "deviceLog"])
    assert {(iv.kind, iv.provider) for iv in started} == {("video", "adb"), ("deviceLog", "adb")}

    arts = sink.finish_scenario_intervals("00-s", started)
    assert {(a.kind, a.provider) for a in arts} == {("video", "adb"), ("deviceLog", "adb")}
    # The video's pull + rm rode the driver's injected run (device-side capture pulled to the host).
    assert any("pull" in c for c in ran) and any("rm" in c for c in ran)


def test_filesink_confirms_ios_on_demand_video_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real seam for iOS: a udid set and no driver_interval routes through
    # `_start_simctl_interval` -> `intervals.start_video(..., confirm_started=True)`. This is the
    # item's primary motivation (the on-demand path whose confirmation wait iOS's fix relies on),
    # so it must be exercised through the sink, not only by calling `start_video` directly.
    from bajutsu.common.evidence import intervals

    class _FakeProc:
        def __init__(self, argv: list[str], stdout_path: Path | None) -> None:
            return None

        def stop(self, sig: int, timeout: float) -> None:
            return None

        def await_stderr(self, needle: str, timeout: float) -> float | None:
            return time.monotonic()  # simctl announced its first processed frame

    monkeypatch.setattr(intervals, "_SubprocessProc", _FakeProc)

    sink = FileSink(tmp_path, udid="UDID")
    started = sink.start_scenario_intervals("00-s", ["video"])
    assert len(started) == 1 and started[0].kind == "video"
    assert started[0].true_start is not None


def test_filesink_reports_a_video_that_never_confirmed_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0354: the wedge's earliest symptom, surfaced so the crash retry can pick the recovery rung
    # from it. A recording that starts normally must not report it, or every scenario would look
    # degraded.
    from bajutsu.common.evidence import intervals

    class _FakeProc:
        def __init__(self, argv: list[str], stdout_path: Path | None) -> None:
            return None

        def stop(self, sig: int, timeout: float) -> None:
            return None

        def await_stderr(self, needle: str, timeout: float) -> float | None:
            return time.monotonic() if announces["v"] else None

    announces = {"v": True}
    monkeypatch.setattr(intervals, "_SubprocessProc", _FakeProc)
    stalls: list[bool] = []
    sink = FileSink(tmp_path, udid="UDID", on_video_start_stall=lambda: stalls.append(True))
    sink.start_scenario_intervals("00-s", ["video"])
    assert stalls == []

    # recordVideo never announces its first frame: the capture pipeline is not producing.
    announces["v"] = False
    sink.start_scenario_intervals("01-s", ["video"])
    assert stalls == [True]


def test_filesink_adopts_a_prestarted_video_instead_of_starting_one(tmp_path: Path) -> None:
    # A device backend starts its video before launch; the sink must adopt that running interval for
    # the "video" kind (relocating it to the artifact path on stop) rather than ask the driver /
    # simctl to start a fresh one after launch — the other kinds still start on demand.
    from bajutsu.common.evidence import FileSink, intervals

    temp = tmp_path / "_video_tmp" / "prestart-SER.mp4"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"clip")

    class _Proc:
        def stop(self, sig: int, timeout: float) -> None:
            pass

        def await_stderr(self, needle: str, timeout: float) -> float | None:
            return None

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
    started: list[Interval] = [
        _Recording(tmp_path / "scenario.mp4", "video", fail=True),
        _Recording(good, "deviceLog", fail=False),
    ]
    out = FileSink(tmp_path).finish_scenario_intervals("s", started)
    assert stopped == ["video", "deviceLog"]  # both stopped despite the first raising
    assert [a.kind for a in out] == ["deviceLog"]  # the failed video is dropped, no phantom


def test_finish_scenario_intervals_redacts_then_emits_a_readable_file(tmp_path: Path) -> None:
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    f = tmp_path / "s" / "deviceLog.txt"
    f.parent.mkdir()
    f.write_text("auth token=topsecret here", encoding="utf-8")
    out = sink.finish_scenario_intervals("s", [_StubInterval(f)])
    assert [a.name for a in out] == ["s/deviceLog.txt"]
    assert "topsecret" not in f.read_text(encoding="utf-8")


def test_finish_scenario_intervals_drops_an_artifact_it_cannot_redact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Redaction is a security control: if the evidence file can't be read to scrub it, the artifact
    # must not ship (fail closed) rather than reach the report unredacted. A directory at the file
    # path makes read_text raise IsADirectoryError (a real OSError) without mocking the filesystem.
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    unreadable = tmp_path / "s" / "deviceLog.txt"
    unreadable.mkdir(parents=True)
    with caplog.at_level("WARNING"):
        out = sink.finish_scenario_intervals("s", [_StubInterval(unreadable)])
    assert out == []
    assert any("redact" in r.message.lower() for r in caplog.records)


def test_finish_scenario_intervals_drops_an_unreadable_file_with_no_secrets_configured(
    tmp_path: Path,
) -> None:
    # The sink's pattern backstop needs no configuration (BE-0331 unit 7), so "nothing is
    # configured" no longer means "nothing to scrub": a file the sink cannot read is a file the
    # backstop could not run over, and it fails closed like any other unscrubbable evidence.
    sink = FileSink(tmp_path, udid="u")
    unreadable = tmp_path / "s" / "deviceLog.txt"
    unreadable.mkdir(parents=True)
    assert sink.finish_scenario_intervals("s", [_StubInterval(unreadable)]) == []


def test_finish_scenario_intervals_emits_a_video_without_reading_it(tmp_path: Path) -> None:
    # A video is opaque bytes the sink cannot inspect, so it ships recorded as unmasked rather than
    # scrubbed — an unreadable one is still emitted, unlike the text evidence above (BE-0151).
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    unreadable = tmp_path / "s" / "scenario.mp4"
    unreadable.mkdir(parents=True)
    out = sink.finish_scenario_intervals("s", [_StubInterval(unreadable, kind="video")])
    assert [a.name for a in out] == ["s/scenario.mp4"]


def test_finish_scenario_intervals_drops_apptrace_when_only_the_raw_is_unredactable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # appTrace ships with a raw stream beside it; if the raw can't be scrubbed the artifact must be
    # dropped too, and the warning must name the raw file (not the main appTrace path).
    sink = FileSink(tmp_path, udid="u", secrets=["topsecret"])
    main = tmp_path / "s" / "appTrace.json"
    main.parent.mkdir()
    main.write_text("clean", encoding="utf-8")
    (tmp_path / "s" / "appTrace.raw").mkdir()  # unreadable raw stream
    with caplog.at_level("WARNING"):
        out = sink.finish_scenario_intervals("s", [_StubInterval(main, kind="appTrace")])
    assert out == []
    assert any("appTrace.raw" in r.getMessage() for r in caplog.records)


# --- BE-0331: the pattern backstop, run last over whatever the sink serializes ---------------

# An Anthropic key shape, the one the motivating leak carried. Synthetic: the guide invented a
# realistic value for a field asking for a key, which is precisely the class no rule keyed on a
# configured name or a known literal can reach.
_SHAPED_KEY = "sk-ant-notarealkey000000000000"


def test_the_backstop_masks_a_shape_the_structural_rules_never_reach(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Nothing is configured and the element names nothing credential-like, so the two BE-0331
    # defaults and every configured rule pass this value over — the backstop is the last thing
    # between it and the artifact. It runs over the serialized bytes, so it reaches an element
    # tree the structural pass just wrote out.
    writer = _writer(tmp_path)
    with caplog.at_level("WARNING"):
        writer.write_elements("elements.json", [_el("note.body", f"jot {_SHAPED_KEY} down")])
    written = (tmp_path / "elements.json").read_text(encoding="utf-8")
    assert _SHAPED_KEY not in written
    assert PLACEHOLDER in written
    # Exactly one warning, naming the artifact and the shape: a value reaching the backstop means an
    # earlier, more precise rule should have caught it, so it is worth surfacing rather than masking
    # silently — and worth surfacing once, not once per pattern the sink tried.
    (record,) = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "elements.json" in record.getMessage()
    assert "anthropicApiKey" in record.getMessage()


def test_the_backstop_names_every_shape_in_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # One artifact, one warning: an operator reads it to decide whether the artifact is safe to
    # share, so the shapes belong together in the line that names the file.
    writer = _writer(tmp_path)
    aws = "AKIAEXAMPLEKEYID1234"
    with caplog.at_level("WARNING"):
        writer.write_text("guide.log", f"{_SHAPED_KEY}\n{aws}\n")
    (record,) = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "anthropicApiKey" in record.getMessage() and "awsAccessKeyId" in record.getMessage()
    written = (tmp_path / "guide.log").read_text(encoding="utf-8")
    assert _SHAPED_KEY not in written and aws not in written


def test_the_backstop_leaves_ordinary_evidence_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A crawl writes an artifact per screen, so a backstop that cried wolf on ordinary text would
    # bury the warning that means something.
    writer = _writer(tmp_path)
    text = "tap home.submit\ntype search.query\npinned=true\n"
    with caplog.at_level("WARNING"):
        writer.write_text("plan.log", text)
    assert (tmp_path / "plan.log").read_text(encoding="utf-8") == text
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


# --- BE-0331: the bytes the sink cannot inspect ---------------------------------------------


def test_write_bytes_records_the_artifact_as_unmasked(tmp_path: Path) -> None:
    # Pixels cannot be masked, so the honest thing is to say so rather than let the artifact be
    # described as scrubbed by rules that never ran (BE-0151, kept for every opaque shape).
    writer = _writer(tmp_path)
    writer.write_bytes("screens/abc.png", b"\x89PNG\r\n\x1a\n fake")
    assert writer.unmasked == ["screens/abc.png"]
    assert (tmp_path / "screens" / "abc.png").read_bytes().startswith(b"\x89PNG")


def test_a_reserved_recording_counts_as_unmasked_only_once_the_caller_says_so(
    tmp_path: Path,
) -> None:
    # `reserve` hands out a path because a subprocess cannot be handed bytes, and reserving alone
    # settles nothing: the caller closes the loop, and only then is the artifact on the record as
    # content the sink could not inspect.
    writer = _writer(tmp_path)
    path = writer.reserve("video/scenario.mp4")
    assert writer.unmasked == []
    path.write_bytes(b"not really a video")
    writer.record_unmasked("video/scenario.mp4")
    assert writer.unmasked == ["video/scenario.mp4"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # restricted like any other artifact


def test_the_text_paths_are_never_recorded_as_unmasked(tmp_path: Path) -> None:
    # The list is what tells a reader which artifacts the rules did not run over, so every shape the
    # sink *can* inspect must stay off it — an over-broad list is as misleading as a missing one.
    writer = _writer(tmp_path)
    writer.write_text("device.log", "nothing here")
    writer.write_json("manifest.json", {"run": "abc"})
    writer.write_elements("elements.json", [_el("home.submit", "Submit")])
    writer.write_exchanges("network.json", [{"url": "https://example.com/"}])
    writer.write_screen_map("screenmap.json", {"stop_reason": "completed"})
    assert writer.scrub_reserved("device.log") is True
    assert writer.unmasked == []

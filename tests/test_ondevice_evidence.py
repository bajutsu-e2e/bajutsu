"""The on-device suites' per-test evidence capture (`ondevice_evidence.py`).

Exercises the real plugin through `pytester`: a real inner pytest session runs the real
`ondevice_evidence` plugin and the real `capture` fixture generator, with fake `start_video`/
`start_log` callables standing in for the adb and XCUITest interval starters alike — so the
keep-on-failure / discard-on-pass behavior is pinned on the fast gate, without a device.
"""

from __future__ import annotations

import inspect
import logging
import subprocess
from pathlib import Path

import ondevice_evidence
import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.evidence import intervals

# The inner conftest registers the real plugin, the same way the real on-device suites' own
# conftest.py does, so its `pytest_runtest_makereport` hook tags each item's stash.
_INNER_CONFTEST = "pytest_plugins = ['ondevice_evidence']\n"

# A minimal stand-in for `intervals.Interval`: records a marker file at the given path and returns
# it from `stop()`. Prefixed to every inner test module below so each exercises `capture`'s own
# contract (start once, stop unconditionally, keep only on failure) rather than a real recording.
_FAKE_STARTERS = """\
class _FakeInterval:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("captured")

    def stop(self):
        return self.path


def _fake_start(serial, path, **kwargs):
    return _FakeInterval(path)


"""

_EVIDENCE_FIXTURE = """\
@pytest.fixture(autouse=True)
def _evidence(request):
    yield from ondevice_evidence.capture(
        "fake-serial", "fake-lane", request,
        start_video=_fake_start, start_log=_fake_start,
    )


"""

_IMPORTS = "import ondevice_evidence\nimport pytest\n\n\n"


_SENTINEL = intervals.Interval(kind="video", path=Path("video.mp4"))


def test_discards_the_capture_when_the_test_passes(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        _IMPORTS + _FAKE_STARTERS + _EVIDENCE_FIXTURE + "def test_ok():\n    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    slug = ondevice_evidence._slug("test_discards_the_capture_when_the_test_passes.py::test_ok")
    assert not (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_keeps_the_capture_when_the_test_fails(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        _IMPORTS + _FAKE_STARTERS + _EVIDENCE_FIXTURE + "def test_broken():\n    assert False\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    slug = ondevice_evidence._slug("test_keeps_the_capture_when_the_test_fails.py::test_broken")
    kept = pytester.path / "runs" / "fake-lane" / slug
    assert (kept / "video.mp4").read_text() == "captured"
    assert (kept / "device.log").read_text() == "captured"


def test_keeps_the_capture_when_setup_fails(pytester: pytest.Pytester) -> None:
    # A failure in another fixture's setup — before the test body ever runs — must still tag the
    # item's stash: `report.failed` covers the "setup" phase report too, not only "call". Realistic
    # for the fault-injection suite, whose per-test `driver` fixture can itself fail waking the
    # display; `_evidence` (autouse) is set up first regardless, at the same function scope.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        _IMPORTS
        + _FAKE_STARTERS
        + _EVIDENCE_FIXTURE
        + "@pytest.fixture\n"
        + "def broken_setup():\n"
        + '    raise RuntimeError("setup blew up before the test body ever ran")\n\n\n'
        + "def test_needs_it(broken_setup):\n    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(errors=1)
    slug = ondevice_evidence._slug("test_keeps_the_capture_when_setup_fails.py::test_needs_it")
    kept = pytester.path / "runs" / "fake-lane" / slug
    assert (kept / "video.mp4").read_text() == "captured"


def test_clears_a_stale_directory_before_recording(pytester: pytest.Pytester) -> None:
    # simctl `recordVideo` (no `--force`) refuses to overwrite an existing file — silently, since
    # its stderr is discarded — so a leftover clip from a crashed attempt, or from an earlier local
    # run of the same test, must never still be there when the next attempt's `start_video` runs.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import pathlib\n"
        "\n"
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "class _FakeInterval:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
        "\n"
        "    def stop(self):\n"
        "        return self.path\n"
        "\n"
        "\n"
        "def _fake_start(serial, path, **kwargs):\n"
        "    # A stand-in for simctl recordVideo's real refusal to overwrite an existing file.\n"
        "    if path.exists():\n"
        "        raise RuntimeError('recordVideo: file already exists')\n"
        "    path.write_text('fresh')\n"
        "    return _FakeInterval(path)\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _evidence(request):\n"
        "    slug = ondevice_evidence._slug(request.node.nodeid)\n"
        "    stale = pathlib.Path('runs') / 'fake-lane' / slug\n"
        "    stale.mkdir(parents=True, exist_ok=True)\n"
        "    (stale / 'video.mp4').write_text('stale from a previous attempt')\n"
        "    yield from ondevice_evidence.capture(\n"
        "        'fake-serial', 'fake-lane', request,\n"
        "        start_video=_fake_start, start_log=_fake_start,\n"
        "    )\n"
        "\n"
        "\n"
        "def test_broken():\n"
        "    assert False\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    slug = ondevice_evidence._slug("test_clears_a_stale_directory_before_recording.py::test_broken")
    kept = pytester.path / "runs" / "fake-lane" / slug
    assert (kept / "video.mp4").read_text() == "fresh"


def test_stops_the_started_video_when_start_log_raises(pytester: pytest.Pytester) -> None:
    # `start_video` can succeed (spawning a real device-side `screenrecord`) and then `start_log`
    # raise — a transient adb hiccup starting the second process must neither orphan the first nor
    # fail a test the driver contract never touched: starting the capture is itself best-effort.
    # `start_video`'s own success still writes a real `video.mp4` (a real `adb pull`, on adb) —
    # which must not be left behind on this *passing* test just because `_PENDING` registration
    # used to be gated on both starters succeeding.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import pathlib\n"
        "\n"
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "class _FakeVideo:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
        "\n"
        "    def stop(self):\n"
        "        pathlib.Path('stopped_video.marker').write_text('stopped')\n"
        "        self.path.parent.mkdir(parents=True, exist_ok=True)\n"
        "        self.path.write_text('a real pulled video')\n"
        "        return self.path\n"
        "\n"
        "\n"
        "def _fake_start_video(serial, path, **kwargs):\n"
        "    return _FakeVideo(path)\n"
        "\n"
        "\n"
        "def _fake_start_log_raises(serial, path, **kwargs):\n"
        "    raise RuntimeError('adb logcat failed to start')\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _evidence(request):\n"
        "    yield from ondevice_evidence.capture(\n"
        "        'fake-serial', 'fake-lane', request,\n"
        "        start_video=_fake_start_video, start_log=_fake_start_log_raises,\n"
        "    )\n"
        "\n"
        "\n"
        "def test_body():\n"
        "    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)  # the start failure is logged, not raised
    assert (pytester.path / "stopped_video.marker").read_text() == "stopped"
    slug = ondevice_evidence._slug(
        "test_stops_the_started_video_when_start_log_raises.py::test_body"
    )
    assert not (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_still_starts_the_log_when_start_video_raises(pytester: pytest.Pytester) -> None:
    # The reverse order: a `start_video` failure (a fork failure, `xcrun`/`adb` transiently missing)
    # must not suppress `start_log` too. The two are independent processes failing for independent
    # reasons, and the device log is the cheaper, more diagnostic artifact of the two — losing it
    # over an unrelated video-side hiccup would leave a failing case with nothing to read at all.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import pathlib\n"
        "\n"
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "def _fake_start_video_raises(serial, path, **kwargs):\n"
        "    raise RuntimeError('adb screenrecord failed to start')\n"
        "\n"
        "\n"
        "class _FakeLog:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
        "\n"
        "    def stop(self):\n"
        "        self.path.parent.mkdir(parents=True, exist_ok=True)\n"
        "        self.path.write_text('a real device log')\n"
        "        return self.path\n"
        "\n"
        "\n"
        "def _fake_start_log(serial, path, **kwargs):\n"
        "    return _FakeLog(path)\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _evidence(request):\n"
        "    yield from ondevice_evidence.capture(\n"
        "        'fake-serial', 'fake-lane', request,\n"
        "        start_video=_fake_start_video_raises, start_log=_fake_start_log,\n"
        "    )\n"
        "\n"
        "\n"
        "def test_broken():\n"
        "    assert False\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)  # the driver contract itself failed; the case stays red
    slug = ondevice_evidence._slug(
        "test_still_starts_the_log_when_start_video_raises.py::test_broken"
    )
    kept = pytester.path / "runs" / "fake-lane" / slug
    assert kept.exists()
    assert not (kept / "video.mp4").exists()  # start_video never ran
    assert (kept / "device.log").read_text() == "a real device log"


class _FakeNode:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.stash: pytest.Stash = pytest.Stash()


class _FakeRequest:
    """A minimal stand-in for `pytest.FixtureRequest`: `capture()` only ever reads `.node`."""

    def __init__(self, nodeid: str) -> None:
        self.node = _FakeNode(nodeid)


def test_warns_and_still_yields_when_the_evidence_directory_cannot_be_prepared(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A stale `runs/<lane>/<slug>` left behind as a *file*, not a directory — `rmtree(ignore_errors=
    # True)` leaves it in place, and `mkdir(exist_ok=True)` then raises `FileExistsError` — must not
    # error the case as a setup failure over evidence-capture plumbing: preparing the directory is
    # capture plumbing too, guarded the same "warn, never raise" way as the two starters below it.
    monkeypatch.chdir(tmp_path)
    slug = ondevice_evidence._slug("fake::test")
    dest = tmp_path / "runs" / "fake-lane" / slug
    dest.parent.mkdir(parents=True)
    dest.write_text("a stale file standing where the evidence directory belongs")

    def _fail_if_called(serial: str, path: Path) -> intervals.Interval:
        raise AssertionError("must not be called once directory preparation has failed")

    caplog.set_level(logging.WARNING)
    gen = ondevice_evidence.capture(
        "fake-serial",
        "fake-lane",
        _FakeRequest("fake::test"),
        start_video=_fail_if_called,
        start_log=_fail_if_called,
    )
    next(gen)  # does not raise, and never reaches either starter
    with pytest.raises(StopIteration):
        next(gen)
    assert any("could not prepare the evidence directory" in r.message for r in caplog.records)


def test_warns_about_a_missing_or_empty_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `_spawn` discards the child's stderr, so a `recordVideo`/`screenrecord` that refused to start
    # or died leaves no other trace, and `if-no-files-found: ignore` on the CI upload step would let
    # an entirely blind capture pass for a clean one. Drive `capture()` directly as a plain generator
    # (it is one; only its callers wrap it as a fixture) rather than through `pytester`, since this
    # only needs to inspect the emitted warnings, not a whole inner pytest session.
    monkeypatch.chdir(tmp_path)

    class _NullInterval(intervals.Interval):
        def stop(self) -> Path:
            return self.path  # nothing ever wrote to it, so the sweep drops it

    def start_records_nothing(serial: str, path: Path) -> intervals.Interval:
        return _NullInterval(kind="video", path=path)

    gen = ondevice_evidence.capture(
        "fake-serial",
        "fake-lane",
        _FakeRequest("fake::test"),
        start_video=start_records_nothing,
        start_log=start_records_nothing,
    )
    next(gen)
    with (
        pytest.warns(UserWarning, match="is missing or empty") as missing_warnings,
        pytest.raises(StopIteration),
    ):
        next(gen)
    assert len(missing_warnings) == 2  # video.mp4 and device.log, both never written


def test_warns_about_missing_evidence_even_when_a_stop_failure_reraises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The missing/empty check used to be plain trailing code after the stop try/except, so the
    # `raise` on an already-failing attempt jumped straight past it — exactly the path whose
    # directory the hook is about to *keep*, where a human downloading it most needs the signal.
    # Moved into a `finally` so it always runs, including here.
    monkeypatch.chdir(tmp_path)

    class _RaisesOnStop(intervals.Interval):
        def stop(self) -> Path:
            raise RuntimeError("adb pull failed")

    def start_raises_on_stop(serial: str, path: Path) -> intervals.Interval:
        return _RaisesOnStop(kind="video", path=path)  # never writes to `path`

    request = _FakeRequest("fake::test")
    request.node.stash[ondevice_evidence._FAILED] = True  # the attempt is already failing

    gen = ondevice_evidence.capture(
        "fake-serial",
        "fake-lane",
        request,
        start_video=start_raises_on_stop,
        start_log=start_raises_on_stop,
    )
    next(gen)
    with (
        pytest.warns(UserWarning, match="is missing or empty") as missing_warnings,
        pytest.raises(RuntimeError, match="adb pull failed"),
    ):
        next(gen)
    assert len(missing_warnings) == 2  # video.mp4 and device.log, both never written


def test_stops_the_log_even_when_stopping_the_video_raises(pytester: pytest.Pytester) -> None:
    # `start_screenrecord`'s own transform deliberately lets a failed `adb pull` propagate out of
    # `stop()` (its own docstring: swallowing it would turn a real problem into a silent one) — that
    # must not skip stopping the logcat process alongside it. The test body itself passed, and the
    # hook is about to discard this very directory regardless, so the stop failure is logged rather
    # than raised — a transient pull hiccup on a passing case must not redden the lane over evidence
    # nobody keeps.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import pathlib\n"
        "\n"
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "class _FakeVideoRaisesOnStop:\n"
        "    def stop(self):\n"
        "        raise RuntimeError('adb pull failed')\n"
        "\n"
        "\n"
        "class _FakeLog:\n"
        "    def stop(self):\n"
        "        pathlib.Path('stopped_log.marker').write_text('stopped')\n"
        "        return pathlib.Path('stopped_log.marker')\n"
        "\n"
        "\n"
        "def _fake_start_video_raises_on_stop(serial, path, **kwargs):\n"
        "    return _FakeVideoRaisesOnStop()\n"
        "\n"
        "\n"
        "def _fake_start_log(serial, path, **kwargs):\n"
        "    return _FakeLog()\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _evidence(request):\n"
        "    yield from ondevice_evidence.capture(\n"
        "        'fake-serial', 'fake-lane', request,\n"
        "        start_video=_fake_start_video_raises_on_stop, start_log=_fake_start_log,\n"
        "    )\n"
        "\n"
        "\n"
        "def test_body():\n"
        "    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)  # the stop failure is logged, not raised
    assert (pytester.path / "stopped_log.marker").read_text() == "stopped"
    slug = ondevice_evidence._slug(
        "test_stops_the_log_even_when_stopping_the_video_raises.py::test_body"
    )
    assert not (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_a_stop_failure_still_raises_when_the_test_already_failed(
    pytester: pytest.Pytester,
) -> None:
    # The other half of the same fix: a stop failure is only ever *swallowed* because the attempt is
    # already clean and the evidence was going to be discarded regardless. When the test itself
    # already failed, the lane is already red, and swallowing here would just hide a second, distinct
    # failure behind the first.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "class _FakeVideoRaisesOnStop:\n"
        "    def stop(self):\n"
        "        raise RuntimeError('adb pull failed')\n"
        "\n"
        "\n"
        "def _fake_start_video_raises_on_stop(serial, path, **kwargs):\n"
        "    return _FakeVideoRaisesOnStop()\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _evidence(request):\n"
        "    yield from ondevice_evidence.capture(\n"
        "        'fake-serial', 'fake-lane', request,\n"
        "        start_video=_fake_start_video_raises_on_stop,\n"
        "        start_log=_fake_start_video_raises_on_stop,\n"
        "    )\n"
        "\n"
        "\n"
        "def test_broken():\n"
        "    assert False\n"
    )
    result = pytester.runpytest_inprocess()
    # The call phase fails on its own assertion, then the stop failure surfaces as a second,
    # separate teardown error — both visible, neither swallowed.
    result.assert_outcomes(failed=1, errors=1)
    slug = ondevice_evidence._slug(
        "test_a_stop_failure_still_raises_when_the_test_already_failed.py::test_broken"
    )
    assert (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_keeps_evidence_when_a_sibling_fixtures_teardown_fails_after_a_passing_test(
    pytester: pytest.Pytester,
) -> None:
    # The exact gap a review found: `_evidence` is autouse, so it is set up first and torn down
    # LAST. pytest builds the "teardown" `TestReport` only after every finalizer for the item has
    # run — `_evidence`'s own included — so a fixture cannot see a fixture torn down *before* it
    # (like `driver` in the real fault-injection suite, whose post-`yield` half is `_wake()` +
    # `_await_wakefulness`) fail its own teardown. The test body passes; a sibling's teardown then
    # raises; the evidence must still be kept — that failure is exactly the one it exists to explain.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        _IMPORTS
        + _FAKE_STARTERS
        + _EVIDENCE_FIXTURE
        + "@pytest.fixture\n"
        + "def wonky_teardown():\n"
        + "    yield\n"
        + "    raise RuntimeError('display would not wake back up')\n\n\n"
        + "def test_body(wonky_teardown):\n"
        + "    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1, errors=1)  # the test body passes; teardown then raises
    slug = ondevice_evidence._slug(
        "test_keeps_evidence_when_a_sibling_fixtures_teardown_fails_after_a_passing_test.py"
        "::test_body"
    )
    kept = pytester.path / "runs" / "fake-lane" / slug
    assert (kept / "video.mp4").read_text() == "captured"
    assert (kept / "device.log").read_text() == "captured"


def test_discards_evidence_once_a_crashed_attempt_recovers_and_passes(
    monkeypatch: pytest.MonkeyPatch, pytester: pytest.Pytester
) -> None:
    # `backend_crash_recovery` (BE-0334, used by the iOS conformance suite) re-runs a WHOLE item via
    # `_initrequest()` on an infra-fault retry, reusing the same `pytest.Item` — so `item.stash`
    # persists across attempts. If the makereport hook only ever latched `_FAILED` true and never
    # cleared it, the crashed attempt's tag would still read true on the later, recovered, passing
    # attempt, wrongly keeping evidence a passing test does not need (and by then holding only the
    # passing attempt's own recording anyway, since `capture`'s leading `rmtree` clears the directory
    # at the start of every attempt).
    #
    # Pinned rather than left ambient: `backend_crash_recovery` reads `BAJUTSU_CRASH_RETRIES` from
    # the real environment at run time, and this test needs at least one retry to reach the
    # recovered-then-passing attempt it's pinning — a `0` in the developer's shell would fail this
    # test over an unset env var, not over anything wrong in `ondevice_evidence.py`.
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "1")
    pytester.makeconftest("pytest_plugins = ['ondevice_evidence', 'backend_crash_recovery']\n")
    pytester.makepyfile(
        _IMPORTS
        + "from bajutsu.common.drivers import base\n\n\n"
        + _FAKE_STARTERS
        + _EVIDENCE_FIXTURE
        + "pytestmark = pytest.mark.backend_crash_recovery\n"
        + "_LAUNCHES = {'n': 0}\n\n\n"
        + "class _FakeDriver:\n"
        + "    def __init__(self, crash):\n"
        + "        self._crash = crash\n\n"
        + "    def act(self):\n"
        + "        if self._crash:\n"
        + "            raise base.BackendCrashError('fake runner crashed mid-test')\n\n\n"
        + "@pytest.fixture(scope='module')\n"
        + "def _backend_launch():\n"
        + "    def launch():\n"
        + "        _LAUNCHES['n'] += 1\n"
        + "        # only the first lease crashes; (driver, teardown) per BE-0342\n"
        + "        return _FakeDriver(crash=_LAUNCHES['n'] == 1), (lambda: None)\n"
        + "    return launch\n\n\n"
        + "@pytest.fixture\n"
        + "def driver(_backend_lease_holder):\n"
        + "    return _backend_lease_holder.driver\n\n\n"
        + "def test_acts(driver):\n"
        + "    driver.act()\n"
        + "    assert _LAUNCHES['n'] == 2  # crashed once, recovered on the cold respawn\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    slug = ondevice_evidence._slug(
        "test_discards_evidence_once_a_crashed_attempt_recovers_and_passes.py::test_acts"
    )
    assert not (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_the_real_starters_accept_captures_two_argument_call() -> None:
    # `capture()` calls every starter as `(serial_or_udid, path)`, and nothing type-checks that:
    # `make typecheck` covers `bajutsu demos scripts`, not `tests/`, and every test above uses a
    # fake. Bind the real signatures so a new required parameter fails here, not on-device.
    for starter in (
        intervals.start_video,
        intervals.start_device_log,
        intervals.start_screenrecord,
        intervals.start_logcat,
    ):
        inspect.signature(starter).bind("device-id", Path("artifact"))
    # The two pre-bound helpers forward more than that, and both unit tests below monkeypatch the
    # real function away — so bind the keyword arguments they actually pass, too.
    inspect.signature(intervals.start_video).bind("udid", Path("v.mp4"), confirm_started=True)
    inspect.signature(intervals.start_screenrecord).bind(
        "serial",
        Path("v.mp4"),
        time_limit=intervals.SCREENRECORD_TIME_LIMIT_S,
        size=intervals.SCREENRECORD_SIZE,
        bit_rate=intervals.SCREENRECORD_BIT_RATE,
        confirm_started=True,
    )


def test_android_screenrecord_forwards_this_modules_pinned_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_start_screenrecord(
        serial: str,
        path: Path,
        *,
        time_limit: int | None = None,
        size: str | None = None,
        bit_rate: int | None = None,
        confirm_started: bool = False,
    ) -> intervals.Interval:
        calls.append((serial, path, time_limit, size, bit_rate, confirm_started))
        return _SENTINEL

    monkeypatch.setattr(intervals, "start_screenrecord", fake_start_screenrecord)
    monkeypatch.setattr(adb, "real_run", lambda cmd: "")
    result = ondevice_evidence.android_screenrecord("serial-1", Path("video.mp4"))
    assert result is _SENTINEL
    assert calls == [
        (
            "serial-1",
            Path("video.mp4"),
            intervals.SCREENRECORD_TIME_LIMIT_S,
            intervals.SCREENRECORD_SIZE,
            intervals.SCREENRECORD_BIT_RATE,
            True,
        )
    ]


def test_android_screenrecord_clears_the_stale_device_side_file_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `start_screenrecord` always records to one fixed device-side path and pulls whatever is there
    # unconditionally on stop — both Android suites reuse it once per test, so a prior test's clip
    # left behind by a swallowed pull failure could otherwise be pulled in as this test's own
    # evidence. Clearing it first, before every spawn, is what closes that.
    run_calls: list[list[str]] = []
    monkeypatch.setattr(adb, "real_run", run_calls.append)
    monkeypatch.setattr(intervals, "start_screenrecord", lambda *a, **kw: _SENTINEL)
    result = ondevice_evidence.android_screenrecord("serial-1", Path("video.mp4"))
    assert result is _SENTINEL
    assert run_calls == [adb.rm_cmd("serial-1", adb.VIDEO_DEVICE_PATH)]


def test_android_screenrecord_tolerates_a_failed_device_side_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transient failure to remove the stale file (device briefly unresponsive, nothing there to
    # remove) must not stop the recording it precedes: the very next `screenrecord` spawn overwrites
    # the same path regardless, so the clear is best-effort, not load-bearing.
    def _raises(cmd):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(adb, "real_run", _raises)
    monkeypatch.setattr(intervals, "start_screenrecord", lambda *a, **kw: _SENTINEL)
    result = ondevice_evidence.android_screenrecord("serial-1", Path("video.mp4"))
    assert result is _SENTINEL


def test_xcuitest_video_confirms_it_actually_started(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare spawn only proves the process started, not that it wrote a frame yet — a fast failing
    # case can tear down before it does, shipping an absent or unplayable mp4 for exactly the
    # failure this module's evidence exists to explain.
    calls: list[tuple[str, Path, bool]] = []

    def fake_start_video(
        udid: str, path: Path, *, confirm_started: bool = False
    ) -> intervals.Interval:
        calls.append((udid, path, confirm_started))
        return _SENTINEL

    monkeypatch.setattr(intervals, "start_video", fake_start_video)
    result = ondevice_evidence.xcuitest_video("udid-1", Path("video.mp4"))
    assert result is _SENTINEL
    assert calls == [("udid-1", Path("video.mp4"), True)]


def test_slug_is_filesystem_safe_and_stable_per_nodeid() -> None:
    slug = ondevice_evidence._slug("tests/test_x.py::TestY::test_z[a b/c]")
    assert slug.startswith("tests_test_x.py_TestY_test_z_a_b_c_")
    assert ondevice_evidence._slug("tests/test_x.py::TestY::test_z[a b/c]") == slug  # stable


def test_slug_never_collides_between_distinct_nodeids() -> None:
    # The readable prefix alone is not injective: a space and a slash both collapse to the same "_",
    # so two distinct parametrize ids can land on the identical readable prefix. Colliding here would
    # let one test's kept (failed) evidence be silently overwritten by another's `dest.mkdir`, and a
    # later green run on the collision could `rmtree` evidence that belonged to the failed one.
    collided_readable = "test_z[a b]"
    other_readable = "test_z[a/b]"
    assert (
        ondevice_evidence._slug(collided_readable).rsplit("_", 1)[0]
        == ondevice_evidence._slug(other_readable).rsplit("_", 1)[0]
    )
    assert ondevice_evidence._slug(collided_readable) != ondevice_evidence._slug(other_readable)

"""The on-device suites' per-test evidence capture (`ondevice_evidence.py`).

Exercises the real plugin through `pytester`: a real inner pytest session runs the real
`ondevice_evidence` plugin and the real `capture` fixture generator, with fake `start_video`/
`start_log` callables standing in for the adb and XCUITest interval starters alike — so the
keep-on-failure / discard-on-pass behavior is pinned on the fast gate, without a device.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import ondevice_evidence
import pytest

from bajutsu.evidence import intervals

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


def test_discards_the_capture_when_the_test_passes(pytester) -> None:
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        _IMPORTS + _FAKE_STARTERS + _EVIDENCE_FIXTURE + "def test_ok():\n    assert True\n"
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    slug = ondevice_evidence._slug("test_discards_the_capture_when_the_test_passes.py::test_ok")
    assert not (pytester.path / "runs" / "fake-lane" / slug).exists()


def test_keeps_the_capture_when_the_test_fails(pytester) -> None:
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


def test_keeps_the_capture_when_setup_fails(pytester) -> None:
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


def test_clears_a_stale_directory_before_recording(pytester) -> None:
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


def test_stops_the_started_video_when_start_log_raises(pytester) -> None:
    # `start_video` can succeed (spawning a real device-side `screenrecord`) and then `start_log`
    # raise — a transient adb hiccup starting the second process must neither orphan the first nor
    # fail a test the driver contract never touched: starting the capture is itself best-effort.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        "import pathlib\n"
        "\n"
        "import ondevice_evidence\n"
        "import pytest\n"
        "\n"
        "\n"
        "class _FakeVideo:\n"
        "    def stop(self):\n"
        "        pathlib.Path('stopped_video.marker').write_text('stopped')\n"
        "        return pathlib.Path('stopped_video.marker')\n"
        "\n"
        "\n"
        "def _fake_start_video(serial, path, **kwargs):\n"
        "    return _FakeVideo()\n"
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


class _FakeNode:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.stash: pytest.Stash = pytest.Stash()


class _FakeRequest:
    """A minimal stand-in for `pytest.FixtureRequest`: `capture()` only ever reads `.node`."""

    def __init__(self, nodeid: str) -> None:
        self.node = _FakeNode(nodeid)


def test_warns_about_a_missing_or_empty_artifact(caplog, monkeypatch, tmp_path) -> None:
    # `_spawn` discards the child's stderr, so a `recordVideo`/`screenrecord` that refused to start
    # or died leaves no other trace, and `if-no-files-found: ignore` on the CI upload step would let
    # an entirely blind capture pass for a clean one. Drive `capture()` directly as a plain generator
    # (it is one; only its callers wrap it as a fixture) rather than through `pytester`, since this
    # only needs to inspect a log record, not a whole inner pytest session.
    monkeypatch.chdir(tmp_path)

    class _NullInterval:
        def stop(self) -> None:
            return None

    def start_records_nothing(serial: str, path: Path) -> _NullInterval:
        return _NullInterval()  # nothing ever writes to `path`

    caplog.set_level(logging.WARNING)
    gen = ondevice_evidence.capture(
        "fake-serial",
        "fake-lane",
        _FakeRequest("fake::test"),
        start_video=start_records_nothing,
        start_log=start_records_nothing,
    )
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    missing_warnings = [r for r in caplog.records if "is missing or empty" in r.message]
    assert len(missing_warnings) == 2  # video.mp4 and device.log, both never written


def test_stops_the_log_even_when_stopping_the_video_raises(pytester) -> None:
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


def test_a_stop_failure_still_raises_when_the_test_already_failed(pytester) -> None:
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
    pytester,
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


def test_discards_evidence_once_a_crashed_attempt_recovers_and_passes(pytester) -> None:
    # `backend_crash_recovery` (BE-0334, used by the iOS conformance suite) re-runs a WHOLE item via
    # `_initrequest()` on an infra-fault retry, reusing the same `pytest.Item` — so `item.stash`
    # persists across attempts. If the makereport hook only ever latched `_FAILED` true and never
    # cleared it, the crashed attempt's tag would still read true on the later, recovered, passing
    # attempt, wrongly keeping evidence a passing test does not need (and by then holding only the
    # passing attempt's own recording anyway, since `capture` reuses the same file path per attempt).
    pytester.makeconftest("pytest_plugins = ['ondevice_evidence', 'backend_crash_recovery']\n")
    pytester.makepyfile(
        _IMPORTS
        + "from bajutsu.drivers import base\n\n\n"
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


def test_android_screenrecord_forwards_this_modules_pinned_bounds(monkeypatch) -> None:
    calls = []

    def fake_start_screenrecord(serial, path, *, time_limit=None, size=None, bit_rate=None):
        calls.append((serial, path, time_limit, size, bit_rate))
        return "sentinel"

    monkeypatch.setattr(intervals, "start_screenrecord", fake_start_screenrecord)
    result = ondevice_evidence.android_screenrecord("serial-1", Path("video.mp4"))
    assert result == "sentinel"
    assert calls == [
        (
            "serial-1",
            Path("video.mp4"),
            ondevice_evidence._TIME_LIMIT_S,
            ondevice_evidence._SIZE,
            ondevice_evidence._BIT_RATE,
        )
    ]


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

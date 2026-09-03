"""The on-device conformance suite's infrastructure-fault recovery (BE-0334 / BE-0342).

The suite drives a real Simulator through a module-scoped lease, outside `bajutsu run`'s recovery.
This exercises the recovery plugin through `pytester`: a real inner pytest session runs the real
`backend_crash_recovery` plugin, the real `LeaseHolder`, and the real budget, with a fake driver
standing in for the Simulator — so the re-lease / retry behavior is pinned on the fast gate, without
a device. A `base.BackendCrashError` is an infrastructure fault and is recovered; a contract
violation is not, and keeps failing immediately. Launch thunks return `(driver, teardown)` so discard
reaches the platform teardown, not a missing `driver.close()` (BE-0342).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

# The inner conftest registers the real plugin; every inner test file supplies a fake `_backend_launch`
# whose driver crashes on a scripted schedule, so the plugin's real re-lease loop runs against it.
_INNER_CONFTEST = "pytest_plugins = ['backend_crash_recovery']\n"


def test_recovers_a_transient_backend_crash(pytester: pytest.Pytester) -> None:
    # The first lease's driver crashes; the plugin cold-respawns and re-runs the test, which then
    # passes on the fresh lease — the exact asymmetry BE-0334 restores to the conformance suite.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()
                assert _LAUNCHES["n"] == 2  # crashed once, recovered on the cold respawn
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)


def test_recovers_a_crash_during_lease_bringup(pytester: pytest.Pytester) -> None:
    # The crash can happen while the lease is coming up (the launch itself), before any test step —
    # the pipeline recovers that too. The first launch raises; the retry leases afresh and passes.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    if _LAUNCHES["n"] == 1:
                        raise base.BackendCrashError("fake runner crashed at bring-up")
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                assert _LAUNCHES["n"] == 2  # bring-up crashed once, recovered on the cold respawn
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)


def test_fails_loudly_when_every_attempt_crashes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A runner that crashes every attempt is not a one-off: recovery is bounded by the count, and the
    # test fails once the budget is spent (BE-0049 — flakiness is never absorbed into a pass). With
    # crash_retries=2, exactly three attempts run.
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "2")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def act(self) -> None:
                    raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()

            def test_attempt_count(_backend_lease_holder):
                # Runs after test_acts (declaration order); by now every attempt has leased.
                assert _LAUNCHES["n"] == 3  # crash_retries=2 -> 3 attempts, all crashed
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1, passed=1)


def test_does_not_retry_a_contract_violation(pytester: pytest.Pytester) -> None:
    # A contract violation (a mis-resolved selector) is not a BackendCrashError, so it is never
    # retried — it fails immediately, on the single lease, exactly as it does today.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_violates(driver):
                assert _LAUNCHES["n"] == 1
                raise base.AmbiguousSelector("two matches; the contract must fail, not retry")
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    # One lease only: the contract violation was not treated as infrastructure.
    result.stdout.fnmatch_lines(["*AmbiguousSelector*"])


def test_reports_a_wedged_device_instead_of_retrying_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0378 unit 3: a `simctl.DeviceTimeout` says the host wedged, not that the driver broke its
    # contract — so the lane names it a host fault. It is still not retried, and the lease is left
    # intact, so the next test pays no cold respawn to answer a stall that clears on its own.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "2")  # retries to spare; none may be spent here
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.backend_cli import simctl

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_wedges(driver):
                raise simctl.DeviceTimeout(
                    "device operation timed out after 60s: xcrun simctl get_app_container UDID"
                    " com.example data (this host's CoreSimulator may be wedged)"
                )

            def test_keeps_the_same_lease(driver):
                assert _LAUNCHES["n"] == 1  # the wedge cost no respawn
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1, passed=1)
    # Named in the job log, so a maintainer reading the red check sees the wedge rather than a
    # conformance failure.
    result.stdout.fnmatch_lines(["*host fault*not retried*CoreSimulator may be wedged*"])
    summary = json.loads(report.read_text())
    assert summary["hostFaults"] == 1
    # The retry tallies are untouched: nothing respawned, so nothing recovered or was exhausted.
    assert summary["respawns"] == 0
    assert summary["recovered"] == 0
    assert summary["exhausted"] == 0
    (event,) = summary["events"]
    assert event["kind"] == "hostFault"
    # The command and the deadline it exceeded ride along on the timeout's own message.
    assert "get_app_container" in event["reason"] and "60s" in event["reason"]


def test_an_absorbed_stall_is_reported_though_its_test_passes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The one host event no failure carries (BE-0378): a stall the harness's own retry cleared. Its
    # test passes, so pytest renders neither the captured log nor a failure section — leaving the lane
    # to pay a full deadline and say nothing, which is the degradation the report exists to surface.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from backend_crash_recovery import record_absorbed_stall

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                return lambda: (_FakeDriver(), (lambda: None))

            @pytest.fixture(scope="module")
            def _stalled(request):
                # Where the real one is raised: a module-scoped fixture's preparatory device read.
                record_absorbed_stall(request.node, "data container read timed out after 60s")

            def test_passes_anyway(_backend_lease_holder, _stalled):
                assert _backend_lease_holder is not None
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*host stall absorbed*timed out after 60s*"])
    summary = json.loads(report.read_text())
    assert summary["absorbedStalls"] == 1
    # Counted apart from the wedge that reddens a check: this one cost time, not a verdict.
    assert summary["hostFaults"] == 0
    assert summary["respawns"] == 0
    (event,) = summary["events"]
    assert event["kind"] == "absorbedStall"


def test_still_retries_a_crash_that_is_also_a_host_fault(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A `BackendCrashError` answers both of BE-0378's questions, and the retry decision outranks the
    # diagnosis: it recovers by respawn as it always did, and is never double-counted as a host fault.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    summary = json.loads(report.read_text())
    assert summary["respawns"] == 1
    assert summary["recovered"] == 1
    assert summary["hostFaults"] == 0
    assert [e["kind"] for e in summary["events"]] == ["crash"]


def test_amortizes_the_lease_across_crash_free_tests(pytester: pytest.Pytester) -> None:
    # The reason the lease is module-scoped (BE-0334 Unit 3): in the common, crash-free case the
    # expensive cold spawn runs once and every test reuses it. The plugin must not erode that.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def act(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_one(driver):
                driver.act()
            def test_two(driver):
                driver.act()
            def test_three(driver):
                driver.act()
                assert _LAUNCHES["n"] == 1  # one shared cold spawn across the whole module
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=3)


def test_a_crash_does_not_cascade_to_later_tests(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A module-scoped lease lets one crash poison every later test (the cascade BE-0334 Unit 3 stops).
    # With crash_retries=0 the crashing test fails at once; the dead lease must then be discarded so the
    # *next* test re-leases a fresh device and passes, rather than inheriting the dead runner.
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "0")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_a_crashes(driver):
                driver.act()  # crashes on the first lease; crash_retries=0 -> fails at once
            def test_b_runs_on_a_fresh_lease(driver):
                driver.act()  # the dead lease was discarded, so this is a fresh, healthy device
                assert _LAUNCHES["n"] == 2
            """
        )
    )
    result = pytester.runpytest_inprocess()
    # No cascade: the crash fails only its own test; the later test re-leases and passes.
    result.assert_outcomes(failed=1, passed=1)


def test_reports_and_counts_a_recovery(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0334 Unit 4: every respawn is counted into an uploaded report, so a degrading lane is visible
    # rather than merely looking slower. A transient crash that recovers records one respawn.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    # The respawn is announced inline in the job log, not hidden until a failing test's captured log.
    result.stdout.fnmatch_lines(["*backend crashed*respawning*"])
    summary = json.loads(report.read_text())
    assert summary["respawns"] == 1
    assert summary["recovered"] == 1
    assert summary["exhausted"] == 0
    assert len(summary["events"]) == 1
    assert summary["events"][0]["willRetry"] is True


def test_reports_an_exhausted_recovery(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A runner that never comes back is counted as an exhausted recovery, not a silent slow lane — the
    # count is what tells a maintainer the underlying fault is getting worse.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "1")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                def act(self) -> None:
                    raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    summary = json.loads(report.read_text())
    assert summary["respawns"] == 1  # crash_retries=1 -> one respawn, then exhausted
    assert summary["recovered"] == 0
    assert summary["exhausted"] == 1
    assert [e["willRetry"] for e in summary["events"]] == [True, False]


def test_reports_a_budget_exhausted_recovery(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The wall-clock budget can stop recovery before the retry *count* is spent — a distinct end state
    # from count exhaustion, surfaced in its own message and its own `budgetSpent` event. A near-zero
    # budget with retries to spare makes the second crash (after the first respawn burned the
    # wall-clock) trip the budget rather than the count.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    monkeypatch.setenv(
        "BAJUTSU_CRASH_RETRIES", "5"
    )  # the count would allow more; the budget stops first
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "0.0001")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.common.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                def act(self) -> None:
                    raise base.BackendCrashError("fake runner crashed mid-test")

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    return _FakeDriver(), (lambda: None)
                return launch

            @pytest.fixture
            def driver(_backend_lease_holder):
                return _backend_lease_holder.driver

            def test_acts(driver):
                driver.act()
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    # The budget-exhausted wording is distinct from the count-exhausted "did not recover across N
    # attempts" line, and the event that gave up carries budgetSpent=True.
    result.stdout.fnmatch_lines(["*did not recover within the crash-recovery budget*"])
    summary = json.loads(report.read_text())
    assert summary["exhausted"] == 1
    assert summary["events"][-1]["willRetry"] is False
    assert summary["events"][-1]["budgetSpent"] is True


def test_writes_an_empty_report_when_nothing_crashes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The "always uploads a clean, present artifact" guarantee: with the report path set but no crash,
    # the plugin still writes a zeroed summary, so the CI upload (if-no-files-found: ignore) never
    # silently omits the artifact on a healthy run.
    report = tmp_path / "recovery.json"
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                return lambda: (_FakeDriver(), (lambda: None))

            def test_noop(_backend_lease_holder):
                assert _backend_lease_holder is not None
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)
    assert json.loads(report.read_text()) == {
        "respawns": 0,
        "recovered": 0,
        "exhausted": 0,
        "hostFaults": 0,
        "absorbedStalls": 0,
        "events": [],
    }


def test_creates_the_report_parent_directory(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The report path may name a directory the lane has not created yet (a nested artifacts dir). The
    # write is best-effort observability, so it creates the parents rather than erroring the session.
    report = tmp_path / "nested" / "artifacts" / "recovery.json"  # neither parent exists yet
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                return lambda: (_FakeDriver(), (lambda: None))

            def test_noop(_backend_lease_holder):
                assert _backend_lease_holder is not None
            """
        )
    )
    result = pytester.runpytest_inprocess()
    assert (
        result.ret == 0
    )  # the session finished cleanly, not an INTERNALERROR from the report write
    result.assert_outcomes(passed=1)
    assert json.loads(report.read_text())["respawns"] == 0


def test_a_failed_report_write_does_not_fail_the_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The report only ever counts, it never gates: an unwritable path (here, a parent that is a file,
    # so it can be neither created nor written) must be swallowed with a warning, not raised out of
    # sessionfinish to fail an otherwise-green suite.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    report = blocker / "recovery.json"  # blocker is a file, so this path can never be written
    monkeypatch.setenv("BAJUTSU_BACKEND_RECOVERY_REPORT", str(report))
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                return lambda: (_FakeDriver(), (lambda: None))

            def test_noop(_backend_lease_holder):
                assert _backend_lease_holder is not None
            """
        )
    )
    result = pytester.runpytest_inprocess()
    assert result.ret == 0  # the failed write did not error the session
    result.assert_outcomes(passed=1)
    assert not report.exists()


def test_writes_no_report_when_the_env_is_unset(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The report is opt-in (an uploaded CI artifact): with no path configured and no crash, the plugin
    # writes nothing and stays entirely inert.
    monkeypatch.delenv("BAJUTSU_BACKEND_RECOVERY_REPORT", raising=False)
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest

            pytestmark = pytest.mark.backend_crash_recovery

            class _FakeDriver:
                pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                return lambda: (_FakeDriver(), (lambda: None))

            def test_noop(_backend_lease_holder):
                assert _backend_lease_holder is not None
            """
        )
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)


# --- BE-0342: the lease's launch/teardown seam ---
# Direct `LeaseHolder` cases pin the discard contract without a nested pytest session; the pytester
# cases above already cover the plugin wiring the same holder into recovery.


class _FakeDriver:
    pass


def test_invalidate_runs_teardown_once_for_the_discarded_lease() -> None:
    from backend_crash_recovery import LeaseHolder

    torn: list[str] = []

    def launch() -> tuple[object, object]:
        return _FakeDriver(), (lambda: torn.append("teardown"))

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    assert holder.driver is not None
    holder.invalidate()
    assert torn == ["teardown"]
    holder.invalidate()  # a second discard with nothing leased tears nothing down
    assert torn == ["teardown"]


def test_final_release_runs_teardown_once() -> None:
    from backend_crash_recovery import LeaseHolder

    torn: list[str] = []

    def launch() -> tuple[object, object]:
        return _FakeDriver(), (lambda: torn.append("teardown"))

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    assert holder.driver is not None
    holder.close()
    assert torn == ["teardown"]


def test_next_driver_access_launches_a_fresh_lease() -> None:
    from backend_crash_recovery import LeaseHolder

    launches: list[object] = []

    def launch() -> tuple[object, object]:
        driver = _FakeDriver()
        launches.append(driver)
        return driver, (lambda: None)

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    first = holder.driver
    holder.invalidate()
    second = holder.driver
    assert first is launches[0]
    assert second is launches[1]
    assert first is not second


def test_generation_names_the_current_lease() -> None:
    # BE-0378 unit 1: the identity a caller memoises an installation-scoped fact against. It starts at
    # zero (nothing leased), and each fresh lease moves it, so a cold respawn drops any such memo.
    from backend_crash_recovery import LeaseHolder

    def launch() -> tuple[object, object]:
        return _FakeDriver(), (lambda: None)

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    assert holder.generation == 0
    assert holder.driver is not None
    assert holder.generation == 1
    assert holder.driver is not None
    assert holder.generation == 1  # the same lease reused: a memo keyed on it stays valid
    holder.invalidate()
    assert holder.driver is not None
    assert holder.generation == 2


def test_a_failed_launch_does_not_move_the_generation() -> None:
    # A bring-up that raised leased nothing, so there is no new installation for a memo to name.
    import pytest
    from backend_crash_recovery import LeaseHolder

    from bajutsu.common.drivers import base

    state = {"started": 0}

    def launch() -> tuple[object, object]:
        state["started"] += 1
        if state["started"] == 1:
            raise base.BackendCrashError("died during readiness")
        return _FakeDriver(), (lambda: None)

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    with pytest.raises(base.BackendCrashError):
        _ = holder.driver
    assert holder.generation == 0
    assert holder.driver is not None
    assert holder.generation == 1


def test_mid_run_teardown_swallows_a_wiring_defect_into_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A missing method (AttributeError) on the mid-run path must not mask the crash — and must not
    # sit at debug level either (BE-0342).
    import logging

    from backend_crash_recovery import LeaseHolder

    def launch() -> tuple[object, object]:
        def teardown() -> None:
            raise AttributeError("no close on this driver")

        return _FakeDriver(), teardown

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    assert holder.driver is not None
    with caplog.at_level(logging.WARNING):
        holder.invalidate()  # must not raise
    assert any(
        "tearing down the discarded on-device lease failed" in r.message for r in caplog.records
    )


def test_final_release_propagates_a_wiring_defect() -> None:
    import pytest
    from backend_crash_recovery import LeaseHolder

    def launch() -> tuple[object, object]:
        def teardown() -> None:
            raise AttributeError("no close on this driver")

        return _FakeDriver(), teardown

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    assert holder.driver is not None
    with pytest.raises(AttributeError, match="no close on this driver"):
        holder.close()


def test_a_failed_launch_leaves_no_leased_teardown() -> None:
    # A launch thunk that tore its own environment down before raising leaves the holder with no
    # teardown recorded, so a later access cold-spawns rather than discarding a lease that never
    # existed. `launch_driver`'s own post-`env.start` guard lives in tests/runner/test_launch.py.
    from backend_crash_recovery import LeaseHolder

    from bajutsu.common.drivers import base

    state = {"started": 0, "torn": 0}

    def launch() -> tuple[object, object]:
        state["started"] += 1
        driver = _FakeDriver()

        def teardown() -> None:
            state["torn"] += 1

        if state["started"] == 1:
            teardown()
            raise base.BackendCrashError("died during readiness")
        return driver, teardown

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    try:
        _ = holder.driver
    except base.BackendCrashError:
        pass
    else:
        raise AssertionError("first launch must raise")
    assert state == {"started": 1, "torn": 1}
    # A later access cold-spawns cleanly; the failed bring-up left no leased teardown behind.
    assert holder.driver is not None
    assert state == {"started": 2, "torn": 1}


def test_never_launched_lease_tears_nothing_down() -> None:
    from backend_crash_recovery import LeaseHolder

    torn: list[str] = []

    def launch() -> tuple[object, object]:
        return _FakeDriver(), (lambda: torn.append("teardown"))

    holder = LeaseHolder(launch)  # type: ignore[arg-type]
    holder.close()
    assert torn == []

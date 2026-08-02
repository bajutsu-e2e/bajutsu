"""The on-device conformance suite's infrastructure-fault recovery (BE-0334 Unit 2).

The suite drives a real Simulator through a module-scoped lease, outside `bajutsu run`'s recovery.
This exercises the recovery plugin through `pytester`: a real inner pytest session runs the real
`backend_crash_recovery` plugin, the real `LeaseHolder`, and the real budget, with a fake driver
standing in for the Simulator — so the re-lease / retry behavior is pinned on the fast gate, without
a device. A `base.BackendCrashError` is an infrastructure fault and is recovered; a contract
violation is not, and keeps failing immediately.
"""

from __future__ import annotations

import textwrap

# The inner conftest registers the real plugin; every inner test file supplies a fake `_backend_launch`
# whose driver crashes on a scripted schedule, so the plugin's real re-lease loop runs against it.
_INNER_CONFTEST = "pytest_plugins = ['backend_crash_recovery']\n"


def test_recovers_a_transient_backend_crash(pytester) -> None:
    # The first lease's driver crashes; the plugin cold-respawns and re-runs the test, which then
    # passes on the fresh lease — the exact asymmetry BE-0334 restores to the conformance suite.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                    self.closed = False
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")
                def close(self) -> None:
                    self.closed = True

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1)  # only the first lease crashes
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


def test_recovers_a_crash_during_lease_bringup(pytester) -> None:
    # The crash can happen while the lease is coming up (the launch itself), before any test step —
    # the pipeline recovers that too. The first launch raises; the retry leases afresh and passes.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def close(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    if _LAUNCHES["n"] == 1:
                        raise base.BackendCrashError("fake runner crashed at bring-up")
                    return _FakeDriver()
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


def test_fails_loudly_when_every_attempt_crashes(pytester, monkeypatch) -> None:
    # A runner that crashes every attempt is not a one-off: recovery is bounded by the count, and the
    # test fails once the budget is spent (BE-0049 — flakiness is never absorbed into a pass). With
    # crash_retries=2, exactly three attempts run.
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "2")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def act(self) -> None:
                    raise base.BackendCrashError("fake runner crashed mid-test")
                def close(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver()
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


def test_does_not_retry_a_contract_violation(pytester) -> None:
    # A contract violation (a mis-resolved selector) is not a BackendCrashError, so it is never
    # retried — it fails immediately, on the single lease, exactly as it does today.
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def close(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver()
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


def test_amortizes_the_lease_across_crash_free_tests(pytester) -> None:
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
                def close(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver()
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


def test_a_crash_does_not_cascade_to_later_tests(pytester, monkeypatch) -> None:
    # A module-scoped lease lets one crash poison every later test (the cascade BE-0334 Unit 3 stops).
    # With crash_retries=0 the crashing test fails at once; the dead lease must then be discarded so the
    # *next* test re-leases a fresh device and passes, rather than inheriting the dead runner.
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "0")
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(
        textwrap.dedent(
            """
            import pytest
            from bajutsu.drivers import base

            pytestmark = pytest.mark.backend_crash_recovery
            _LAUNCHES = {"n": 0}

            class _FakeDriver:
                def __init__(self, crash: bool) -> None:
                    self._crash = crash
                def act(self) -> None:
                    if self._crash:
                        raise base.BackendCrashError("fake runner crashed mid-test")
                def close(self) -> None:
                    pass

            @pytest.fixture(scope="module")
            def _backend_launch():
                def launch():
                    _LAUNCHES["n"] += 1
                    return _FakeDriver(crash=_LAUNCHES["n"] == 1)  # only the first lease is poisoned
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

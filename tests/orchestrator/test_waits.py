"""Tests for the orchestrator condition waits (wait for/until, screenChanged, settled)."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


def test_wait_for_appears() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1 and all(e["identifier"] != "ready" for e in driver.screen):
            driver.screen = [*driver.screen, el("ready", "R")]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_timeout() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout" in result.steps[0].reason


def test_wait_for_tolerates_transient_empty_first_poll() -> None:
    # BE-0231 Unit 3: on a cold-boot launch the first poll can land mid-transition on an empty tree.
    # The `for` wait must read that as "not yet" and keep polling within its budget, not as "gone" —
    # an empty first poll must not fail the step or consume the budget before the element renders.
    driver = FakeDriver([])  # first query() lands on an empty tree during the launch transition

    def on_sleep(t: float) -> None:
        if all(e["identifier"] != "stable.row.1" for e in driver.screen):
            driver.screen = [el("stable.row.1", "Row 1")]

    result = run_scenario(
        driver,
        _scenario(
            {"name": "x", "steps": [{"wait": {"for": {"id": "stable.row.1"}, "timeout": 1.0}}]}
        ),
        clock=FakeClock(on_sleep),
    )
    assert result.ok and result.steps[0].ok


def test_wait_until_gone() -> None:
    driver = FakeDriver([el("spinner", "")])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = []

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"wait": {"until": {"gone": {"id": "spinner"}}, "timeout": 1.0}}],
            }
        ),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = [el("b", "B", ["button"])]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed_times_out_when_screen_is_static() -> None:
    # A screen that never changes must fail the step, not pass it — a wrongly-passing
    # wait would silently weaken every downstream assertion.
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout: screenChanged" in result.steps[0].reason


def test_wait_settled_waits_for_a_stable_screen() -> None:
    driver = FakeDriver([el("home", "Home", ["button"])])

    def on_sleep(t: float) -> None:
        if t < 0.15:  # a transition still in progress: the frame keeps moving
            driver.screen = [
                {
                    "identifier": "home",
                    "label": "Home",
                    "traits": ["button"],
                    "value": None,
                    "frame": (t, 0.0, 10.0, 10.0),
                }
            ]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 2.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok and result.steps[0].ok


def test_wait_settled_proceeds_on_blank_screen() -> None:
    driver = FakeDriver([])  # collapsed / covered: never settles, but must not fail the step

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 0.3}}]}),
        clock=FakeClock(),
    )
    assert result.ok and result.steps[0].ok


def test_wait_skips_sleep_when_query_exceeds_poll_interval() -> None:
    """When query() takes longer than _POLL, additional sleep is skipped."""
    from bajutsu.orchestrator import _POLL, _wait
    from bajutsu.scenario import Wait

    sleeps: list[float] = []

    class TrackingClock:
        def __init__(self) -> None:
            self._t = 0.0

        def now(self) -> float:
            return self._t

        def sleep(self, s: float) -> None:
            sleeps.append(s)
            self._t += s

    clock = TrackingClock()
    query_latency = _POLL * 3  # query takes 3x _POLL
    query_count = 0

    class SlowQueryDriver:
        name = "slow"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            clock._t += query_latency  # simulate query latency on the clock
            query_count += 1
            if query_count >= 3:
                return [el("target", "T")]
            return []

    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})
    ok, reason = _wait(SlowQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    assert reason == ""
    # query cost > _POLL -> no extra sleep needed
    assert all(s < 0.01 for s in sleeps), f"expected near-zero sleeps, got {sleeps}"


class _LogicalClock:
    """A clock whose only motion is `sleep` advancing logical time (no real waiting)."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


def _slow_render_driver(clock: _LogicalClock, reveal_at: float) -> base.Driver:
    """A driver standing in for a slow (software) renderer: the target only presents once
    logical time passes `reveal_at` — modelling the CI x86_64 emulator taking longer than a
    hardware-accelerated one to draw a sheet/cover."""

    class SlowRenderDriver:
        name = "slow-render"

        def query(self) -> list[base.Element]:
            return [el("target", "T")] if clock.now() >= reveal_at else []

    return SlowRenderDriver()  # type: ignore[return-value]


def test_wait_floor_env_extends_the_ceiling(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """BAJUTSU_MIN_WAIT_TIMEOUT raises a wait's ceiling so a slow renderer has time to present,
    without editing the shared scenario (its `timeout: 5` is the same across every backend)."""
    from bajutsu.orchestrator import _wait
    from bajutsu.scenario import Wait

    # The sheet presents at t=8s — past the shared 5s ceiling, under a 15s floor.
    reveal_at = 8.0
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})

    # Without the floor, the 5s ceiling times out before the slow renderer draws the element.
    clock = _LogicalClock()
    ok, reason = _wait(_slow_render_driver(clock, reveal_at), w, clock)
    assert not ok
    assert "timeout" in reason

    # The Android lane opts in to a larger floor, so the same 5s scenario tolerates the slow draw.
    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "15")
    clock2 = _LogicalClock()
    ok2, reason2 = _wait(_slow_render_driver(clock2, reveal_at), w, clock2)
    assert ok2
    assert reason2 == ""


def test_wait_floor_never_shrinks_a_larger_scenario_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The floor is a minimum, not an override: a scenario asking for more than the floor keeps it."""
    from bajutsu.orchestrator import _wait
    from bajutsu.scenario import Wait

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "3")
    # Element presents at t=8s: below the 3s floor but within the scenario's own 10s ceiling.
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 10.0})
    clock = _LogicalClock()
    ok, reason = _wait(_slow_render_driver(clock, 8.0), w, clock)
    assert ok
    assert reason == ""


def test_wait_floor_raises_on_malformed_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A malformed BAJUTSU_MIN_WAIT_TIMEOUT (e.g. '15s') must raise ValueError immediately,
    not silently fall back to 0 — a silent fallback would quietly disable the floor and
    reintroduce the very timeout flakiness the env var is meant to prevent."""
    import pytest

    from bajutsu.orchestrator.waits import _timeout_floor

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "15s")
    with pytest.raises(ValueError, match="BAJUTSU_MIN_WAIT_TIMEOUT"):
        _timeout_floor()


def test_wait_still_sleeps_when_query_is_fast() -> None:
    """When query() is fast, sleep remains at _POLL as before."""
    from bajutsu.orchestrator import _POLL, _wait
    from bajutsu.scenario import Wait

    sleeps: list[float] = []

    class TrackingClock:
        def __init__(self) -> None:
            self._t = 0.0

        def now(self) -> float:
            return self._t

        def sleep(self, s: float) -> None:
            sleeps.append(s)
            self._t += s

    clock = TrackingClock()
    query_count = 0

    class FastQueryDriver:
        name = "fast"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            # query is instant (does not advance clock)
            query_count += 1
            if query_count >= 3:
                return [el("target", "T")]
            return []

    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})
    ok, _reason = _wait(FastQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    # query is instant -> sleep stays at _POLL
    assert all(abs(s - _POLL) < 0.001 for s in sleeps), f"expected {_POLL}s sleeps, got {sleeps}"


# --- if / forEach control flow ---

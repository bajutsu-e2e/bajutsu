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

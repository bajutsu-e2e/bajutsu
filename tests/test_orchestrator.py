"""Tests for the orchestrator run loop.

Use FakeDriver (in-memory backend) and FakeClock (sleep advances time) to test
act -> wait -> verify deterministically without a Simulator.
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Relaunch, Scenario


class FakeClock:
    """Advance logical time on sleep; `on_sleep` mutates the world over time."""

    def __init__(self, on_sleep: Callable[[float], None] | None = None) -> None:
        self._t = 0.0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self._t)


def _el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def test_happy_path_tap_and_expect() -> None:
    driver = FakeDriver([_el("home.title", "ホーム"), _el("settings.open", "設定", ["button"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "open settings",
            "steps": [{"tap": {"id": "settings.open"}}],
            "expect": [{"exists": {"id": "home.title"}}],
        }),
        clock=FakeClock(),
    )
    assert result.ok
    assert driver.actions == [("tap", {"id": "settings.open"})]


def test_run_scenario_records_duration() -> None:
    # The result carries the scenario's wall-clock (measured off the injected clock) so the
    # report can show per-scenario and total execution time.
    here = _el("here", "H")
    driver = FakeDriver([])  # 'here' shows only after the first poll-sleep advances the clock

    def appear(_t: float) -> None:
        driver.screen = [here]

    scn = _scenario({"name": "d", "steps": [{"wait": {"for": {"id": "here"}, "timeout": 1}}]})
    result = run_scenario(driver, scn, clock=FakeClock(appear))
    assert result.ok
    assert result.duration_s == 0.05  # exactly one 0.05s poll elapsed


def test_relaunch_invokes_injected_callback() -> None:
    # A relaunch step calls the injected relauncher with its env/args overrides.
    seen: list[Relaunch] = []
    scn = _scenario({"name": "r", "steps": [{"relaunch": {"env": {"SEED": "9"}, "args": ["--fresh"]}}]})
    res = run_scenario(FakeDriver([_el("home.title", "H")]), scn, relaunch=seen.append)
    assert res.ok, res.failure
    assert len(seen) == 1 and seen[0].env == {"SEED": "9"} and seen[0].args == ["--fresh"]


def test_relaunch_without_callback_fails_cleanly() -> None:
    # No relauncher injected (e.g. fake driver) -> a clear failure, not a crash.
    scn = _scenario({"name": "r", "steps": [{"relaunch": {}}]})
    res = run_scenario(FakeDriver([_el("home.title", "H")]), scn)
    assert not res.ok and "relaunch" in (res.failure or "")


def test_react_transition_then_expect() -> None:
    home = [_el("settings.open", "設定", ["button"])]
    settings = [_el("settings.reindex", "再生成", ["button"]), _el("settings.title", "設定")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"id": "settings.open"}:
            d.screen = settings

    driver = FakeDriver(home, react=react)
    result = run_scenario(
        driver,
        _scenario({
            "name": "drill into settings",
            "steps": [{"tap": {"id": "settings.open"}}, {"tap": {"id": "settings.reindex"}}],
            "expect": [{"exists": {"id": "settings.title"}}],
        }),
        clock=FakeClock(),
    )
    assert result.ok


def test_tap_not_found_fails_and_stops() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "missing"}}, {"tap": {"id": "a"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and "step 0" in result.failure
    assert len(result.steps) == 1  # stops after the failing step


def test_tap_ambiguous_fails() -> None:
    driver = FakeDriver([_el("row.1", "A", ["cell"]), _el("row.2", "B", ["cell"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"idMatches": "row.*"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "件一致" in result.steps[0].reason  # ambiguous


def test_assert_step_intermediate() -> None:
    driver = FakeDriver([_el("counter", "c", ["staticText"], value="3")])
    ok = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "3"}}]}]}),
        clock=FakeClock(),
    )
    assert ok.ok
    bad = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "4"}}]}]}),
        clock=FakeClock(),
    )
    assert not bad.ok
    assert bad.steps[0].assertion_results[0].ok is False


def test_wait_for_appears() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1 and all(e["identifier"] != "ready" for e in driver.screen):
            driver.screen = [*driver.screen, _el("ready", "R")]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_timeout() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout" in result.steps[0].reason


def test_wait_until_gone() -> None:
    driver = FakeDriver([_el("spinner", "")])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = []

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": {"gone": {"id": "spinner"}}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = [_el("b", "B", ["button"])]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_settled_waits_for_a_stable_screen() -> None:
    driver = FakeDriver([_el("home", "Home", ["button"])])

    def on_sleep(t: float) -> None:
        if t < 0.15:  # a transition still in progress: the frame keeps moving
            driver.screen = [{
                "identifier": "home", "label": "Home", "traits": ["button"],
                "value": None, "frame": (t, 0.0, 10.0, 10.0),
            }]

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


def test_type_and_swipe_actions() -> None:
    driver = FakeDriver([_el("search.field", "検索", ["textField"]), _el("list", "", ["table"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "x",
            "steps": [
                {"type": {"text": "hello", "into": {"id": "search.field"}}},
                {"swipe": {"on": {"id": "list"}, "direction": "up"}},
                {"swipe": {"from": [1, 2], "to": [3, 4]}},
            ],
        }),
        clock=FakeClock(),
    )
    assert result.ok
    assert [a[0] for a in driver.actions] == ["tap", "type", "swipe", "swipe"]


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
                return [_el("target", "T")]
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
                return [_el("target", "T")]
            return []

    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 5.0})
    ok, _reason = _wait(FastQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    # query is instant -> sleep stays at _POLL
    assert all(abs(s - _POLL) < 0.001 for s in sleeps), f"expected {_POLL}s sleeps, got {sleeps}"


def test_expect_failure() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "x",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"exists": {"id": "missing"}}],
        }),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("expect:")

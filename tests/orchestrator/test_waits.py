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
    ok, reason, tree = _wait(SlowQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    assert reason == ""
    # the settled tree (the poll where the target appeared) is handed back for reuse (BE-0259)
    assert tree == [el("target", "T")]
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


def test_run_scenario_writes_wait_diagnostic_on_first_wait_timeout(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """BE-0231 Unit 1 end to end: a first `wait` that times out writes wait-timeout.json into the run
    dir via the sink — unconditionally, regardless of capturePolicy — carrying the readiness signal
    and provenance the pool folded in, so the failure is decidable from artifacts."""
    import json

    from bajutsu.evidence import FileSink
    from bajutsu.platform_lifecycle import ReadinessResult

    driver = FakeDriver([el("a", "A"), el("b", "B")])  # content present, but never the awaited row
    sink = FileSink(
        tmp_path,
        readiness=ReadinessResult(True, "count", 1.5),
        provenance={"scenarioHash": "sha256:x", "toolVersion": "9.9.9"},
    )
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
        sink=sink,
    )
    assert not result.ok
    diag = next(a for a in result.steps[0].artifacts if a.kind == "waitDiagnostic")
    doc = json.loads((tmp_path / diag.name).read_text(encoding="utf-8"))
    assert doc["readiness"]["signal"] == "count"
    assert doc["provenance"]["scenarioHash"] == "sha256:x"
    assert doc["trace"]["elementsAtTimeout"] == 2
    assert [e["identifier"] for e in doc["elements"]] == ["a", "b"]


def test_no_wait_diagnostic_when_wait_succeeds_or_is_not_a_for_wait(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The diagnostic fires only on a `for`-wait timeout: a satisfied wait and a timed-out `until`
    wait (which the trace does not record) both leave no waitDiagnostic artifact."""
    from bajutsu.evidence import FileSink

    # A `for` wait that is immediately satisfied → no diagnostic.
    ok_sink = FileSink(tmp_path / "ok")
    ok_result = run_scenario(
        FakeDriver([el("ready", "R")]),
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(),
        sink=ok_sink,
    )
    assert ok_result.ok
    assert not any(a.kind == "waitDiagnostic" for a in ok_result.steps[0].artifacts)

    # A `wait until: gone` that times out → the `for` trace never ran, so no diagnostic.
    gone_sink = FileSink(tmp_path / "gone")
    gone_result = run_scenario(
        FakeDriver([el("stays", "S")]),
        _scenario(
            {"name": "x", "steps": [{"wait": {"until": {"gone": {"id": "stays"}}, "timeout": 0.2}}]}
        ),
        clock=FakeClock(),
        sink=gone_sink,
    )
    assert not gone_result.ok
    assert not any(a.kind == "waitDiagnostic" for a in gone_result.steps[0].artifacts)


def test_wait_diagnostic_written_once_after_on_blocked_retry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """When a first wait times out, on_blocked clears the block, and the retry times out too, exactly
    one diagnostic is written — from the retry's own (fresh) trace, not the first attempt's."""
    from bajutsu.evidence import FileSink
    from bajutsu.orchestrator.types import AlertEvent

    calls = {"n": 0}

    def on_blocked(_driver: object) -> AlertEvent:
        calls["n"] += 1
        return AlertEvent(label="Not Now")

    sink = FileSink(tmp_path)
    result = run_scenario(
        FakeDriver([el("a", "A")]),  # the awaited "never" is absent both times
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
        sink=sink,
        on_blocked=on_blocked,
    )
    assert not result.ok
    assert calls["n"] == 1  # on_blocked fired once, then the wait was retried
    diagnostics = [a for a in result.steps[0].artifacts if a.kind == "waitDiagnostic"]
    assert len(diagnostics) == 1
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_wait_records_trace_on_timeout_for_diagnosis() -> None:
    """BE-0231 Unit 1: a `for` wait that times out fills the supplied WaitTrace so the failure is
    diagnosable — how many polls, when the tree first became non-empty, and how many elements were
    present at the timeout — separating "nothing rendered" from "content rendered, awaited element
    absent"."""
    from bajutsu.orchestrator.waits import WaitTrace, _wait
    from bajutsu.scenario import Wait

    # The tree is empty until t=1s, then shows 2 elements — but never the awaited "target".
    def driver_for(clock: _LogicalClock) -> base.Driver:
        class D:
            name = "d"

            def query(self) -> list[base.Element]:
                return [el("a", "A"), el("b", "B")] if clock.now() >= 1.0 else []

        return D()  # type: ignore[return-value]

    clock = _LogicalClock()
    trace = WaitTrace()
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 2.0})
    ok, reason, tree = _wait(driver_for(clock), w, clock, trace=trace)
    assert not ok
    assert "timeout" in reason
    # even on timeout the last-seen tree is handed back (the diagnostic reuses it — BE-0259)
    assert tree == [el("a", "A"), el("b", "B")]
    assert trace.target and trace.target in reason  # the awaited selector, as the reason renders it
    assert trace.timeout_s == 2.0
    assert trace.polls >= 2
    assert trace.first_nonempty_s is not None and trace.first_nonempty_s >= 1.0
    assert trace.elements_at_timeout == 2  # content was present, just not the awaited element


def test_wait_trace_stays_empty_when_tree_never_renders() -> None:
    """A tree that never becomes non-empty leaves first_nonempty_s None — the "nothing rendered"
    hypothesis, distinct from "rendered but the awaited element was absent"."""
    from bajutsu.orchestrator.waits import WaitTrace, _wait
    from bajutsu.scenario import Wait

    class Empty:
        name = "empty"

        def query(self) -> list[base.Element]:
            return []

    clock = _LogicalClock()
    trace = WaitTrace()
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 1.0})
    ok, _reason, tree = _wait(Empty(), w, clock, trace=trace)  # type: ignore[arg-type]
    assert not ok
    assert tree == []  # the (empty) tree is still handed back, never None, for a `for` wait
    assert trace.first_nonempty_s is None
    assert trace.elements_at_timeout == 0


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
    ok, reason, _ = _wait(_slow_render_driver(clock, reveal_at), w, clock)
    assert not ok
    assert "timeout" in reason

    # The Android lane opts in to a larger floor, so the same 5s scenario tolerates the slow draw.
    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "15")
    clock2 = _LogicalClock()
    ok2, reason2, _ = _wait(_slow_render_driver(clock2, reveal_at), w, clock2)
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
    ok, reason, _ = _wait(_slow_render_driver(clock, 8.0), w, clock)
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
    ok, _reason, _tree = _wait(FastQueryDriver(), w, clock)  # type: ignore[arg-type]
    assert ok
    # query is instant -> sleep stays at _POLL
    assert all(abs(s - _POLL) < 0.001 for s in sleeps), f"expected {_POLL}s sleeps, got {sleeps}"


# --- if / forEach control flow ---

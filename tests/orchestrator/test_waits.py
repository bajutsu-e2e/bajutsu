"""Tests for the orchestrator condition waits (wait for/until, screenChanged, settled)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _orch import FakeClock, _scenario
from conftest import GUARD_LABEL, AlertingDriver, el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.scenario import Wait
from bajutsu.evidence import FileSink
from bajutsu.evidence.network import ScreenTransition
from bajutsu.orchestrator import AlertGuardConfig, _wait, run_scenario
from bajutsu.orchestrator.waits import _TRANSITION_QUIESCENCE


class _GuardStub(FakeDriver):
    """Minimal driver stub for the collapsed-tree guard tests: advertises no HANDLE_SYSTEM_ALERT
    capability, so the mid-wait gate takes its collapsed-tree branch rather than the native path
    (BE-0315). Since BE-0402 that branch can only *report* a block, never clear one.

    Subclasses override `query()` alone; the `FakeDriver` base keeps the rest of the `Driver` surface
    real, so a stub can be passed where a driver is expected without a cast."""

    def __init__(self) -> None:
        super().__init__([])

    def capabilities(self) -> set[str]:
        return set()


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
                    "nativeZ": None,
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


# --- BE-0310: the screen-transition signal, consulted from the `settled` wait ---


def test_wait_settled_ignores_a_transition_from_before_the_wait_started() -> None:
    """A transition observed before this settle wait began — e.g. left over from a prior step, since
    the collector is scenario-scoped, not per-wait — must not be treated as authoritative: taking it
    would settle instantly and miss the current step's own (still in-flight, fire-and-forget)
    transition. The wait falls back to the tree-diff path, which waits the screen out. Mirrors the
    since-start guard the readiness gate applies to the same signal (BE-0310)."""
    driver = FakeDriver([el("a", "A")])
    clock = FakeClock()
    stale = [(ScreenTransition(kind="screenChanged"), -1.0 - _TRANSITION_QUIESCENCE)]
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, _tree = _wait(driver, w, clock, transitions=lambda: stale)
    assert ok and reason == ""
    assert clock.now() > 0.0  # polled the tree (fell back), not the instant signal-path return


def test_wait_settled_picks_up_a_transition_that_arrives_mid_wait() -> None:
    """A transition whose fire-and-forget report lands mid-wait — the canonical tap → navigate →
    settled case, where `viewDidAppear` fires only after the appearance animation, so its POST
    arrives a few hundred ms into the wait, not at entry — switches the wait onto the signal path
    rather than committing to tree-diff for the whole duration. Mirrors the readiness gate's
    mid-poll pickup (`test_await_ready_catches_a_transition_that_arrives_mid_poll`)."""

    class Churning:  # a new tree each poll, so the tree-diff path never settles on its own —
        name = "churning"  # isolating the signal pickup as the only thing that can end the wait

        def __init__(self) -> None:
            self._n = 0

        def query(self) -> list[base.Element]:
            self._n += 1
            return [el(f"row{self._n}", "R")]

    events: list[tuple[ScreenTransition, float]] = []
    injected = False

    def on_sleep(t: float) -> None:
        nonlocal injected
        if not injected and t >= 0.2:  # the report lands well into the wait, not at entry
            events.append((ScreenTransition(kind="screenChanged"), t))
            injected = True

    clock = FakeClock(on_sleep)
    w = Wait.model_validate({"until": "settled", "timeout": 5.0})
    ok, reason, _tree = _wait(Churning(), w, clock, transitions=lambda: events)  # type: ignore[arg-type]
    assert ok and reason == ""
    assert injected  # the transition really arrived mid-wait, after tree-diff had been polling
    # Settled only after the quiescence window elapsed since that mid-wait transition — proof the
    # wait switched to the signal path, not the never-settling tree-diff (which would run to the 5s
    # deadline on a churning screen).
    assert clock.now() >= events[-1][1] + _TRANSITION_QUIESCENCE
    assert clock.now() < 5.0  # ended via the signal, well before the deadline


def test_wait_settled_signal_waits_out_the_quiescence_window() -> None:
    """A transition just observed must not settle instantly — the wait holds out for
    `_TRANSITION_QUIESCENCE` of silence first."""
    driver = FakeDriver([el("a", "A")])
    clock = FakeClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, _tree = _wait(driver, w, clock, transitions=lambda: fresh)
    assert ok and reason == ""
    assert clock.now() >= _TRANSITION_QUIESCENCE


def test_wait_settled_signal_restarts_the_window_on_a_new_transition() -> None:
    """A fresh transition arriving mid-wait pushes settlement out further: the debounce is 'no
    further transition for the quiescence window since the LATEST one', not a fixed timer
    started from the first."""
    driver = FakeDriver([el("a", "A")])
    events: list[tuple[ScreenTransition, float]] = [(ScreenTransition(kind="screenChanged"), 0.0)]
    injected = False

    def on_sleep(t: float) -> None:
        nonlocal injected
        if not injected and t >= _TRANSITION_QUIESCENCE / 2:
            events.append((ScreenTransition(kind="screenChanged"), t))
            injected = True

    clock = FakeClock(on_sleep)
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, _tree = _wait(driver, w, clock, transitions=lambda: events)
    assert ok and reason == ""
    assert injected  # the mid-wait injection actually happened
    # Settled only after quiescence elapsed since the SECOND (later) transition.
    assert clock.now() >= events[-1][1] + _TRANSITION_QUIESCENCE


def test_wait_settled_signal_hits_the_deadline_while_still_awaiting_quiescence() -> None:
    """A transition that keeps arriving (quiescence never elapses) must not hang the wait: it
    proceeds best-effort once the step's own deadline passes, exactly like the tree-diff
    fallback's own timeout behavior — the deadline still bounds the signal path."""
    driver = FakeDriver([el("a", "A")])
    clock = FakeClock()

    def transitions() -> list[tuple[ScreenTransition, float]]:
        # A transition "just observed" on every call: the quiescence window never elapses.
        return [(ScreenTransition(kind="screenChanged"), clock.now())]

    w = Wait.model_validate(
        {"until": "settled", "timeout": 0.1}
    )  # shorter than the quiescence window
    ok, reason, _tree = _wait(driver, w, clock, transitions=transitions)
    assert ok and reason == ""  # best-effort: proceeds, never fails the step
    assert clock.now() >= 0.1  # gave up at the deadline, not before


def test_wait_settled_falls_back_to_tree_diff_when_no_transitions_reported() -> None:
    """No signal reported (the app doesn't link BajutsuKit, or hasn't transitioned yet): the wait
    keeps its original two-consecutive-unchanged-reads behavior, unaffected by BE-0310."""
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
                    "nativeZ": None,
                }
            ]

    clock = FakeClock(on_sleep)
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, _tree = _wait(driver, w, clock, transitions=list)
    assert ok and reason == ""


class _ScriptedScreens(FakeDriver):
    """A FakeDriver whose successive `query()` calls return a scripted sequence of trees (the last
    frame repeats once exhausted) — lets a settle test drive an exact poll-by-poll screen history."""

    def __init__(self, frames: list[list[base.Element]]) -> None:
        super().__init__(frames[0])
        self._frames = frames
        self._i = 0

    def query(self) -> list[base.Element]:
        frame = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return frame


def test_wait_settled_does_not_confirm_on_a_momentary_empty() -> None:
    """A tree that flickers empty mid-settle ("非空→空→非空") must reset the stable-poll counter, so
    `settled` never confirms on the transient empty (or the read right after it). It requires two
    consecutive identical *non-empty* reads, so the value handed back is the settled screen — never
    the blank flicker. Were the empty to count, the wait would confirm a poll too early on a screen
    still mid-transition."""
    a = [el("home", "Home", ["button"])]
    # previous=A, then A (almost stable), then the flicker [] resets, then A A A settles on A.
    driver = _ScriptedScreens([list(a), list(a), [], list(a), list(a), list(a)])
    clock = FakeClock()
    w = Wait.model_validate({"until": "settled", "timeout": 5.0})
    ok, reason, tree = _wait(driver, w, clock, transitions=list)
    assert ok and reason == ""
    assert tree == a  # settled on the non-empty screen, never the momentary empty
    assert any(e["identifier"] for e in tree)


def test_wait_settled_via_run_scenario_threads_the_signal() -> None:
    """End-to-end: `run_scenario`'s `transitions` reaches the settled wait (the plumbing this item
    adds through `_run_steps` / `_run_step_body` / `_wait`). A transition reported "now" — since the
    wait began — takes the signal path; a fresh one on every read never quiesces, so the wait runs to
    its deadline (best-effort) instead of the tree-diff fallback's instant settle on this static
    screen, which is what proves the signal, not the fallback, decided it."""
    driver = FakeDriver([el("home", "Home")])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 2.0}}]}),
        clock=clock,
        transitions=lambda: [(ScreenTransition(kind="screenChanged"), clock.now())],
    )
    assert result.ok and result.steps[0].ok
    assert clock.now() >= 2.0  # ran to the deadline via the signal path, not the tree-diff fallback


def test_wait_skips_sleep_when_query_exceeds_poll_interval() -> None:
    """When query() takes longer than _POLL, additional sleep is skipped."""
    from bajutsu.orchestrator import _POLL

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


def test_run_scenario_writes_wait_diagnostic_on_first_wait_timeout(tmp_path: Path) -> None:
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


def test_no_wait_diagnostic_when_wait_succeeds_or_is_not_a_for_wait(tmp_path: Path) -> None:
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


def test_wait_diagnostic_written_once_after_on_blocked_retry(tmp_path: Path) -> None:
    """When a first wait times out, on_blocked clears the block, and the retry times out too, exactly
    one diagnostic is written — from the retry's own (fresh) trace, not the first attempt's."""
    from bajutsu.evidence import FileSink
    from bajutsu.orchestrator.types import AlertEvent

    sink = FileSink(tmp_path)
    driver = AlertingDriver([el("a", "A")])  # the awaited "never" is absent both times
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
        sink=sink,
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]),
    )
    assert not result.ok
    assert driver.dismissals == 1  # the guard cleared a prompt once, then the wait was retried
    diagnostics = [a for a in result.steps[0].artifacts if a.kind == "waitDiagnostic"]
    assert len(diagnostics) == 1
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_wait_records_trace_on_timeout_for_diagnosis() -> None:
    """BE-0231 Unit 1: a `for` wait that times out fills the supplied WaitTrace so the failure is
    diagnosable — how many polls, when the tree first became non-empty, and how many elements were
    present at the timeout — separating "nothing rendered" from "content rendered, awaited element
    absent"."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import WaitTrace, _wait

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
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import WaitTrace, _wait

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


def test_wait_floor_env_extends_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """BAJUTSU_MIN_WAIT_TIMEOUT raises a wait's ceiling so a slow renderer has time to present,
    without editing the shared scenario (its `timeout: 5` is the same across every backend)."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator import _wait

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


def test_wait_floor_never_shrinks_a_larger_scenario_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor is a minimum, not an override: a scenario asking for more than the floor keeps it."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator import _wait

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "3")
    # Element presents at t=8s: below the 3s floor but within the scenario's own 10s ceiling.
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 10.0})
    clock = _LogicalClock()
    ok, reason, _ = _wait(_slow_render_driver(clock, 8.0), w, clock)
    assert ok
    assert reason == ""


def test_wait_floor_raises_on_malformed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed BAJUTSU_MIN_WAIT_TIMEOUT (e.g. '15s') must raise ValueError immediately,
    not silently fall back to 0 — a silent fallback would quietly disable the floor and
    reintroduce the very timeout flakiness the env var is meant to prevent."""
    import pytest

    from bajutsu.orchestrator.waits import _timeout_floor

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "15s")
    with pytest.raises(ValueError, match="BAJUTSU_MIN_WAIT_TIMEOUT"):
        _timeout_floor()


def test_wait_floor_clamps_a_negative_env_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A negative floor is meaningless as a minimum, so it clamps to 0 rather than raising or, worse,
    shrinking a wait below its scenario timeout — a negative `max()` argument would silently shorten
    every wait's ceiling. Distinct from the malformed case above: '-5' parses as a float, so it is a
    value question (clamp), not a parse error (raise)."""
    from bajutsu.orchestrator.waits import _timeout_floor

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "-5")
    assert _timeout_floor() == 0.0


def test_wait_still_sleeps_when_query_is_fast() -> None:
    """When query() is fast, sleep remains at _POLL as before."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator import _POLL, _wait

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


# --- BE-0269: early system-alert guard intervention during a wait ---


class _CollapsingDriver(AlertingDriver):
    """A driver whose tree is collapsed by a SpringBoard prompt until the guard's native path
    answers it, at which point it reveals `revealed`. Models the SpringBoard-alert failure signature
    that `shows_app_ui` detects: no actionable content while blocked.

    Native-capable, because since BE-0402 the native path is the only one that can clear anything:
    the collapsed tree is what brings the gate's attention, the seeded prompt is what it acts on.
    """

    name = "collapsing"

    def __init__(self, revealed: list[base.Element]) -> None:
        super().__init__(on_dismiss=lambda d: setattr(d, "cleared", True))
        self._revealed = revealed
        self.cleared = False

    def query(self) -> list[base.Element]:
        return self._revealed if self.cleared else []


def test_wait_for_guard_fires_mid_wait_and_records_the_alert() -> None:
    """BE-0269 Units 1+3: a collapsed tree during a `for` wait triggers the guard mid-wait; once it
    clears the block the awaited element is found well before the timeout, and the dismissed alert is
    recorded so the report shows the step only passed on a recovery."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _CollapsingDriver([el("ready", "R")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 30.0})
    ok, reason, tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]), alerts=alerts
    )
    assert ok and reason == ""
    assert tree == [el("ready", "R")]
    assert driver.dismissals == 1  # the guard fired exactly once, mid-wait
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    assert clock.now() < 1.0  # cleared in a few poll intervals, not the full 30s budget


class _CollapsingThenRevealedDriver(AlertingDriver):
    """An `AlertingDriver` that reports an empty (collapsed) tree until its prompt is answered, then
    reveals `revealed` — models a SpringBoard alert covering the app during a mid-wait poll
    (BE-0269), while still supporting `screenshot()`/`tap()` so a full `run_scenario` (not just
    `_wait`) can exercise it. `query()` syncs `self.screen` once cleared, so a later step's `tap()`
    (which resolves against `self.screen`, not `query()`) sees the revealed tree too."""

    def __init__(self, revealed: list[base.Element]) -> None:
        super().__init__(on_dismiss=lambda d: setattr(d, "cleared", True))
        self._revealed = revealed
        self.cleared = False

    def query(self) -> list[base.Element]:
        if not self.cleared:
            return []
        self.screen = list(self._revealed)
        return list(self._revealed)


def test_mid_wait_alert_guard_dismiss_preserves_correct_before_after_evidence(
    tmp_path: Path,
) -> None:
    """The mid-wait alert-guard dismiss must not corrupt the report's evidence (BE-0341
    non-regression): every step's recorded tree is the settled post-dismiss one, so neither the
    waiting step nor the one after it is left describing the collapsed screen the guard fired
    against — which the waiting step still keeps as its own `before.png`."""
    from bajutsu.orchestrator.types import AlertEvent

    ready = el("ready", "R")
    nxt = el("next", "Next", ["button"])
    driver = _CollapsingThenRevealedDriver([ready, nxt])

    run_dir = tmp_path / "run1"
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "t",
                "steps": [
                    {"wait": {"for": {"id": "ready"}, "timeout": 5}},
                    {"tap": {"id": "next"}},
                ],
            }
        ),
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]),
        clock=FakeClock(),
        sink=FileSink(run_dir),
    )
    assert result.ok, result.failure
    assert result.steps[0].alerts == [AlertEvent(label=GUARD_LABEL)]

    def _els(step_index: int) -> list[dict[str, object]]:
        art = next(a for a in result.steps[step_index].artifacts if a.kind == "elements")
        els = json.loads((run_dir / art.name).read_text(encoding="utf-8"))
        assert isinstance(els, list)
        return els

    # step0's (the wait's) tree is its post-action one: the screen the dismissal settled on, never
    # the collapsed one the guard fired against. Its `before.png` still holds that pre-wait moment.
    assert {e["identifier"] for e in _els(0)} == {"ready", "next"}
    assert any(a.name.endswith("before.png") for a in result.steps[0].artifacts)
    # step1's own tree likewise reflects the post-dismiss, settled state.
    assert {e["identifier"] for e in _els(1)} == {"ready", "next"}


def test_wait_guard_debounces_a_transient_collapse() -> None:
    """BE-0269 Unit 2: a single collapsed poll (a transient render frame) must not read as a blocked
    screen — only a short run of consecutive collapsed polls does. Since BE-0402 the cost of getting
    that wrong is a note blaming a block that was never there, so the debounce still guards it."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _AlertGuardGate, _wait

    class OneFrameCollapse(_GuardStub):
        name = "one-frame"

        def __init__(self) -> None:
            super().__init__()
            self.polls = 0

        def query(self) -> list[base.Element]:
            self.polls += 1
            return [] if self.polls == 1 else [el("ready", "R")]

    gate = _AlertGuardGate(
        driver=OneFrameCollapse(), clock=_LogicalClock(), guard=AlertGuardConfig(), alerts=[]
    )
    gate.observe([])  # one transient collapsed frame, below the debounce threshold
    assert gate.blocked_note == ""

    ok, reason, _tree = _wait(
        OneFrameCollapse(),
        Wait.model_validate({"for": {"id": "ready"}, "timeout": 30.0}),
        _LogicalClock(),
        alert_guard=AlertGuardConfig(),
        alerts=[],
    )
    assert ok and reason == ""


def test_wait_guard_reports_a_persistent_collapse_it_cannot_clear() -> None:
    """BE-0402: on a backend with no native path, a persistently collapsed screen is not something
    the guard will act on — it neither guesses nor calls a model. What it does instead is refuse to
    fail silently: the wait times out on schedule and says the screen looked blocked, hedged, because
    the collapsed tree is a correlation and no query ever named a button behind it."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    class NeverClears(_GuardStub):
        name = "stuck"

        def query(self) -> list[base.Element]:
            return []  # collapsed forever, and nothing on this backend can clear it

    driver = NeverClears()
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "never"}, "timeout": 30.0})
    ok, reason, _tree = _wait(driver, w, clock, alert_guard=AlertGuardConfig(), alerts=[])
    assert not ok
    assert reason.startswith("wait timeout: for")
    assert "the screen appears blocked" in reason
    assert "buttons:" not in reason  # nothing enumerated it, so nothing is named
    assert driver.actions == []  # and the guard actuated nothing on the way there


def test_wait_guard_never_fires_while_app_ui_is_visible() -> None:
    """BE-0269 Unit 1: the deterministic pre-check (`shows_app_ui`) — not a blind timer — is the
    trigger, so a wait whose tree always shows app content never asks the guard to look."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    class AppVisible(_GuardStub):
        name = "app"

        def __init__(self) -> None:
            super().__init__()
            self.polls = 0

        def query(self) -> list[base.Element]:
            self.polls += 1
            return [el("row", "Row")] if self.polls >= 5 else [el("other", "Other")]

    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "row"}, "timeout": 30.0})
    ok, reason, _tree = _wait(AppVisible(), w, clock, alert_guard=AlertGuardConfig(), alerts=[])
    assert ok and reason == ""


def test_wait_settled_guard_fires_on_a_collapsed_screen() -> None:
    """BE-0269 Unit 3: `settled` never treats a collapsed tree as settled, so an alert would burn the
    whole timeout; the guard now fires mid-settle to clear it, then the screen settles normally."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _CollapsingDriver([el("home", "Home")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]), alerts=alerts
    )
    assert ok  # a settle never fails the step
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    assert tree == [el("home", "Home")]
    assert clock.now() < 2.0  # cleared and settled quickly, not the full 30s


def test_wait_settled_signal_guard_fires_on_a_collapsed_screen() -> None:
    """BE-0269's mid-wait alert guard still fires in the signal-based settle path (BE-0310), not
    only the tree-diff fallback above: a collapsed screen during the quiescence wait is cleared
    instead of silently waiting out the whole window collapsed."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _CollapsingDriver([el("home", "Home")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, tree = _wait(
        driver,
        w,
        clock,
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]),
        alerts=alerts,
        transitions=lambda: fresh,
    )
    assert ok  # a settle never fails the step
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    assert tree == [el("home", "Home")]  # revealed once the guard cleared it
    assert clock.now() < 2.0  # cleared well inside the signal path's own quiescence window


class _NoNativeFake(FakeDriver):
    """A FakeDriver without the native HANDLE_SYSTEM_ALERT capability, so the guard exercises the
    collapsed-tree branch end to end (FakeDriver otherwise advertises it, BE-0316)."""

    def capabilities(self) -> set[str]:
        return super().capabilities() - {base.Capability.HANDLE_SYSTEM_ALERT}


def test_run_scenario_reports_a_block_it_cannot_clear_on_the_step() -> None:
    """BE-0402 end to end: on a backend with no native path, a `for` wait blocked by something
    outside the app's view fails — as it would with no guard configured — but the step's own reason
    names what the guard saw, so a human reading a red run is not left with a bare missing element.
    """
    driver = _NoNativeFake([])  # collapsed under something the app's tree cannot see

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 30.0}}]}),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(),
    )
    assert not result.ok
    reason = result.steps[0].reason or ""
    assert "wait timeout: for" in reason
    assert "the screen appears blocked" in reason
    assert result.steps[0].alerts == []  # nothing was dismissed, and nothing claims otherwise


def test_wait_screen_changed_guard_fires_when_started_under_an_alert() -> None:
    """BE-0269 Unit 3: a `screenChanged` wait that begins with the screen already collapsed by a
    system alert would never observe a change; the guard clears it mid-wait — which itself changes
    the screen — so the wait completes instead of burning the whole timeout."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _CollapsingDriver([el("home", "Home")])  # the `before` snapshot is the collapsed tree
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "screenChanged", "timeout": 30.0})
    ok, reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]), alerts=alerts
    )
    assert ok and reason == ""
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    assert clock.now() < 1.0


class _LateAlertDriver(AlertingDriver):
    """A driver whose SpringBoard prompt surfaces on the *second* native probe, not the first.

    The shape a wait's own loop has to catch: an alert already up when the wait begins is answered
    from the `before` snapshot, so only a prompt that arrives afterwards exercises the `gate.observe`
    inside each polling loop.
    """

    name = "late"
    polls = 0

    def system_alert_labels(self) -> list[str]:
        self.polls += 1
        return super().system_alert_labels() if self.polls > 1 else []


def test_wait_screen_changed_guard_fires_on_a_prompt_that_arrives_mid_wait() -> None:
    """The `screenChanged` branch is guarded on every poll, not only on its `before` snapshot.

    The companion to the test above, where the alert is already up when the wait begins. A prompt
    that surfaces *after* the wait started is the more common shape (an action opens it), and it
    would otherwise sit unanswered for the whole timeout: the screen it froze never changes, so the
    condition stays unmet, and only a poll inside the loop can see it.
    """
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _LateAlertDriver([el("home", "Home")], on_dismiss=lambda d: setattr(d, "screen", []))
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "screenChanged", "timeout": 30.0})
    ok, reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]), alerts=alerts
    )
    assert ok and reason == ""  # answering the prompt is itself the screen change
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    # One native poll interval's worth of waiting, not the 30s budget: the guard answered the prompt
    # on the first poll that could see it.
    assert clock.now() < 2.0


class _UnsettledUntilAnswered(_LateAlertDriver):
    """A screen that keeps changing while its prompt is up, and settles once the guard answers it.

    A settle loop exits the moment two consecutive polls match, so a static screen never reaches its
    own loop body. This is the case the settle guard exists for: an animation the prompt is holding
    open, which finishes only once the prompt goes away.
    """

    def __init__(self) -> None:
        super().__init__([el("home", "Home")])
        self.frames = 0

    def query(self) -> list[base.Element]:
        if self.dismissals:
            return [el("home", "Home")]
        self.frames += 1
        return [el("home", "Home"), el(f"spinner{self.frames}", "…")]


def test_wait_settled_guard_fires_on_a_prompt_that_arrives_mid_settle() -> None:
    """The tree-diff settle loop observes every poll too, not only its first read."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _UnsettledUntilAnswered()
    alerts: list[AlertEvent] = []
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, _tree = _wait(
        driver,
        w,
        _LogicalClock(),
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]),
        alerts=alerts,
    )
    assert ok  # a settle never fails the step
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]


def test_wait_settled_signal_guard_fires_on_a_prompt_that_arrives_mid_settle() -> None:
    """And so does the signal-based settle path (BE-0310), inside its quiescence window."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.types import AlertEvent
    from bajutsu.orchestrator.waits import _wait

    driver = _UnsettledUntilAnswered()
    alerts: list[AlertEvent] = []
    ticks: list[float] = []
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, _tree = _wait(
        driver,
        w,
        _LogicalClock(),
        on_tick=lambda _remaining: ticks.append(0.0),
        # Below the quiescence window, so the native probe gets a second turn inside it — the
        # default one-second cadence would put every probe after the settle had already returned.
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL], poll_interval=0.05),
        alerts=alerts,
        transitions=lambda: fresh,
    )
    assert ok
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]
    assert ticks  # the heartbeat keeps reporting while the settle waits out the prompt


def test_wait_guard_does_not_extend_the_deadline() -> None:
    """BE-0269 Unit 3: the guard fires within the original timeout budget and never resets the
    deadline — if the awaited element would only appear long after the deadline, the wait still
    times out on schedule rather than being kept alive by the intervention."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    class SlowReveal(AlertingDriver):
        name = "slow"

        def __init__(self, clock: _LogicalClock) -> None:
            super().__init__()
            self._clock = clock

        def query(self) -> list[base.Element]:
            return [el("ready", "R")] if self._clock.now() >= 10.0 else []

    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 1.0})
    # The guard dismisses its prompt at once, but the element is still 10s out.
    ok, reason, _tree = _wait(
        SlowReveal(clock), w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]), alerts=[]
    )
    assert not ok
    assert "timeout" in reason
    assert clock.now() < 2.0  # honored the 1s budget; the guard did not push the deadline to 10s


def test_wait_guard_fires_without_an_alerts_list() -> None:
    """BE-0269: a guarded wait called with no `alerts` list (a direct `_wait` call) still fires the
    guard and recovers — the recording is simply dropped, never a crash on a None list."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    driver = _CollapsingDriver([el("ready", "R")])
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 30.0})
    ok, _reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(labels=[GUARD_LABEL])
    )  # no alerts list
    assert ok
    assert driver.dismissals == 1


# --- live "what am I waiting for" progress ---


def test_describe_wait_renders_each_condition() -> None:
    """describe_wait renders every wait shape in the live-progress wording — selectors as
    `key=value`, which differs from `_wait`'s timeout reason (a raw selector dict)."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import describe_wait

    def desc(data: dict[str, object]) -> str:
        return describe_wait(Wait.model_validate(data))

    assert desc({"for": {"id": "home.title"}, "timeout": 1.0}) == "for id='home.title'"
    assert desc({"until": {"gone": {"id": "spinner"}}, "timeout": 1.0}) == "until gone id='spinner'"
    assert desc({"until": "settled", "timeout": 1.0}) == "until settled"
    assert desc({"until": "screenChanged", "timeout": 1.0}) == "until screenChanged"
    assert (
        desc({"until": {"request": {"method": "GET", "path": "/login"}}, "timeout": 1.0})
        == "until request GET /login"
    )


def test_wait_tick_fires_once_even_when_immediately_satisfied() -> None:
    """A wait that resolves on its first poll must still surface its condition once, so the common
    fast case is not invisible: the entry tick fires before the first condition check."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    driver = FakeDriver([el("ready", "R")])  # target already present
    seen: list[float] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 5.0})
    ok, _reason, _tree = _wait(driver, w, clock, on_tick=seen.append)
    assert ok
    assert len(seen) == 1  # only the entry tick — no polling happened
    assert seen[0] == 5.0  # remaining == the full timeout at entry


def test_wait_ticks_count_down_across_a_long_wait() -> None:
    """While a wait is pending, ticks keep arriving (throttled, ~5s apart) with a shrinking
    remaining budget, so the run log shows the wait is still blocked and on what."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    clock = _LogicalClock()
    seen: list[float] = []
    w = Wait.model_validate({"for": {"id": "target"}, "timeout": 30.0})
    # Reveal the target only after 17 logical seconds, so the wait stays pending across several
    # 5s-throttled ticks (entry at 30.0, then ~25/20/15 left).
    ok, _reason, _tree = _wait(_slow_render_driver(clock, 17.0), w, clock, on_tick=seen.append)
    assert ok
    # Entry tick (30.0) plus ~one per 5s while pending. The upper bound is the real guard: a lost
    # _TICK_INTERVAL gate would emit on every ~50ms poll and balloon `seen` to dozens of entries.
    assert 3 <= len(seen) <= 6
    assert seen[0] == 30.0
    assert seen == sorted(seen, reverse=True)  # remaining only ever decreases
    assert all(r >= 0.0 for r in seen)


def test_wait_ticks_fire_for_every_non_for_branch() -> None:
    """The heartbeat must stream from `settled` / `gone` / `request` / `screenChanged` too, not only
    `for`: each branch keeps polling a never-satisfied condition to its deadline, so the entry tick
    plus in-loop ticks fire. Guards against a branch silently dropping `hb.tick`."""
    from bajutsu.common.scenario import Wait
    from bajutsu.orchestrator.waits import _wait

    class Churning(FakeDriver):  # a new tree every poll -> never settles -> loops to deadline
        name = "churning"

        def __init__(self) -> None:
            super().__init__([])
            self._n = 0

        def query(self) -> list[base.Element]:
            self._n += 1
            return [el(f"row{self._n}", "R")]

    class Static(
        FakeDriver
    ):  # a constant tree: `gone` never vanishes, `screenChanged` never differs
        name = "static"

        def __init__(self) -> None:
            super().__init__([])

        def query(self) -> list[base.Element]:
            return [el("spinner", "S")]

    def ticks(w: Wait, driver: base.Driver, network: object = None) -> list[float]:
        clock = _LogicalClock()
        seen: list[float] = []
        if network is None:
            _wait(driver, w, clock, on_tick=seen.append)
        else:
            _wait(driver, w, clock, network=network, on_tick=seen.append)  # type: ignore[arg-type]
        return seen

    cases = {
        "settled": ticks(Wait.model_validate({"until": "settled", "timeout": 20.0}), Churning()),
        "gone": ticks(
            Wait.model_validate({"until": {"gone": {"id": "spinner"}}, "timeout": 20.0}), Static()
        ),
        "screenChanged": ticks(
            Wait.model_validate({"until": "screenChanged", "timeout": 20.0}), Static()
        ),
        "request": ticks(
            Wait.model_validate({"until": {"request": {"path": "/never"}}, "timeout": 20.0}),
            Static(),
            network=list,  # a no-op network source: always zero observed exchanges
        ),
    }
    for label, seen in cases.items():
        # Upper bound guards the throttle: a lost _TICK_INTERVAL gate would emit per ~50ms poll.
        # A 20s timeout with a 5s cadence yields the entry tick plus ~three in-loop ticks.
        assert 2 <= len(seen) <= 8, f"{label}: expected entry + throttled in-loop ticks, got {seen}"
        assert seen[0] == 20.0, f"{label}: entry tick should report the full timeout"
        assert seen == sorted(seen, reverse=True), (
            f"{label}: remaining must only decrease, got {seen}"
        )


def test_run_scenario_streams_what_the_wait_awaits() -> None:
    """End to end: with progress wired, a pending wait streams a `waiting <condition>` line naming
    the awaited selector, not a bare `wait`."""
    driver = FakeDriver([el("a", "A")])  # the awaited row never appears -> the wait times out
    lines: list[str] = []
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 0.3}}]}),
        clock=FakeClock(),
        progress=lines.append,
    )
    assert not result.ok
    assert any("waiting for id='ready'" in ln for ln in lines)


# --- if / forEach control flow ---

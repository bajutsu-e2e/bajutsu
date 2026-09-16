"""Tests for the orchestrator condition waits (wait for/until, screenChanged, settled)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _orch import FakeClock, _scenario
from conftest import GUARD_LABEL, AlertingDriver, el, guard_rule

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import FileSink
from bajutsu.common.evidence.network import ScreenTransition
from bajutsu.common.orchestrator import AlertGuardConfig, _wait, run_scenario
from bajutsu.common.orchestrator.waits import _TRANSITION_QUIESCENCE
from bajutsu.common.scenario import Wait


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


def test_wait_settled_signal_reads_the_device_only_once_with_no_gate_or_interrupt() -> None:
    """With neither a system-alert guard nor a scenario `interrupts` handler, nothing consumes a
    mid-window tree, so the signal path must not poll the device on every tick while it waits out
    the quiescence window (BE-0407 Unit 5) — it queries once, up front (`_wait_settled`'s own
    pre-check) and once more for the settled tree it hands back, regardless of how long the window
    is relative to `_POLL`."""

    class _CountingDriver(FakeDriver):
        def __init__(self, screen: list[base.Element]) -> None:
            super().__init__(screen)
            self.queries = 0

        def query(self) -> list[base.Element]:
            self.queries += 1
            return super().query()

    screen = [el("a", "A")]
    driver = _CountingDriver(screen)
    clock = FakeClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, tree = _wait(driver, w, clock, transitions=lambda: fresh)
    assert ok and reason == ""
    assert clock.now() >= _TRANSITION_QUIESCENCE
    # Without this fix, a 0.3s quiescence window polled every 0.05s would cost ~6 extra reads.
    assert driver.queries == 2
    # The lazy final query (`settled_tree()`) must still hand back a real tree, not an accidental
    # `[]` or a stale one from before the window closed.
    assert tree == screen


def test_wait_settled_signal_interrupt_ends_the_settle_with_no_gate_registered() -> None:
    """An `on_interrupt_poll` alone (no system-alert guard) must still be consulted on the signal
    path (BE-0314) — and BE-0407 Unit 5's `watched` gate must cover that case too, not just a
    guard's."""
    driver = FakeDriver([el("a", "A")])
    clock = FakeClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})
    ok, reason, _tree = _wait(
        driver, w, clock, transitions=lambda: fresh, on_interrupt_poll=lambda _els: True
    )
    assert not ok and reason == "interrupt recovery failed"


def test_wait_settled_signal_polls_the_device_each_tick_for_an_interrupt_with_no_gate() -> None:
    """The same interrupt-only (no gate) signal wait, but an overlay that appears only on a later
    poll: proves the per-tick re-query actually happens, not just that `on_interrupt_poll` gets
    called at all with a tree fixed at entry. A mutant that stops re-querying `current` inside the
    loop would keep feeding the overlay-free entry tree forever and never notice."""
    base_screen = [el("a", "A")]
    overlay_screen = [el("a", "A"), el("overlay", "Overlay")]
    # frame 0: `_wait_settled`'s own pre-check query. frame 1: the signal path's entry query (still
    # no overlay). frame 2+: per-tick queries inside the settle loop — the overlay finally appears.
    driver = _ScriptedScreens([base_screen, base_screen, overlay_screen])
    clock = FakeClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 2.0})

    def on_interrupt_poll(els: list[base.Element]) -> bool:
        return any(e["identifier"] == "overlay" for e in els)

    ok, reason, _tree = _wait(
        driver, w, clock, transitions=lambda: fresh, on_interrupt_poll=on_interrupt_poll
    )
    assert not ok and reason == "interrupt recovery failed"


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
    screen = [el("a", "A")]
    driver = FakeDriver(screen)
    clock = FakeClock()

    def transitions() -> list[tuple[ScreenTransition, float]]:
        # A transition "just observed" on every call: the quiescence window never elapses.
        return [(ScreenTransition(kind="screenChanged"), clock.now())]

    w = Wait.model_validate(
        {"until": "settled", "timeout": 0.1}
    )  # shorter than the quiescence window
    ok, reason, tree = _wait(driver, w, clock, transitions=transitions)
    assert ok and reason == ""  # best-effort: proceeds, never fails the step
    assert clock.now() >= 0.1  # gave up at the deadline, not before
    # `settled_tree()`'s deadline exit must still hand back a real tree, queried fresh since no
    # gate/interrupt kept `current` warm on this branch.
    assert tree == screen


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
    from bajutsu.common.orchestrator import _POLL

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

    from bajutsu.common.evidence import FileSink
    from bajutsu.common.platform_lifecycle import ReadinessResult

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
    from bajutsu.common.evidence import FileSink

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
    from bajutsu.common.evidence import FileSink
    from bajutsu.common.orchestrator.types import AlertEvent

    sink = FileSink(tmp_path)
    driver = AlertingDriver([el("a", "A")])  # the awaited "never" is absent both times
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
        sink=sink,
        alert_guard=AlertGuardConfig(rules=[guard_rule()]),
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
    from bajutsu.common.orchestrator.waits import WaitTrace, _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.waits import WaitTrace, _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator import _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator import _wait
    from bajutsu.common.scenario import Wait

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

    from bajutsu.common.orchestrator.waits import _timeout_floor

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "15s")
    with pytest.raises(ValueError, match="BAJUTSU_MIN_WAIT_TIMEOUT"):
        _timeout_floor()


def test_wait_floor_clamps_a_negative_env_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A negative floor is meaningless as a minimum, so it clamps to 0 rather than raising or, worse,
    shrinking a wait below its scenario timeout — a negative `max()` argument would silently shorten
    every wait's ceiling. Distinct from the malformed case above: '-5' parses as a float, so it is a
    value question (clamp), not a parse error (raise)."""
    from bajutsu.common.orchestrator.waits import _timeout_floor

    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "-5")
    assert _timeout_floor() == 0.0


def test_wait_still_sleeps_when_query_is_fast() -> None:
    """When query() is fast, sleep remains at _POLL as before."""
    from bajutsu.common.orchestrator import _POLL, _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _CollapsingDriver([el("ready", "R")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 30.0})
    ok, reason, tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()]), alerts=alerts
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
    from bajutsu.common.orchestrator.types import AlertEvent

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
        alert_guard=AlertGuardConfig(rules=[guard_rule()]),
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
    from bajutsu.common.orchestrator.waits import _AlertGuardGate, _wait
    from bajutsu.common.scenario import Wait

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


def test_wait_guard_asserts_probe_native_never_reports_already_dismissed() -> None:
    # BE-0418 added a sixth `NativeAlertState` member, but this poll never passes `dismissed` --
    # unlike `AlertGuardConfig.__call__`'s own round loop -- so `_resolve_alert_rule`'s subset-based
    # retry (the only path that can return `None`) never runs, and `probe_native` can never actually
    # report "already_dismissed" here. The rest of `_observe_native` still tests the five states
    # that predate it by equality, not a `match` or `assert_never`, so a state reaching here it does
    # not recognize would otherwise read silently as "nothing is blocking" (review finding) --
    # asserted instead, so a future change that starts threading real `dismissed` state through this
    # poll fails loudly the moment it does, rather than corrupting `blocked_note` silently.
    from bajutsu.common.orchestrator.types import AlertEvent, NativeAlertState
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _AlwaysAlreadyDismissed(AlertGuardConfig):
        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            return "already_dismissed", None, []

    gate = _AlertGuardGate(
        driver=FakeDriver([]), clock=_LogicalClock(), guard=_AlwaysAlreadyDismissed(), alerts=[]
    )
    with pytest.raises(AssertionError):
        gate.observe([])


def test_wait_guard_matches_in_tree_shapes_the_same_way_dismiss_from_tree_once_does() -> None:
    # The mid-wait gate's own `_dismiss_from_tree` and the one-shot `dismiss_from_tree_once` are
    # declared twins over the same screen (BE-0418 review finding): both must resolve two
    # differently-shaped in-tree rules the same way, or which button a scenario gets would depend
    # on whether a `wait` happened to be running when the sheet appeared. `narrow` is declared
    # first -- the plain, declaration-order match dismiss_from_tree_once no longer makes -- but
    # `wide`'s own shape is also fully present, so widest-first still picks `wide` here too.
    from bajutsu.common.orchestrator.types import AlertEvent, ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    narrow = ResolvedAlertRule(
        identifying_labels=frozenset({"Save"}), tap_label="Save", native=False, in_tree=True
    )
    wide = ResolvedAlertRule(
        identifying_labels=frozenset({"Save", "Not Now"}),
        tap_label="Not Now",
        native=False,
        in_tree=True,
    )
    guard = AlertGuardConfig(rules=[narrow, wide])
    driver = FakeDriver([el(None, "Save", ["button"]), el(None, "Not Now", ["button"])])
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe(driver.query())
    assert gate.alerts == [AlertEvent(label="Not Now")]


def test_wait_guard_never_taps_the_tree_while_a_native_alert_races() -> None:
    # Twin of `AlertGuardConfig.__call__`'s own `if not buttons` gate (BE-0418 review finding):
    # `probe_native`'s time-of-check/time-of-use race answers "absent" over a *non-empty* button
    # read, and this poll reaches the tree through the same `Driver.tap` that call reasons about --
    # so licensing the tap on `state == "absent"` alone risks it landing under a live SpringBoard
    # alert, which XCUITest answers with its own default button before synthesizing the interaction
    # (BE-0399). Before the fix, `probed_absent` ignored the non-empty `buttons` this probe reports
    # and tapped the tree sheet anyway.
    from bajutsu.common.orchestrator.types import AlertEvent, NativeAlertState, ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAway(AlertGuardConfig):
        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            return "absent", None, ["Allow", "Don't Allow"]

    tree_rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Save"}), tap_label="Save", native=False, in_tree=True
    )
    driver = FakeDriver([el(None, "Save", ["button"])])
    guard = _RacesAway(rules=[tree_rule])
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe(driver.query())
    assert gate.alerts == []
    assert not any(action[0] == "tap" for action in driver.actions)


def test_wait_guard_names_an_ambiguous_matched_alert_uncleared_not_unhandled() -> None:
    # `probe_native` reaches "unhandled" two ways: a genuinely unidentified alert, and a matched
    # rule whose tap found the label twice (`AmbiguousSelector`, "the other half of that race").
    # This poll used to treat both as "no rule identifies it", telling the author no rule named
    # their prompt when one did -- exactly what `uncleared_prompt_note`'s docstring says must not
    # happen (BE-0418 review finding). Re-resolving with `matching_alert_rule` tells them apart.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule, uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _AmbiguousEveryPoll(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.AmbiguousSelector("the alert offers this label twice")

    driver = _AmbiguousEveryPoll([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            )
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert gate.blocked_note == uncleared_prompt_note("Allow")


def test_wait_guard_reports_a_co_present_alert_no_rule_identifies_on_an_ambiguous_match() -> None:
    # `AmbiguousSelector` fires only after a rule has already matched, and `buttons` here is the
    # whole SpringBoard enumeration, not that rule's own shape -- so a co-present button no rule
    # identifies can sit alongside it on the very same read. This branch used to report only the
    # ambiguous rule's own diagnosis (`uncleared_prompt_note`), silently dropping "Weird Button"
    # for a whole `poll_interval` -- the exact BE-0402 disclosure this branch exists to make, and
    # the same shape the `raced` branch just below already handles via `identified_alert_rules`
    # (BE-0418 review finding).
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _AmbiguousEveryPoll(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.AmbiguousSelector("the alert offers this label twice")

    driver = _AmbiguousEveryPoll([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
        el(None, "Weird Button", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            )
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert "Weird Button" in gate.blocked_note
    assert "Allow" not in gate.blocked_note and "Don't Allow" not in gate.blocked_note


def test_wait_guard_keeps_an_unhandled_note_when_a_matched_alert_races() -> None:
    # Twin of the `AlertGuardConfig.__call__` TOCTOU fix, for this poll's own note-clear
    # (BE-0418 review finding): `"absent"` carries two meanings here too -- a genuinely empty
    # enumeration, and a matched rule's own tap racing away over a *non-empty* read that says
    # nothing about a *different* button the same read enumerated. Poll 1 finds only a button no
    # rule identifies ("Weird Button") and names it; poll 2's own matched rule ("OK"/"Cancel")
    # races away, but "Weird Button" is still right there in that very same read -- the note must
    # survive, not be wiped by a race that never proved the surface clear. Poll 2's own element list
    # is a non-empty app tree, not `[]`: an empty list is the one input where the collapsed-tree
    # proxy's own `shows_app_ui` is false *and* its debounce has not yet fired, so it is the one
    # input that cannot expose the proxy silently erasing or overwriting the note on its own, past
    # the earlier `raced` guard (BE-0418 review finding) -- a real device's app tree stays visible
    # under an out-of-process SpringBoard alert (`shows_app_ui` is true), which is exactly what the
    # native probe exists to see past. Poll 3 -- no `clock.sleep` before it, so `poll_interval`
    # has not elapsed and the native probe does not run again -- pins the same guarantee one tick
    # further out: `_native_unhandled` is the only thing standing between this tick and the proxy
    # until the next native probe is due, so a race must not drop that latch either, only the
    # explicit clear a few lines above it (BE-0418 review finding).
    from bajutsu.common.orchestrator.types import AlertEvent, NativeAlertState, ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _MatchedAlertRacesPastAnUnhandledOne(AlertGuardConfig):
        polls: int = 0

        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            self.polls += 1
            if self.polls == 1:
                return "unhandled", None, ["Weird Button"]
            return "absent", None, ["OK", "Cancel", "Weird Button"]

    # A rule for the raced shape makes the branch's own subtraction real: without one,
    # `matching_alert_rule` finds nothing to subtract and the whole read -- "OK" and "Cancel"
    # included -- becomes "leftover", so the note under test would be a freshly re-derived
    # superset rather than the preserved one this test means to pin (BE-0418 review finding).
    guard = _MatchedAlertRacesPastAnUnhandledOne(
        rules=[ResolvedAlertRule(identifying_labels=frozenset({"OK", "Cancel"}), tap_label="OK")]
    )
    driver = FakeDriver([])
    clock = _LogicalClock()
    gate = _AlertGuardGate(driver=driver, clock=clock, guard=guard, alerts=[])
    gate.observe([])
    assert "Weird Button" in gate.blocked_note
    clock.sleep(guard.poll_interval)
    gate.observe([el("home", "Home", ["button"])])
    assert "Weird Button" in gate.blocked_note
    assert "OK" not in gate.blocked_note and "Cancel" not in gate.blocked_note
    gate.observe([el("home", "Home", ["button"])])  # same poll_interval window: no native re-probe
    assert "Weird Button" in gate.blocked_note


def test_wait_guard_reports_a_co_present_alert_no_rule_identifies_on_a_race() -> None:
    # Preserving an existing note is not the same as producing one (BE-0418 review finding): the
    # `raced` branch above stops this poll from clearing or overwriting `blocked_note`, but the very
    # first poll has no earlier note to preserve. A co-present button no rule identifies, enumerated
    # by the very same read as the rule that raced away, must still be reported -- mirroring
    # `AlertGuardConfig.__call__`'s own race branch, which subtracts only the raced rule's own
    # labels from the read rather than the whole surface.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAway([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
        el(None, "Weird Button", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            )
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert "Weird Button" in gate.blocked_note
    assert "Allow" not in gate.blocked_note and "Don't Allow" not in gate.blocked_note


def test_wait_guard_does_not_call_a_co_present_declared_prompt_unhandled_on_a_race() -> None:
    # The leftover computation above must subtract every rule a *declared* shape identifies on this
    # read, not only the one that raced: `matching_alert_rule` returns just its own first match, so
    # a second, different declared prompt stacked alongside the raced one would otherwise survive
    # into `leftover` and be reported as an alert no rule identifies -- the exact misdiagnosis
    # `uncleared_prompt_note`'s docstring says must not happen, since a rule does identify it and the
    # very next native probe would dismiss it (BE-0418 review finding). "notifications" races away;
    # "paste" is a second, disjoint prompt fully present on the very same read.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAway([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
        el(None, "Allow Paste", ["button"]),
        el(None, "Don't Allow Paste", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow Paste", "Don't Allow Paste"}),
                tap_label="Allow Paste",
            ),
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert gate.blocked_note == ""


def test_wait_guard_keeps_the_collapsed_tree_proxys_hedged_note_through_a_leftover_free_race() -> (
    None
):
    # The one case the `raced` branch's own clear-guard (`not raced`, alongside `"unhandled"` and
    # `_tree_gave_up`) protects that no existing test reaches: a race whose own read leaves nothing
    # over (`leftover` empty) and that was not already latched `_native_unhandled`. Neither the
    # `if leftover:` branch nor the `elif self._native_unhandled:` branch below fires then, so the
    # only thing standing between this poll and an erased note is the `not raced` conjunct of
    # `_observe_native`'s own clear-guard (`if state != "unhandled" and not raced and not
    # self._tree_gave_up`) declining to clear it in the first place -- the collapsed-tree proxy's
    # hedged `alert_block_note([])`, for a non-SpringBoard surface the native query cannot
    # enumerate, must survive a race that says nothing about whether *that* surface cleared
    # (BE-0418 review finding).
    from bajutsu.common.orchestrator.types import ResolvedAlertRule, alert_block_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _CollapsedThenRacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _CollapsedThenRacesAway([])
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            )
        ]
    )
    clock = _LogicalClock()
    gate = _AlertGuardGate(driver=driver, clock=clock, guard=guard, alerts=[])
    # Three collapsed polls (no SpringBoard alert, no app UI either) debounce into the proxy's own
    # hedged note -- the native probe answers "absent" over an empty read each time, so none of
    # these polls touch the `raced` branch under test.
    for _ in range(3):
        gate.observe([])
    assert gate.blocked_note == alert_block_note([])
    # A fresh native probe (the clock has moved a full `poll_interval`) now races over a read that
    # is nothing but the declared rule's own shape -- `leftover` is empty and `_native_unhandled` is
    # still False, so this is the one case only the `not raced` conjunct protects.
    clock.sleep(guard.poll_interval)
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
    ]
    gate.observe([])
    assert gate.blocked_note == alert_block_note([])


def test_wait_guard_reports_nothing_when_a_race_leaves_no_leftover() -> None:
    # The other half of the fresh-diagnosis fix above: a race whose own read holds nothing beyond
    # the raced rule's own shape has no leftover to report, so this poll must not manufacture a note
    # out of the very buttons it just subtracted.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAway([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            )
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert gate.blocked_note == ""


def test_wait_guard_does_not_double_count_a_label_two_declared_rules_share() -> None:
    # Coverage for the multiplicity loop's own "already removed" path: two declared rules can share
    # one label (the built-in `notifications` and `tracking` both grant "Allow"), so once the first
    # shape's own processing consumes that occurrence, the second shape's identical label is already
    # gone from `leftover` -- not a distinct button to remove a second time.
    from bajutsu.common.orchestrator.types import AlertEvent, NativeAlertState, ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesWithTwoRulesSharingAllow(AlertGuardConfig):
        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            return "absent", None, ["Allow", "Don't Allow", "Ask App Not to Track"]

    guard = _RacesWithTwoRulesSharingAllow(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Ask App Not to Track"}), tap_label="Allow"
            ),
        ]
    )
    driver = FakeDriver([])
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert gate.blocked_note == ""


def test_wait_guard_does_not_subtract_a_native_rule_an_excluded_label_rules_out() -> None:
    # The second half of the same fix: a rule `matching_alert_rule` would refuse (an excluded label
    # is present) must not have its labels subtracted here either -- the exact reason
    # `_native_round_worth_another_try` grew its own `excluded_labels` check (BE-0418 review
    # finding). Stubbed the same way, for the same reason.
    from bajutsu.common.orchestrator.types import AlertEvent, NativeAlertState, ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesWithAnExcludedRulePresent(AlertGuardConfig):
        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            return "absent", None, ["Save", "Not Now", "Never for This Card"]

    guard = _RacesWithAnExcludedRulePresent(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Save", "Not Now"}),
                tap_label="Save",
                excluded_labels=frozenset({"Never for This Card"}),
            )
        ]
    )
    driver = FakeDriver([])
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    assert "Save" in gate.blocked_note
    assert "Not Now" in gate.blocked_note
    assert "Never for This Card" in gate.blocked_note


def test_wait_guard_clears_a_stale_unhandled_note_when_a_leftover_free_race_disproves_it() -> None:
    # `buttons` here is the whole SpringBoard enumeration, not one alert's own set: a race whose
    # own read holds nothing but the raced rule's own shape is positive evidence that any *other*
    # button an earlier probe named is gone (BE-0418 review finding). Poll 1 names "Weird Button"
    # as unhandled; poll 2's own matched rule ("Allow"/"Don't Allow") races away, but this read no
    # longer holds "Weird Button" at all -- the stale note must clear, not survive on the strength
    # of the earlier `raced` preservation guard, which exists for evidence the current read does
    # *not* disprove.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAwayOnNotifications(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAwayOnNotifications([])
    driver.system_alert_buttons = [el(None, "Weird Button", ["button"])]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            )
        ]
    )
    clock = _LogicalClock()
    gate = _AlertGuardGate(driver=driver, clock=clock, guard=guard, alerts=[])
    gate.observe([])
    assert "Weird Button" in gate.blocked_note
    clock.sleep(guard.poll_interval)
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
    ]
    gate.observe([el("home", "Home", ["button"])])
    assert gate.blocked_note == ""


def test_wait_guard_keeps_an_in_tree_give_up_note_through_a_race_with_a_leftover() -> None:
    # `_tree_gave_up` is the exception the clear-guard above and the sibling `elif` below both make
    # (BE-0418 review finding): an in-tree give-up names a prompt a rule *did* identify and a tap
    # failed to clear, so a *different*, co-present alert racing away must not replace that note
    # with the hedged "unhandled" form just because this branch also found a genuine leftover.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule, uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesAway(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    driver = _RacesAway([])
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
        el(None, "Weird Button", ["button"]),
    ]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            )
        ]
    )
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate._tree_gave_up = True
    gate._tree_gave_up_shape = frozenset({"Not Now"})
    gate.blocked_note = uncleared_prompt_note("Not Now")
    # The given-up sheet is still on screen (its own retirement is a different finding, pinned
    # below), so its label is in this poll's own tree too (BE-0418 review finding).
    gate.observe([el(None, "Not Now", ["button"])])
    assert gate.blocked_note == uncleared_prompt_note("Not Now")


def test_wait_guard_keeps_an_in_tree_give_up_note_through_an_unhandled_native_alert() -> None:
    # The `"unhandled"` branch's own note-set is the one write in `_observe_native` that used to
    # have no `_tree_gave_up` exception, even though the clear-guard above it and both branches
    # this PR adds around it all make one (BE-0418 review finding). A guarded `wait` on a screen
    # holding a `savePassword` sheet spends its tap budget and gives up on the tree side; a later,
    # unrelated native alert no rule identifies must not overwrite that tree note with the hedged
    # "unhandled" form while the given-up sheet is still on screen (its own retirement, once it
    # is not, is a different finding -- see the test below).
    from bajutsu.common.orchestrator.types import uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    driver = FakeDriver([])
    driver.system_alert_buttons = [el(None, "Weird Button", ["button"])]
    guard = AlertGuardConfig()
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate._tree_gave_up = True
    gate._tree_gave_up_shape = frozenset({"Not Now"})
    gate.blocked_note = uncleared_prompt_note("Not Now")
    # The given-up sheet is still on screen (its own retirement is a different finding, pinned
    # below), so its label is in this poll's own tree too (BE-0418 review finding).
    gate.observe([el(None, "Not Now", ["button"])])
    assert gate.blocked_note == uncleared_prompt_note("Not Now")


def test_wait_guard_retires_an_in_tree_give_up_once_the_sheet_leaves_the_tree() -> None:
    # The one thing missing from the deference the two tests above pin: nothing ever *lifted*
    # `_tree_gave_up`, since `_dismiss_from_tree` is the only other place that resets it and it
    # runs only when `probed_absent` holds -- which a live, undeclared SpringBoard alert stops
    # from holding for as long as that alert is up (BE-0418 review finding). A given-up sheet
    # that closes (or is navigated past) while an unrelated native alert is still up would
    # otherwise leave its own stale note standing for the rest of the wait, with the alert that
    # is actually blocking the screen never named. Retiring the latch from this poll's own tree —
    # rather than waiting for a `probed_absent` poll that may never come — is what lets the fresher
    # diagnosis through.
    from bajutsu.common.orchestrator.types import uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    driver = FakeDriver([])
    driver.system_alert_buttons = [el(None, "Weird Button", ["button"])]
    guard = AlertGuardConfig()
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate._tree_gave_up = True
    gate._tree_gave_up_shape = frozenset({"Not Now"})
    gate.blocked_note = uncleared_prompt_note("Not Now")
    # "Not Now" is gone from this poll's own tree -- the sheet closed on its own.
    gate.observe([])
    assert not gate._tree_gave_up
    assert gate._tree_gave_up_shape is None
    # The unrelated native alert's own diagnosis now gets through, naming the button that is
    # actually still blocking the screen instead of the sheet that already left it.
    assert "Weird Button" in gate.blocked_note
    assert "Not Now" not in gate.blocked_note


def test_wait_guard_retires_an_in_tree_give_up_by_shape_not_by_the_label_alone() -> None:
    # The retirement above must key on the given-up *shape*, not the label alone (BE-0418 review
    # finding): two `in_tree` rules can share one tap label under different choices --
    # `savePassword`'s three shapes all tap "Not Now" -- so a *different*, genuinely live prompt
    # that merely shares the given-up label must not keep the latch armed for a sheet that already
    # left. Gave up on the web-form shape ("Save Password"/"Never for This Website"/"Not Now");
    # that sheet then closes and the 26.5 in-app shape ("Save"/"Not Now") is presented in its
    # place -- a different, genuinely live prompt sharing only "Not Now" with the one given up on.
    from bajutsu.common.orchestrator.types import uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    driver = FakeDriver([])
    driver.system_alert_buttons = [el(None, "Weird Button", ["button"])]
    guard = AlertGuardConfig()
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate._tree_gave_up = True
    gate._tree_gave_up_shape = frozenset({"Save Password", "Never for This Website", "Not Now"})
    gate.blocked_note = uncleared_prompt_note("Not Now")
    # "Not Now" is still on screen, but the web-form shape's other two labels are gone: this is a
    # different prompt (the 26.5 in-app shape) that merely shares the given-up label, not the same
    # sheet still showing.
    gate.observe([el(None, "Save", ["button"]), el(None, "Not Now", ["button"])])
    assert not gate._tree_gave_up
    assert gate._tree_gave_up_shape is None
    # The unrelated native alert's own diagnosis now gets through, rather than the stale note about
    # the web-form sheet -- which already left -- continuing to mask it.
    assert "Weird Button" in gate.blocked_note


def test_wait_guard_clears_the_note_the_moment_it_retires_a_give_up_with_no_native_probe_due() -> (
    None
):
    # BE-0418 review finding: retiring `_tree_gave_up` without also clearing the note it was
    # holding leaves the stale diagnosis standing for up to a whole `poll_interval` -- exactly the
    # window this retirement exists to close. Every other write to `blocked_note` in
    # `_observe_native` is gated on `not self._tree_gave_up`, so while the latch stood, nothing else
    # could have touched the note; retiring the latch here without also clearing it hands the job to
    # whichever write runs next, and on a poll where the native probe is not due -- and a live,
    # undeclared alert has already latched `_native_unhandled`, which returns above the collapsed-
    # tree proxy -- there is no next writer this tick at all.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule, uncleared_prompt_note
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    driver = FakeDriver([])
    driver.system_alert_buttons = [el(None, "Weird Button", ["button"])]
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Not Now"}),
                tap_label="Not Now",
                native=False,
                in_tree=True,
            )
        ]
    )
    clock = _LogicalClock()
    gate = _AlertGuardGate(driver=driver, clock=clock, guard=guard, alerts=[])
    # A previous poll already probed natively and latched the undeclared alert as unhandled --
    # `_last_native` is no longer `None`, so the very next poll is not guaranteed to probe again.
    gate._last_native = 0.0
    gate._native_unhandled = True
    gate._tree_gave_up = True
    gate._tree_gave_up_shape = frozenset({"Not Now"})
    gate.blocked_note = uncleared_prompt_note("Not Now")
    # One `_POLL` tick later -- nowhere near a full `poll_interval` -- "Not Now" is gone from the
    # tree, but the native probe is not due and `_native_unhandled` returns before the collapsed-
    # tree proxy ever runs: no other write to `blocked_note` happens this poll.
    clock.sleep(0.05)
    gate.observe([])
    assert not gate._tree_gave_up
    assert gate.blocked_note == ""


def test_wait_guard_does_not_blame_a_scrim_for_time_a_race_withheld_its_own_tap() -> None:
    # BE-0418 review finding: narrowing `probed_absent` to a genuinely empty read means a raced
    # native alert withholds the in-tree tap's own licence for as long as it keeps racing away --
    # but `_tree_not_tappable_since` is a wall-clock horizon that keeps ticking regardless of
    # whether `_dismiss_from_tree` ever runs. Left unreset, a scrim that lifts *during* the race is
    # still given up on the moment the race resolves, purely because unlicensed wall-clock time was
    # counted against it -- the very first retry since the scrim lifted sees the whole, un-attempted
    # gap and gives up without ever attempting the tap.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _StuckThenRaces(FakeDriver):
        def __init__(self, screen: list[base.Element]) -> None:
            super().__init__(screen)
            self.tappable = False

        def tap(self, sel: base.Selector) -> None:
            if sel.get("label") == "Not Now" and not self.tappable:
                raise base.ElementNotTappable("scrim still presenting")
            super().tap(sel)

        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the prompt raced away")

    tree = [el(None, "Not Now", ["button"])]
    driver = _StuckThenRaces(tree)
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Not Now"}),
                tap_label="Not Now",
                native=False,
                in_tree=True,
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}),
                tap_label="Allow",
                native=True,
                in_tree=False,
            ),
        ]
    )
    clock = _LogicalClock()
    gate = _AlertGuardGate(driver=driver, clock=clock, guard=guard, alerts=[])

    # Poll 1 (t=0): a genuinely empty native read licenses the in-tree tap; the sheet resolves but
    # a scrim still covers its button.
    gate.observe(tree)

    # Polls 2-3 (t=1, t=2): an unrelated SpringBoard alert raises and its own tap races away on
    # each probe -- `probed_absent` stays False throughout, withholding the tree's own licence for
    # two full `poll_interval`s, which alone already meets `_decline_giveup`'s default 2s horizon.
    driver.system_alert_buttons = [
        el(None, "Allow", ["button"]),
        el(None, "Don't Allow", ["button"]),
    ]
    clock.sleep(guard.poll_interval)
    gate.observe(tree)
    clock.sleep(guard.poll_interval)
    gate.observe(tree)

    # The scrim lifted well before either raced poll ran -- the sheet has been tappable the whole
    # time the race was withholding the licence. A full `poll_interval` so the native probe is due
    # again and reports the surface genuinely empty, re-licensing the in-tree tap.
    driver.tappable = True
    driver.system_alert_buttons = []
    clock.sleep(guard.poll_interval)
    gate.observe(tree)

    assert not gate._tree_gave_up
    assert len(gate.alerts) == 1
    assert gate.alerts[0].label == "Not Now"


def test_wait_guard_does_not_blame_a_scrim_for_time_a_reserved_alert_withheld_its_own_tap() -> None:
    # BE-0418 review finding: `"reserved"` withholds the in-tree tap's own licence exactly the way
    # `raced` does -- `probed_absent` is False for as long as the step's own `handleSystemAlert`
    # alert stays up, which can be the step's entire timeout -- but the earlier fix for `raced`
    # left this path uncovered, so the not-tappable horizon kept ticking through it regardless.
    from bajutsu.common.orchestrator.types import ResolvedAlertRule
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _StuckThenReserved(FakeDriver):
        def __init__(self, screen: list[base.Element]) -> None:
            super().__init__(screen)
            self.tappable = False

        def tap(self, sel: base.Selector) -> None:
            if sel.get("label") == "Not Now" and not self.tappable:
                raise base.ElementNotTappable("scrim still presenting")
            super().tap(sel)

    tree = [el(None, "Not Now", ["button"])]
    driver = _StuckThenReserved(tree)
    guard = AlertGuardConfig(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Not Now"}),
                tap_label="Not Now",
                native=False,
                in_tree=True,
            )
        ]
    )
    clock = _LogicalClock()
    gate = _AlertGuardGate(
        driver=driver, clock=clock, guard=guard, alerts=[], reserved={"label": "Allow"}
    )

    # Poll 1 (t=0): a genuinely empty native read licenses the in-tree tap; the sheet resolves but
    # a scrim still covers its button.
    gate.observe(tree)

    # Polls 2-3 (t=1, t=2): the step's own alert raises and its selector reserves it -- `probed_absent`
    # stays False throughout, withholding the tree's own licence for two full `poll_interval`s, which
    # alone already meets `_decline_giveup`'s default 2s horizon.
    driver.system_alert_buttons = [el(None, "Allow", ["button"])]
    clock.sleep(guard.poll_interval)
    gate.observe(tree)
    clock.sleep(guard.poll_interval)
    gate.observe(tree)

    # The step answers its own alert and the scrim lifts well before either reserved poll ran -- the
    # sheet has been tappable the whole time the reservation was withholding the licence. A full
    # `poll_interval` so the native probe is due again and reports the surface genuinely empty,
    # re-licensing the in-tree tap.
    driver.tappable = True
    driver.system_alert_buttons = []
    clock.sleep(guard.poll_interval)
    gate.observe(tree)

    assert not gate._tree_gave_up
    assert len(gate.alerts) == 1
    assert gate.alerts[0].label == "Not Now"


def test_wait_guard_does_not_credit_a_rule_matching_alert_rule_would_refuse() -> None:
    # The leftover computation above must match `matching_alert_rule`'s own terms exactly, not a
    # bare subset test (BE-0418 review finding): a shape whose labels are present but not
    # *uniquely* is a prompt no later probe resolves either (the per-label uniqueness collision
    # `AlertGuardConfig.__call__`'s own "unhandled" branch documents for the built-in `notifications`
    # / `tracking` pair), so its buttons must stay in the leftover rather than being credited away.
    # Stubbed via `probe_native`, the same isolation this file's other race tests already use, since
    # a real read reaching this exact collision would answer "unhandled" before ever racing.
    from bajutsu.common.orchestrator.types import (
        AlertEvent,
        NativeAlertState,
        ResolvedAlertRule,
        alert_block_note,
    )
    from bajutsu.common.orchestrator.waits import _AlertGuardGate

    class _RacesOnPasteAlongsideACollidingNotifications(AlertGuardConfig):
        def probe_native(
            self,
            driver: base.Driver,
            reserved: base.Selector | None = None,
            *,
            dismissed: frozenset[frozenset[str]] = frozenset(),
        ) -> tuple[NativeAlertState, AlertEvent | None, list[str]]:
            return "absent", None, ["Allow", "Don't Allow", "Allow", "Don't Allow", "Allow Paste"]

    guard = _RacesOnPasteAlongsideACollidingNotifications(
        rules=[
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Allow"
            ),
            ResolvedAlertRule(
                identifying_labels=frozenset({"Allow Paste"}), tap_label="Allow Paste"
            ),
        ]
    )
    driver = FakeDriver([])
    gate = _AlertGuardGate(driver=driver, clock=_LogicalClock(), guard=guard, alerts=[])
    gate.observe([])
    # "Allow"/"Don't Allow" collide (each appears twice) and stay in the leftover uncredited;
    # "Allow Paste" is uniquely identified and correctly excluded from it.
    assert gate.blocked_note == alert_block_note(["Allow", "Don't Allow", "Allow", "Don't Allow"])


def test_wait_guard_reports_a_persistent_collapse_it_cannot_clear() -> None:
    """BE-0402: on a backend with no native path, a persistently collapsed screen is not something
    the guard will act on — it neither guesses nor calls a model. What it does instead is refuse to
    fail silently: the wait times out on schedule and says the screen looked blocked, hedged, because
    the collapsed tree is a correlation and no query ever named a button behind it."""
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _CollapsingDriver([el("home", "Home")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()]), alerts=alerts
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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _CollapsingDriver([el("home", "Home")])
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    fresh = [(ScreenTransition(kind="screenChanged"), 0.0)]
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, tree = _wait(
        driver,
        w,
        clock,
        alert_guard=AlertGuardConfig(rules=[guard_rule()]),
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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _CollapsingDriver([el("home", "Home")])  # the `before` snapshot is the collapsed tree
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "screenChanged", "timeout": 30.0})
    ok, reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()]), alerts=alerts
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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _LateAlertDriver([el("home", "Home")], on_dismiss=lambda d: setattr(d, "screen", []))
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    w = Wait.model_validate({"until": "screenChanged", "timeout": 30.0})
    ok, reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()]), alerts=alerts
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
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _UnsettledUntilAnswered()
    alerts: list[AlertEvent] = []
    w = Wait.model_validate({"until": "settled", "timeout": 30.0})
    ok, _reason, _tree = _wait(
        driver,
        w,
        _LogicalClock(),
        alert_guard=AlertGuardConfig(rules=[guard_rule()]),
        alerts=alerts,
    )
    assert ok  # a settle never fails the step
    assert driver.dismissals == 1
    assert alerts == [AlertEvent(label=GUARD_LABEL)]


def test_wait_settled_signal_guard_fires_on_a_prompt_that_arrives_mid_settle() -> None:
    """And so does the signal-based settle path (BE-0310), inside its quiescence window."""
    from bajutsu.common.orchestrator.types import AlertEvent
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
        alert_guard=AlertGuardConfig(rules=[guard_rule()], poll_interval=0.05),
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
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
        SlowReveal(clock), w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()]), alerts=[]
    )
    assert not ok
    assert "timeout" in reason
    assert clock.now() < 2.0  # honored the 1s budget; the guard did not push the deadline to 10s


def test_wait_guard_fires_without_an_alerts_list() -> None:
    """BE-0269: a guarded wait called with no `alerts` list (a direct `_wait` call) still fires the
    guard and recovers — the recording is simply dropped, never a crash on a None list."""
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

    driver = _CollapsingDriver([el("ready", "R")])
    clock = _LogicalClock()
    w = Wait.model_validate({"for": {"id": "ready"}, "timeout": 30.0})
    ok, _reason, _tree = _wait(
        driver, w, clock, alert_guard=AlertGuardConfig(rules=[guard_rule()])
    )  # no alerts list
    assert ok
    assert driver.dismissals == 1


# --- live "what am I waiting for" progress ---


def test_describe_wait_renders_each_condition() -> None:
    """describe_wait renders every wait shape in the live-progress wording — selectors as
    `key=value`, which differs from `_wait`'s timeout reason (a raw selector dict)."""
    from bajutsu.common.orchestrator.waits import describe_wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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
    from bajutsu.common.orchestrator.waits import _wait
    from bajutsu.common.scenario import Wait

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

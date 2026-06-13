"""Record loop (Tier 1) — drive an app with an agent and emit a scenario.

observe (query) -> agent proposes the next action -> execute it -> repeat, until
the agent signals done or max_steps is hit. The recorded steps form a deterministic
scenario that `run` later replays with no AI.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from bajutsu.agent import Agent, Observation
from bajutsu.drivers import base
from bajutsu.orchestrator import BlockedHandler, Clock, RealClock, _action_of, _do_action, _wait
from bajutsu.scenario import Assertion, Scenario, Selector, Step

# A live-progress sink: each turn's decision is handed to it as a one-line string.
Reporter = Callable[[str], None]


def _describe_selector(sel: Selector | None) -> str:
    """A compact human label for a selector — id if present, else label/value/traits[index]."""
    if sel is None:
        return "?"
    if sel.id:
        return f"#{sel.id}"
    parts = []
    if sel.label is not None:
        parts.append(f"label={sel.label!r}")
    if sel.value is not None:
        parts.append(f"value={sel.value!r}")
    if sel.traits:
        parts.append(f"traits={sel.traits}")
    if sel.index is not None:
        parts.append(f"index={sel.index}")
    return " ".join(parts) or "?"


def _describe_step(step: Step) -> str:
    """A one-line summary of a proposed step, for live record output."""
    if step.tap is not None:
        return f"tap {_describe_selector(step.tap)}"
    if step.type is not None:
        return f"type {step.type.text!r} into {_describe_selector(step.type.into)}"
    if step.wait is not None:
        return f"wait for {_describe_selector(step.wait.for_)}"
    return next((f for f in step.model_dump(exclude_none=True)), "step")


def _screenshot_bytes(driver: base.Driver) -> bytes | None:
    """Capture a PNG of the current screen as bytes (best-effort)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        data = Path(path).read_bytes()
        Path(path).unlink(missing_ok=True)
        return data or None
    except Exception:  # noqa: BLE001 — screenshots are optional context for the agent
        return None


def _settle_target(assertion: Assertion) -> base.Selector | None:
    """The selector of a positive-existence assertion (something that must be present)."""
    if assertion.exists is not None and not assertion.exists.negate:
        return assertion.exists.sel.as_selector()
    if assertion.value is not None:
        return assertion.value.sel.as_selector()
    if assertion.label is not None:
        return assertion.label.sel.as_selector()
    for state in (assertion.enabled, assertion.disabled, assertion.selected):
        if state is not None:
            return state.as_selector()
    return None  # notExists / count: no single element to wait for


def _settle_step(expect: list[Assertion], timeout: float = 5.0) -> Step | None:
    """A wait for the first asserted element, recorded before the assertions.

    The agent observes a settled screen between turns, but deterministic replay runs
    fast and can verify before an async transition (e.g. a sheet) has rendered. A wait
    for an asserted element makes the recorded scenario self-sufficient without adding
    implicit timing to `run`.
    """
    for assertion in expect:
        target = _settle_target(assertion)
        if target is not None:
            return Step.model_validate({"wait": {"for": target, "timeout": timeout}})
    return None


def _execute(driver: base.Driver, step: Step, clock: Clock) -> None:
    kind = _action_of(step)
    if kind == "wait":
        assert step.wait is not None
        _wait(driver, step.wait, clock)
    elif kind == "assert_":
        return  # assertions are checks, not actions to perform while recording
    else:
        _do_action(driver, step)


def _shows_app_ui(elements: list[base.Element]) -> bool:
    """Whether the tree shows the app's own UI (rather than being collapsed under a system
    overlay). A SpringBoard alert collapses the app's tree to a bare window; a live app screen
    has actionable content. "Actionable" = any non-application element carrying an `id` OR a
    `label`, so apps WITHOUT accessibility identifiers (label/coordinate-driven, e.g. sample2)
    are not mistaken for a blocked screen — the bug that made the guard fire every turn."""
    return any(
        (el.get("identifier") or el.get("label")) and "application" not in (el.get("traits") or [])
        for el in elements
    )


def _clear_blocking(
    driver: base.Driver, guard: BlockedHandler, clock: Clock, max_tries: int = 3,
    report: Reporter | None = None,
) -> None:
    """Dismiss anything covering the app (e.g. a system alert) before the agent observes.

    A SpringBoard alert has no queryable app content and collapses the tree to a bare
    window — leaving the agent nothing to act on. While the tree stays collapsed, keep
    asking the guard to clear it: an alert caught mid-animation can be missed on the first
    screenshot. When `report` is given, the guard's detection and dismissal are streamed
    so the watcher sees it stepping in.
    """
    say = report or (lambda _msg: None)
    announced = False
    for _ in range(max_tries):
        if _shows_app_ui(driver.query()):
            return  # the app is showing actionable elements; nothing blocking
        if not announced:
            say("⚠️  the app screen looks blocked by a system prompt — asking the alert guard to clear it …")
            announced = True
        event = guard(driver)  # try to dismiss whatever is covering the app
        if event is not None:
            label = getattr(event, "label", "")
            say(f"🛡️  dismissed a system alert · tapped {label!r}" if label
                else "🛡️  dismissed a system alert")
        clock.sleep(0.5)  # let it animate out before re-checking


def _execute_with_recovery(
    driver: base.Driver, step: Step, clock: Clock, guard: BlockedHandler | None,
    report: Reporter | None = None,
) -> bool:
    """Execute a step; if it fails because a prompt is covering the app, clear it and retry."""
    try:
        _execute(driver, step, clock)
        return True
    except base.SelectorError:
        if guard is None:
            return False
        (report or (lambda _m: None))("⚠️  a step could not act — a system prompt may be covering the app; recovering …")
        _clear_blocking(driver, guard, clock, report=report)
        try:
            _execute(driver, step, clock)
            return True
        except base.SelectorError:
            return False


def _plan_goal(agent: Agent, goal: str, say: Reporter) -> list[str]:
    """Decompose the goal into concrete steps up front and stream them to the watcher.

    Best-effort: an agent without a `plan` method (e.g. a test fake) or a planning call that
    fails just yields no plan — the loop then runs exactly as before. When a plan is produced
    it is both explained here and fed back to the agent each turn via `Observation.plan`.
    """
    planner = getattr(agent, "plan", None)
    if planner is None:
        return []
    try:
        plan = [str(step) for step in planner(goal)]
    except Exception as exc:  # noqa: BLE001 — planning is optional context, never fatal to record
        say(f"… could not plan the goal up front ({exc}); proceeding step by step")
        return []
    if plan:
        say(f"\U0001f5fa️  plan — {goal}")
        for i, step in enumerate(plan, 1):
            say(f"   {i}. {step}")
    return plan


def record(
    driver: base.Driver,
    goal: str,
    agent: Agent,
    *,
    name: str = "recorded",
    max_steps: int = 30,
    clock: Clock | None = None,
    with_screenshot: bool = True,
    alert_guard: BlockedHandler | None = None,
    report: Reporter | None = None,
) -> Scenario:
    """Explore toward `goal` with `agent`, returning the recorded scenario.

    If `alert_guard` is given, an unexpected OS prompt (e.g. iOS "Save Password?")
    that surfaces while authoring is dismissed so the agent keeps a clean view. The
    dismissal is environmental, not a recorded step; replay handles it with
    `run --dismiss-alerts`.

    If `report` is given, each turn's decision (the agent's proposed action and reason)
    is streamed to it as a one-line string, so a caller can show progress live.
    """
    clock = clock or RealClock()
    say = report or (lambda _msg: None)
    steps: list[Step] = []
    expect: list[Assertion] = []
    plan = _plan_goal(agent, goal, say)

    for _ in range(max_steps):
        if alert_guard is not None:
            _clear_blocking(driver, alert_guard, clock, report=report)
        elements = driver.query()
        if alert_guard is not None and not _shows_app_ui(elements):
            # A prompt slipped in after the last clear: don't ask the agent to act on a
            # dead screen (it would hallucinate ids); re-clear on the next iteration.
            clock.sleep(0.3)
            continue
        n = len(steps) + 1
        say(f"[{n}] observing {len(elements)} elements; asking the agent …")
        screenshot = _screenshot_bytes(driver) if with_screenshot else None
        proposal = agent.next_action(
            Observation(
                goal=goal, screen=elements, history=list(steps),
                screenshot=screenshot, plan=plan,
            )
        )
        if proposal.note:  # the agent's reasoning for this turn, shown before the action it chose
            say(f"[{n}] \U0001f4ad {proposal.note}")
        if proposal.done:
            say(f"[{n}] ✓ finish · {len(proposal.expect)} assertion(s)")
            expect = proposal.expect
            settle = _settle_step(expect)
            if settle is not None:
                steps.append(settle)  # let an async screen render before replay verifies
            break
        if proposal.step is None:
            say(f"[{n}] agent proposed no action; stopping")
            break
        say(f"[{n}] → {_describe_step(proposal.step)}")
        if not _execute_with_recovery(driver, proposal.step, clock, alert_guard, report=report):
            say(f"[{n}] ! could not resolve that target on the live screen; stopping")
            break  # the proposed action did not resolve, even after clearing prompts
        steps.append(proposal.step)

    return Scenario(name=name, steps=steps, expect=expect)

"""Tests for the record loop, driven by a scripted fake agent."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from conftest import ShotDriver

from bajutsu.agent_protocols import Observation, Proposal
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.elements import shows_app_ui
from bajutsu.handoff import HandoffRequest, HandoffResponse, HumanHandoffUnavailable
from bajutsu.record import (
    _execute,
    _format_elapsed,
    _is_looping,
    _screenshot_bytes,
    _should_attach,
    _summarize_screen,
    _tokenize_secrets,
    record,
)
from bajutsu.scenario import Assertion, Step, dump_scenarios, load_scenarios


class FakeAgent:
    def __init__(self, proposals: list[Proposal]) -> None:
        self._proposals = proposals
        self._i = 0

    def next_action(self, observation: Observation) -> Proposal:
        proposal = self._proposals[self._i]
        self._i += 1
        return proposal


class LoopAgent:
    """Always proposes tapping `a` (never done) — for max_steps testing."""

    def next_action(self, observation: Observation) -> Proposal:
        return Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})])


class SeenAgent(FakeAgent):
    """A scripted fake that also records the observations it was handed, so a test can inspect
    which turns carried a screenshot (BE-0192, vision-on-demand)."""

    def __init__(self, proposals: list[Proposal]) -> None:
        super().__init__(proposals)
        self.seen: list[Observation] = []

    def next_action(self, observation: Observation) -> Proposal:
        self.seen.append(observation)
        return super().next_action(observation)


class PlanningAgent(FakeAgent):
    """A fake that also decomposes the goal up front and records the observations it saw."""

    def __init__(self, proposals: list[Proposal], plan_steps: list[str]) -> None:
        super().__init__(proposals)
        self._plan_steps = plan_steps
        self.seen: list[Observation] = []

    def plan(self, goal: str) -> list[str]:
        return self._plan_steps

    def next_action(self, observation: Observation) -> Proposal:
        self.seen.append(observation)
        return super().next_action(observation)


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_record_produces_scenario() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    agent = FakeAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
            Proposal(done=True, expect=[Assertion.model_validate({"exists": {"id": "done"}})]),
        ]
    )

    scenario = record(driver, "reach done", agent, name="reach")
    assert scenario.name == "reach"
    # the tap, then an auto-inserted wait for the asserted element so replay settles
    assert len(scenario.steps) == 2
    assert scenario.steps[0].tap is not None
    assert scenario.steps[0].tap.id == "go"
    assert scenario.steps[1].wait is not None and scenario.steps[1].wait.for_ is not None
    assert scenario.steps[1].wait.for_.id == "done"
    assert scenario.expect[0].exists is not None
    assert scenario.expect[0].exists.sel.id == "done"

    # the recorded scenario round-trips through YAML
    reloaded = load_scenarios(dump_scenarios([scenario]))
    assert reloaded[0].steps[0].tap is not None
    assert reloaded[0].steps[0].tap.id == "go"
    assert reloaded[0].steps[1].wait is not None


def test_record_capture_video_tags_the_first_step_for_a_scenario_wide_recording() -> None:
    from bajutsu.orchestrator.evidence_rules import requested_intervals

    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), Proposal(done=True)]
    )
    scenario = record(driver, "x", agent, capture_video=True)
    assert scenario.steps[0].capture == ["video"]
    # a single step's inline capture is what makes the runner record the whole scenario
    assert "video" in requested_intervals(scenario)
    # and it survives the YAML round-trip
    assert load_scenarios(dump_scenarios([scenario]))[0].steps[0].capture == ["video"]


def test_record_without_capture_video_requests_no_interval() -> None:
    from bajutsu.orchestrator.evidence_rules import requested_intervals

    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), Proposal(done=True)]
    )
    scenario = record(driver, "x", agent)  # default: no video
    assert scenario.steps[0].capture is None
    assert requested_intervals(scenario) == []


class RecordingHandoff:
    """A scripted handoff responder that records the requests it received (BE-0179)."""

    def __init__(self, responses: list[HandoffResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.requests: list[HandoffRequest] = []

    def request(self, request: HandoffRequest) -> HandoffResponse:
        self.requests.append(request)
        response = self._responses[self._i]
        self._i += 1
        return response


def test_record_hands_off_on_needs_human_then_resumes() -> None:
    # A "needs human" turn pauses, hands off, and the loop resumes by re-observing — the human's
    # turn consumes no recorded step, and the next proposed action is recorded as usual (BE-0179).
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [
            Proposal(needs_human=True, human_prompt="enter the one-time password"),
            Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
            Proposal(done=True),
        ]
    )
    handoff = RecordingHandoff([HandoffResponse(acted=True)])
    scenario = record(driver, "log in", agent, handoff=handoff)

    assert len(handoff.requests) == 1
    assert handoff.requests[0].reason == "enter the one-time password"
    assert handoff.requests[0].screen  # the current screen summary travels with the request
    assert [s.tap.id for s in scenario.steps if s.tap is not None] == ["go"]


def test_record_resumes_after_a_value_response() -> None:
    # A value response (the headline case — the human supplies an OTP) also resumes by re-observing;
    # the substrate deliberately does not itself record the value (a child item decides that).
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [
            Proposal(needs_human=True, human_prompt="enter the OTP"),
            Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
            Proposal(done=True),
        ]
    )
    handoff = RecordingHandoff([HandoffResponse(values=["999111"])])
    scenario = record(driver, "log in", agent, handoff=handoff)
    assert len(handoff.requests) == 1
    assert [s.tap.id for s in scenario.steps if s.tap is not None] == ["go"]


def _noid(label: str) -> base.Element:
    """An addressable-by-label-only element (no accessibility id), for the degenerate-tree case."""
    return {
        "identifier": None,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _shots(driver: ShotDriver) -> int:
    """How many times the driver's screen was actually captured (lazy-capture check, BE-0192)."""
    return sum(1 for kind, _ in driver.actions if kind == "screenshot")


# --- BE-0192: vision-on-demand ---------------------------------------------------------------


def test_should_attach_triggers() -> None:
    # _should_attach takes crawl.screen_identity signatures ("id:…" / "structural:…").
    id_screen = "id:hash-a"
    # First turn (no previous) and any signature change always attach.
    assert _should_attach(id_screen, None) is True
    assert _should_attach(id_screen, "id:hash-b") is True
    # A view already seen with a rich, addressable (id) tree is text-only.
    assert _should_attach(id_screen, id_screen) is False
    # A degenerate (structural) tree attaches even when unchanged — the generous no-id trigger.
    structural = "structural:hash-c"
    assert _should_attach(structural, structural) is True


def test_record_attaches_on_first_turn_then_skips_the_same_screen() -> None:
    # First observation of a screen carries a screenshot; a same-fingerprint follow-up turn is
    # text-only, and — lazy capture — the driver is not screenshotted on that turn.
    driver = ShotDriver([_el("a", "A"), _el("b", "B")])  # two ids → stable id fingerprint
    agent = SeenAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]), Proposal(done=True)]
    )
    record(driver, "stay put", agent)
    assert agent.seen[0].screenshot is not None  # new screen → attached
    assert agent.seen[1].screenshot is None  # same screen, id-rich → text-only
    assert _shots(driver) == 1  # captured only for the turn that attached


def test_record_reattaches_when_the_screen_changes() -> None:
    # A fingerprint change re-attaches: the agent sees each newly-reached screen with an image.
    nxt = [_el("c", "C"), _el("d", "D")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = ShotDriver([_el("a", "A"), _el("b", "B")], react=react)
    agent = SeenAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]), Proposal(done=True)]
    )
    record(driver, "move on", agent)
    assert agent.seen[0].screenshot is not None
    assert agent.seen[1].screenshot is not None  # new fingerprint → re-attached


def test_record_attaches_for_a_degenerate_tree_even_on_the_same_screen() -> None:
    # A no-id (structural-fingerprint) screen attaches every turn, regardless of fingerprint — the
    # tap_point case where vision is the way in.
    driver = ShotDriver([_noid("X"), _noid("Y")])
    agent = SeenAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"label": "X"}})]),
            Proposal(steps=[Step.model_validate({"tap": {"label": "Y"}})]),
            Proposal(done=True),
        ]
    )
    record(driver, "no ids here", agent)
    assert agent.seen[0].screenshot is not None
    assert agent.seen[1].screenshot is not None  # unchanged, but degenerate → still attached


def test_record_escalates_to_a_screenshot_when_the_agent_asks() -> None:
    # On a text-only turn the agent calls need_screenshot; the loop re-issues the SAME screen once
    # with the image attached, and the eventual action is recorded normally.
    driver = ShotDriver([_el("a", "A"), _el("b", "B")])
    agent = SeenAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]),  # turn 1 (attached)
            Proposal(need_screenshot=True),  # turn 2, text-only → escalate
            Proposal(steps=[Step.model_validate({"tap": {"id": "b"}})]),  # re-issue with image
            Proposal(done=True),  # turn 3
        ]
    )
    scenario = record(driver, "peek", agent)
    assert agent.seen[1].screenshot is None  # the turn where the agent asked was text-only
    assert agent.seen[2].screenshot is not None  # re-issued with the image on the same screen
    # The action decided after escalation is recorded like any other; escalation itself adds no step.
    assert [s.tap.id for s in scenario.steps if s.tap is not None] == ["a", "b"]
    assert _shots(driver) == 2  # turn 1's attach + the one escalation capture


def test_record_attaches_every_turn_when_the_screen_always_changes() -> None:
    # Regression: when every turn reaches a new screen, the image is sent every turn, exactly as
    # before vision-on-demand.
    screens = [[_el("c", "C")], [_el("e", "E")]]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and screens:
            d.screen = screens.pop(0)

    driver = ShotDriver([_el("a", "A")], react=react)
    agent = SeenAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]),
            Proposal(steps=[Step.model_validate({"tap": {"id": "c"}})]),
            Proposal(done=True),
        ]
    )
    record(driver, "always moving", agent)
    assert all(obs.screenshot is not None for obs in agent.seen)
    assert _shots(driver) == 3


def test_record_with_screenshot_disabled_never_attaches_or_escalates() -> None:
    # with_screenshot=False (a driver with no screenshot capability) keeps every turn text-only and
    # never captures — the escalation cannot fire because there is no image to hand back.
    driver = ShotDriver([_el("a", "A"), _el("b", "B")])
    agent = SeenAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]), Proposal(done=True)]
    )
    record(driver, "no vision", agent, with_screenshot=False)
    assert all(obs.screenshot is None for obs in agent.seen)
    assert all(obs.vision_available is False for obs in agent.seen)  # the guidance keys off this
    assert _shots(driver) == 0


def test_record_handles_two_handoffs_in_one_run() -> None:
    # A multi-factor flow pauses twice (OTP, then CAPTCHA); each resumes without consuming a step
    # number, and the run still finishes normally.
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [
            Proposal(needs_human=True, human_prompt="enter the OTP"),
            Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
            Proposal(needs_human=True, human_prompt="solve the CAPTCHA"),
            Proposal(done=True),
        ]
    )
    handoff = RecordingHandoff([HandoffResponse(values=["999111"]), HandoffResponse(acted=True)])
    scenario = record(driver, "log in", agent, handoff=handoff)
    assert [r.reason for r in handoff.requests] == ["enter the OTP", "solve the CAPTCHA"]
    assert [s.tap.id for s in scenario.steps if s.tap is not None] == ["go"]


def test_record_masks_a_secret_in_the_handoff_reason() -> None:
    # A declared secret literal in the handoff prompt is tokenized before it reaches the request
    # (and thus the stream / logs), like the normal step's intent (BE-0120).
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(needs_human=True, human_prompt="the code is s3cr3t"), Proposal(done=True)]
    )
    handoff = RecordingHandoff([HandoffResponse(acted=True)])
    record(driver, "x", agent, handoff=handoff, secret_tokens=[("s3cr3t", "${secrets.OTP}")])
    assert "s3cr3t" not in handoff.requests[0].reason
    assert "${secrets.OTP}" in handoff.requests[0].reason


def test_record_needs_human_without_a_responder_fails_cleanly() -> None:
    # No responder (CI / non-interactive): a raised handoff is a clean, labeled failure, never a
    # hang and never an AI guess (BE-0179).
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent([Proposal(needs_human=True, human_prompt="solve the CAPTCHA")])
    with pytest.raises(HumanHandoffUnavailable, match="CAPTCHA"):
        record(driver, "x", agent)  # no handoff given


def test_record_handoff_cancel_stops_cleanly() -> None:
    # A cancelled handoff ends the record cleanly — no further steps, no crash.
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent([Proposal(needs_human=True, human_prompt="help")])
    handoff = RecordingHandoff([HandoffResponse(cancelled=True)])
    scenario = record(driver, "x", agent, handoff=handoff)
    assert scenario.steps == []


def test_summarize_screen_lists_labels_and_counts() -> None:
    summary = _summarize_screen([_el("go", "Go"), _el("cancel", "Cancel")])
    assert "2 element(s)" in summary
    assert "Go" in summary and "Cancel" in summary


def test_record_sets_scenario_provenance_from_goal() -> None:
    # The goal is the scenario-level `from:` provenance (BE-0044), and it round-trips.
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), Proposal(done=True)]
    )
    scenario = record(driver, "reach the done screen", agent, name="reach")
    assert scenario.from_ == "reach the done screen"
    assert load_scenarios(dump_scenarios([scenario]))[0].from_ == "reach the done screen"


def test_record_streams_plan_and_feeds_it_to_the_agent() -> None:
    driver = FakeDriver([_el("go", "Go")])
    agent = PlanningAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), Proposal(done=True)],
        plan_steps=["Tap Go", "Confirm the result"],
    )
    msgs: list[str] = []
    record(driver, "reach the result", agent, report=msgs.append)
    joined = "\n".join(msgs)
    assert "plan — reach the result" in joined  # the decomposition is explained to the watcher
    assert "1. Tap Go" in joined and "2. Confirm the result" in joined
    # and the same plan is handed to the agent every turn so it can follow the procedure
    assert agent.seen[0].plan == ["Tap Go", "Confirm the result"]


def test_record_without_a_planning_agent_still_works() -> None:
    """A fake agent with no `plan` method records exactly as before — planning is optional."""
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), Proposal(done=True)]
    )
    scenario = record(driver, "x", agent)
    assert scenario.steps and scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"


def test_record_stops_on_unresolvable_action() -> None:
    driver = FakeDriver([_el("a", "A")])
    agent = FakeAgent([Proposal(steps=[Step.model_validate({"tap": {"id": "missing"}})])])
    scenario = record(driver, "x", agent)
    assert scenario.steps == []  # could not execute -> not recorded


def test_record_respects_max_steps() -> None:
    driver = FakeDriver([_el("a", "A")])
    scenario = record(driver, "x", LoopAgent(), max_steps=3)
    assert len(scenario.steps) == 3


class ObservingAgent(FakeAgent):
    """Records the observations it was given, so a test can inspect what the loop passed in."""

    def __init__(self, proposals: list[Proposal]) -> None:
        super().__init__(proposals)
        self.seen: list[Observation] = []

    def next_action(self, observation: Observation) -> Proposal:
        self.seen.append(observation)
        return super().next_action(observation)


def test_record_without_screenshot_observes_elements_only() -> None:
    # BE-0194 §3: --no-screenshot flows through as with_screenshot=False, so the loop never captures
    # a screenshot and the agent sees an elements-only observation (the cheapest possible record).
    driver = FakeDriver([_el("go", "Go")])
    agent = ObservingAgent([Proposal(done=True)])
    record(driver, "x", agent, with_screenshot=False)
    assert agent.seen and agent.seen[0].screenshot is None


def test_is_looping_detects_repetition_and_oscillation() -> None:
    assert _is_looping(["tap a", "tap a", "tap a"])  # same action three times running
    assert _is_looping(["tap Open", "tap Close", "tap Open", "tap Close"])  # A,B,A,B oscillation
    assert not _is_looping(["tap a", "tap b", "tap a"])  # progress-ish, not yet a cycle
    assert not _is_looping(["tap a", "tap b", "tap c", "tap d"])  # all distinct


def test_format_elapsed() -> None:
    assert _format_elapsed(13.44) == "13.4s"
    assert _format_elapsed(63) == "1m 03s"
    assert _format_elapsed(125) == "2m 05s"


def test_record_reports_elapsed_time_on_completion() -> None:
    driver = FakeDriver([_el("a", "A")])
    msgs: list[str] = []
    record(driver, "x", FakeAgent([Proposal(done=True, expect=[])]), report=msgs.append)
    assert any("record finished in" in m for m in msgs)


def test_record_shows_which_plan_step_is_running() -> None:
    driver = FakeDriver([_el("a", "A")])
    msgs: list[str] = []
    agent = PlanningAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})], note="do it", plan_step=2),
            Proposal(done=True, expect=[]),
        ],
        plan_steps=["step one", "step two", "step three"],
    )
    record(driver, "x", agent, report=msgs.append)
    assert any("(plan 2/3)" in m for m in msgs)


def test_record_shows_intent_and_action_on_one_line() -> None:
    # The step's intent (the agent's reason) and the concrete action are streamed together, so a
    # watcher sees what each step is trying to do next to what it did.
    driver = FakeDriver([_el("a", "A")])
    msgs: list[str] = []
    agent = FakeAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})], note="open the panel"),
            Proposal(done=True, expect=[]),
        ]
    )
    record(driver, "x", agent, report=msgs.append)
    assert any("open the panel" in m and "→" in m and "tap" in m for m in msgs)


def test_record_stops_on_an_oscillation_before_max_steps() -> None:
    # An agent that cycles open/close forever is cut off by loop detection, not left to burn every
    # turn (the real-world stuck-record failure). A,B,A,B → stop after four recorded steps.
    driver = FakeDriver([_el("open", "Open"), _el("close", "Close")])
    cycle = [
        Proposal(steps=[Step.model_validate({"tap": {"id": "open"}})]),
        Proposal(steps=[Step.model_validate({"tap": {"id": "close"}})]),
    ] * 10
    scenario = record(driver, "x", FakeAgent(cycle), max_steps=30)
    assert [s.tap.id for s in scenario.steps if s.tap] == ["open", "close", "open", "close"]


def _vel(label: str | None, traits: list[str]) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": traits,
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_shows_app_ui_recognizes_label_only_screen() -> None:
    # An app without accessibility identifiers (a -noax variant): label-only elements are still app UI.
    app = _vel("BajutsuSample", ["application"])
    assert shows_app_ui([app, _vel("Get Started", ["button"])]) is True
    assert shows_app_ui([_el("onboarding.start", "Get Started")]) is True  # id-only also counts
    # A tree collapsed under a system alert — only the bare app window, nothing actionable.
    assert shows_app_ui([app]) is False
    assert shows_app_ui([]) is False


class _NoSleep:
    def sleep(self, _seconds: float) -> None:
        return None


class _AdvancingClock:
    """A logical clock that advances on sleep, so a condition wait reaches its deadline fast."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


# ---------------------------------------------------------------------------
# Shared step executor (BE-0201): record ignores a wait failure; enrich's hook raises.
# ---------------------------------------------------------------------------


def test_execute_ignores_a_wait_failure_by_default() -> None:
    """record replays a timed-out wait as a no-op: no hook, so it records forward."""
    driver = FakeDriver([_el("a", "A")])  # target never appears
    step = Step.model_validate({"wait": {"for": {"id": "missing"}, "timeout": 0.1}})

    _execute(driver, step, _AdvancingClock())  # must not raise


def test_execute_invokes_the_wait_failure_hook_with_the_reason() -> None:
    """enrich passes a hook so a wait it cannot settle stops the replay; the reason is forwarded."""
    driver = FakeDriver([_el("a", "A")])  # target never appears
    step = Step.model_validate({"wait": {"for": {"id": "missing"}, "timeout": 0.1}})

    seen: list[str] = []
    _execute(driver, step, _AdvancingClock(), on_wait_failure=seen.append)

    assert seen and "timeout" in seen[0].lower()


def test_execute_does_not_call_the_hook_when_the_wait_succeeds() -> None:
    """A wait whose target is present settles without touching the failure hook."""
    driver = FakeDriver([_el("here", "Here")])
    step = Step.model_validate({"wait": {"for": {"id": "here"}, "timeout": 0.1}})

    called = {"n": 0}

    def hook(_reason: str) -> None:
        called["n"] += 1

    _execute(driver, step, _AdvancingClock(), on_wait_failure=hook)

    assert called["n"] == 0


def test_alert_guard_activity_is_reported() -> None:
    """When a system prompt collapses the tree, the guard's detection and dismissal are streamed."""
    from bajutsu.orchestrator import AlertEvent

    app = _vel("App", ["application"])
    driver = FakeDriver([app])  # blocked: only the bare app window is visible

    def guard(d: base.Driver) -> AlertEvent:
        assert isinstance(d, FakeDriver)
        d.screen = [app, _vel("Get Started", ["button"])]  # dismissing reveals the app
        return AlertEvent(label="Not Now")

    agent = FakeAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"label": "Get Started"}})]),
            Proposal(done=True),
        ]
    )
    msgs: list[str] = []
    record(
        driver,
        "x",
        agent,
        clock=_NoSleep(),
        with_screenshot=False,
        alert_guard=guard,
        report=msgs.append,
    )
    joined = "\n".join(msgs)
    assert "blocked by a system prompt" in joined
    assert "dismissed a system alert" in joined and "Not Now" in joined


def test_alert_guard_not_fired_on_a_label_only_app() -> None:
    """Regression: with the guard on and a no-id app, the screen must not look 'blocked' — else
    the guard fires (a vision call) every turn and the loop never reaches the agent."""
    calls = {"n": 0}

    def guard(_driver: base.Driver) -> None:
        calls["n"] += 1

    driver = FakeDriver([_vel("Get Started", ["button"])])
    agent = FakeAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"label": "Get Started"}})]),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "start", agent, alert_guard=guard)
    assert calls["n"] == 0  # the app showed actionable UI; the guard never had to fire
    assert scenario.steps and scenario.steps[0].tap is not None


def test_settle_wait_targets_value_assertion() -> None:
    driver = FakeDriver([_el("go", "Go")])
    finish = Proposal(
        done=True,
        expect=[Assertion.model_validate({"value": {"sel": {"id": "counter"}, "equals": "2"}})],
    )
    agent = FakeAgent([Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), finish])
    scenario = record(driver, "g", agent)
    assert scenario.steps[-1].wait is not None and scenario.steps[-1].wait.for_ is not None
    assert scenario.steps[-1].wait.for_.id == "counter"


def test_no_settle_wait_for_negated_assertion() -> None:
    driver = FakeDriver([_el("go", "Go")])
    finish = Proposal(
        done=True, expect=[Assertion.model_validate({"exists": {"id": "x", "negate": True}})]
    )
    agent = FakeAgent([Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]), finish])
    scenario = record(driver, "g", agent)
    assert all(step.wait is None for step in scenario.steps)  # nothing positive to wait for


# --- secret tokenization (BE-0120) ---


def _field(identifier: str, label: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": ["textField"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_record_tokenizes_a_typed_secret_but_types_the_real_value() -> None:
    """A recorded secret lands as ${secrets.X} in the scenario, yet the app is driven with the
    real value so the agent still reaches the authenticated screen."""
    driver = FakeDriver([_field("password", "Password")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}})
                ]
            ),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "log in", agent, secret_tokens=[("hunter2", "${secrets.PASSWORD}")])

    # The written scenario carries the token, not the literal — and it round-trips through YAML.
    assert scenario.steps[0].type is not None
    assert scenario.steps[0].type.text == "${secrets.PASSWORD}"
    assert scenario.steps[0].type.into is not None and scenario.steps[0].type.into.id == "password"
    reloaded = load_scenarios(dump_scenarios([scenario]))
    assert reloaded[0].steps[0].type is not None
    assert reloaded[0].steps[0].type.text == "${secrets.PASSWORD}"
    assert "hunter2" not in dump_scenarios([scenario])

    # But the device was driven with the real credential (execution uses the unmodified value).
    assert ("type", "hunter2") in driver.actions


def test_record_leaves_non_secret_text_unchanged() -> None:
    """Ordinary typed text (the common case) is recorded verbatim even with secrets configured."""
    driver = FakeDriver([_field("username", "Username")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[Step.model_validate({"type": {"text": "alice", "into": {"id": "username"}}})]
            ),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "log in", agent, secret_tokens=[("hunter2", "${secrets.PASSWORD}")])
    assert scenario.steps[0].type is not None and scenario.steps[0].type.text == "alice"


def test_record_narrates_a_tokenization_without_leaking_the_literal() -> None:
    """The author is told a field was tokenized, and the raw secret never appears in the stream —
    not in the step line, nor in the agent's free-text reasoning (`note`)."""
    driver = FakeDriver([_field("password", "Password")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}})
                ],
                note="typing hunter2 into the password field",  # reasoning echoes the literal
            ),
            Proposal(done=True),
        ]
    )
    msgs: list[str] = []
    record(
        driver,
        "log in",
        agent,
        secret_tokens=[("hunter2", "${secrets.PASSWORD}")],
        report=msgs.append,
    )
    joined = "\n".join(msgs)
    assert "${secrets.PASSWORD}" in joined  # the substitution is surfaced
    assert "hunter2" not in joined  # not in the step line, nor in the echoed note


def test_record_without_secrets_records_the_literal_as_before() -> None:
    """No secret bindings => today's behavior: the typed text is recorded exactly as entered."""
    driver = FakeDriver([_field("password", "Password")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}})
                ]
            ),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "log in", agent)  # secret_tokens defaults to None
    assert scenario.steps[0].type is not None and scenario.steps[0].type.text == "hunter2"


def test_tokenize_secrets_replaces_longest_value_first() -> None:
    """A secret that is a substring of another is replaced first, so no partial literal remains."""
    step = Step.model_validate({"type": {"text": "abcdef", "into": {"id": "f"}}})
    # "abc" alone would leave "def"; ordering longest-first substitutes the full match cleanly.
    tokens = [("abcdef", "${secrets.FULL}"), ("abc", "${secrets.PART}")]
    tokenized, substituted = _tokenize_secrets(step, tokens)
    assert tokenized.type is not None and tokenized.type.text == "${secrets.FULL}"
    assert substituted == ["${secrets.FULL}"]


def test_tokenize_secrets_is_a_no_op_for_non_type_steps() -> None:
    step = Step.model_validate({"tap": {"id": "go"}})
    tokenized, substituted = _tokenize_secrets(step, [("hunter2", "${secrets.PASSWORD}")])
    assert tokenized is step and substituted == []


def test_tokenize_secrets_does_not_corrupt_an_already_inserted_token() -> None:
    # A later value ("secrets") appears inside the token inserted for the first value; a naive
    # sequential replace would splice it into a malformed nested token. Two-pass masking must not.
    step = Step.model_validate({"type": {"text": "verylongsecret", "into": {"id": "f"}}})
    tokens = [("verylongsecret", "${secrets.X}"), ("secrets", "${secrets.Y}")]
    tokenized, substituted = _tokenize_secrets(step, tokens)
    assert tokenized.type is not None and tokenized.type.text == "${secrets.X}"
    assert substituted == ["${secrets.X}"]


# --- _screenshot_bytes (the unified best-effort capture helper, BE-0132) ---


class _RaisingShotDriver(FakeDriver):
    """A FakeDriver whose screenshot fails — the stale-simulator / full-disk case."""

    attempted_path: str | None = None

    def screenshot(self, path: str) -> None:
        self.attempted_path = path  # record it so the test can assert the temp file was cleaned up
        raise RuntimeError("simulator gone")


def test_screenshot_bytes_returns_captured_png() -> None:
    assert _screenshot_bytes(ShotDriver([_el("go", "Go")])) == b"\x89PNG\r\n\x1a\n fake"


def test_screenshot_bytes_none_when_nothing_captured(caplog: pytest.LogCaptureFixture) -> None:
    # The base FakeDriver writes no bytes: a genuine empty capture, not a failure — so it
    # returns None without logging a warning (the empty case must stay distinct from failure).
    with caplog.at_level(logging.WARNING, logger="bajutsu.record"):
        assert _screenshot_bytes(FakeDriver([_el("go", "Go")])) is None
    assert not caplog.records


def test_screenshot_bytes_surfaces_failure_instead_of_swallowing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A real capture failure must not be indistinguishable from an empty capture: it returns
    # None (best-effort, callers continue) but leaves a warning in the log so it is visible.
    driver = _RaisingShotDriver([_el("go", "Go")])
    with caplog.at_level(logging.WARNING, logger="bajutsu.record"):
        assert _screenshot_bytes(driver) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "simulator gone" in caplog.text
    # The temp file is cleaned up even on failure, so repeated failures don't leak PNGs.
    assert driver.attempted_path is not None and not Path(driver.attempted_path).exists()


# --- multi-action batch turns (BE-0178) ---


def test_record_executes_a_full_intra_screen_batch_in_order() -> None:
    # Several actions determinable from one screen run in one turn; the screen identity is stable
    # (no transition), so the whole batch executes and is recorded in order — one model turn.
    driver = FakeDriver([_el("a", "A"), _el("b", "B"), _el("c", "C")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"tap": {"id": "a"}}),
                    Step.model_validate({"tap": {"id": "b"}}),
                    Step.model_validate({"tap": {"id": "c"}}),
                ]
            ),
            Proposal(done=True),  # a second turn re-observes and finishes
        ]
    )
    scenario = record(driver, "batch", agent)
    assert [s.tap.id for s in scenario.steps if s.tap] == ["a", "b", "c"]
    # the artifact stays a flat, individually-resolved step list — unchanged shape, round-trips
    reloaded = load_scenarios(dump_scenarios([scenario]))
    assert [s.tap.id for s in reloaded[0].steps if s.tap] == ["a", "b", "c"]


def test_record_aborts_batch_on_screen_change_recording_only_the_prefix() -> None:
    # The screen moves out from under the plan after the first step; the rest of the batch is
    # abandoned (never executed, never recorded) and the loop re-observes (Decision 2, "仕切り直し").
    after = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "a":
            d.screen = after

    driver = FakeDriver([_el("a", "A"), _el("b", "B"), _el("c", "C")], react=react)
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"tap": {"id": "a"}}),
                    Step.model_validate({"tap": {"id": "b"}}),
                    Step.model_validate({"tap": {"id": "c"}}),
                ]
            ),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "batch", agent)
    assert [s.tap.id for s in scenario.steps if s.tap] == ["a"]  # only the executed prefix
    assert len([a for a in driver.actions if a[0] == "tap"]) == 1  # b and c never executed


def test_record_aborts_batch_on_midbatch_resolve_failure_recording_prefix() -> None:
    # A batched step that no longer resolves — after at least one executed — aborts the rest and
    # re-observes; only the executed prefix is recorded (unlike a length-1 unresolvable, which stops).
    driver = FakeDriver([_el("a", "A"), _el("c", "C")])
    agent = FakeAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"tap": {"id": "a"}}),
                    Step.model_validate({"tap": {"id": "missing"}}),
                    Step.model_validate({"tap": {"id": "c"}}),
                ]
            ),
            Proposal(done=True),
        ]
    )
    scenario = record(driver, "batch", agent)
    assert [s.tap.id for s in scenario.steps if s.tap] == ["a"]


def test_record_runs_actions_then_finishes_in_one_turn() -> None:
    # A turn may end with finish after actions (Decision 3): the action runs, then the loop finishes
    # with finish's assertions — action and conclusion in a single model turn.
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    agent = FakeAgent(
        [
            Proposal(
                steps=[Step.model_validate({"tap": {"id": "go"}})],
                done=True,
                expect=[Assertion.model_validate({"exists": {"id": "done"}})],
            )
        ]
    )
    scenario = record(driver, "reach done", agent)
    assert scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"
    assert scenario.steps[1].wait is not None and scenario.steps[1].wait.for_ is not None
    assert scenario.steps[1].wait.for_.id == "done"
    assert scenario.expect[0].exists is not None and scenario.expect[0].exists.sel.id == "done"


def test_record_announces_the_next_plan_step_before_observing() -> None:
    # Before each observe, the loop names the plan step it is about to work toward, so a watcher
    # isn't left staring at a silent model round-trip (the concrete action is decided live).
    driver = FakeDriver([_el("a", "A")])
    msgs: list[str] = []
    agent = PlanningAgent(
        [
            Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})], note="do it", plan_step=1),
            Proposal(done=True, expect=[]),
        ],
        plan_steps=["tap the button", "confirm the result"],
    )
    record(driver, "x", agent, report=msgs.append)
    joined = "\n".join(msgs)
    assert "next — plan 1/2: tap the button" in joined  # the first turn's up-front intent
    # once the agent attributes its action to plan step 1, the next turn's hint points past it
    assert "next — plan 2/2: confirm the result" in joined


def test_record_without_a_plan_emits_no_next_hint() -> None:
    # A fake agent with no `plan` method → no plan → no "next" hint (nothing to look ahead to).
    driver = FakeDriver([_el("a", "A")])
    msgs: list[str] = []
    record(
        driver,
        "x",
        FakeAgent(
            [Proposal(steps=[Step.model_validate({"tap": {"id": "a"}})]), Proposal(done=True)]
        ),
        report=msgs.append,
    )
    assert not any("next — plan" in m for m in msgs)


def test_record_batch_announces_and_advances_plan_cursor_past_executed_steps() -> None:
    # (B) a multi-action turn is announced as a batch; (A) the "next" hint then points past every
    # plan step the batch covered — the model labels a whole batch with one plan_step, so advancing
    # only past that one would leave the hint naming work the batch already did.
    driver = FakeDriver(
        [_field("email", "Email"), _field("password", "Password"), _el("submit", "Submit")]
    )
    msgs: list[str] = []
    agent = PlanningAgent(
        [
            Proposal(
                steps=[
                    Step.model_validate({"type": {"into": {"id": "email"}, "text": "a@b.co"}}),
                    Step.model_validate({"type": {"into": {"id": "password"}, "text": "pw"}}),
                    Step.model_validate({"tap": {"id": "submit"}}),
                ],
                plan_step=2,
            ),
            Proposal(done=True, expect=[]),
        ],
        plan_steps=["welcome", "fill email", "fill password", "submit", "confirm"],
    )
    record(driver, "x", agent, report=msgs.append)
    joined = "\n".join(msgs)
    assert "📦 batch — 3 actions from one observation" in joined  # (B) the batch is announced
    assert "next — plan 5/5: confirm" in joined  # (A) advanced past the 3 batched steps (2→4)
    assert "next — plan 3/" not in joined  # and not left naming a step the batch already did

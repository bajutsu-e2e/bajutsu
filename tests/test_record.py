"""Tests for the record loop, driven by a scripted fake agent."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from conftest import ShotDriver

from bajutsu.agent import Observation, Proposal
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.elements import shows_app_ui
from bajutsu.record import (
    _format_elapsed,
    _is_looping,
    _screenshot_bytes,
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
        return Proposal(step=Step.model_validate({"tap": {"id": "a"}}))


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
            Proposal(step=Step.model_validate({"tap": {"id": "go"}})),
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


def test_record_sets_scenario_provenance_from_goal() -> None:
    # The goal is the scenario-level `from:` provenance (BE-0044), and it round-trips.
    driver = FakeDriver([_el("go", "Go")])
    agent = FakeAgent(
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)]
    )
    scenario = record(driver, "reach the done screen", agent, name="reach")
    assert scenario.from_ == "reach the done screen"
    assert load_scenarios(dump_scenarios([scenario]))[0].from_ == "reach the done screen"


def test_record_streams_plan_and_feeds_it_to_the_agent() -> None:
    driver = FakeDriver([_el("go", "Go")])
    agent = PlanningAgent(
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)],
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
        [Proposal(step=Step.model_validate({"tap": {"id": "go"}})), Proposal(done=True)]
    )
    scenario = record(driver, "x", agent)
    assert scenario.steps and scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"


def test_record_stops_on_unresolvable_action() -> None:
    driver = FakeDriver([_el("a", "A")])
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "missing"}}))])
    scenario = record(driver, "x", agent)
    assert scenario.steps == []  # could not execute -> not recorded


def test_record_respects_max_steps() -> None:
    driver = FakeDriver([_el("a", "A")])
    scenario = record(driver, "x", LoopAgent(), max_steps=3)
    assert len(scenario.steps) == 3


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
            Proposal(step=Step.model_validate({"tap": {"id": "a"}}), note="do it", plan_step=2),
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
            Proposal(step=Step.model_validate({"tap": {"id": "a"}}), note="open the panel"),
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
        Proposal(step=Step.model_validate({"tap": {"id": "open"}})),
        Proposal(step=Step.model_validate({"tap": {"id": "close"}})),
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
            Proposal(step=Step.model_validate({"tap": {"label": "Get Started"}})),
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
            Proposal(step=Step.model_validate({"tap": {"label": "Get Started"}})),
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
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "go"}})), finish])
    scenario = record(driver, "g", agent)
    assert scenario.steps[-1].wait is not None and scenario.steps[-1].wait.for_ is not None
    assert scenario.steps[-1].wait.for_.id == "counter"


def test_no_settle_wait_for_negated_assertion() -> None:
    driver = FakeDriver([_el("go", "Go")])
    finish = Proposal(
        done=True, expect=[Assertion.model_validate({"exists": {"id": "x", "negate": True}})]
    )
    agent = FakeAgent([Proposal(step=Step.model_validate({"tap": {"id": "go"}})), finish])
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
                step=Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}})
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
                step=Step.model_validate({"type": {"text": "alice", "into": {"id": "username"}}})
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
                step=Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}}),
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
                step=Step.model_validate({"type": {"text": "hunter2", "into": {"id": "password"}}})
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

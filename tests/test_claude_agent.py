"""Tests for ClaudeAgent with an injected fake Anthropic client (no real API)."""

from __future__ import annotations

from typing import Any

from simyoke.agent import Observation
from simyoke.claude_agent import ClaudeAgent
from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.record import record
from simyoke.scenario import dump_scenarios, load_scenarios


class _Block:
    def __init__(self, name: str, inp: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _Message:
    def __init__(self, block: _Block) -> None:
        self.content = [block]


class _Messages:
    def __init__(self, messages: list[_Message], calls: list[dict[str, Any]]) -> None:
        self._messages = messages
        self._calls = calls
        self._i = 0

    def create(self, **kwargs: Any) -> _Message:
        self._calls.append(kwargs)
        message = self._messages[min(self._i, len(self._messages) - 1)]
        self._i += 1
        return message


class FakeClient:
    """Mimics anthropic.Anthropic for `client.messages.create(...)`."""

    def __init__(self, *blocks: _Block) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages([_Message(b) for b in blocks], self.calls)


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _obs(goal: str = "g") -> Observation:
    return Observation(goal=goal, screen=[_el("a", "A")], history=[])


def test_tap_proposal() -> None:
    agent = ClaudeAgent(client=FakeClient(_Block("tap", {"id": "settings.open"})))
    proposal = agent.next_action(_obs())
    assert proposal.step is not None
    assert proposal.step.tap is not None
    assert proposal.step.tap.id == "settings.open"


def test_type_text_proposal() -> None:
    agent = ClaudeAgent(client=FakeClient(_Block("type_text", {"id": "f", "text": "hi"})))
    step = agent.next_action(_obs()).step
    assert step is not None and step.type is not None
    assert step.type.text == "hi"
    assert step.type.into is not None and step.type.into.id == "f"


def test_wait_proposal() -> None:
    agent = ClaudeAgent(client=FakeClient(_Block("wait_for", {"id": "spinner", "timeout": 5})))
    step = agent.next_action(_obs()).step
    assert step is not None and step.wait is not None
    assert step.wait.for_ is not None and step.wait.for_.id == "spinner"


def test_finish_proposal_with_assertions() -> None:
    block = _Block("finish", {"assertions": [
        {"id": "home.title", "check": "exists"},
        {"id": "counter", "check": "valueEquals", "text": "3"},
        {"id": "spinner", "check": "notExists"},
    ]})
    proposal = ClaudeAgent(client=FakeClient(block)).next_action(_obs())
    assert proposal.done is True
    assert proposal.expect[0].exists is not None
    assert proposal.expect[1].value is not None and proposal.expect[1].value.equals == "3"
    assert proposal.expect[2].exists is not None and proposal.expect[2].exists.negate is True


def test_request_uses_forced_tool_choice_and_cache() -> None:
    client = FakeClient(_Block("tap", {"id": "a"}))
    ClaudeAgent(client=client, model="claude-opus-4-8").next_action(_obs())
    call = client.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["tool_choice"] == {"type": "any"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in call["tools"]} == {"tap", "type_text", "wait_for", "finish"}


def test_claude_agent_drives_record() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    client = FakeClient(
        _Block("tap", {"id": "go"}),
        _Block("finish", {"assertions": [{"id": "done", "check": "exists"}]}),
    )
    scenario = record(driver, "reach done", ClaudeAgent(client=client), name="reach")

    assert scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"
    assert scenario.expect[0].exists is not None and scenario.expect[0].exists.sel.id == "done"
    # the recorded scenario round-trips
    assert load_scenarios(dump_scenarios([scenario]))[0].name == "reach"

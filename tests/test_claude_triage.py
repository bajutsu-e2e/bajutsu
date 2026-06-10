"""Tests for ClaudeTriageAgent with an injected fake Anthropic client (no real API)."""

from __future__ import annotations

from typing import Any

from bajutsu.claude_triage import ClaudeTriageAgent, _render
from bajutsu.drivers import base
from bajutsu.triage import FailedStep, TriageContext


class _Block:
    def __init__(self, name: str, inp: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _Message:
    def __init__(self, *blocks: _Block) -> None:
        self.content = list(blocks)


class _Messages:
    def __init__(self, message: _Message, calls: list[dict[str, Any]]) -> None:
        self._message = message
        self._calls = calls

    def create(self, **kwargs: Any) -> _Message:
        self._calls.append(kwargs)
        return self._message


class FakeClient:
    """Mimics anthropic.Anthropic for `client.messages.create(...)`."""

    def __init__(self, *blocks: _Block) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(_Message(*blocks), self.calls)


def _el(identifier: str, label: str) -> base.Element:
    return {"identifier": identifier, "label": label, "traits": ["button"], "value": None,
            "frame": (0.0, 0.0, 10.0, 10.0)}


def _ctx(**over: Any) -> TriageContext:
    base_ctx = {
        "scenario": "s", "failure": "step0 tap: 一致なし",
        "failed_step": FailedStep(0, "tap", "一致なし: home.titel"),
        "failed_expectations": [], "elements": [_el("home.title", "Home")],
        "scenario_yaml": "- name: s\n  steps:\n    - tap: { id: home.titel }\n",
        "target_id": "home.titel", "evidence": ["deviceLog"],
    }
    base_ctx.update(over)
    return TriageContext(**base_ctx)


def _diagnose(category: str = "selector", summary: str = "id renamed",
              suggestions: list[str] | None = None) -> _Block:
    return _Block("diagnose", {
        "category": category, "summary": summary,
        "suggestions": suggestions if suggestions is not None else ["did you mean home.title?"],
    })


def test_diagnose_maps_to_triage() -> None:
    agent = ClaudeTriageAgent(client=FakeClient(_diagnose()))
    result = agent.triage(_ctx())
    assert result.category == "selector"
    assert result.summary == "id renamed"
    assert result.suggestions == ["did you mean home.title?"]


def test_unknown_category_is_clamped() -> None:
    agent = ClaudeTriageAgent(client=FakeClient(_diagnose(category="bogus")))
    assert agent.triage(_ctx()).category == "unknown"


def test_no_tool_call_falls_back_to_unknown() -> None:
    agent = ClaudeTriageAgent(client=FakeClient())  # message with no tool_use blocks
    result = agent.triage(_ctx())
    assert result.category == "unknown"
    assert result.suggestions == []


def test_request_uses_forced_tool_choice_and_cache() -> None:
    client = FakeClient(_diagnose())
    ClaudeTriageAgent(client=client, model="claude-opus-4-8").triage(_ctx())
    call = client.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["tool_choice"] == {"type": "any"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in call["tools"]] == ["diagnose"]
    assert call["tools"][0]["input_schema"]["properties"]["category"]["enum"] == [
        "selector", "timing", "assertion", "unknown"
    ]


def test_render_carries_the_failure_context() -> None:
    text = _render(_ctx())
    assert "Scenario: s" in text
    assert "Failed step: [0] tap — 一致なし: home.titel" in text
    assert "Target id of the failed step: home.titel" in text
    assert "id=home.title" in text                 # the real screen
    assert "Scenario definition (YAML):" in text
    assert "Evidence captured: deviceLog" in text
    assert text.rstrip().endswith("Call the `diagnose` tool exactly once.")


def test_render_handles_empty_elements_and_expectations() -> None:
    text = _render(_ctx(elements=[], failed_step=None, target_id=None,
                        failed_expectations=["value equals='2': id='counter'"]))
    assert "(no element tree captured)" in text
    assert "Failed expectations:" in text
    assert "  - value equals='2': id='counter'" in text

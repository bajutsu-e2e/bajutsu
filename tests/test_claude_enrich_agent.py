"""Tests for ClaudeEnrichmentAgent with an injected fake Anthropic client."""

from __future__ import annotations

import base64

from conftest import FakeAnthropic, FakeBlock

from bajutsu.agent import StepContext
from bajutsu.claude_enrich_agent import ClaudeEnrichmentAgent, _render_enrichment
from bajutsu.drivers import base
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact, Scenario, Step


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _scenario(steps: list[Step], name: str = "test", goal: str | None = None) -> Scenario:
    s = Scenario(name=name, steps=steps)
    if goal:
        s.from_ = goal
    return s


def _ctx(step: Step, screen: list[base.Element]) -> StepContext:
    return StepContext(step=step, screen=screen)


# ---------------------------------------------------------------------------
# Basic proposal
# ---------------------------------------------------------------------------


def test_propose_assertions_returns_enrichment_proposal() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [
                {"id": "done", "check": "exists", "intent": "done screen is shown"},
            ],
            "reason": "the scenario reaches the done screen",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps, goal="reach done")
    contexts = [_ctx(steps[0], [_el("done", "Done")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert len(proposal.expect) == 1
    assert proposal.expect[0].exists is not None
    assert proposal.expect[0].exists.sel.id == "done"
    assert proposal.expect[0].from_ == "done screen is shown"
    assert proposal.note == "the scenario reaches the done screen"


def test_propose_assertions_value_equals() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [
                {"id": "counter", "check": "valueEquals", "text": "3"},
            ],
            "reason": "counter shows 3",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "inc"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("counter", "Counter", ["staticText"])])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.expect[0].value is not None
    assert proposal.expect[0].value.equals == "3"
    assert proposal.expect[0].value.sel.id == "counter"


def test_propose_assertions_label_contains() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [
                {"label": "Count: 2", "check": "labelContains", "text": "2"},
            ],
            "reason": "count label shows 2",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "inc"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("inc", "+")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.expect[0].label is not None
    assert proposal.expect[0].label.contains == "2"


def test_propose_assertions_not_exists() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [{"id": "spinner", "check": "notExists"}],
            "reason": "loading done",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.expect[0].exists is not None
    assert proposal.expect[0].exists.negate is True


# ---------------------------------------------------------------------------
# Settle step
# ---------------------------------------------------------------------------


def test_settle_step_derived_from_first_positive_assertion() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [{"id": "result", "check": "exists"}],
            "reason": "result visible",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("result", "Result")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.settle is not None
    assert proposal.settle.wait is not None
    assert proposal.settle.wait.for_ is not None
    assert proposal.settle.wait.for_.id == "result"


def test_no_settle_for_negated_only_assertions() -> None:
    block = FakeBlock(
        "propose_assertions",
        {
            "assertions": [{"id": "spinner", "check": "notExists"}],
            "reason": "nothing positive",
        },
    )
    agent = ClaudeEnrichmentAgent(client=FakeAnthropic(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.settle is None


# ---------------------------------------------------------------------------
# API call shape
# ---------------------------------------------------------------------------


def test_request_uses_forced_tool_choice_and_cache() -> None:
    client = FakeAnthropic(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(client=client, model="claude-opus-4-8")
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    agent.propose_assertions(scenario, contexts)

    call = client.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["tool_choice"] == {"type": "tool", "name": "propose_assertions"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in call["tools"]} == {"propose_assertions"}


def test_screenshot_sent_as_image_block() -> None:
    client = FakeAnthropic(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(client=client)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    contexts = [StepContext(step=steps[0], screen=[_el("go", "Go")], screenshot=png)]

    agent.propose_assertions(scenario, contexts)

    content = client.calls[0]["messages"][0]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert len(images) == 1
    assert base64.standard_b64decode(images[0]["source"]["data"]) == png


def test_no_screenshot_is_text_only() -> None:
    client = FakeAnthropic(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(client=client)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    agent.propose_assertions(scenario, contexts)

    content = client.calls[0]["messages"][0]["content"]
    assert all(c["type"] == "text" for c in content)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_includes_goal_and_step_contexts() -> None:
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps, goal="reach done")
    contexts = [_ctx(steps[0], [_el("done", "Done", ["staticText"])])]

    text = _render_enrichment(scenario, contexts)

    assert "reach done" in text
    assert "tap" in text
    assert "done" in text.lower()


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_secret_in_elements_is_masked() -> None:
    client = FakeAnthropic(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    redactor = Redactor(Redact(labels=["password"]), values=["s3cret"])
    agent = ClaudeEnrichmentAgent(client=client, redactor=redactor)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    screen: list[base.Element] = [
        {
            "identifier": "pw",
            "label": "password",
            "traits": ["textField"],
            "value": "s3cret",
            "frame": (0.0, 0.0, 10.0, 10.0),
        }
    ]
    contexts = [StepContext(step=steps[0], screen=screen)]

    agent.propose_assertions(scenario, contexts)

    text = next(c["text"] for c in client.calls[0]["messages"][0]["content"] if c["type"] == "text")
    assert "s3cret" not in text
    assert "[REDACTED]" in text

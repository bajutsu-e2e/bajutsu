"""Tests for ClaudeEnrichmentAgent with an injected fake AI backend (BE-0104)."""

from __future__ import annotations

from conftest import FakeBackend, FakeBlock

from bajutsu.agent import StepContext
from bajutsu.ai.base import ImagePart, NamedTool, TextPart
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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps, goal="reach done")
    contexts = [_ctx(steps[0], [_el("done", "Done")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert len(proposal.expect) == 1
    assert proposal.expect[0].exists is not None
    assert proposal.expect[0].exists.sel.id == "done"
    assert proposal.expect[0].from_ == "done screen is shown"
    assert proposal.note == "the scenario reaches the done screen"


def test_output_language_is_folded_into_the_enrichment_prompt() -> None:
    # BE-0188: enrichment's generated prose (the assertion `intent` / `note`) follows `ai.language`.
    from bajutsu.anthropic_client import AiConfig

    block = FakeBlock(
        "propose_assertions",
        {"assertions": [{"id": "x", "check": "exists"}], "reason": "ok"},
    )
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps, goal="reach done")
    contexts = [_ctx(steps[0], [_el("x", "X")])]

    default = FakeBackend(block)
    ClaudeEnrichmentAgent(backend=default).propose_assertions(scenario, contexts)
    assert "日本語" not in default.requests[0].system

    ja = FakeBackend(block)
    ClaudeEnrichmentAgent(backend=ja, ai=AiConfig(language="ja")).propose_assertions(
        scenario, contexts
    )
    assert "日本語" in ja.requests[0].system


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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
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
    agent = ClaudeEnrichmentAgent(backend=FakeBackend(block))
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    proposal = agent.propose_assertions(scenario, contexts)

    assert proposal.settle is None


# ---------------------------------------------------------------------------
# API call shape
# ---------------------------------------------------------------------------


def test_request_uses_forced_tool_choice() -> None:
    backend = FakeBackend(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(backend=backend, model="claude-opus-4-8")
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    agent.propose_assertions(scenario, contexts)

    request = backend.requests[0]
    assert request.model == "claude-opus-4-8"
    assert isinstance(request.tool_choice, NamedTool)
    assert request.tool_choice.name == "propose_assertions"
    assert {t.name for t in request.tools} == {"propose_assertions"}


def test_screenshot_sent_as_image_part() -> None:
    backend = FakeBackend(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(backend=backend)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    contexts = [StepContext(step=steps[0], screen=[_el("go", "Go")], screenshot=png)]

    agent.propose_assertions(scenario, contexts)

    content = backend.requests[0].messages[0].content
    images = [c for c in content if isinstance(c, ImagePart)]
    assert len(images) == 1
    assert images[0].data == png


def test_no_screenshot_is_text_only() -> None:
    backend = FakeBackend(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    agent = ClaudeEnrichmentAgent(backend=backend)
    steps = [Step.model_validate({"tap": {"id": "go"}})]
    scenario = _scenario(steps)
    contexts = [_ctx(steps[0], [_el("go", "Go")])]

    agent.propose_assertions(scenario, contexts)

    content = backend.requests[0].messages[0].content
    assert all(isinstance(c, TextPart) for c in content)


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
    backend = FakeBackend(FakeBlock("propose_assertions", {"assertions": [], "reason": "ok"}))
    redactor = Redactor(Redact(labels=["password"]), values=["s3cret"])
    agent = ClaudeEnrichmentAgent(backend=backend, redactor=redactor)
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

    text = next(c.text for c in backend.requests[0].messages[0].content if isinstance(c, TextPart))
    assert "s3cret" not in text
    assert "[REDACTED]" in text

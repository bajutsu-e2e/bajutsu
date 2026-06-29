"""Tests for ClaudeAgent with an injected fake Anthropic client (no real API)."""

from __future__ import annotations

import base64

from conftest import FakeAnthropic, FakeBlock

from bajutsu.agent import Observation
from bajutsu.claude_agent import ClaudeAgent, proposal_from_call
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.record import record
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact, dump_scenarios, load_scenarios


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
    agent = ClaudeAgent(client=FakeAnthropic(FakeBlock("tap", {"id": "settings.open"})))
    proposal = agent.next_action(_obs())
    assert proposal.step is not None
    assert proposal.step.tap is not None
    assert proposal.step.tap.id == "settings.open"


def test_type_text_proposal() -> None:
    agent = ClaudeAgent(client=FakeAnthropic(FakeBlock("type_text", {"id": "f", "text": "hi"})))
    step = agent.next_action(_obs()).step
    assert step is not None and step.type is not None
    assert step.type.text == "hi"
    assert step.type.into is not None and step.type.into.id == "f"


def test_wait_proposal() -> None:
    agent = ClaudeAgent(
        client=FakeAnthropic(FakeBlock("wait_for", {"id": "spinner", "timeout": 5}))
    )
    step = agent.next_action(_obs()).step
    assert step is not None and step.wait is not None
    assert step.wait.for_ is not None and step.wait.for_.id == "spinner"


def test_finish_proposal_with_assertions() -> None:
    block = FakeBlock(
        "finish",
        {
            "assertions": [
                {"id": "home.title", "check": "exists"},
                {"id": "counter", "check": "valueEquals", "text": "3"},
                {"id": "spinner", "check": "notExists"},
            ]
        },
    )
    proposal = ClaudeAgent(client=FakeAnthropic(block)).next_action(_obs())
    assert proposal.done is True
    assert proposal.expect[0].exists is not None
    assert proposal.expect[1].value is not None and proposal.expect[1].value.equals == "3"
    assert proposal.expect[2].exists is not None and proposal.expect[2].exists.negate is True


def test_action_reason_becomes_step_provenance() -> None:
    # The tool's `reason` (why this action advances the goal) is the natural-language intent, so
    # it lands in the step's `from:` provenance (BE-0044).
    p = proposal_from_call("tap", {"id": "settings.open", "reason": "open settings"})
    assert p.step is not None and p.step.from_ == "open settings"


def test_assertion_intent_becomes_provenance_optional() -> None:
    p = proposal_from_call(
        "finish",
        {
            "reason": "the goal is reached",
            "assertions": [
                {"id": "title", "check": "exists", "intent": "the settings title is shown"},
                {"id": "x", "check": "exists"},  # no intent -> no provenance
            ],
        },
    )
    assert p.expect[0].from_ == "the settings title is shown"
    assert p.expect[1].from_ is None


def test_request_uses_forced_tool_choice_and_cache() -> None:
    client = FakeAnthropic(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(client=client, model="claude-opus-4-8").next_action(_obs())
    call = client.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["tool_choice"] == {"type": "any"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in call["tools"]} == {"tap", "type_text", "wait_for", "finish"}


def test_screenshot_sent_as_image_block() -> None:
    client = FakeAnthropic(FakeBlock("tap", {"id": "a"}))
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    obs = Observation(goal="g", screen=[_el("a", "A")], history=[], screenshot=png)
    ClaudeAgent(client=client).next_action(obs)
    content = client.calls[0]["messages"][0]["content"]
    image = next(c for c in content if c["type"] == "image")
    assert image["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image["source"]["data"]) == png
    assert any(c["type"] == "text" for c in content)


def test_no_screenshot_is_text_only() -> None:
    client = FakeAnthropic(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(client=client).next_action(_obs())  # no screenshot
    content = client.calls[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["text"]


def test_claude_agent_drives_record() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    client = FakeAnthropic(
        FakeBlock(
            "plan", {"steps": ["Tap Go", "Confirm Done is shown"]}
        ),  # the up-front decomposition
        FakeBlock("tap", {"id": "go"}),
        FakeBlock("finish", {"assertions": [{"id": "done", "check": "exists"}]}),
    )
    scenario = record(driver, "reach done", ClaudeAgent(client=client), name="reach")

    assert scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"
    assert scenario.expect[0].exists is not None and scenario.expect[0].exists.sel.id == "done"
    # the recorded scenario round-trips
    assert load_scenarios(dump_scenarios([scenario]))[0].name == "reach"


def test_plan_decomposes_goal_into_steps() -> None:
    client = FakeAnthropic(
        FakeBlock("plan", {"steps": ["Tap Get Started", " ", "Confirm home is shown"]})
    )
    steps = ClaudeAgent(client=client).plan("sign in")
    assert steps == ["Tap Get Started", "Confirm home is shown"]  # blanks dropped, order kept
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "plan"}  # the plan call is forced
    assert {t["name"] for t in call["tools"]} == {"plan"}


def test_plan_is_rendered_into_the_turn_prompt() -> None:
    from bajutsu.claude_agent import _render

    obs = Observation(
        goal="g", screen=[_el("a", "A")], history=[], plan=["First do X", "Then do Y"]
    )
    text = _render(obs)
    assert "Planned steps" in text and "1. First do X" in text and "2. Then do Y" in text


# --- BE-0047: the textual element tree is redacted before it reaches the model ---


def test_secret_in_element_value_is_masked_before_send() -> None:
    # A configured `redact` label and a literal secret value: both must be [REDACTED] in the text
    # block the model receives — what the model sees matches what evidence masks.
    client = FakeAnthropic(FakeBlock("tap", {"id": "a"}))
    redactor = Redactor(Redact(labels=["カード番号"]), values=["sk-secret-token"])
    screen = [
        _el("card", "カード番号"),  # value masked because its label is configured
        {
            "identifier": "tok",
            "label": "token: sk-secret-token",  # literal secret embedded in a label
            "traits": ["staticText"],
            "value": "sk-secret-token",
            "frame": (0.0, 0.0, 1.0, 1.0),
        },
    ]
    obs = Observation(goal="g", screen=screen, history=[])
    ClaudeAgent(client=client, redactor=redactor).next_action(obs)
    text = next(c["text"] for c in client.calls[0]["messages"][0]["content"] if c["type"] == "text")
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


def test_no_redactor_leaves_text_unmasked() -> None:
    client = FakeAnthropic(FakeBlock("tap", {"id": "a"}))
    screen = [_el("a", "plain-label")]
    ClaudeAgent(client=client).next_action(Observation(goal="g", screen=screen, history=[]))
    text = next(c["text"] for c in client.calls[0]["messages"][0]["content"] if c["type"] == "text")
    assert "plain-label" in text


# --- authoring against an app with no ids and no values (label / value / traits) ---


def _vel(label: str | None, traits: list[str], value: str | None = None) -> base.Element:
    """A value-/id-less element: only a label (maybe), traits, and an auto placeholder value."""
    return {
        "identifier": None,
        "label": label,
        "traits": traits,
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_tap_by_label_when_no_id() -> None:
    step = (
        ClaudeAgent(client=FakeAnthropic(FakeBlock("tap", {"label": "Get Started"})))
        .next_action(_obs())
        .step
    )
    assert step is not None and step.tap is not None
    assert step.tap.id is None and step.tap.label == "Get Started"


def test_type_into_field_by_value_and_traits() -> None:
    block = FakeBlock("type_text", {"value": "Email", "traits": ["textField"], "text": "a@b.co"})
    step = ClaudeAgent(client=FakeAnthropic(block)).next_action(_obs()).step
    assert step is not None and step.type is not None and step.type.into is not None
    assert step.type.into.value == "Email" and step.type.into.traits == ["textField"]
    assert step.type.text == "a@b.co"


def test_tap_by_traits_and_index() -> None:
    step = (
        ClaudeAgent(client=FakeAnthropic(FakeBlock("tap", {"traits": ["textField"], "index": 1})))
        .next_action(_obs())
        .step
    )
    assert step is not None and step.tap is not None
    assert step.tap.traits == ["textField"] and step.tap.index == 1


def test_finish_label_contains_for_valueless_counter() -> None:
    block = FakeBlock(
        "finish", {"assertions": [{"label": "Count: 2", "check": "labelContains", "text": "2"}]}
    )
    proposal = ClaudeAgent(client=FakeAnthropic(block)).next_action(_obs())
    assert proposal.done is True
    assert proposal.expect[0].label is not None
    assert proposal.expect[0].label.sel.label == "Count: 2"
    assert proposal.expect[0].label.contains == "2"


def test_authored_valueless_selectors_resolve_uniquely() -> None:
    """A scenario authored against a no-id/no-value screen must produce selectors that the
    deterministic driver resolves to exactly one element — the half `run` replays without AI."""
    # The home screen as idb reports it for the value-less sample2: a label-only count text
    # and a "+" button, plus two unlabeled fields distinguished only by placeholder/position.
    screen = [
        _vel("Count: 2", ["staticText"]),
        _vel("+", ["button"]),
        _vel(None, ["textField"], value="Email"),
        _vel(None, ["textField"], value="Password"),
    ]
    plus = (
        ClaudeAgent(client=FakeAnthropic(FakeBlock("tap", {"label": "+"}))).next_action(_obs()).step
    )
    email = (
        ClaudeAgent(
            client=FakeAnthropic(
                FakeBlock("type_text", {"value": "Email", "traits": ["textField"], "text": "x"})
            )
        )
        .next_action(_obs())
        .step
    )
    count = (
        ClaudeAgent(
            client=FakeAnthropic(
                FakeBlock(
                    "finish",
                    {"assertions": [{"label": "Count: 2", "check": "labelContains", "text": "2"}]},
                )
            )
        )
        .next_action(_obs())
        .expect[0]
    )

    assert plus is not None and plus.tap is not None
    assert base.resolve_unique(screen, plus.tap.as_selector())["label"] == "+"
    assert email is not None and email.type is not None and email.type.into is not None
    assert base.resolve_unique(screen, email.type.into.as_selector())["value"] == "Email"
    assert count.label is not None
    assert base.resolve_unique(screen, count.label.sel.as_selector())["label"] == "Count: 2"

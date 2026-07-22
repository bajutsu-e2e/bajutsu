"""Tests for ClaudeAgent with an injected fake AI backend (no real provider, BE-0104)."""

from __future__ import annotations

from conftest import FakeBackend, FakeBlock, FakeUsage

from bajutsu.agents.claude import TOOLS, ClaudeAgent, proposal_from_call
from bajutsu.agents.protocols import Observation
from bajutsu.ai.base import (
    AnyTool,
    ImagePart,
    MessageResponse,
    NamedTool,
    TextPart,
    ToolUseBlock,
)
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.redaction import Redactor
from bajutsu.record import record
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


def _text_of(backend: FakeBackend, call: int = 0) -> str:
    return next(
        p.text for p in backend.requests[call].messages[0].content if isinstance(p, TextPart)
    )


def test_tap_proposal() -> None:
    agent = ClaudeAgent(backend=FakeBackend(FakeBlock("tap", {"id": "settings.open"})))
    proposal = agent.next_action(_obs())
    assert proposal.step is not None
    assert proposal.step.tap is not None
    assert proposal.step.tap.id == "settings.open"


def test_type_text_proposal() -> None:
    agent = ClaudeAgent(backend=FakeBackend(FakeBlock("type_text", {"id": "f", "text": "hi"})))
    step = agent.next_action(_obs()).step
    assert step is not None and step.type is not None
    assert step.type.text == "hi"
    assert step.type.into is not None and step.type.into.id == "f"


def test_ask_human_proposal() -> None:
    # BE-0179: the ask_human tool maps to a "needs human" proposal, carrying the prompt shown to
    # the human — no step, not done.
    agent = ClaudeAgent(
        backend=FakeBackend(
            FakeBlock(
                "ask_human", {"prompt": "enter the one-time code", "reason": "cannot know it"}
            )
        )
    )
    proposal = agent.next_action(_obs())
    assert proposal.needs_human is True
    assert proposal.human_prompt == "enter the one-time code"
    assert proposal.step is None and proposal.done is False


def test_ask_human_value_fields_survive_the_live_combine_path() -> None:
    # BE-0182: the value-handoff fields must reach `record()` through the live agent path
    # (next_action → _to_proposal → _combine), not only via a direct proposal_from_call. _combine
    # must forward human_field/human_classify/human_var, or the record value branch is dead code.
    agent = ClaudeAgent(
        backend=FakeBackend(
            FakeBlock(
                "ask_human",
                {
                    "prompt": "enter the one-time code",
                    "reason": "an OTP the run cannot know",
                    "id": "login.otp",
                    "classify": "totp",
                    "name": "otp_code",
                },
            )
        )
    )
    proposal = agent.next_action(_obs())
    assert proposal.needs_human is True
    assert proposal.human_field is not None and proposal.human_field.id == "login.otp"
    assert proposal.human_classify == "totp"
    assert proposal.human_var == "otp_code"


def test_ask_human_takeover_bypass_survives_the_live_combine_path() -> None:
    # BE-0185: the takeover bypass must reach `record()` through the live agent path (next_action →
    # _to_proposal → _combine), or the record loop's bypassable-marker branch is dead code.
    agent = ClaudeAgent(
        backend=FakeBackend(
            FakeBlock(
                "ask_human",
                {
                    "prompt": "approve the Face ID prompt",
                    "reason": "a biometric prompt only a human can clear",
                    "bypass": "disable biometrics behind a test flag",
                },
            )
        )
    )
    proposal = agent.next_action(_obs())
    assert proposal.needs_human is True
    assert proposal.human_bypass == "disable biometrics behind a test flag"
    assert proposal.human_field is None  # a takeover names no field


def test_ask_human_top_level_description_teaches_the_bypass_field() -> None:
    # BE-0185: the top-level description is where ask_human teaches the paired-field pattern (it
    # already nudges `classify`/`name` for the value case). Without a parallel nudge for the takeover
    # case the model rarely populates `bypass`, leaving the takeover-bypass classification effectively
    # dead through the live-agent path — so the top-level text must name `bypass` too.
    ask_human = next(tool for tool in TOOLS if tool.name == "ask_human")
    assert "bypass" in ask_human.description


def test_system_prompt_teaches_the_bypass_field() -> None:
    # BE-0185: `SYSTEM_PROMPT` and `TOOLS` ride the same live request, and the model reads the prose
    # block first — so the paired-field nudge must live in both. The tool description names `bypass`
    # (above); the prose bullet must too, or the higher-level guidance the model reads first still
    # only teaches the value case and `bypass` stays unpopulated.
    from bajutsu.agents.claude import SYSTEM_PROMPT

    assert "bypass" in SYSTEM_PROMPT


def test_ask_human_falls_back_to_reason_when_no_prompt() -> None:
    proposal = proposal_from_call("ask_human", {"reason": "an OTP arrives out-of-band"})
    assert proposal.needs_human is True and proposal.human_prompt == "an OTP arrives out-of-band"


def test_ask_human_carries_the_value_field_and_classification() -> None:
    # BE-0182: when the agent flags the field a value goes into (and proposes how to resolve it), the
    # proposal carries the target selector, the classification, and a placeholder name so the record
    # loop can type the value live and record a deterministic placeholder step.
    proposal = proposal_from_call(
        "ask_human",
        {
            "prompt": "enter the one-time code",
            "reason": "an OTP the run cannot know",
            "id": "login.otp",
            "classify": "totp",
            "name": "otp_code",
        },
    )
    assert proposal.needs_human is True
    assert proposal.human_field is not None and proposal.human_field.id == "login.otp"
    assert proposal.human_classify == "totp"
    assert proposal.human_var == "otp_code"


def test_ask_human_ignores_an_unrecognized_classification() -> None:
    # BE-0182: classify is a proposal, not a verdict — an out-of-vocabulary value narrows to None so
    # the record loop emits the neutral "classify and resolve" TODO rather than a bogus one.
    proposal = proposal_from_call(
        "ask_human",
        {
            "prompt": "enter the code",
            "reason": "cannot know",
            "id": "login.otp",
            "classify": "bogus",
        },
    )
    assert proposal.needs_human is True
    assert proposal.human_field is not None and proposal.human_field.id == "login.otp"
    assert proposal.human_classify is None


def test_ask_human_without_a_field_stays_a_bare_handoff() -> None:
    # A handoff that names no field (a CAPTCHA, a takeover) carries no value-field details, so the
    # loop resumes by re-observing rather than recording a placeholder step (BE-0182).
    proposal = proposal_from_call("ask_human", {"prompt": "solve the CAPTCHA", "reason": "cannot"})
    assert proposal.needs_human is True
    assert proposal.human_field is None and proposal.human_classify is None


def test_plan_step_flows_through_proposal() -> None:
    assert proposal_from_call("tap", {"id": "a", "reason": "r", "plan_step": 2}).plan_step == 2
    assert (
        proposal_from_call("tap", {"id": "a", "reason": "r"}).plan_step is None
    )  # omitted -> None
    p = proposal_from_call("finish", {"assertions": [], "reason": "done", "plan_step": 4})
    assert p.done and p.plan_step == 4


def test_swipe_proposal() -> None:
    block = FakeBlock("swipe", {"id": "list", "direction": "up", "reason": "scroll to reveal it"})
    step = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs()).step
    assert step is not None and step.swipe is not None
    assert step.swipe.on is not None and step.swipe.on.id == "list"
    assert step.swipe.direction == "up"
    assert step.swipe.amount is None  # omitted → default nudge


def test_swipe_proposal_carries_amount() -> None:
    block = FakeBlock(
        "swipe", {"id": "list", "direction": "up", "amount": 0.6, "reason": "far down"}
    )
    step = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs()).step
    assert step is not None and step.swipe is not None and step.swipe.amount == 0.6


def test_wait_proposal() -> None:
    agent = ClaudeAgent(backend=FakeBackend(FakeBlock("wait_for", {"id": "spinner", "timeout": 5})))
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
    proposal = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs())
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


def test_request_uses_forced_tool_choice() -> None:
    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(backend=backend, model="claude-opus-4-8").next_action(_obs())
    request = backend.requests[0]
    assert request.model == "claude-opus-4-8"
    assert isinstance(request.tool_choice, AnyTool)  # force one tool call
    assert {t.name for t in request.tools} == {
        "tap",
        "tap_point",
        "swipe",
        "type_text",
        "wait_for",
        "finish",
        "need_screenshot",
        "ask_human",
    }


def test_effort_is_threaded_into_the_request() -> None:
    from bajutsu.agents.ai_config import AiConfig

    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(backend=backend, ai=AiConfig(effort="high")).next_action(_obs())
    assert backend.requests[0].effort == "high"


def test_output_language_is_folded_into_the_system_prompt() -> None:
    # BE-0188: `ai.language` appends a language instruction to the system prompt; `auto` (default)
    # appends nothing, so the prompt stays byte-identical (and prompt-cacheable).
    from bajutsu.agents.ai_config import AiConfig

    default = FakeBackend(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(backend=default).next_action(_obs())
    assert "日本語" not in default.requests[0].system

    ja = FakeBackend(FakeBlock("tap", {"id": "a"}))
    agent = ClaudeAgent(backend=ja, ai=AiConfig(language="ja"))
    agent.next_action(_obs())
    assert "日本語" in ja.requests[0].system
    # The plan prompt is constrained too, so the planned steps come out in the chosen language.
    ja_plan = FakeBackend(FakeBlock("plan", {"steps": ["a"]}))
    ClaudeAgent(backend=ja_plan, ai=AiConfig(language="ja")).plan("g")
    assert "日本語" in ja_plan.requests[0].system


def test_screenshot_sent_as_image_part() -> None:
    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    obs = Observation(goal="g", screen=[_el("a", "A")], history=[], screenshot=png)
    ClaudeAgent(backend=backend).next_action(obs)
    content = backend.requests[0].messages[0].content
    image = next(c for c in content if isinstance(c, ImagePart))
    assert image.data == png
    assert any(isinstance(c, TextPart) for c in content)


def test_no_screenshot_is_text_only() -> None:
    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
    ClaudeAgent(backend=backend).next_action(_obs())  # no screenshot
    content = backend.requests[0].messages[0].content
    assert [type(c) for c in content] == [TextPart]


def test_text_only_turn_with_vision_available_invites_need_screenshot() -> None:
    # BE-0192: a text-only turn in a vision-capable session nudges the agent to escalate on demand.
    from bajutsu.agents.claude import _render

    obs = Observation(
        goal="g", screen=[_el("a", "A")], history=[]
    )  # vision_available defaults True
    text = _render(obs)
    assert "Call need_screenshot only if" in text  # the on-demand invitation
    assert "No screenshot this turn" in text


def test_no_vision_session_forbids_need_screenshot() -> None:
    # BE-0192: in --no-screenshot mode (vision_available=False) the guidance must explicitly forbid
    # escalating — need_screenshot can never be satisfied and would dead-end the record. The
    # on-demand invitation must be absent; a clear prohibition must be present.
    from bajutsu.agents.claude import _render

    obs = Observation(goal="g", screen=[_el("a", "A")], history=[], vision_available=False)
    text = _render(obs)
    assert "Call need_screenshot only if" not in text  # never invited here
    assert "Do NOT" in text and "need_screenshot" in text  # explicitly forbidden
    assert "No screenshots are available this session" in text


def test_claude_agent_drives_record() -> None:
    nxt = [_el("done", "Done", ["staticText"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = nxt

    driver = FakeDriver([_el("go", "Go")], react=react)
    backend = FakeBackend(
        FakeBlock(
            "plan", {"steps": ["Tap Go", "Confirm Done is shown"]}
        ),  # the up-front decomposition
        FakeBlock("tap", {"id": "go"}),
        FakeBlock("finish", {"assertions": [{"id": "done", "check": "exists"}]}),
    )
    scenario = record(driver, "reach done", ClaudeAgent(backend=backend), name="reach")

    assert scenario.steps[0].tap is not None and scenario.steps[0].tap.id == "go"
    assert scenario.expect[0].exists is not None and scenario.expect[0].exists.sel.id == "done"
    # the recorded scenario round-trips
    assert load_scenarios(dump_scenarios([scenario]))[0].name == "reach"


def test_plan_decomposes_goal_into_steps() -> None:
    backend = FakeBackend(
        FakeBlock("plan", {"steps": ["Tap Get Started", " ", "Confirm home is shown"]})
    )
    steps = ClaudeAgent(backend=backend).plan("sign in")
    assert steps == ["Tap Get Started", "Confirm home is shown"]  # blanks dropped, order kept
    request = backend.requests[0]
    assert isinstance(request.tool_choice, NamedTool) and request.tool_choice.name == "plan"
    assert {t.name for t in request.tools} == {"plan"}
    # The plan is best-effort, so it's bounded by a short timeout (a hung CLI must not stall the run).
    from bajutsu.agents.claude import PLAN_TIMEOUT_S

    assert request.timeout_s == PLAN_TIMEOUT_S


def test_plan_is_rendered_into_the_turn_prompt() -> None:
    from bajutsu.agents.claude import _render

    obs = Observation(
        goal="g", screen=[_el("a", "A")], history=[], plan=["First do X", "Then do Y"]
    )
    text = _render(obs)
    assert "Planned steps" in text and "1. First do X" in text and "2. Then do Y" in text


# --- BE-0194 §1: lossless element-line compaction (drop empty fields, keep every addressing one) ---


def _full_el(
    identifier: str | None,
    label: str | None,
    value: str | None,
    traits: list[str],
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": traits,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _element_lines(text: str) -> list[str]:
    """The per-element `- …` lines of a rendered turn (between the header and the summary)."""
    return [line for line in text.splitlines() if line.startswith("- ")]


def test_empty_value_and_traits_are_dropped_from_the_line() -> None:
    from bajutsu.agents.claude import _render

    obs = Observation(goal="g", screen=[_full_el("a", "A", None, [])], history=[])
    line = _element_lines(_render(obs))[0]
    assert line == "- id='a' label='A'"
    assert "value=" not in line and "traits=" not in line


def test_every_addressing_field_is_kept_when_present() -> None:
    from bajutsu.agents.claude import _render

    obs = Observation(
        goal="g", screen=[_full_el("f", "Email", "you@x.co", ["textField"])], history=[]
    )
    line = _element_lines(_render(obs))[0]
    assert "id='f'" in line and "label='Email'" in line
    assert "value='you@x.co'" in line and "traits=['textField']" in line


def test_valueless_element_keeps_traits_so_it_stays_addressable() -> None:
    from bajutsu.agents.claude import _render

    # No id, no label, no value — only traits. It must still render (addressable by traits),
    # so compaction never turns it into a dropped, unaddressable element.
    obs = Observation(goal="g", screen=[_full_el(None, None, None, ["button"])], history=[])
    line = _element_lines(_render(obs))[0]
    assert line == "- traits=['button']"


# --- BE-0194 §2: a safe cap that never drops an addressable element on a pathological screen ---


def test_small_screen_never_reports_omissions() -> None:
    from bajutsu.agents.claude import _render

    screen = [_full_el("a", "A", None, ["button"]), _full_el(None, None, None, [])]
    text = _render(Observation(goal="g", screen=screen, history=[]))
    assert "omitted" not in text


def test_large_screen_collapses_only_the_non_addressable_remainder() -> None:
    from bajutsu.agents.claude import _LARGE_SCREEN_ELEMENTS, _render

    addressable = [_full_el(f"row{i}", f"Row {i}", None, ["button"]) for i in range(60)]
    decorative = [_full_el(None, None, None, []) for _ in range(5)]
    assert len(addressable) + len(decorative) > _LARGE_SCREEN_ELEMENTS
    text = _render(Observation(goal="g", screen=[*addressable, *decorative], history=[]))
    lines = _element_lines(text)
    # every addressable element is rendered in full; none is dropped
    assert sum(1 for line in lines if "id='row" in line) == 60
    # the decorative remainder is collapsed into one reported summary line, count correct
    assert "- (+5 further non-addressable elements omitted)" in lines


def test_large_screen_of_only_addressable_elements_reports_nothing() -> None:
    from bajutsu.agents.claude import _LARGE_SCREEN_ELEMENTS, _render

    screen = [
        _full_el(f"r{i}", f"R{i}", None, ["button"]) for i in range(_LARGE_SCREEN_ELEMENTS + 5)
    ]
    text = _render(Observation(goal="g", screen=screen, history=[]))
    assert "omitted" not in text  # nothing non-addressable to collapse


# --- BE-0047: the textual element tree is redacted before it reaches the model ---


def test_secret_in_element_value_is_masked_before_send() -> None:
    # A configured `redact` label and a literal secret value: both must be [REDACTED] in the text
    # part the model receives — what the model sees matches what evidence masks.
    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
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
    ClaudeAgent(backend=backend, redactor=redactor).next_action(obs)
    text = _text_of(backend)
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


def test_no_redactor_leaves_text_unmasked() -> None:
    backend = FakeBackend(FakeBlock("tap", {"id": "a"}))
    screen = [_el("a", "plain-label")]
    ClaudeAgent(backend=backend).next_action(Observation(goal="g", screen=screen, history=[]))
    text = _text_of(backend)
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
        ClaudeAgent(backend=FakeBackend(FakeBlock("tap", {"label": "Get Started"})))
        .next_action(_obs())
        .step
    )
    assert step is not None and step.tap is not None
    assert step.tap.id is None and step.tap.label == "Get Started"


def test_type_into_field_by_value_and_traits() -> None:
    block = FakeBlock("type_text", {"value": "Email", "traits": ["textField"], "text": "a@b.co"})
    step = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs()).step
    assert step is not None and step.type is not None and step.type.into is not None
    assert step.type.into.value == "Email" and step.type.into.traits == ["textField"]
    assert step.type.text == "a@b.co"


def test_tap_by_traits_and_index() -> None:
    step = (
        ClaudeAgent(backend=FakeBackend(FakeBlock("tap", {"traits": ["textField"], "index": 1})))
        .next_action(_obs())
        .step
    )
    assert step is not None and step.tap is not None
    assert step.tap.traits == ["textField"] and step.tap.index == 1


def test_finish_label_contains_for_valueless_counter() -> None:
    block = FakeBlock(
        "finish", {"assertions": [{"label": "Count: 2", "check": "labelContains", "text": "2"}]}
    )
    proposal = ClaudeAgent(backend=FakeBackend(block)).next_action(_obs())
    assert proposal.done is True
    assert proposal.expect[0].label is not None
    assert proposal.expect[0].label.sel.label == "Count: 2"
    assert proposal.expect[0].label.contains == "2"


def test_authored_valueless_selectors_resolve_uniquely() -> None:
    """A scenario authored against a no-id/no-value screen must produce selectors that the
    deterministic driver resolves to exactly one element — the half `run` replays without AI."""
    # The home screen as the accessibility tree reports it for a value-less no-id app: a label-only count text
    # and a "+" button, plus two unlabeled fields distinguished only by placeholder/position.
    screen = [
        _vel("Count: 2", ["staticText"]),
        _vel("+", ["button"]),
        _vel(None, ["textField"], value="Email"),
        _vel(None, ["textField"], value="Password"),
    ]
    plus = (
        ClaudeAgent(backend=FakeBackend(FakeBlock("tap", {"label": "+"}))).next_action(_obs()).step
    )
    email = (
        ClaudeAgent(
            backend=FakeBackend(
                FakeBlock("type_text", {"value": "Email", "traits": ["textField"], "text": "x"})
            )
        )
        .next_action(_obs())
        .step
    )
    count = (
        ClaudeAgent(
            backend=FakeBackend(
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


# --- multi-action turns: several tool-use blocks map to Proposal.steps (BE-0178) ---


class _ContentBackend:
    """A backend returning one fixed multi-block response — a single batched turn."""

    def __init__(self, *blocks: FakeBlock) -> None:
        self._content = [ToolUseBlock(name=b.name, input=b.input) for b in blocks]

    def create_message(self, request: object) -> MessageResponse:
        return MessageResponse(content=list(self._content), usage=FakeUsage())


def test_multiple_tool_blocks_map_to_ordered_steps() -> None:
    backend = _ContentBackend(
        FakeBlock("type_text", {"id": "email", "text": "a@b.co", "reason": "email"}),
        FakeBlock("type_text", {"id": "pw", "text": "x", "reason": "password"}),
        FakeBlock("tap", {"id": "submit", "reason": "sign in"}),
    )
    proposal = ClaudeAgent(backend=backend).next_action(_obs())
    assert not proposal.done and len(proposal.steps) == 3
    assert [s.type.into.id for s in proposal.steps if s.type] == ["email", "pw"]
    assert proposal.steps[2].tap is not None and proposal.steps[2].tap.id == "submit"
    # each step keeps its own reason as provenance; the turn-level note is the first action's
    assert proposal.steps[0].from_ == "email" and proposal.note == "email"


def test_finish_block_after_actions_keeps_preceding_steps() -> None:
    # finish terminates the batch (Decision 3): the action before it stays, its assertions → expect.
    backend = _ContentBackend(
        FakeBlock("tap", {"id": "go", "reason": "go"}),
        FakeBlock("finish", {"assertions": [{"id": "done", "check": "exists"}], "reason": "done"}),
    )
    proposal = ClaudeAgent(backend=backend).next_action(_obs())
    assert proposal.done is True
    assert len(proposal.steps) == 1 and proposal.steps[0].tap is not None
    assert proposal.steps[0].tap.id == "go"
    assert proposal.expect and proposal.expect[0].exists is not None


def test_need_screenshot_proposal() -> None:
    # BE-0192: the need_screenshot tool maps to an escalation proposal — no step, not done, not a
    # human handoff; the record loop re-issues the same screen with an image attached.
    proposal = proposal_from_call("need_screenshot", {"reason": "the control I need is not listed"})
    assert proposal.need_screenshot is True
    assert proposal.step is None and proposal.done is False and proposal.needs_human is False


def test_need_screenshot_carries_plan_step() -> None:
    assert proposal_from_call("need_screenshot", {"reason": "r", "plan_step": 3}).plan_step == 3


def test_need_screenshot_block_maps_to_escalation() -> None:
    # A need_screenshot tool_use from either backend flows through _to_proposal / _combine to the
    # escalation signal, carrying no steps.
    backend = _ContentBackend(FakeBlock("need_screenshot", {"reason": "must see the screen"}))
    proposal = ClaudeAgent(backend=backend).next_action(_obs())
    assert proposal.need_screenshot is True and proposal.steps == [] and proposal.done is False


def test_a_block_after_finish_is_dropped() -> None:
    # finish ends the turn: any action emitted after it in the same turn is ignored.
    backend = _ContentBackend(
        FakeBlock("finish", {"assertions": [], "reason": "done"}),
        FakeBlock("tap", {"id": "late", "reason": "too late"}),
    )
    proposal = ClaudeAgent(backend=backend).next_action(_obs())
    assert proposal.done is True and proposal.steps == []

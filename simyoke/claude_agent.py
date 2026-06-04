"""ClaudeAgent — the authoring agent backed by Claude (Anthropic SDK).

Implements the Agent protocol: given an Observation, the model is forced to call
one tool to either propose the next UI action (tap/type/wait) or finish (with the
assertions that verify the goal). The system prompt and tool definitions are static
and prompt-cached; the per-turn observation is the variable user message.

`anthropic` is lazy-imported so this module loads without an API key, and the
client is injectable for testing.
"""

from __future__ import annotations

import base64
from typing import Any

from simyoke.agent import Observation, Proposal
from simyoke.scenario import Assertion, Step

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are an iOS end-to-end test author. You drive an app on the \
iOS Simulator to accomplish a goal, then record the steps as a deterministic test.

Each turn you receive the goal, a screenshot of the current screen, and the \
screen's accessibility elements (each with a stable `id`). Use the screenshot to \
read the visual layout and any state the element list does not capture; always act \
by the `id` from the element list. You must call exactly one tool:

- tap(id): tap the element with that identifier.
- type_text(id, text): focus the field and type text into it.
- wait_for(id, timeout): wait until an element with that id appears.
- finish(assertions): the goal is reached; provide machine-checkable assertions \
that verify it, addressed by id.

Rules:
- Act only on elements by the `id` shown in the screen. Never invent ids.
- Take the most direct path to the goal. Do not repeat an action that did not \
change the screen.
- Call finish as soon as the goal is verifiably reached, with assertions that \
prove it (a result element exists, a value equals an expected string, etc.)."""

# Static tool definitions (cached together with the system prompt).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "tap",
        "description": "Tap the element with this accessibility identifier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "accessibility identifier"},
                "reason": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "type_text",
        "description": "Focus the field with this id and type the given text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["id", "text"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait until an element with this id appears, up to timeout seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "timeout": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["id", "timeout"],
        },
    },
    {
        "name": "finish",
        "description": "The goal is reached; provide the assertions that verify it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assertions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "check": {
                                "type": "string",
                                "enum": ["exists", "notExists", "valueEquals", "labelContains"],
                            },
                            "text": {
                                "type": "string",
                                "description": "expected text for valueEquals / labelContains",
                            },
                        },
                        "required": ["id", "check"],
                    },
                },
                "reason": {"type": "string"},
            },
            "required": ["assertions"],
        },
    },
]


def _render(observation: Observation) -> str:
    lines = [f"Goal: {observation.goal}", "", "Current screen elements:"]
    for element in observation.screen:
        identifier = element["identifier"]
        if not identifier:
            continue  # only surface stable, identified elements
        lines.append(
            f"- id={identifier} label={element['label']!r} "
            f"traits={element['traits']} value={element['value']!r}"
        )
    lines += ["", f"Steps taken so far: {len(observation.history)}"]
    lines.append("Call exactly one tool: tap, type_text, wait_for, or finish.")
    return "\n".join(lines)


def _to_assertion(item: Any) -> Assertion:
    identifier = item["id"]
    check = item["check"]
    text = item.get("text")
    if check == "exists":
        return Assertion.model_validate({"exists": {"id": identifier}})
    if check == "notExists":
        return Assertion.model_validate({"exists": {"id": identifier, "negate": True}})
    if check == "valueEquals":
        return Assertion.model_validate({"value": {"sel": {"id": identifier}, "equals": text}})
    if check == "labelContains":
        return Assertion.model_validate({"label": {"sel": {"id": identifier}, "contains": text}})
    raise ValueError(f"unknown assertion check: {check!r}")


def _to_proposal(message: Any) -> Proposal:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        return Proposal(done=True, note="model returned no tool call")
    name = tool_use.name
    args = tool_use.input
    note = args.get("reason", "")
    if name == "tap":
        return Proposal(step=Step.model_validate({"tap": {"id": args["id"]}}), note=note)
    if name == "type_text":
        step = {"type": {"into": {"id": args["id"]}, "text": args["text"]}}
        return Proposal(step=Step.model_validate(step), note=note)
    if name == "wait_for":
        step = {"wait": {"for": {"id": args["id"]}, "timeout": args["timeout"]}}
        return Proposal(step=Step.model_validate(step), note=note)
    if name == "finish":
        expect = [_to_assertion(a) for a in args.get("assertions", [])]
        return Proposal(done=True, expect=expect, note=note)
    raise ValueError(f"unknown tool: {name!r}")


class ClaudeAgent:
    """Agent implementation that asks Claude for the next action via tool use."""

    def __init__(self, client: Any = None, model: str = MODEL, max_tokens: int = 1024) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def next_action(self, observation: Observation) -> Proposal:
        client = self._ensure_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            tool_choice={"type": "any"},  # force one tool call; no thinking with forced choice
            messages=[{"role": "user", "content": _user_content(observation)}],
        )
        return _to_proposal(message)


def _user_content(observation: Observation) -> list[dict[str, Any]]:
    """The per-turn user message: the screenshot (if any) followed by the text."""
    content: list[dict[str, Any]] = []
    if observation.screenshot is not None:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(observation.screenshot).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": _render(observation)})
    return content

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

from bajutsu import usage
from bajutsu.agent import Observation, Proposal
from bajutsu.anthropic_client import make_client, resolve_model
from bajutsu.scenario import Assertion, Step

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are an iOS end-to-end test author. You drive an app on the \
iOS Simulator to accomplish a goal, then record the steps as a deterministic test.

Each turn you receive the goal, a screenshot of the current screen, and the \
screen's accessibility elements. Each element has a `label`, `value`, and `traits`, \
and — only if the app instrumented it — a stable `id`. Address an element by:

- `id` when it has one — non-localized and data-derived, so ALWAYS prefer it; otherwise
- any combination of `label` (exact accessibility label), `value` (exact value — e.g. \
an empty text field exposes its placeholder like "Email" as its value), and `traits` \
(e.g. ["textField"], ["button"]). These are ANDed, so combine them to pin one element \
(a text field with value "Email" → value="Email", traits=["textField"]).
- add `index` (0-based, in the listed order) only when several elements still match.

Use the screenshot to map the goal's wording to the right element when the text \
differs from it (e.g. a "+" button is the increment control). You must call exactly one tool:

- tap(id|label): tap that element.
- type_text(id|label, text): focus the field and type text into it.
- wait_for(id|label, timeout): wait until that element appears.
- finish(assertions): the goal is reached; provide machine-checkable assertions \
that verify it, each addressing an element by id or label.

Rules:
- Act only on elements present in the screen list; address them by their real `id` \
or `label`. Never invent an id or a label that is not shown.
- Take the most direct path to the goal. Do not repeat an action that did not \
change the screen.
- Always fill `reason`: one short sentence of your reasoning for THIS turn — what you \
see on the screen and why this action moves toward the goal. This is shown live to the \
person watching, so make it a clear thought, not a restatement of the action.
- Call finish only once the goal is FULLY reached. If the goal names a target \
value or count (e.g. "the count shows 2"), confirm the current screen already \
shows it before finishing; if not, keep acting. Then provide assertions that \
prove it — prefer `valueEquals` against an element's `value`, or `labelContains` \
when the number is part of the label (e.g. a "Count: 2" text)."""

PLAN_SYSTEM = """You are an iOS end-to-end test author. Before driving the app, break the \
user's goal into a short, ordered list of concrete, human-readable steps — the procedure a \
tester would follow on screen to accomplish it.

Each step is ONE plain-language action or check, in the order it happens, e.g.:
- "Tap the 'Get Started' button on the welcome screen"
- "Enter an email address into the Email field"
- "Tap 'Add to Cart' on the product"
- "Confirm the cart badge shows 2 items"

Guidance:
- Keep it concrete and minimal — 2–8 steps is typical. End with the check that confirms the goal.
- You have NOT seen the screen yet, so describe intent, not specific element ids; do not invent ids.
- This plan is shown to the person watching and guides the run, but the live screen is the source \
of truth — it is fine if the actual run deviates.

Call the `plan` tool exactly once."""

PLAN_TOOL: dict[str, Any] = {
    "name": "plan",
    "description": "Record the ordered, concrete steps to accomplish the goal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ordered concrete steps, each a short plain-language action or check",
            }
        },
        "required": ["steps"],
    },
}

# A reusable selector fragment: address an element by id (preferred), else by any combination
# of label / value / traits, with index as a last-resort disambiguator. Shared by every tool so
# id-first and label-only apps use one shape. The conditions are ANDed at resolve time.
_TARGET_PROPS: dict[str, Any] = {
    "id": {"type": "string", "description": "accessibility identifier (preferred when present)"},
    "label": {"type": "string", "description": "exact accessibility label (when there is no id)"},
    "value": {
        "type": "string",
        "description": "exact accessibility value — e.g. a text field's placeholder ('Email') "
        "while it is empty, or a status text's value",
    },
    "traits": {
        "type": "array",
        "items": {"type": "string"},
        "description": "required traits, e.g. ['button'] or ['textField'] — narrows an unlabeled element",
    },
    "index": {"type": "integer", "description": "0-based pick among elements that still match"},
}

# The agent's reasoning for the turn — required on every tool so the watcher always sees a
# thought (it is streamed live during `record`), not just the chosen action.
_REASON_PROP: dict[str, Any] = {
    "reason": {
        "type": "string",
        "description": "one short sentence of your reasoning for this turn: what you see and "
        "why this action advances the goal",
    }
}

# Static tool definitions (cached together with the system prompt).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "tap",
        "description": "Tap the element addressed by id or label.",
        "input_schema": {
            "type": "object",
            "properties": {**_TARGET_PROPS, **_REASON_PROP},
            "required": ["reason"],
        },
    },
    {
        "name": "type_text",
        "description": "Focus the field (addressed by id or label) and type the given text.",
        "input_schema": {
            "type": "object",
            "properties": {**_TARGET_PROPS, "text": {"type": "string"}, **_REASON_PROP},
            "required": ["text", "reason"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait until the element (addressed by id or label) appears, up to timeout seconds.",
        "input_schema": {
            "type": "object",
            "properties": {**_TARGET_PROPS, "timeout": {"type": "number"}, **_REASON_PROP},
            "required": ["timeout", "reason"],
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
                            **_TARGET_PROPS,
                            "check": {
                                "type": "string",
                                "enum": ["exists", "notExists", "valueEquals", "labelContains"],
                            },
                            "text": {
                                "type": "string",
                                "description": "expected text for valueEquals / labelContains",
                            },
                            "intent": {
                                "type": "string",
                                "description": "the natural-language phrase this check verifies "
                                "(recorded as the assertion's `from:` provenance)",
                            },
                        },
                        "required": ["check"],
                    },
                },
                **_REASON_PROP,
            },
            "required": ["assertions", "reason"],
        },
    },
]


def _render(observation: Observation) -> str:
    lines = [f"Goal: {observation.goal}"]
    if observation.plan:
        lines.append("")
        lines.append(
            "Planned steps (your up-front decomposition of the goal — follow it in order, "
            "adapting to what the screen actually shows):"
        )
        lines += [f"  {i}. {step}" for i, step in enumerate(observation.plan, 1)]
    lines += ["", "Current screen elements:"]
    shown = 0
    for element in observation.screen:
        identifier, label, value, traits = (
            element["identifier"],
            element["label"],
            element["value"],
            element["traits"],
        )
        if "application" in traits:
            continue  # the app root is not an actionable target
        if not (identifier or label or value or traits):
            continue  # nothing to address it by
        lines.append(f"- id={identifier!r} label={label!r} value={value!r} traits={traits}")
        shown += 1
    if not shown:
        lines.append("- (no addressable elements; the screen may still be loading)")
    lines += ["", f"Steps taken so far: {len(observation.history)}"]
    lines.append("Call exactly one tool: tap, type_text, wait_for, or finish.")
    return "\n".join(lines)


def _target(args: Any) -> dict[str, Any]:
    """A selector dict addressing one element: id when given, else any of label / value /
    traits (ANDed), with index as a last-resort disambiguator."""
    if args.get("id"):
        sel: dict[str, Any] = {"id": args["id"]}
        if args.get("index") is not None:
            sel["index"] = args["index"]
        return sel
    sel = {}
    if args.get("label") is not None:
        sel["label"] = args["label"]
    if args.get("value") is not None:
        sel["value"] = args["value"]
    if args.get("traits"):
        sel["traits"] = args["traits"]
    if not sel:
        raise ValueError("target needs an id, label, value, or traits")
    if args.get("index") is not None:
        sel["index"] = args["index"]
    return sel


def _provenance(value: str | None) -> dict[str, str]:
    """`{"from": value}` when there is a phrase to record, else `{}` — so an empty provenance is
    omitted rather than written as `from: ""` (BE-0044)."""
    return {"from": value} if value else {}


def _to_assertion(item: Any) -> Assertion:
    sel = _target(item)
    check = item["check"]
    text = item.get("text")
    # The natural-language phrase this check verifies (BE-0044 provenance) — optional.
    prov = _provenance(item.get("intent"))
    if check == "exists":
        return Assertion.model_validate({"exists": sel, **prov})
    if check == "notExists":
        return Assertion.model_validate({"exists": {**sel, "negate": True}, **prov})
    if check == "valueEquals":
        return Assertion.model_validate({"value": {"sel": sel, "equals": text}, **prov})
    if check == "labelContains":
        return Assertion.model_validate({"label": {"sel": sel, "contains": text}, **prov})
    raise ValueError(f"unknown assertion check: {check!r}")


def proposal_from_call(name: str, args: dict[str, Any]) -> Proposal:
    """Turn one tool/action call — `(name, args)` — into a Proposal. Shared by the API agent
    (a Claude tool_use block) and the Claude Code agent (a structured-output object), so both
    backends map the same action shape to the same scenario step.

    The tool's `reason` (why this action advances the goal) is the natural-language intent behind
    the action, so it is recorded as the step's `from:` provenance (BE-0044) as well as the note."""
    note = args.get("reason", "")
    prov = _provenance(note)
    if name == "tap":
        return Proposal(step=Step.model_validate({"tap": _target(args), **prov}), note=note)
    if name == "type_text":
        step = {"type": {"into": _target(args), "text": args["text"]}, **prov}
        return Proposal(step=Step.model_validate(step), note=note)
    if name == "wait_for":
        step = {"wait": {"for": _target(args), "timeout": args["timeout"]}, **prov}
        return Proposal(step=Step.model_validate(step), note=note)
    if name == "finish":
        expect = [_to_assertion(a) for a in args.get("assertions", [])]
        return Proposal(done=True, expect=expect, note=note)
    raise ValueError(f"unknown tool: {name!r}")


def steps_from_plan(raw: Any) -> list[str]:
    """Normalize a `plan` tool/structured-output result into a clean list of step strings.
    Shared by both backends so the API agent and the Claude Code agent return the same shape."""
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(step).strip() for step in raw if str(step).strip()]


def _to_proposal(message: Any) -> Proposal:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        return Proposal(done=True, note="model returned no tool call")
    return proposal_from_call(tool_use.name, tool_use.input)


class ClaudeAgent:
    """Agent implementation that asks Claude for the next action via tool use."""

    def __init__(
        self, client: Any = None, model: str | None = None, max_tokens: int = 1024
    ) -> None:
        self._client = client
        self._model = resolve_model(MODEL) if model is None else model
        self._max_tokens = max_tokens

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = make_client()
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
        usage.record(getattr(message, "usage", None))
        return _to_proposal(message)

    def plan(self, goal: str) -> list[str]:
        client = self._ensure_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": PLAN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[PLAN_TOOL],
            tool_choice={"type": "tool", "name": "plan"},  # force the plan call
            messages=[{"role": "user", "content": f"Goal: {goal}"}],
        )
        usage.record(getattr(message, "usage", None))
        block = next((b for b in message.content if b.type == "tool_use"), None)
        if block is None:
            return []
        return steps_from_plan(block.input.get("steps"))


def _user_content(observation: Observation) -> list[dict[str, Any]]:
    """The per-turn user message: the screenshot (if any) followed by the text."""
    content: list[dict[str, Any]] = []
    if observation.screenshot is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(observation.screenshot).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": _render(observation)})
    return content

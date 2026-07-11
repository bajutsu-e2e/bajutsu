"""ClaudeAgent — the authoring agent backed by Claude (Anthropic SDK).

Implements the Agent protocol: given an Observation, the model is forced to call
one tool to either propose the next UI action (tap/type/wait) or finish (with the
assertions that verify the goal). The system prompt and tool definitions are static
and prompt-cached; the per-turn observation is the variable user message.

`anthropic` is lazy-imported so this module loads without an API key, and the
client is injectable for testing.
"""

from __future__ import annotations

from typing import Any

from bajutsu import usage
from bajutsu.agent import Observation, Proposal
from bajutsu.ai import (
    AiBackend,
    AnyTool,
    ContentPart,
    ImagePart,
    Message,
    MessageRequest,
    MessageResponse,
    NamedTool,
    TextPart,
    ToolDef,
    ToolUseBlock,
    create_backend,
    resolved_provider,
)
from bajutsu.anthropic_client import (
    AiConfig,
    language_instruction,
    resolve_effort,
    resolve_model,
)
from bajutsu.redaction import Redactor
from bajutsu.scenario import Assertion, Step

MODEL = "claude-opus-4-8"

# Wall-clock cap for the best-effort up-front plan call — well above its normal few seconds, but
# short enough that an occasional CLI hang fails fast and the loop proceeds without a plan.
PLAN_TIMEOUT_S = 60.0

# Above this many on-screen elements a turn is considered pathological (a long list / data table),
# and the non-addressable remainder is reported as a count rather than silently dropped (BE-0194 §2).
# A global constant, not per-app config (prime directive 3). Addressable elements are never dropped.
_LARGE_SCREEN_ELEMENTS = 50

SYSTEM_PROMPT = """You are an iOS end-to-end test author. You drive an app on the \
iOS Simulator to accomplish a goal, then record the steps as a deterministic test.

Each turn you receive the goal and the screen's accessibility elements. A screenshot \
of the current screen MAY also be present, but not every turn: it is attached the first \
time a screen is seen and whenever the elements are too sparse to act on, and omitted on \
a screen you have already seen whose elements fully determine the action. The element \
list is ALWAYS authoritative for addressing. Each element has a `label`, `value`, and \
`traits`, and — only if the app instrumented it — a stable `id`. Address an element by:

- `id` when it has one — non-localized and data-derived, so ALWAYS prefer it; otherwise
- any combination of `label` (exact accessibility label), `value` (exact value — e.g. \
an empty text field exposes its placeholder like "Email" as its value), and `traits` \
(e.g. ["textField"], ["button"]). These are ANDed, so combine them to pin one element \
(a text field with value "Email" → value="Email", traits=["textField"]).
- add `index` (0-based, in the listed order) only when several elements still match.

When a screenshot is present, use it to map the goal's wording to the right element when \
the text differs from it (e.g. a "+" button is the increment control). Call one of these tools:

- tap(id|label): tap that element.
- tap_point(x, y): tap a screen location by NORMALIZED coordinates (0..1 from the \
top-left corner), read from the screenshot. Use this for a control you can SEE in the \
screenshot but that is NOT in the element list — a tab-bar tab, a segmented-control \
segment, a toolbar item, on an app whose accessibility tree omits it. Aim at the CENTER \
of the control's visible hit area. For a tab-bar tab, that is the center of the rectangle \
enclosing BOTH its icon and its label — not the icon alone, and not the empty strip below \
the label. Horizontally, the i-th of N equal-width tabs sits at x ≈ (i − 0.5)/N (the 3rd \
of 5 tabs → x ≈ 0.5); vertically, aim midway through the icon and label, typically \
y ≈ 0.94 in a bottom tab bar.
- swipe(id|label, direction): swipe on a visible element (up/down/left/right) to SCROLL \
a list or form. Use it to bring a control that is off-screen — neither in the element \
list nor visible in the screenshot — into view before acting on it. Set `amount` (a \
fraction of the screen, 0–1) to control how far it scrolls: a small screen-relative default, or \
0.5–0.9 to move quickly toward a control you expect to be far down. Increase it if a \
previous swipe barely moved the screen.
- type_text(id|label, text): focus the field and type text into it.
- wait_for(id|label, timeout): wait until that element appears.
- finish(assertions): the goal is reached; provide machine-checkable assertions \
that verify it, each addressing an element by id or label.
- need_screenshot(): ask to see the current screen. Use ONLY on a turn that arrived \
without a screenshot, and only when you genuinely cannot proceed from the element list \
alone — a control you need is not listed, or you must read an appearance the elements do \
not expose. The same, unchanged screen is re-shown once with a screenshot attached; do \
not call it when the elements already determine your action.
- ask_human(prompt): hand off to a human when the next step needs a value you cannot \
possibly know in a real run (a one-time password, a verification / 2FA code, a CAPTCHA) \
or an action only a human can perform. The person authoring supplies it and the recording \
resumes — you never guess.

Rules:
- Usually call ONE tool. You MAY call several action tools in one turn ONLY when each is \
determinable from the CURRENT screen without seeing the previous action's effect — e.g. fill \
several form fields, then tap Submit; the actions run in the order you give them. Do NOT batch \
when a later action depends on what an earlier one reveals (a field that only appears after a \
tap, a screen you must first navigate to): emit one action and see the next screen. If the \
screen changes partway through a batch, the remaining actions are dropped and you re-observe — \
so batching is safe, but only helps when the whole batch really is determinable up front.
- Act only on elements present in the screen list; address them by their real `id` \
or `label`. Never invent an id or a label that is not shown.
- For a control missing from the list, choose by WHERE it is: if you can see it in the \
screenshot, tap_point its center; if you cannot see it, it is off-screen — swipe to \
scroll it into view first. Never use tap_point for an element that IS listed — address \
that one by id/label, which is far more stable. Prefer these over giving up on the goal.
- NEVER type a one-time password, a verification code, or a 2FA code — even when one is \
shown on the screen. In a real run it arrives out-of-band and you cannot know it, so reading \
a test fixture's code would bake a stale value into the recording. Call ask_human for it.
- Take the most direct path to the goal. NEVER repeat an action that did not move you \
toward the goal, and never re-open a screen you just closed. If a step left you where you \
were, or you are cycling between two screens, change your approach: scroll to look \
elsewhere, or tap_point a control you can see. The recent steps you have taken are listed \
each turn — read them and do not loop.
- Always fill `reason`: one short sentence of your reasoning for THIS turn — what you \
see on the screen and why this action moves toward the goal. This is shown live to the \
person watching, so make it a clear thought, not a restatement of the action.
- When a plan is shown this turn, set `plan_step` to the number of the planned step this \
action carries out, so the watcher sees where the run is in the plan. Omit it if there is no plan.
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

PLAN_TOOL: ToolDef = ToolDef(
    name="plan",
    description="Record the ordered, concrete steps to accomplish the goal.",
    input_schema={
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
)

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

# Which planned step this action carries out — surfaced live so the watcher sees the run's place in
# the plan. Optional: omit when there is no plan (`Observation.plan` empty) or the move fits none.
_PLAN_PROP: dict[str, Any] = {
    "plan_step": {
        "type": "integer",
        "description": "the 1-based number of the planned step (from the plan shown this turn) that "
        "this action carries out; omit when there is no plan",
    }
}

# Static tool definitions (cached together with the system prompt).
TOOLS: list[ToolDef] = [
    ToolDef(
        name="tap",
        description="Tap the element addressed by id or label.",
        input_schema={
            "type": "object",
            "properties": {**_TARGET_PROPS, **_REASON_PROP, **_PLAN_PROP},
            "required": ["reason"],
        },
    ),
    ToolDef(
        name="tap_point",
        description="Tap a screen location by normalized coordinates (0..1) read from the "
        "screenshot — only for a visible control absent from the element list, e.g. a tab-bar tab.",
        input_schema={
            "type": "object",
            "properties": {
                "x": {
                    "type": "number",
                    "description": "horizontal center, a fraction of screen width (0 = left, 1 = right)",
                },
                "y": {
                    "type": "number",
                    "description": "vertical center, a fraction of screen height (0 = top, 1 = bottom)",
                },
                **_REASON_PROP,
                **_PLAN_PROP,
            },
            "required": ["x", "y", "reason"],
        },
    ),
    ToolDef(
        name="swipe",
        description="Swipe on a visible element in a direction to scroll a list/form and reveal an "
        "off-screen control (one neither in the element list nor visible in the screenshot).",
        input_schema={
            "type": "object",
            "properties": {
                **_TARGET_PROPS,
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {
                    "type": "number",
                    "description": "how far to scroll as a fraction of the screen (0-1): ~0.2 a "
                    "little, ~0.5 half a screen, ~0.9 nearly a full screen. Judge it from how far "
                    "the target likely is; omit for a small screen-relative default.",
                },
                **_REASON_PROP,
                **_PLAN_PROP,
            },
            "required": ["direction", "reason"],
        },
    ),
    ToolDef(
        name="type_text",
        description="Focus the field (addressed by id or label) and type the given text.",
        input_schema={
            "type": "object",
            "properties": {
                **_TARGET_PROPS,
                "text": {"type": "string"},
                **_REASON_PROP,
                **_PLAN_PROP,
            },
            "required": ["text", "reason"],
        },
    ),
    ToolDef(
        name="wait_for",
        description="Wait until the element (addressed by id or label) appears, up to timeout seconds.",
        input_schema={
            "type": "object",
            "properties": {
                **_TARGET_PROPS,
                "timeout": {"type": "number"},
                **_REASON_PROP,
                **_PLAN_PROP,
            },
            "required": ["timeout", "reason"],
        },
    ),
    ToolDef(
        name="finish",
        description="The goal is reached; provide the assertions that verify it.",
        input_schema={
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
                **_PLAN_PROP,
            },
            "required": ["assertions", "reason"],
        },
    ),
    ToolDef(
        name="need_screenshot",
        description="Ask to see the current screen. Call this ONLY on a turn that arrived without a "
        "screenshot, and only when you genuinely cannot proceed from the element list alone — a "
        "control you need is not listed, or you must read an appearance the elements do not expose. "
        "The same, unchanged screen is then re-shown to you once with a screenshot attached.",
        input_schema={
            "type": "object",
            "properties": {**_REASON_PROP, **_PLAN_PROP},
            "required": ["reason"],
        },
    ),
    ToolDef(
        name="ask_human",
        description="Hand off to a human: the next step needs a value you cannot possibly know in a "
        "real run (a one-time password, a verification / 2FA code, a CAPTCHA answer) or an action "
        "only a human can perform. Never guess or read such a value off the screen.",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "what the human must supply or do, in one short sentence "
                    "(e.g. 'enter the one-time verification code shown on the device')",
                },
                **_REASON_PROP,
                **_PLAN_PROP,
            },
            "required": ["prompt", "reason"],
        },
    ),
]


def _hist_hint(sel: Any) -> str:
    """The id or label to name a selector by in a recent-actions line (`?` when it has neither)."""
    return next((str(v) for v in (getattr(sel, "id", None), getattr(sel, "label", None)) if v), "?")


def _history_line(step: Step) -> str:
    """A compact summary of an already-taken step, shown back to the agent to curb repetition.

    Secrets are never echoed — typed text is omitted.
    """
    if step.tap is not None:
        return f"tap {_hist_hint(step.tap)}"
    if step.tap_point is not None:
        return f"tap point ({step.tap_point.x:.2f}, {step.tap_point.y:.2f})"
    if step.swipe is not None and step.swipe.on is not None:
        return f"swipe {step.swipe.direction} on {_hist_hint(step.swipe.on)}"
    if step.type is not None:
        return f"type into {_hist_hint(step.type.into)}"
    if step.wait is not None:
        return f"wait for {_hist_hint(step.wait.for_)}"
    return next(iter(step.model_dump(exclude_none=True)), "step")


def _render(observation: Observation, redactor: Redactor | None = None) -> str:
    lines = [f"Goal: {observation.goal}"]
    if observation.plan:
        lines.append("")
        lines.append(
            "Planned steps (your up-front decomposition of the goal — follow it in order, "
            "adapting to what the screen actually shows):"
        )
        lines += [f"  {i}. {step}" for i, step in enumerate(observation.plan, 1)]
    lines += ["", "Current screen elements:"]
    # Mask secrets in the element tree before it reaches the model (BE-0047): a configured label /
    # field value or a literal secret echoed into label/value is replaced with [REDACTED].
    screen = (
        redactor.redact_elements(observation.screen) if redactor is not None else observation.screen
    )
    shown = 0
    omitted = 0  # purely-decorative elements skipped (BE-0194 §2) — reported only past the cap
    for element in screen:
        identifier, label, value, traits = (
            element["identifier"],
            element["label"],
            element["value"],
            element["traits"],
        )
        if "application" in traits:
            continue  # the app root is not an actionable target
        if not (identifier or label or value or traits):
            omitted += 1  # nothing to address it by
            continue
        # Compact the line (BE-0194 §1): emit only the fields that carry information — every
        # addressing field (id / label / non-empty value / non-empty traits) is kept, an empty one
        # is dropped. Lossless for addressing; the element stays fully addressable.
        fields = []
        if identifier:
            fields.append(f"id={identifier!r}")
        if label:
            fields.append(f"label={label!r}")
        if value:
            fields.append(f"value={value!r}")
        if traits:
            fields.append(f"traits={traits}")
        lines.append("- " + " ".join(fields))
        shown += 1
    if not shown:
        lines.append("- (no addressable elements; the screen may still be loading)")
    elif omitted and len(screen) > _LARGE_SCREEN_ELEMENTS:
        # A pathological screen (BE-0194 §2): every addressable element above is kept, and the
        # non-addressable remainder is collapsed into a reported count rather than silently dropped,
        # so the agent knows the screen was truncated (it can swipe to reveal more).
        lines.append(f"- (+{omitted} further non-addressable elements omitted)")
    lines += ["", f"Steps taken so far: {len(observation.history)}"]
    if observation.history:
        # Show the recent actions so the agent can see whether it is looping (repeating an action or
        # cycling between screens) and change course — the single biggest cause of a stuck record.
        recent = observation.history[-6:]
        lines.append("Recent actions (most recent last) — do not repeat these fruitlessly:")
        lines += [f"  - {_history_line(s)}" for s in recent]
    if observation.screenshot is None and observation.vision_available:
        # Vision-on-demand (BE-0192): this turn carries no image, but the session can supply one on
        # request. Say so, and remind the agent it can pull the screen back with need_screenshot when
        # the elements above genuinely do not suffice.
        lines.append(
            "No screenshot this turn — the elements above are authoritative for addressing. Call "
            "need_screenshot only if you genuinely must see the screen to proceed (a control you "
            "need is not listed, or you must read an appearance the elements do not expose)."
        )
    elif observation.screenshot is None:
        # Screenshots are off for the whole session (`--no-screenshot`), so need_screenshot can never
        # be satisfied — telling the agent to escalate would dead-end the record. Direct it to act
        # from the elements alone and explicitly not to escalate (BE-0192).
        lines.append(
            "No screenshots are available this session — act from the elements above alone. Do NOT "
            "call need_screenshot; it cannot be satisfied here."
        )
    lines.append(
        "Call tap, tap_point, swipe, type_text, wait_for, or finish — one tool, or several "
        "action tools together only when all are determinable from THIS screen (see the rules)."
    )
    return "\n".join(lines)


def _target(args: Any) -> dict[str, Any]:
    """A selector dict addressing one element: id when given, else label/value/traits (ANDed), index as last resort."""
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
    """`{"from": value}` when there is a phrase to record, else `{}` so empty provenance is omitted (BE-0044)."""
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
    """Turn one tool/action call — `(name, args)` — into a Proposal.

    Shared by the API agent (a Claude tool_use block) and the Claude Code agent (a
    structured-output object), so both backends map the same action shape to the same
    scenario step.

    The tool's `reason` (why this action advances the goal) is the natural-language intent behind
    the action, so it is recorded as the step's `from:` provenance (BE-0044) as well as the note.
    """
    note = args.get("reason", "")
    prov = _provenance(note)
    raw_ps = args.get("plan_step")
    ps = raw_ps if isinstance(raw_ps, int) and not isinstance(raw_ps, bool) else None
    if name == "tap":
        step = {"tap": _target(args), **prov}
        return Proposal(steps=[Step.model_validate(step)], note=note, plan_step=ps)
    if name == "tap_point":
        point = {"tapPoint": {"x": args["x"], "y": args["y"]}, **prov}
        return Proposal(steps=[Step.model_validate(point)], note=note, plan_step=ps)
    if name == "swipe":
        spec: dict[str, Any] = {"on": _target(args), "direction": args["direction"]}
        if args.get("amount") is not None:
            spec["amount"] = args["amount"]
        return Proposal(
            steps=[Step.model_validate({"swipe": spec, **prov})], note=note, plan_step=ps
        )
    if name == "type_text":
        step = {"type": {"into": _target(args), "text": args["text"]}, **prov}
        return Proposal(steps=[Step.model_validate(step)], note=note, plan_step=ps)
    if name == "wait_for":
        step = {"wait": {"for": _target(args), "timeout": args["timeout"]}, **prov}
        return Proposal(steps=[Step.model_validate(step)], note=note, plan_step=ps)
    if name == "finish":
        expect = [_to_assertion(a) for a in args.get("assertions", [])]
        return Proposal(done=True, expect=expect, note=note, plan_step=ps)
    if name == "ask_human":
        # A "needs human" turn (BE-0179): the loop hands off to a human and resumes by re-observing.
        return Proposal(
            needs_human=True, human_prompt=args.get("prompt") or note, note=note, plan_step=ps
        )
    if name == "need_screenshot":
        # An escalation (BE-0192): on a text-only turn the agent asks to see the screen. The loop
        # re-issues the same observation once with a screenshot attached — no step, not done.
        return Proposal(need_screenshot=True, note=note, plan_step=ps)
    raise ValueError(f"unknown tool: {name!r}")


def steps_from_plan(raw: Any) -> list[str]:
    """Normalize a `plan` tool/structured-output result into a clean list of step strings.

    Shared by both backends so the API agent and the Claude Code agent return the same shape.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(step).strip() for step in raw if str(step).strip()]


def _combine(subs: list[Proposal]) -> Proposal:
    """Fold the per-action proposals of one turn into a single batch proposal (BE-0178).

    Actions are collected in order; a `finish` terminates the batch (Decision 3) — the actions
    before it stay in `steps`, and its assertions become the batch's `expect`. A `needs_human`
    (BE-0179) likewise terminates the batch, carrying its `human_prompt` so the loop can hand off.
    Turn-level `note` and `plan_step` are taken from the first action (each step also carries its
    own `from_` reason).
    """
    steps: list[Step] = []
    note, plan_step = subs[0].note, subs[0].plan_step
    for sub in subs:
        if sub.need_screenshot:
            # An escalation (BE-0192) discards the turn's actions: the agent wants to see the screen
            # before committing to any action, so nothing is executed this turn — the loop re-issues
            # with the image and the agent re-decides. Returning `steps=[]` (not the accumulated
            # steps) makes that honest, so a stray `[tap, need_screenshot]` batch never executes the
            # tap on a turn the escalation cannot re-issue (e.g. a screenshot was already attached).
            return Proposal(steps=[], need_screenshot=True, note=note, plan_step=plan_step)
        if sub.needs_human:
            return Proposal(
                steps=steps,
                needs_human=True,
                human_prompt=sub.human_prompt,
                note=note,
                plan_step=plan_step,
            )
        if sub.done:
            return Proposal(
                steps=steps, done=True, expect=sub.expect, note=note, plan_step=plan_step
            )
        steps.extend(sub.steps)
    return Proposal(steps=steps, note=note, plan_step=plan_step)


def _to_proposal(response: MessageResponse) -> Proposal:
    # Map every tool-use block in the turn to a step, in order (BE-0178) — the agent may emit
    # several actions determinable from the current screen. A turn with no tool call is done.
    subs = [
        proposal_from_call(b.name, b.input) for b in response.content if isinstance(b, ToolUseBlock)
    ]
    if not subs:
        return Proposal(done=True, note="model returned no tool call")
    return _combine(subs)


class ClaudeAgent:
    """Agent implementation that asks Claude for the next action via tool use."""

    def __init__(
        self,
        backend: AiBackend | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        *,
        ai: AiConfig | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._backend = backend
        self._ai = ai
        self._redactor = redactor
        self._model = resolve_model(MODEL, ai) if model is None else model
        self._effort = resolve_effort(ai)  # passed to backends that support it (claude-code)
        # Output-language suffix (BE-0188), empty for `auto`. Folded onto the static system prompts
        # below so the reasoning/plan prose comes out in the chosen language.
        self._lang = language_instruction(ai)
        self._max_tokens = max_tokens

    def _ensure_backend(self) -> AiBackend:
        if self._backend is None:
            self._backend = create_backend(ai=self._ai)
        return self._backend

    def next_action(self, observation: Observation) -> Proposal:
        # Force one tool call; no thinking with forced choice.
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=SYSTEM_PROMPT + self._lang,
                messages=[Message(role="user", content=_user_content(observation, self._redactor))],
                tools=TOOLS,
                tool_choice=AnyTool(),
                model=self._model,
                max_tokens=self._max_tokens,
                effort=self._effort,
            )
        )
        usage.record(
            response.usage,
            usage.CATEGORY_ACTION,
            provider=resolved_provider(self._ai),
            model=self._model,
        )
        return _to_proposal(response)

    def plan(self, goal: str) -> list[str]:
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=PLAN_SYSTEM + self._lang,
                messages=[Message(role="user", content=[TextPart(text=f"Goal: {goal}")])],
                tools=[PLAN_TOOL],
                tool_choice=NamedTool(name="plan"),  # force the plan call
                model=self._model,
                max_tokens=self._max_tokens,
                effort=self._effort,
                # The plan is best-effort (the loop proceeds without it), so bound it: a hung CLI
                # fails fast here instead of stalling the run at "thinking about how to approach…".
                timeout_s=PLAN_TIMEOUT_S,
            )
        )
        usage.record(
            response.usage,
            usage.CATEGORY_PLAN,
            provider=resolved_provider(self._ai),
            model=self._model,
        )
        block = response.first_tool_use()
        if block is None:
            return []
        return steps_from_plan(block.input.get("steps"))


def _user_content(observation: Observation, redactor: Redactor | None = None) -> list[ContentPart]:
    """The per-turn user message: the screenshot (if any) followed by the redacted text.

    The screenshot is sent as-is — images cannot be pixel-masked; the textual element tree is
    redacted via `redactor` (BE-0047). Both reach only the user-configured provider/endpoint.
    """
    content: list[ContentPart] = []
    if observation.screenshot is not None:
        content.append(ImagePart(data=observation.screenshot))
    content.append(TextPart(text=_render(observation, redactor)))
    return content

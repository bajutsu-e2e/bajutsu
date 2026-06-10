"""ClaudeTriageAgent — a Claude-backed diagnosis behind the TriageAgent protocol.

Same boundary as the rule-based `HeuristicTriageAgent`: triage is **advisory**. Given a
`TriageContext` (the failure, the failed step, the a11y element tree nearest the failure, and
the scenario definition), the model is forced to call one tool that returns a structured
`Triage` (summary + category + minimal suggestions). It reasons over the same evidence the
heuristic sees, just without hand-written rules.

`anthropic` is lazy-imported so this module loads without an API key, and the client is
injectable for testing — mirroring `claude_agent.ClaudeAgent`.
"""

from __future__ import annotations

import base64
from typing import Any

from bajutsu.triage import Triage, TriageContext

MODEL = "claude-opus-4-8"

_CATEGORIES = ("selector", "timing", "assertion", "unknown")

SYSTEM_PROMPT = """You are an iOS end-to-end test triage assistant. A deterministic test \
scenario ran against an app on the iOS Simulator and a step or expectation failed. Explain \
the ROOT CAUSE of the failure and propose the minimal fix a human should apply.

You are advisory only — you never decide pass/fail, you diagnose and suggest. Reason strictly \
from the evidence given: the failure message, the failed step, the accessibility element tree \
captured nearest the failure, a screenshot of that screen when one is attached, and the \
scenario definition. Use the screenshot for visual state the element tree omits (what screen \
is actually shown, a blocking overlay, an empty/loading state). Never invent element ids.

Call the `diagnose` tool exactly once with:
- category, one of:
  - selector: the step's target id could not be resolved (absent from the screen, or it \
matched more than one element). If the target id is missing but a similar id IS on the \
captured screen, the id was likely renamed — say "did you mean <id>?".
  - timing: a wait/condition was not met before its timeout, or an assertion raced ahead of \
asynchronous UI — the element is reachable but not present yet.
  - assertion: the screen was reached but an expectation about its state did not hold.
  - unknown: the evidence does not support any of the above.
- summary: one or two sentences naming the concrete root cause.
- suggestions: concrete, minimal edits (a renamed id, `within` / `index` to disambiguate a \
selector, a longer timeout or an explicit wait, a corrected expected value). Prefer the \
smallest change that makes the scenario deterministic again."""

# Static tool definition (cached together with the system prompt). Its shape mirrors the
# `Triage` dataclass so the tool input maps straight back to it.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "diagnose",
        "description": "Report the root-cause diagnosis of the failed scenario and the minimal fixes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "one or two sentences naming the concrete root cause",
                },
                "category": {"type": "string", "enum": list(_CATEGORIES)},
                "suggestions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concrete, minimal fixes a human can apply",
                },
            },
            "required": ["summary", "category", "suggestions"],
        },
    },
]


def _render(context: TriageContext) -> str:
    """The user message: the failure context, laid out for the model to reason over."""
    lines = [
        f"Scenario: {context.scenario}",
        f"Failure: {context.failure or '(none reported)'}",
    ]
    if context.failed_step is not None:
        fs = context.failed_step
        lines.append(f"Failed step: [{fs.index}] {fs.action} — {fs.reason}")
    if context.target_id:
        lines.append(f"Target id of the failed step: {context.target_id}")
    if context.failed_expectations:
        lines.append("Failed expectations:")
        lines += [f"  - {e}" for e in context.failed_expectations]

    lines += ["", "Accessibility elements captured nearest the failure:"]
    if context.elements:
        for e in context.elements:
            lines.append(
                f"- id={e.get('identifier') or ''} label={e.get('label')!r} "
                f"traits={e.get('traits')} value={e.get('value')!r}"
            )
    else:
        lines.append("(no element tree captured)")

    if context.scenario_yaml:
        lines += ["", "Scenario definition (YAML):", context.scenario_yaml.rstrip()]
    if context.evidence:
        lines += ["", f"Evidence captured: {', '.join(context.evidence)}"]
    if context.screenshot is not None:
        lines += ["", "A screenshot of the screen at the failure is attached above."]
    lines += ["", "Call the `diagnose` tool exactly once."]
    return "\n".join(lines)


def _user_content(context: TriageContext) -> list[dict[str, Any]]:
    """The user message: the failure screenshot (if any) followed by the text context."""
    content: list[dict[str, Any]] = []
    if context.screenshot is not None:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(context.screenshot).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": _render(context)})
    return content


def _to_triage(message: Any) -> Triage:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        return Triage("Claude returned no diagnosis.", "unknown", [])
    args = tool_use.input
    category = str(args.get("category", "unknown"))
    if category not in _CATEGORIES:
        category = "unknown"
    suggestions = [str(s) for s in (args.get("suggestions") or [])]
    return Triage(str(args.get("summary", "")), category, suggestions)


class ClaudeTriageAgent:
    """TriageAgent implementation that asks Claude for the diagnosis via forced tool use."""

    def __init__(self, client: Any = None, model: str = MODEL, max_tokens: int = 1024) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def triage(self, context: TriageContext) -> Triage:
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
            tool_choice={"type": "any"},  # force the one diagnose call; no thinking with forced choice
            messages=[{"role": "user", "content": _user_content(context)}],
        )
        return _to_triage(message)

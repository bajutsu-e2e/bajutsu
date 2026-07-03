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

from bajutsu import usage
from bajutsu.anthropic_client import AiConfig, ensure_client, resolve_model
from bajutsu.redaction import Redactor
from bajutsu.triage import FIX_KINDS, Fix, Triage, TriageContext, fix_summary

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
smallest change that makes the scenario deterministic again.
- fix (an automatically-applicable edit; include ONLY when you are confident, else omit). \
`find` MUST be an exact substring of the scenario definition shown below, and `replace` is \
what it becomes:
  - renameId: a misspelled/renamed selector id whose correct id is visible on screen. \
find = the id the scenario uses now, replace = the correct id.
  - addIndex: an ambiguous selector that matched several elements. find = the exact selector \
fragment of the failing step (e.g. `{ id: row.cell }`), replace = the same fragment with \
`index:` (or `within:`) added to pick one.
  - raiseTimeout: a wait that timed out though the element was reachable. find = the exact \
`timeout: N` fragment of the failing wait, replace = it with a larger number.
Omit `fix` for assertion failures, or whenever you cannot name an exact `find` fragment."""

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
                "fix": {
                    "type": "object",
                    "description": "an automatically-applicable edit; `find` MUST be an exact "
                    "substring of the scenario definition shown",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": list(FIX_KINDS),
                            "description": "renameId (misspelled/renamed id), addIndex "
                            "(disambiguate an ambiguous match), raiseTimeout (lengthen a wait)",
                        },
                        "find": {
                            "type": "string",
                            "description": "exact text in the scenario to replace",
                        },
                        "replace": {"type": "string", "description": "the replacement text"},
                    },
                    "required": ["kind", "find", "replace"],
                },
            },
            "required": ["summary", "category", "suggestions"],
        },
    },
]


def _render(context: TriageContext, redactor: Redactor | None = None) -> str:
    """The user message: the failure context, laid out for the model to reason over.

    Every textual field that could carry a secret — the failure message, the failed step's action
    and reason, the failed expectations, the element tree, and the scenario YAML — is masked via
    `redactor` before it reaches the model (BE-0047). The screenshot (sent in `_user_content`)
    cannot be.
    """
    scrub = redactor.redact_text if redactor is not None else (lambda t: t)
    lines = [
        f"Scenario: {context.scenario}",
        f"Failure: {scrub(context.failure) or '(none reported)'}",
    ]
    if context.failed_step is not None:
        fs = context.failed_step
        # `action` comes from the manifest and can embed typed text (e.g. a password), so scrub it
        # too — not just `reason` (BE-0047).
        lines.append(f"Failed step: [{fs.index}] {scrub(fs.action)} — {scrub(fs.reason)}")
    if context.target_id:
        lines.append(f"Target id of the failed step: {context.target_id}")
    if context.failed_expectations:
        lines.append("Failed expectations:")
        lines += [f"  - {scrub(e)}" for e in context.failed_expectations]

    lines += ["", "Accessibility elements captured nearest the failure:"]
    elements = (
        redactor.redact_elements(context.elements) if redactor is not None else context.elements
    )
    if elements:
        for e in elements:
            lines.append(
                f"- id={e.get('identifier') or ''} label={e.get('label')!r} "
                f"traits={e.get('traits')} value={e.get('value')!r}"
            )
    else:
        lines.append("(no element tree captured)")

    if context.scenario_yaml:
        lines += ["", "Scenario definition (YAML):", scrub(context.scenario_yaml).rstrip()]
    if context.evidence:
        lines += ["", f"Evidence captured: {', '.join(context.evidence)}"]
    if context.screenshot is not None:
        lines += ["", "A screenshot of the screen at the failure is attached above."]
    lines += ["", "Call the `diagnose` tool exactly once."]
    return "\n".join(lines)


def _user_content(context: TriageContext, redactor: Redactor | None = None) -> list[dict[str, Any]]:
    """The user message: the failure screenshot (if any) followed by the redacted text context."""
    content: list[dict[str, Any]] = []
    if context.screenshot is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(context.screenshot).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": _render(context, redactor)})
    return content


def _parse_fix(raw: Any) -> Fix | None:
    """Accept a model-proposed fix only if it is a well-formed, non-trivial find/replace."""
    if not isinstance(raw, dict):
        return None
    kind, find, replace = raw.get("kind"), raw.get("find"), raw.get("replace")
    if kind not in FIX_KINDS or not isinstance(find, str) or not isinstance(replace, str):
        return None
    if not find or not replace or find == replace:
        return None
    return Fix(kind, fix_summary(kind, find, replace), find, replace)


def _to_triage(message: Any) -> Triage:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        return Triage("Claude returned no diagnosis.", "unknown", [])
    args = tool_use.input
    category = str(args.get("category", "unknown"))
    if category not in _CATEGORIES:
        category = "unknown"
    suggestions = [str(s) for s in (args.get("suggestions") or [])]
    return Triage(
        str(args.get("summary", "")), category, suggestions, fix=_parse_fix(args.get("fix"))
    )


class ClaudeTriageAgent:
    """TriageAgent implementation that asks Claude for the diagnosis via forced tool use."""

    def __init__(
        self,
        client: Any = None,
        model: str | None = None,
        max_tokens: int = 1024,
        *,
        ai: AiConfig | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._client = client
        self._ai = ai
        self._redactor = redactor
        self._model = resolve_model(MODEL, ai) if model is None else model
        self._max_tokens = max_tokens

    def _ensure_client(self) -> Any:
        return ensure_client(self)

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
            tool_choice={
                "type": "any"
            },  # force the one diagnose call; no thinking with forced choice
            messages=[{"role": "user", "content": _user_content(context, self._redactor)}],
        )
        usage.record(getattr(message, "usage", None))
        return _to_triage(message)

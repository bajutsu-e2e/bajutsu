"""ClaudeEnrichmentAgent — propose assertions for an existing scenario (BE-0014).

Given a scenario whose steps have been replayed, ask Claude to observe the screen
after each step and propose the verifying assertions the scenario lacks. The system
prompt and tool definition are static and prompt-cached; the per-call content is the
scenario definition plus the per-step screen states.

`anthropic` is lazy-imported so this module loads without an API key, and the
client is injectable for testing.
"""

from __future__ import annotations

from typing import Any

from bajutsu.agent_protocols import EnrichmentProposal, StepContext
from bajutsu.ai import (
    AiBackend,
    ContentPart,
    ImagePart,
    Message,
    MessageRequest,
    NamedTool,
    TextPart,
    ToolDef,
)
from bajutsu.ai.prompts import render_elements
from bajutsu.ai_config import AiConfig, language_instruction
from bajutsu.claude_agent import _TARGET_PROPS, _to_assertion
from bajutsu.claude_backed_agent import ClaudeBackedAgent
from bajutsu.record import describe_step, settle_step
from bajutsu.redaction import Redactor
from bajutsu.scenario import Scenario

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are an iOS end-to-end test enrichment agent. You receive a scenario whose \
steps have already been replayed on a device, together with the accessibility \
element tree (and optionally a screenshot) captured after each step.

Your job is to propose the machine-checkable assertions that verify the scenario \
reached the correct state. The assertions are scenario-level: they check the \
screen AFTER ALL steps have executed.

Focus on the final screen state — the elements visible after the last step — \
and propose assertions that confirm the goal was achieved. Address elements by:

- `id` when it has one (preferred — non-localized and stable); otherwise
- any combination of `label` (exact), `value` (exact), and `traits` (e.g. \
["textField"]). These are ANDed.
- add `index` (0-based) only when several elements still match.

Available assertion checks:
- exists: the element is present on screen
- notExists: the element is NOT present
- valueEquals: the element's value equals the given text
- labelContains: the element's label contains the given text

Guidelines:
- Propose 1–5 assertions that meaningfully verify the scenario's outcome.
- Prefer specific checks (valueEquals, labelContains) over bare exists when \
the value or label carries the proof.
- Fill `intent` with a short natural-language phrase describing what each \
assertion verifies.
- Fill `reason` with a brief explanation of why these assertions together \
prove the scenario succeeded.
- Only reference elements that actually appear in the final screen state.

Call the propose_assertions tool exactly once."""

ENRICH_TOOL: ToolDef = ToolDef(
    name="propose_assertions",
    description="Propose machine-checkable assertions for this scenario.",
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
                            "enum": [
                                "exists",
                                "notExists",
                                "valueEquals",
                                "labelContains",
                            ],
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
            "reason": {
                "type": "string",
                "description": "brief explanation of what the scenario accomplishes "
                "and why these assertions verify it",
            },
        },
        "required": ["assertions", "reason"],
    },
)


def _render_elements(screen: list[Any], redactor: Redactor | None = None) -> list[str]:
    elements = redactor.redact_elements(screen) if redactor is not None else screen
    # Verbose view (every field, empties included) so the enrichment model sees the full shape of
    # each element; indented one level to sit under its per-step "Screen after this step:" header.
    lines = [f"  {line}" for line in render_elements(elements, compact=False)]
    return lines or ["  - (no addressable elements)"]


def _render_enrichment(
    scenario: Scenario,
    step_contexts: list[StepContext],
    redactor: Redactor | None = None,
) -> str:
    lines: list[str] = []
    if scenario.from_:
        lines.append(f"Scenario goal: {scenario.from_}")
    lines.append(f"Scenario: {scenario.name}")
    lines.append(f"Total steps: {len(scenario.steps)}")
    lines.append("")

    for i, ctx in enumerate(step_contexts, 1):
        lines.append(f"--- Step {i}/{len(step_contexts)}: {describe_step(ctx.step)} ---")
        lines.append("Screen after this step:")
        lines.extend(_render_elements(ctx.screen, redactor))
        lines.append("")

    lines.append("Propose assertions that verify this scenario reached the correct final state.")
    lines.append("Call the propose_assertions tool exactly once.")
    return "\n".join(lines)


def _user_content(
    scenario: Scenario,
    step_contexts: list[StepContext],
    redactor: Redactor | None = None,
) -> list[ContentPart]:
    content: list[ContentPart] = [
        ImagePart(data=ctx.screenshot) for ctx in step_contexts if ctx.screenshot is not None
    ]
    content.append(TextPart(text=_render_enrichment(scenario, step_contexts, redactor)))
    return content


class ClaudeEnrichmentAgent(ClaudeBackedAgent):
    """EnrichmentAgent implementation backed by Claude (Anthropic SDK)."""

    def __init__(
        self,
        backend: AiBackend | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        *,
        ai: AiConfig | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        super().__init__(
            backend=backend, ai=ai, default_model=MODEL, model=model, redactor=redactor
        )
        self._lang = language_instruction(ai)  # output-language suffix, empty for `auto` (BE-0188)
        self._max_tokens = max_tokens

    def propose_assertions(
        self,
        scenario: Scenario,
        step_contexts: list[StepContext],
    ) -> EnrichmentProposal:
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=SYSTEM_PROMPT + self._lang,
                messages=[
                    Message(
                        role="user",
                        content=_user_content(scenario, step_contexts, self._redactor),
                    )
                ],
                tools=[ENRICH_TOOL],
                tool_choice=NamedTool(name="propose_assertions"),
                model=self._model,
                max_tokens=self._max_tokens,
            )
        )
        self._record_usage(response)

        block = response.first_tool_use()
        if block is None:
            return EnrichmentProposal(note="model returned no tool call")

        args = block.input
        expect = [_to_assertion(a) for a in args.get("assertions", [])]
        settle = settle_step(expect)
        note = args.get("reason", "")
        return EnrichmentProposal(expect=expect, settle=settle, note=note)

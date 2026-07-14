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

from collections.abc import Sequence
from typing import Any

from bajutsu import usage
from bajutsu.ai import (
    AiBackend,
    AnyTool,
    ContentPart,
    ImagePart,
    Message,
    MessageRequest,
    MessageResponse,
    TextPart,
    ToolDef,
    resolved_provider,
)
from bajutsu.ai.prompts import NEVER_JUDGE_BOUNDARY, render_elements
from bajutsu.ai_config import AiConfig
from bajutsu.claude_backed_agent import ClaudeBackedAgent
from bajutsu.redaction import Redactor
from bajutsu.triage import (
    FIX_KINDS,
    CrossRunTriageContext,
    Fix,
    RunEvidence,
    Triage,
    TriageContext,
    fix_summary,
)

MODEL = "claude-opus-4-8"

_CATEGORIES = ("selector", "timing", "assertion", "unknown")

SYSTEM_PROMPT = f"""You are an iOS end-to-end test triage assistant. A deterministic test \
scenario ran against an app on the iOS Simulator and a step or expectation failed. Explain \
the ROOT CAUSE of the failure and propose the minimal fix a human should apply.

You are advisory only — you diagnose and suggest. {NEVER_JUDGE_BOUNDARY} Reason strictly \
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
fragment of the failing step (e.g. `{{ id: row.cell }}`), replace = the same fragment with \
`index:` (or `within:`) added to pick one.
  - raiseTimeout: a wait that timed out though the element was reachable. find = the exact \
`timeout: N` fragment of the failing wait, replace = it with a larger number.
Omit `fix` for assertion failures, or whenever you cannot name an exact `find` fragment."""

# Static tool definition (cached together with the system prompt). Its shape mirrors the
# `Triage` dataclass so the tool input maps straight back to it.
TOOLS: list[ToolDef] = [
    ToolDef(
        name="diagnose",
        description="Report the root-cause diagnosis of the failed scenario and the minimal fixes.",
        input_schema={
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
    ),
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
    # Key the fallback off `elements` itself, not the filtered result: a captured tree that renders
    # to nothing (app-root-only, a blank/loading screen) is a different root cause than a failed
    # capture, and a triage assistant must not conflate the two. When the tree is present but every
    # element is filtered out, say so explicitly rather than leave the section blank.
    if elements:
        body = render_elements(elements, compact=False)
        lines += body or [
            "(no addressable elements; only the app root or empty elements were captured)"
        ]
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


def _user_content(context: TriageContext, redactor: Redactor | None = None) -> list[ContentPart]:
    """The user message: the failure screenshot (if any) followed by the redacted text context."""
    content: list[ContentPart] = []
    if context.screenshot is not None:
        content.append(ImagePart(data=context.screenshot))
    content.append(TextPart(text=_render(context, redactor)))
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


def _to_triage(response: MessageResponse, categories: tuple[str, ...] = _CATEGORIES) -> Triage:
    tool_use = response.first_tool_use()
    if tool_use is None:
        return Triage("Claude returned no diagnosis.", "unknown", [])
    args = tool_use.input
    category = str(args.get("category", "unknown"))
    if category not in categories:
        category = "unknown"
    suggestions = [str(s) for s in (args.get("suggestions") or [])]
    return Triage(
        str(args.get("summary", "")), category, suggestions, fix=_parse_fix(args.get("fix"))
    )


def _forced_diagnose(
    backend: AiBackend,
    content: list[ContentPart],
    *,
    system: str,
    tools: list[ToolDef],
    model: str,
    max_tokens: int,
    ai: AiConfig | None,
) -> MessageResponse:
    """Run one forced `diagnose` tool call and record its usage — shared by both triage agents."""
    response = backend.create_message(
        MessageRequest(
            system=system,
            messages=[Message(role="user", content=content)],
            tools=tools,
            tool_choice=AnyTool(),
            model=model,
            max_tokens=max_tokens,
        )
    )
    usage.record(response.usage, provider=resolved_provider(ai), model=model)
    return response


class ClaudeTriageAgent(ClaudeBackedAgent):
    """TriageAgent implementation that asks Claude for the diagnosis via forced tool use."""

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
        self._max_tokens = max_tokens

    def triage(self, context: TriageContext) -> Triage:
        # Force the one diagnose call; no thinking with forced choice.
        response = _forced_diagnose(
            self._ensure_backend(),
            _user_content(context, self._redactor),
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            model=self._model,
            max_tokens=self._max_tokens,
            ai=self._ai,
        )
        return _to_triage(response)


# --- cross-run flaky triage (BE-0220 Half 2) ---

# The intermittency root causes — a different axis from the single-run categories above. A flaky
# scenario fails not because one run went wrong but because something VARIES between its runs.
_CROSS_RUN_CATEGORIES = (
    "selector-ambiguity",  # a selector resolved to one element in some runs, several in others
    "timing",  # a wait/assertion raced asynchronous UI — it won some runs, lost others
    "network-variance",  # a backend response varied between runs (latency, payload, error)
    "state-leak",  # leftover state from a prior run changed the starting conditions
    "unknown",  # the cross-run evidence does not support any of the above
)

CROSS_RUN_SYSTEM_PROMPT = f"""You are an iOS end-to-end test flakiness investigator. One \
deterministic test scenario ran many times against an app on the iOS Simulator at a FIXED content \
fingerprint (its definition never changed), yet its verdict flips: some runs pass, some fail. \
Explain WHY it is intermittent and propose the minimal fix that makes it deterministic again.

You are advisory only — you diagnose and suggest. {NEVER_JUDGE_BOUNDARY} Reason strictly from \
the evidence given: for the failing runs and the passing runs, the failure message, the failed \
step, and the accessibility element tree captured nearest the failure (failing) or the run's end \
(passing), plus the scenario definition. The signal is the DELTA — what differs between a run that \
passed and one that failed under the same definition. Never invent element ids.

Call the `diagnose` tool exactly once with:
- category, one of:
  - selector-ambiguity: a selector resolved to exactly one element in some runs but matched \
several (or none) in others — the screen's element set varies.
  - timing: a wait or assertion raced asynchronous UI — the element/condition was reachable but \
present only after a delay that some runs beat and others did not.
  - network-variance: a backend response varied between runs (latency, payload, or an error) and \
the scenario did not wait for or tolerate the variation.
  - state-leak: state left by a previous run changed the starting conditions of the later ones.
  - unknown: the evidence does not support any of the above.
- summary: one or two sentences naming the concrete cause of the INTERMITTENCY (contrast a pass \
with a fail), not a single run's failure.
- suggestions: concrete, minimal edits that remove the non-determinism (disambiguate a selector \
with `within`/`index`, add or lengthen an explicit `wait`, wait on the varying condition). Prefer \
the smallest change that makes the scenario deterministic.
- fix (an automatically-applicable edit; include ONLY when you are confident, else omit). `find` \
MUST be an exact substring of the scenario definition shown below, and `replace` is what it \
becomes:
  - renameId: a selector id that should be corrected. find = the id now, replace = the correct id.
  - addIndex: an ambiguous selector. find = the exact selector fragment of the flaky step, \
replace = the same fragment with `index:` (or `within:`) added to pick one deterministically.
  - raiseTimeout: a wait that some runs lost. find = the exact `timeout: N` fragment, replace = \
it with a larger number.
Omit `fix` whenever you cannot name an exact `find` fragment. Do NOT weaken an assertion to make \
the test pass — never drop an `expect`, loosen a value/label match, or widen a selector past \
uniqueness."""

# Same shape as `TOOLS`, but the category enum is the cross-run set. The fix kinds are unchanged —
# BE-0220 Half 2 keeps the constrained BE-0023 fix set; a full YAML rewrite is a later unit.
CROSS_RUN_TOOLS: list[ToolDef] = [
    ToolDef(
        name="diagnose",
        description="Report the root cause of the scenario's intermittency and the minimal fix.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "one or two sentences naming the cause of the intermittency",
                },
                "category": {"type": "string", "enum": list(_CROSS_RUN_CATEGORIES)},
                "suggestions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "concrete, minimal edits that remove the non-determinism",
                },
                "fix": {
                    "type": "object",
                    "description": "an automatically-applicable edit; `find` MUST be an exact "
                    "substring of the scenario definition shown",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": list(FIX_KINDS),
                            "description": "renameId, addIndex (disambiguate), raiseTimeout",
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
    ),
]


def _representative_screenshot_run(runs: Sequence[RunEvidence]) -> RunEvidence | None:
    """The one run per group whose screenshot is actually attached (the payload is bounded to one).

    `_render_evidence` and `_cross_run_user_content` both key off this, so the note a run carries and
    the image actually sent stay in lockstep.
    """
    return next((ev for ev in runs if ev.screenshot is not None), None)


def _render_evidence(
    ev: RunEvidence, label: str, scrub: Any, redactor: Redactor | None, *, screenshot_attached: bool
) -> list[str]:
    """One run's block in the cross-run message: its verdict plus the state nearest its end/failure."""
    lines = [f"{label} {ev.run_id} ({'passed' if ev.ok else 'failed'}):"]
    if ev.failure:
        lines.append(f"  Failure: {scrub(ev.failure)}")
    if ev.failed_step is not None:
        fs = ev.failed_step
        lines.append(f"  Failed step: [{fs.index}] {scrub(fs.action)} — {scrub(fs.reason)}")
    lines += [f"  Failed expectation: {scrub(e)}" for e in ev.failed_expectations]
    elements = redactor.redact_elements(ev.elements) if redactor is not None else ev.elements
    caption = "  Elements nearest the failure:" if not ev.ok else "  Elements at the run's end:"
    lines.append(caption)
    # Same distinction as `_render`: a captured-but-empty-after-filter tree is not a failed capture,
    # and a present-but-all-filtered tree still gets an explicit line rather than a blank section.
    if elements:
        body = render_elements(elements, compact=False)
        lines += [f"    {line}" for line in body] or ["    (no addressable elements)"]
    else:
        lines.append("    (no element tree captured)")
    if screenshot_attached:
        lines.append("  A screenshot of this run's screen is attached above.")
    return lines


def _render_cross_run(context: CrossRunTriageContext, redactor: Redactor | None = None) -> str:
    """The user message: the flaky scenario's passing and failing runs, laid out to contrast.

    Every textual field that could carry a secret is masked via `redactor` before it reaches the
    model (BE-0047), exactly as the single-run `_render` does; the screenshots (sent in
    `_cross_run_user_content`) cannot be.
    """
    scrub = redactor.redact_text if redactor is not None else (lambda t: t)
    lines = [f"Scenario: {context.scenario}"]
    if context.scenario_hash:
        lines.append(f"Content fingerprint (scenarioHash): {context.scenario_hash}")
    if context.target_id:
        lines.append(f"Target id of the flaky step: {context.target_id}")
    lines.append(
        "This scenario flips verdict at one fixed fingerprint. Contrast its failing and passing "
        "runs and explain what VARIES between them."
    )
    for group, label in ((context.failing, "Failing run"), (context.passing, "Passing run")):
        shot_run = _representative_screenshot_run(group)
        for ev in group:
            attached = ev is shot_run
            lines += [
                "",
                *_render_evidence(ev, label, scrub, redactor, screenshot_attached=attached),
            ]
    if context.scenario_yaml:
        lines += ["", "Scenario definition (YAML):", scrub(context.scenario_yaml).rstrip()]
    lines += ["", "Call the `diagnose` tool exactly once."]
    return "\n".join(lines)


def _cross_run_user_content(
    context: CrossRunTriageContext, redactor: Redactor | None = None
) -> list[ContentPart]:
    """The user message: the representative failing/passing screenshots then the redacted text.

    Attaches at most one failing and one passing screenshot (the first of each) to bound the payload
    — the text block already carries every run's element tree.
    """
    content: list[ContentPart] = []
    for runs in (context.failing, context.passing):
        shot_run = _representative_screenshot_run(runs)
        if shot_run is not None and shot_run.screenshot is not None:
            content.append(ImagePart(data=shot_run.screenshot))
    content.append(TextPart(text=_render_cross_run(context, redactor)))
    return content


class ClaudeCrossRunTriageAgent(ClaudeBackedAgent):
    """CrossRunTriageAgent implementation that asks Claude to diagnose intermittency via forced tool use.

    The Half-2 counterpart to `ClaudeTriageAgent`: same forced-`diagnose` boundary and the same
    advisory `Triage` output, but it reasons over a `CrossRunTriageContext` (a flaky scenario's
    passing and failing runs) and classifies the intermittency's cause.
    """

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
        self._max_tokens = max_tokens

    def triage_flaky(self, context: CrossRunTriageContext) -> Triage:
        response = _forced_diagnose(
            self._ensure_backend(),
            _cross_run_user_content(context, self._redactor),
            system=CROSS_RUN_SYSTEM_PROMPT,
            tools=CROSS_RUN_TOOLS,
            model=self._model,
            max_tokens=self._max_tokens,
            ai=self._ai,
        )
        return _to_triage(response, _CROSS_RUN_CATEGORIES)

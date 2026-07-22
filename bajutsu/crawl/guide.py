"""AI guide for the autonomous crawl (BE-0038).

The guide proposes which replayable actions to try from a screen — taps and *realistic* text
inputs that may open a new screen or enable a disabled control whose precondition isn't obvious
(a valid email, a password that meets the rules). It only influences **what to explore**: screen
identity, transition/crash detection and the screen map stay deterministic in
[`core.py`](core.py), so the crawl is never a verdict (prime directive #1).

The model call sits behind an ``ActionProposer`` protocol, so the guide is exercised in the gate
with a scripted fake — no LLM, mirroring how `record` tests the authoring agent. The proposer's
actions are unioned with the deterministic `candidate_actions` as a safety net, so the crawl
still advances if the model proposes nothing useful.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bajutsu.agents.ai_config import AiConfig, language_instruction
from bajutsu.agents.claude_backed import ClaudeBackedAgent
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
from bajutsu.ai.prompts import NEVER_JUDGE_BOUNDARY, render_elements
from bajutsu.analytics import usage
from bajutsu.crawl import core as crawl
from bajutsu.crawl import tabs as crawl_tabs
from bajutsu.drivers import base
from bajutsu.evidence.redaction import Redactor
from bajutsu.screenshots import screenshot_bytes

MODEL = "claude-opus-4-8"

# Receives the AI's reasoning as it explores, so a watcher (the crawl log / the web UI) can see
# what the model is thinking and which operations it chose.
Report = Callable[[str], None]


@dataclass
class Proposal:
    """The proposer's output for one screen.

    The operations to try, plus `thought` — the model's short reasoning, surfaced live so a watcher
    sees what the AI is doing.
    """

    actions: list[crawl.Action] = field(default_factory=list)
    thought: str = ""
    tokens: int = 0  # tokens the model spent on this proposal (0 when unknown / no AI call)


class ActionProposer(Protocol):
    """Proposes the operations to try from a screen, given its elements, screenshot, the deterministic candidates, and any OS prompt just dismissed."""

    def propose(
        self,
        elements: list[base.Element],
        screenshot: bytes | None,
        candidates: list[crawl.Action],
        dismissed: tuple[str, ...],
    ) -> Proposal: ...


def ai_guide(
    proposer: ActionProposer,
    report: Report | None = None,
    tab_locator: crawl_tabs.TabLocator | None = None,
) -> crawl.Guide:
    """Adapt an `ActionProposer` to the crawl `Guide` signature, running the BE-0038 pipeline.

    First **inspect deterministically** (`candidate_actions`), then hand those operations + the
    screen (and any OS prompt just dismissed to reach it) to the proposer so it can reason about
    what's possible and **combine** them (realistic inputs, multi-field fills, id-less elements);
    finally union the proposal with the deterministic baseline (proposer first, so its values win
    on de-dup), narrating its reasoning via `report`.

    When `tab_locator` is set and a tab bar is present whose individual tabs the tree can't address
    (iOS surfaces a SwiftUI TabView as one "Tab Bar" group with no per-tab ids), it locates the
    tabs by vision — the same fallback the alert guard uses — and prepends a coordinate tap per tab,
    so the crawl still switches tabs first.
    """

    def guide(
        driver: base.Driver, elements: list[base.Element], context: crawl.GuideContext
    ) -> list[crawl.Action]:
        if report is not None:
            report("📸 capturing the current screen…")
        shot = screenshot_bytes(driver)
        candidates = crawl.candidate_actions(elements)  # deterministic inspection, fed to the AI
        tabs = _locate_tabs(tab_locator, elements, shot, report)
        if report is not None and context.dismissed:
            report(f"🛡️  factoring in a just-dismissed OS prompt: {', '.join(context.dismissed)}")
        if report is not None:
            report("🤖 asking Claude to choose the next operations (this waits on the model)…")
        proposal = proposer.propose(elements, shot, candidates, context.dismissed)
        if report is not None:
            spent = f" · {proposal.tokens:,} tokens" if proposal.tokens else ""
            report(f"🤖 Claude proposed {len(proposal.actions)} operation(s){spent}")
            if proposal.thought:
                report(f"🤔 {proposal.thought}")
            for a in proposal.actions:
                report(f"   → try {a.describe()}")
        # Tabs first (switch the whole view before drilling in), then the proposal, then the
        # deterministic baseline; de-dup keeps the earliest, so a vision tab beats a later duplicate.
        return _dedup([*tabs, *proposal.actions, *candidates])

    return guide


def _locate_tabs(
    tab_locator: crawl_tabs.TabLocator | None,
    elements: list[base.Element],
    shot: bytes | None,
    report: Report | None,
) -> list[crawl.Action]:
    """Vision fallback for an un-addressable tab bar: a coordinate tap per tab, only when a locator and screenshot exist and such a tab bar is present."""
    if tab_locator is None or shot is None or not crawl_tabs.needs_vision_tabs(elements):
        return []
    if report is not None:
        report("👁️  tab bar not addressable in the tree — asking Claude to locate tabs by vision…")
    targets = tab_locator.locate(shot)
    actions = [crawl.Action("tap_point", label=t.label, point=(t.x, t.y)) for t in targets]
    if report is not None and actions:
        named = ", ".join(t.label or f"({t.x:.2f},{t.y:.2f})" for t in targets)
        report(
            f"👁️  tab bar not addressable in the tree — vision found {len(actions)} tab(s): {named}"
        )
    return actions


def _dedup(actions: list[crawl.Action]) -> list[crawl.Action]:
    """Drop later duplicates by (kind, selector key) so the proposer's choice for an element wins over the deterministic baseline's."""
    seen: set[tuple[str, str]] = set()
    out: list[crawl.Action] = []
    for a in actions:
        k = (a.kind, a.key)
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


def make_guide(
    report: Report | None = None,
    *,
    ai: AiConfig | None = None,
    redactor: Redactor | None = None,
) -> crawl.Guide:
    """The AI crawl guide, narrating its reasoning through `report`.

    The guide reaches the model through the SDK-based `AiBackend` seam (BE-0104), so the resolved
    `ai` config (BE-0047) picks the provider — Anthropic API, Bedrock, or the Anthropic CLI (`ant`,
    BE-0163). `ai` and `redactor` thread the BE-0047 data-sovereignty guarantees (provider config +
    textual-input redaction) into every AI call the guide makes (BE-0097).
    """
    proposer: ActionProposer = ClaudeActionProposer(ai=ai, redactor=redactor)
    return ai_guide(proposer, report=report, tab_locator=crawl_tabs.ClaudeTabLocator(ai=ai))


# --- Claude-backed proposer ---------------------------------------------------------------

_SYSTEM = f"""You drive a breadth-first crawl of an iOS app to discover as many distinct screens \
as possible. You are given the current screen (a screenshot and its element list) and the \
operations a DETERMINISTIC inspector already found here. Reason about what is possible and \
propose the operations most likely to reveal a NEW screen or to unblock a disabled control whose \
enabling condition is not obvious.

Rules:
- Build on the inspector's operations: keep the useful ones, and **combine** them when a single \
operation isn't enough — e.g. a `fill` that enters several fields at once so a submit button \
validates, since a button can stay disabled until the whole form is valid.
- For a text field, supply a realistic value for what it asks (a valid email, a password meeting \
common rules, a plausible name/number) — this is how you enable a control the placeholder can't.
- Switch through a tab bar's tabs before drilling into a tab's own content.
- Add any operation the inspector skipped (e.g. an element with no id, addressed by `label`).
- Address each element by `id` when it has one (most stable), else by `label` (+ `index`).
- If an OS prompt was just dismissed to reach this screen (noted below), take it into account — \
the app asked for something (a permission, to save a password); pick what makes sense next.
- You only choose what to TRY. {NEVER_JUDGE_BOUNDARY}"""

_PROPOSE_TOOL: ToolDef = ToolDef(
    name="propose_actions",
    description="Propose the operations to try from this screen, most promising first.",
    input_schema={
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "one short sentence: what this screen is and why you'll try these "
                "operations (shown live to the watcher)",
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["tap", "type", "fill"]},
                        "id": {
                            "type": "string",
                            "description": "accessibility identifier (preferred)",
                        },
                        "label": {
                            "type": "string",
                            "description": "exact label when there is no id",
                        },
                        "index": {"type": "integer", "description": "0-based pick among matches"},
                        "value": {
                            "type": "string",
                            "description": "text to enter (for a type action)",
                        },
                        "fields": {
                            "type": "array",
                            "description": "for a `fill`: every field to enter at once, with a "
                            "realistic value — use this when a control activates only after several "
                            "fields are valid (e.g. email + password)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["id", "value"],
                            },
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["thought", "actions"],
    },
)


def _render_elements(elements: list[base.Element]) -> str:
    """A compact text view of the screen for the model (alongside the screenshot)."""
    return "\n".join(render_elements(elements, compact=True)) or "(no addressable elements)"


def _text_block(
    elements: list[base.Element],
    candidates: list[crawl.Action],
    dismissed: tuple[str, ...],
) -> str:
    """The textual screen description for the model: its elements, the deterministic inspector's operations, and any OS prompt just dismissed to reach it."""
    found = "\n".join(f"- {a.describe()}" for a in candidates) or "(none)"
    text = (
        f"Screen elements:\n{_render_elements(elements)}\n\n"
        f"Operations the deterministic inspector already found here:\n{found}"
    )
    if dismissed:
        text += f"\n\nAn OS prompt was just dismissed to reach this screen (tapped: {', '.join(dismissed)})."
    return text


def _content(
    elements: list[base.Element],
    screenshot: bytes | None,
    candidates: list[crawl.Action],
    dismissed: tuple[str, ...],
    redactor: Redactor | None = None,
) -> list[ContentPart]:
    content: list[ContentPart] = []
    if screenshot:
        content.append(ImagePart(data=screenshot))
    text = _text_block(elements, candidates, dismissed)
    if redactor is not None:
        text = redactor.redact_text(text)
    content.append(TextPart(text=text))
    return content


def _actions_from(payload: dict[str, Any], cap: int) -> list[crawl.Action]:
    """Turn the tool call's `actions` array into crawl Actions, skipping malformed entries and capping the count so one screen can't blow the step budget."""
    out: list[crawl.Action] = []
    for item in payload.get("actions") or []:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if action == "fill":
            pairs = tuple(
                (str(f["id"]), str(f.get("value") or ""))
                for f in (item.get("fields") or [])
                if isinstance(f, dict) and f.get("id")
            )
            if pairs:
                out.append(crawl.Action("fill", fields=pairs))
        else:
            kind = "type" if action == "type" else "tap"
            target = str(item.get("id") or "")
            label = item.get("label")
            if not target and not label:
                continue  # need a stable selector to replay
            index = item.get("index")
            out.append(
                crawl.Action(
                    kind,
                    target=target,
                    label=str(label) if label is not None else None,
                    index=int(index) if isinstance(index, int) else None,
                    value=str(item["value"]) if kind == "type" and item.get("value") else None,
                )
            )
        if len(out) >= cap:
            break
    return out


def _proposal_from(payload: dict[str, Any], cap: int) -> Proposal:
    """Build a `Proposal` (actions + the model's `thought`) from the tool call's input."""
    return Proposal(actions=_actions_from(payload, cap), thought=str(payload.get("thought") or ""))


class ClaudeActionProposer(ClaudeBackedAgent):
    """Asks Claude for the screen's candidate operations via a forced tool call.

    Talks to the model through the vendor-neutral backend (BE-0104).
    """

    def __init__(
        self,
        backend: AiBackend | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        max_actions: int = 8,
        *,
        ai: AiConfig | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        super().__init__(
            backend=backend, ai=ai, default_model=MODEL, model=model, redactor=redactor
        )
        self._lang = language_instruction(ai)  # output-language suffix, empty for `auto` (BE-0188)
        self._max_tokens = max_tokens
        self._max_actions = max_actions

    def propose(
        self,
        elements: list[base.Element],
        screenshot: bytes | None,
        candidates: list[crawl.Action],
        dismissed: tuple[str, ...],
    ) -> Proposal:
        if self._redactor is not None:
            elements = self._redactor.redact_elements(elements)
        content = _content(elements, screenshot, candidates, dismissed, self._redactor)
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=_SYSTEM + self._lang,
                messages=[Message(role="user", content=content)],
                tools=[_PROPOSE_TOOL],
                tool_choice=NamedTool(name="propose_actions"),
                model=self._model,
                max_tokens=self._max_tokens,
            )
        )
        # reporting only (BE-0104) — never on the pass/fail path
        self._record_usage(response)
        block = response.first_tool_use()
        if block is None:
            return Proposal()
        proposal = _proposal_from(block.input, self._max_actions)
        proposal.tokens = usage.of(response.usage).total_tokens
        return proposal

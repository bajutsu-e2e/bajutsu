"""AI guide for the autonomous crawl (BE-0038 ``--guide ai``).

The guide proposes which replayable actions to try from a screen — taps and *realistic* text
inputs that may open a new screen or enable a disabled control whose precondition isn't obvious
(a valid email, a password that meets the rules). It only influences **what to explore**: screen
identity, transition/crash detection and the screen map stay deterministic in
[`crawl.py`](crawl.py), so the crawl is never a verdict (prime directive #1).

The model call sits behind an ``ActionProposer`` protocol, so the guide is exercised in the gate
with a scripted fake — no LLM, mirroring how `record` tests the authoring agent. The proposer's
actions are unioned with the deterministic `candidate_actions` as a safety net, so the crawl
still advances if the model proposes nothing useful.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bajutsu import crawl, crawl_tabs
from bajutsu.drivers import base
from bajutsu.record import _screenshot_bytes

MODEL = "claude-opus-4-8"

# Receives the AI's reasoning as it explores, so a watcher (the crawl log / the web UI) can see
# what the model is thinking and which operations it chose.
Report = Callable[[str], None]


@dataclass
class Proposal:
    """The proposer's output for one screen: the operations to try, plus `thought` — the model's
    short reasoning, surfaced live so a watcher sees what the AI is doing."""

    actions: list[crawl.Action] = field(default_factory=list)
    thought: str = ""


class ActionProposer(Protocol):
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
    """Adapt an `ActionProposer` to the crawl `Guide` signature, running the BE-0038 pipeline:
    first **inspect deterministically** (`candidate_actions`), then hand those operations + the
    screen (and any OS prompt just dismissed to reach it) to the proposer so it can reason about
    what's possible and **combine** them (realistic inputs, multi-field fills, id-less elements);
    finally union the proposal with the deterministic baseline (proposer first, so its values win
    on de-dup), narrating its reasoning via `report`.

    When `tab_locator` is set and a tab bar is present whose individual tabs the tree can't address
    (idb surfaces a SwiftUI TabView as one "Tab Bar" group with no per-tab ids), it locates the
    tabs by vision — the same fallback the alert guard uses — and prepends a coordinate tap per tab,
    so the crawl still switches tabs first."""

    def guide(
        driver: base.Driver, elements: list[base.Element], context: crawl.GuideContext
    ) -> list[crawl.Action]:
        shot = _screenshot_bytes(driver)
        candidates = crawl.candidate_actions(elements)  # deterministic inspection, fed to the AI
        tabs = _locate_tabs(tab_locator, elements, shot, report)
        if report is not None and context.dismissed:
            report(f"🛡️  factoring in a just-dismissed OS prompt: {', '.join(context.dismissed)}")
        proposal = proposer.propose(elements, shot, candidates, context.dismissed)
        if report is not None:
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
    """Vision fallback for an un-addressable tab bar: only when a locator is set, a screenshot
    exists, and a tab bar is present whose tabs the tree can't address. A coordinate tap per tab."""
    if tab_locator is None or shot is None or not crawl_tabs.needs_vision_tabs(elements):
        return []
    targets = tab_locator.locate(shot)
    actions = [crawl.Action("tap_point", label=t.label, point=(t.x, t.y)) for t in targets]
    if report is not None and actions:
        named = ", ".join(t.label or f"({t.x:.2f},{t.y:.2f})" for t in targets)
        report(
            f"👁️  tab bar not addressable in the tree — vision found {len(actions)} tab(s): {named}"
        )
    return actions


def _dedup(actions: list[crawl.Action]) -> list[crawl.Action]:
    """Drop later duplicates by (kind, selector key) so the proposer's choice for an element wins
    over the deterministic baseline's."""
    seen: set[tuple[str, str]] = set()
    out: list[crawl.Action] = []
    for a in actions:
        k = (a.kind, a.key)
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


def make_guide(kind: str, report: Report | None = None) -> crawl.Guide | None:
    """The crawl guide for `kind`: ``""``/``off`` → deterministic (None lets the engine use its
    default `candidate_actions`); ``ai`` → the Claude-backed guide, narrating its reasoning through
    `report`. Any other value is an error."""
    if kind in ("", "off"):
        return None
    if kind == "ai":
        return ai_guide(
            ClaudeActionProposer(),
            report=report,
            tab_locator=crawl_tabs.ClaudeTabLocator(),
        )
    raise ValueError(f"unknown crawl guide: {kind!r} (use 'off' or 'ai')")


# --- Claude-backed proposer ---------------------------------------------------------------

_SYSTEM = """You drive a breadth-first crawl of an iOS app to discover as many distinct screens \
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
- You only choose what to TRY. You never decide pass/fail and never judge results."""

_PROPOSE_TOOL: dict[str, Any] = {
    "name": "propose_actions",
    "description": "Propose the operations to try from this screen, most promising first.",
    "input_schema": {
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
}


def _render_elements(elements: list[base.Element]) -> str:
    """A compact text view of the screen for the model (alongside the screenshot)."""
    lines: list[str] = []
    for el in elements:
        traits = el.get("traits") or []
        if not (el.get("identifier") or el.get("label") or traits):
            continue
        parts = []
        if el.get("identifier"):
            parts.append(f"id={el['identifier']!r}")
        if el.get("label"):
            parts.append(f"label={el['label']!r}")
        if traits:
            parts.append(f"traits={','.join(traits)}")
        if el.get("value"):
            parts.append(f"value={el['value']!r}")
        lines.append("- " + " ".join(parts))
    return "\n".join(lines) or "(no addressable elements)"


def _content(
    elements: list[base.Element],
    screenshot: bytes | None,
    candidates: list[crawl.Action],
    dismissed: tuple[str, ...],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if screenshot:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(screenshot).decode("ascii"),
                },
            }
        )
    found = "\n".join(f"- {a.describe()}" for a in candidates) or "(none)"
    text = (
        f"Screen elements:\n{_render_elements(elements)}\n\n"
        f"Operations the deterministic inspector already found here:\n{found}"
    )
    if dismissed:
        text += f"\n\nAn OS prompt was just dismissed to reach this screen (tapped: {', '.join(dismissed)})."
    content.append({"type": "text", "text": text})
    return content


def _actions_from(payload: dict[str, Any], cap: int) -> list[crawl.Action]:
    """Turn the tool call's `actions` array into crawl Actions, skipping malformed entries and
    capping the count so one screen can't blow the step budget."""
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


class ClaudeActionProposer:
    """Asks Claude (Anthropic SDK) for the screen's candidate operations via a forced tool call.
    `anthropic` is lazy-imported so the module loads without the SDK or an API key."""

    def __init__(
        self, client: Any = None, model: str = MODEL, max_tokens: int = 1024, max_actions: int = 8
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._max_actions = max_actions

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def propose(
        self,
        elements: list[base.Element],
        screenshot: bytes | None,
        candidates: list[crawl.Action],
        dismissed: tuple[str, ...],
    ) -> Proposal:
        message = self._ensure_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[_PROPOSE_TOOL],
            tool_choice={"type": "tool", "name": "propose_actions"},
            messages=[
                {"role": "user", "content": _content(elements, screenshot, candidates, dismissed)}
            ],
        )
        block = next((b for b in message.content if b.type == "tool_use"), None)
        if block is None:
            return Proposal()
        return _proposal_from(block.input, self._max_actions)

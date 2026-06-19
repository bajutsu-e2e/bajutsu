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

from bajutsu import crawl
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
    def propose(self, elements: list[base.Element], screenshot: bytes | None) -> Proposal: ...


def ai_guide(proposer: ActionProposer, report: Report | None = None) -> crawl.Guide:
    """Adapt an `ActionProposer` to the crawl `Guide` signature: capture the screen, ask the
    proposer, narrate its reasoning + chosen operations (via `report`), then union its actions
    with the deterministic ones (proposer first, so its realistic input values win on de-dup)."""

    def guide(driver: base.Driver, elements: list[base.Element]) -> list[crawl.Action]:
        shot = _screenshot_bytes(driver)
        proposal = proposer.propose(elements, shot)
        if report is not None:
            if proposal.thought:
                report(f"🤔 {proposal.thought}")
            for a in proposal.actions:
                report(f"   → try {a.describe()}")
        return _dedup([*proposal.actions, *crawl.candidate_actions(elements)])

    return guide


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
        return ai_guide(ClaudeActionProposer(), report=report)
    raise ValueError(f"unknown crawl guide: {kind!r} (use 'off' or 'ai')")


# --- Claude-backed proposer ---------------------------------------------------------------

_SYSTEM = """You drive a breadth-first crawl of an iOS app to discover as many distinct screens \
as possible. Given the current screen (a screenshot and its element list), propose the set of \
operations most likely to reveal a NEW screen or to unblock a disabled control whose enabling \
condition is not obvious.

Rules:
- Address each element by `id` when it has one (most stable), else by `label` (+ `index` to \
disambiguate duplicates).
- For a text field, propose a `type` with a realistic value for what it asks (a valid email, a \
password meeting common rules, a plausible name/number) — this is how you enable a submit button \
that stays disabled until the form is valid.
- Prefer operations that change the screen; skip pure navigation you've clearly already covered.
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
                        "action": {"type": "string", "enum": ["tap", "type"]},
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


def _content(elements: list[base.Element], screenshot: bytes | None) -> list[dict[str, Any]]:
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
    content.append({"type": "text", "text": "Screen elements:\n" + _render_elements(elements)})
    return content


def _actions_from(payload: dict[str, Any], cap: int) -> list[crawl.Action]:
    """Turn the tool call's `actions` array into crawl Actions, skipping malformed entries and
    capping the count so one screen can't blow the step budget."""
    out: list[crawl.Action] = []
    for item in payload.get("actions") or []:
        if not isinstance(item, dict):
            continue
        kind = "type" if item.get("action") == "type" else "tap"
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

    def propose(self, elements: list[base.Element], screenshot: bytes | None) -> Proposal:
        message = self._ensure_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[_PROPOSE_TOOL],
            tool_choice={"type": "tool", "name": "propose_actions"},
            messages=[{"role": "user", "content": _content(elements, screenshot)}],
        )
        block = next((b for b in message.content if b.type == "tool_use"), None)
        if block is None:
            return Proposal()
        return _proposal_from(block.input, self._max_actions)

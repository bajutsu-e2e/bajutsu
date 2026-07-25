"""Shared support for the record / crawl real-model tests (BE-0295).

Both `test_real_model_smoke.py` (does a real response parse today?) and
`test_real_model_fixtures.py` (capture a real response once, replay it forever) ask the same
question — does a genuine model response parse into the propose loop's action schema — so the
showcase-screen loader, the credential gate, and the parse-validity assertions live here as the one
source of truth. The capture harness (`RecordingBackend` + fixture save/load) lives here too, since
the smoke tests could grow to capture as well.

Nothing here runs a model; the live callers pass a real backend in. No LLM ever touches the
`run` / CI verdict (prime directive 1) — this is the AI *authoring* surface alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bajutsu import crawl
from bajutsu.agents.protocols import Proposal
from bajutsu.ai import credential_gap
from bajutsu.ai.base import AiBackend, MessageRequest, MessageResponse, ToolUseBlock
from bajutsu.crawl import guide
from bajutsu.drivers import base
from bajutsu.evidence.golden import load_golden

ROOT = Path(__file__).resolve().parent.parent
GOLDENS = ROOT / "demos" / "showcase" / "scenarios" / "golden" / "goldens"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "be0295"

# A concrete authoring goal against the showcase "controls" screen (the `log.*` namespace: a note
# field, an Intense toggle, a Submit button), so the record agent has a real intent to propose for.
RECORD_GOAL = "Turn the Intense option on, then submit the log"

# `credential_gap()` returns the missing-credential reason, or None when a real model can be reached
# (BE-0047). None → run the live path; a reason → skip, keeping the gate credential-free. It runs at
# import time, so a misconfigured `ai.provider` (an unregistered name raises `ValueError` in
# `registry._provider_name`) must skip these signal-first tests, never fail collection.
try:
    _GAP: str | None = credential_gap()
except ValueError as exc:
    _GAP = f"provider misconfigured: {exc}"
requires_credential = pytest.mark.skipif(
    _GAP is not None,
    reason=f"real-model path is signal-first (BE-0282); real model unavailable: {_GAP}",
)


def showcase_screen(name: str) -> list[base.Element]:
    """A committed showcase golden as a screen the propose loops can be asked about, no Simulator."""
    return list(load_golden(GOLDENS / f"{name}.json").values())


def crawl_candidates(screen: list[base.Element]) -> list[crawl.Action]:
    """The crawl candidate actions the navigation proposer is offered for a screen."""
    return crawl.candidate_actions(screen)


def assert_parses_to_record_action(proposal: Proposal) -> None:
    """The real record response mapped to a well-formed turn outcome, not silently dropped.

    A step, a finish, a human handoff, or a screenshot request all count — `next_action` forces one
    tool call, so an empty `Proposal` means the response failed to parse into the action schema.
    """
    assert (
        proposal.step is not None
        or proposal.done
        or proposal.needs_human
        or proposal.need_screenshot
    ), f"real record response did not parse into any action: {proposal}"


def assert_parses_to_crawl_actions(proposal: guide.Proposal) -> None:
    """The real crawl response mapped to at least one replayable action, each with a stable selector.

    "Replayable" here means faithfully serializable into a scenario, since a crawl's payoff is the
    committable candidate flow (`crawl/repro.py`). A `tap_point` is a bare coordinate that schema
    can't address, so `repro.py` drops any path containing one ("faithful or nothing") — an action
    with only `point` is not replayable, so this must not count it.
    """
    assert proposal.actions, "real crawl response parsed into no replayable action"
    for action in proposal.actions:
        # `action.key` is never empty — it falls back to `@{label}#…` even with no selector — so
        # assert a genuine, serializable addressing source instead: an id, a label, or a fill's
        # fields (the exact sources `repro.py` can turn into a scenario). `point` is excluded: a
        # coordinate-only action passes `key` but is exactly what `repro.py` refuses to serialize.
        assert action.target or action.label or action.fields, (
            f"crawl action has no stable selector source (a bare tap_point is not replayable): {action}"
        )


class RecordingBackend:
    """An `AiBackend` that delegates to a real backend and keeps every response it returns.

    Wrapping the live backend is what lets a capture observe the raw tool-use a real model produced
    *before* the propose loop parses it away, so it can be saved as a replay fixture. Wrapping a
    `FakeBackend` instead makes the whole capture path deterministically testable without a model.
    """

    def __init__(self, inner: AiBackend) -> None:
        self._inner = inner
        self.responses: list[MessageResponse] = []

    def create_message(self, request: MessageRequest) -> MessageResponse:
        response = self._inner.create_message(request)
        self.responses.append(response)
        return response


def tool_use_payload(response: MessageResponse) -> list[dict[str, Any]]:
    """The response's tool-use blocks as plain `{name, input}` dicts — the fixture's whole content.

    Only tool-use blocks are kept: the propose loops read nothing else from a forced-tool turn, and
    `load_fixture` reconstructs exactly this shape as one replayed turn (`_FixtureReplay`).
    """
    return [
        {"name": block.name, "input": block.input}
        for block in response.content
        if isinstance(block, ToolUseBlock)
    ]


def save_fixture(path: Path, response: MessageResponse) -> None:
    """Persist a captured response's tool-use blocks as a JSON replay fixture.

    Raises ``ValueError`` when the response carries no tool-use blocks — saving an empty fixture
    would let a broken live capture (a model that returned no tool call) produce a committed
    `[]` file that ``_FixtureReplay`` silently replays as ``Proposal(done=True)``, masking the
    failure rather than surfacing it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = tool_use_payload(response)
    if not payload:
        raise ValueError(
            f"refusing to save an empty fixture to {path}: "
            "the response contained no tool-use blocks — the live capture likely failed"
        )
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class _FixtureReplay:
    """Replays a saved turn's tool-use blocks as one multi-block `MessageResponse`.

    The record loop maps *every* tool-use block in a single turn to a step (`_to_proposal`,
    BE-0178: the agent may emit several actions), so a captured turn must replay as one response
    whose `content` holds all the blocks in order. `FakeBackend` instead returns each block as its
    own single-block response on successive `create_message()` calls, which would silently drop all
    but the first block of a real parallel multi-action capture — the exact shape a fixture exists
    to guard.
    """

    def __init__(self, blocks: list[ToolUseBlock]) -> None:
        self._response = MessageResponse(content=list(blocks))

    def create_message(self, request: MessageRequest) -> MessageResponse:
        return self._response


def load_fixture(path: Path) -> AiBackend:
    """Rebuild a backend from a saved fixture, replaying the captured tool-use as one turn."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    blocks = [ToolUseBlock(name=entry["name"], input=entry["input"]) for entry in payload]
    return _FixtureReplay(blocks)

"""Real-model verification of the record and crawl propose loops (BE-0295).

Every other test of these parse paths drives them with `FakeBackend(FakeBlock(...))` — a response
shaped exactly as the test author expects, never one a real model produced. These key-gated smoke
tests close that gap: given a genuine record / crawl prompt over a real showcase screen, they call a
real model and assert its structured tool-use response parses into the propose loop's action schema.

They are signal-first, not a gate (the BE-0282 precedent): skipped whenever no AI credential is
configured, so the deterministic gate stays hermetic and needs no Simulator. No LLM ever touches the
`run` / CI verdict (prime directive 1) — these exercise the AI *authoring* path alone.

The showcase screen is a committed golden element tree, so the smoke needs no Simulator; only the
model call is live. The harness wiring and the validity assertions are themselves checked
deterministically with a `FakeBackend` below, so a live run genuinely validates rather than passing
vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeBackend, FakeBlock

from bajutsu import crawl
from bajutsu.agents.claude import ClaudeAgent
from bajutsu.agents.protocols import Observation, Proposal
from bajutsu.ai import create_backend, credential_gap
from bajutsu.crawl import guide
from bajutsu.crawl.guide import ClaudeActionProposer
from bajutsu.drivers import base
from bajutsu.evidence.golden import load_golden

ROOT = Path(__file__).resolve().parent.parent
GOLDENS = ROOT / "demos" / "showcase" / "scenarios" / "golden" / "goldens"

# A concrete authoring goal against the showcase "controls" screen (the `log.*` namespace: a note
# field, an Intense toggle, a Submit button), so the record agent has a real intent to propose for.
RECORD_GOAL = "Turn the Intense option on, then submit the log"

# `credential_gap()` returns the missing-credential reason, or None when a real model can be reached
# (BE-0047). None → run the live smoke; a reason → skip, keeping the gate credential-free.
_GAP = credential_gap()
_requires_credential = pytest.mark.skipif(
    _GAP is not None,
    reason=f"real-model smoke is signal-first (BE-0282); no AI credential: {_GAP}",
)


def _showcase_screen(name: str) -> list[base.Element]:
    """A committed showcase golden as a screen the propose loops can be asked about, no Simulator."""
    return list(load_golden(GOLDENS / f"{name}.json").values())


def _assert_parses_to_record_action(proposal: Proposal) -> None:
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


def _assert_parses_to_crawl_actions(proposal: guide.Proposal) -> None:
    """The real crawl response mapped to at least one replayable action, each with a stable selector."""
    assert proposal.actions, "real crawl response parsed into no replayable action"
    for action in proposal.actions:
        assert action.key, f"crawl action has no stable selector: {action}"


# --- Deterministic harness self-checks (no model; always run) -----------------------------------
# Prove the loader yields a usable screen and the validity assertions genuinely accept a parsed
# action, so the key-gated live tests below validate for real instead of passing on an empty result.


def test_record_smoke_harness_validates_a_parsed_action() -> None:
    screen = _showcase_screen("controls")
    assert any(el["identifier"] == "log.intense" for el in screen)
    agent = ClaudeAgent(
        backend=FakeBackend(FakeBlock("tap", {"id": "log.intense", "reason": "toggle it on"}))
    )
    proposal = agent.next_action(Observation(goal=RECORD_GOAL, screen=screen, history=[]))
    _assert_parses_to_record_action(proposal)


def test_crawl_smoke_harness_validates_parsed_actions() -> None:
    screen = _showcase_screen("controls")
    candidates = crawl.candidate_actions(screen)
    proposer = ClaudeActionProposer(
        backend=FakeBackend(
            FakeBlock(
                "propose_actions",
                {
                    "thought": "explore the log form",
                    "actions": [{"action": "tap", "id": "log.submit"}],
                },
            )
        )
    )
    proposal = proposer.propose(screen, None, candidates, ())
    _assert_parses_to_crawl_actions(proposal)


# --- Key-gated live smoke tests (real model) ----------------------------------------------------


@_requires_credential
def test_record_propose_parses_a_real_model_response() -> None:
    screen = _showcase_screen("controls")
    agent = ClaudeAgent(backend=create_backend())
    proposal = agent.next_action(Observation(goal=RECORD_GOAL, screen=screen, history=[]))
    _assert_parses_to_record_action(proposal)


@_requires_credential
def test_crawl_navigate_parses_a_real_model_response() -> None:
    screen = _showcase_screen("controls")
    candidates = crawl.candidate_actions(screen)
    proposer = ClaudeActionProposer(backend=create_backend())
    proposal = proposer.propose(screen, None, candidates, ())
    _assert_parses_to_crawl_actions(proposal)

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
vacuously. The screen loader, credential gate, and parse-validity assertions are shared with
`test_real_model_fixtures.py` via `real_model_support`.
"""

from __future__ import annotations

import pytest
from conftest import FakeBackend, FakeBlock
from real_model_support import (
    RECORD_GOAL,
    assert_parses_to_crawl_actions,
    assert_parses_to_record_action,
    crawl_candidates,
    requires_credential,
    showcase_screen,
)

from bajutsu.agents.claude import ClaudeAgent
from bajutsu.agents.protocols import Observation
from bajutsu.ai import create_backend
from bajutsu.crawl.guide import ClaudeActionProposer

# --- Deterministic harness self-checks (no model; always run) -----------------------------------
# Prove the loader yields a usable screen and the validity assertions genuinely accept a parsed
# action, so the key-gated live tests below validate for real instead of passing on an empty result.


def test_record_smoke_harness_validates_a_parsed_action() -> None:
    screen = showcase_screen("controls")
    assert any(el["identifier"] == "log.intense" for el in screen)
    agent = ClaudeAgent(
        backend=FakeBackend(FakeBlock("tap", {"id": "log.intense", "reason": "toggle it on"}))
    )
    proposal = agent.next_action(Observation(goal=RECORD_GOAL, screen=screen, history=[]))
    assert_parses_to_record_action(proposal)


def test_crawl_smoke_harness_validates_parsed_actions() -> None:
    screen = showcase_screen("controls")
    candidates = crawl_candidates(screen)
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
    assert_parses_to_crawl_actions(proposal)


# --- Key-gated live smoke tests (real model) ----------------------------------------------------


@pytest.mark.live
@requires_credential
def test_record_propose_parses_a_real_model_response() -> None:
    screen = showcase_screen("controls")
    agent = ClaudeAgent(backend=create_backend())
    proposal = agent.next_action(Observation(goal=RECORD_GOAL, screen=screen, history=[]))
    assert_parses_to_record_action(proposal)


@pytest.mark.live
@requires_credential
def test_crawl_navigate_parses_a_real_model_response() -> None:
    screen = showcase_screen("controls")
    candidates = crawl_candidates(screen)
    proposer = ClaudeActionProposer(backend=create_backend())
    proposal = proposer.propose(screen, None, candidates, ())
    assert_parses_to_crawl_actions(proposal)

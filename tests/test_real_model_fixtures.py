"""Capture-and-replay regression fixtures for the record and crawl propose loops (BE-0295).

The key-gated smoke tests in `test_real_model_smoke.py` prove a real model's response parses *today*;
they leave no artifact behind. This module adds the other half of BE-0295: a harness that captures a
real model's raw tool-use response once and replays it as a permanent regression fixture, so a
captured real shape keeps being checked between live runs — the exact gap a hand-built `FakeBlock`
cannot fill (it is only ever the shape the test author expected).

Three layers, mirroring the smoke file's signal-first design (the BE-0282 precedent):

- **Deterministic round-trip self-checks** (no credential, always run): drive a loop through a
  `RecordingBackend`, serialize the captured response, reload it from disk, and re-parse — proving
  the capture → save → load → replay machinery end to end without a model. A live capture reuses the
  very same path, so it can be trusted to persist a valid fixture rather than passing vacuously.
- **Committed-fixture replay** (signal-first): once a real fixture is captured and committed under
  `tests/fixtures/be0295/`, replay it deterministically on every run; skip while none exists.
- **Key-gated live capture** (real model): run the loop against a real model, assert the response
  parses, and persist it as the committed fixture — the one step that needs a credential.

No LLM ever touches the `run` / CI verdict (prime directive 1): every path here exercises the AI
*authoring* surface alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeBackend, FakeBlock
from real_model_support import (
    FIXTURES_DIR,
    RECORD_GOAL,
    RecordingBackend,
    _FixtureReplay,
    assert_parses_to_crawl_actions,
    assert_parses_to_record_action,
    crawl_candidates,
    load_fixture,
    requires_credential,
    save_fixture,
    showcase_screen,
)

from bajutsu.agents.claude import ClaudeAgent
from bajutsu.agents.protocols import Observation, Proposal
from bajutsu.ai import create_backend
from bajutsu.ai.base import AiBackend, MessageResponse, ToolUseBlock
from bajutsu.crawl import guide
from bajutsu.crawl.guide import ClaudeActionProposer

_RECORD_FIXTURE = FIXTURES_DIR / "record.json"
_CRAWL_FIXTURE = FIXTURES_DIR / "crawl.json"


def _record_proposal(backend: AiBackend) -> Proposal:
    screen = showcase_screen("controls")
    agent = ClaudeAgent(backend=backend)
    return agent.next_action(Observation(goal=RECORD_GOAL, screen=screen, history=[]))


def _crawl_proposal(backend: AiBackend) -> guide.Proposal:
    screen = showcase_screen("controls")
    proposer = ClaudeActionProposer(backend=backend)
    return proposer.propose(screen, None, crawl_candidates(screen), ())


# --- Deterministic round-trip self-checks (no model; always run) --------------------------------
# Prove the capture harness records a response, serializes it to a fixture, reloads it, and replays
# it into a valid action — so a live capture below persists a genuinely re-parseable artifact.


def test_record_capture_replay_roundtrip(tmp_path: Path) -> None:
    inner = FakeBackend(FakeBlock("tap", {"id": "log.intense", "reason": "toggle it on"}))
    recording = RecordingBackend(inner)
    assert_parses_to_record_action(_record_proposal(recording))
    assert len(recording.responses) == 1, "one propose turn should record exactly one response"

    fixture = tmp_path / "record.json"
    save_fixture(fixture, recording.responses[0])
    assert_parses_to_record_action(_record_proposal(load_fixture(fixture)))


def test_record_multiblock_capture_replay_roundtrip(tmp_path: Path) -> None:
    # Verify the _FixtureReplay path: a real model may emit several tool-use blocks in one turn
    # (BE-0178 batched actions). `FakeBackend` returns each block on successive calls, so it cannot
    # simulate that shape — `_FixtureReplay` (used by `load_fixture`) replays all blocks in one
    # `MessageResponse.content`, which is the only shape `_to_proposal` sees from a real capture.
    blocks = [
        ToolUseBlock(name="tap", input={"id": "log.intense", "reason": "toggle on"}),
        ToolUseBlock(name="tap", input={"id": "log.submit", "reason": "submit after"}),
    ]
    replay = _FixtureReplay(blocks)
    assert_parses_to_record_action(_record_proposal(replay))

    # Also exercise the full save → load round-trip with a multi-block response.
    multi_response = MessageResponse(content=list(blocks))
    fixture = tmp_path / "record_multi.json"
    save_fixture(fixture, multi_response)
    assert_parses_to_record_action(_record_proposal(load_fixture(fixture)))


def test_crawl_capture_replay_roundtrip(tmp_path: Path) -> None:
    inner = FakeBackend(
        FakeBlock(
            "propose_actions",
            {"thought": "explore the log form", "actions": [{"action": "tap", "id": "log.submit"}]},
        )
    )
    recording = RecordingBackend(inner)
    assert_parses_to_crawl_actions(_crawl_proposal(recording))
    assert len(recording.responses) == 1, "one navigate turn should record exactly one response"

    fixture = tmp_path / "crawl.json"
    save_fixture(fixture, recording.responses[0])
    assert_parses_to_crawl_actions(_crawl_proposal(load_fixture(fixture)))


# --- Committed-fixture replay (signal-first: skip until a real capture lands) --------------------


@pytest.mark.skipif(not _RECORD_FIXTURE.exists(), reason="no captured record fixture yet (BE-0295)")
def test_committed_record_fixture_parses() -> None:
    assert_parses_to_record_action(_record_proposal(load_fixture(_RECORD_FIXTURE)))


@pytest.mark.skipif(not _CRAWL_FIXTURE.exists(), reason="no captured crawl fixture yet (BE-0295)")
def test_committed_crawl_fixture_parses() -> None:
    assert_parses_to_crawl_actions(_crawl_proposal(load_fixture(_CRAWL_FIXTURE)))


# --- Key-gated live capture (real model): persist the committed fixture --------------------------


@pytest.mark.live
@requires_credential
def test_capture_record_fixture() -> None:
    recording = RecordingBackend(create_backend())
    assert_parses_to_record_action(_record_proposal(recording))
    save_fixture(_RECORD_FIXTURE, recording.responses[0])
    # Fail the capture fast if the saved payload cannot round-trip back through the replay path.
    assert_parses_to_record_action(_record_proposal(load_fixture(_RECORD_FIXTURE)))


@pytest.mark.live
@requires_credential
def test_capture_crawl_fixture() -> None:
    recording = RecordingBackend(create_backend())
    assert_parses_to_crawl_actions(_crawl_proposal(recording))
    save_fixture(_CRAWL_FIXTURE, recording.responses[0])
    # Fail the capture fast if the saved payload cannot round-trip back through the replay path.
    assert_parses_to_crawl_actions(_crawl_proposal(load_fixture(_CRAWL_FIXTURE)))

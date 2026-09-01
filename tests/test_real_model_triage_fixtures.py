"""Capture-and-replay regression fixture for the triage --ai diagnosis path (BE-0296).

The key-gated smoke test in `test_real_model_triage_smoke.py` proves a real model's diagnosis parses
*today*; it leaves no artifact behind. This module adds the other half of BE-0296: a harness that
captures a real model's raw `diagnose` tool-use once and replays it as a permanent regression
fixture, so a captured real shape keeps being checked between live runs — the exact gap a hand-built
`FakeBlock` cannot fill (it is only ever the shape the test author expected).

Three layers, mirroring the smoke file's signal-first design (the BE-0282 precedent):

- **Deterministic round-trip self-check** (no credential, always run): drive triage through a
  `RecordingBackend`, serialize the captured response, reload it from disk, and re-parse — proving
  the capture -> save -> load -> replay machinery end to end without a model, so a live capture can
  be trusted to persist a valid fixture rather than passing vacuously.
- **Committed-fixture replay** (signal-first): once a real fixture is captured and committed under
  `tests/fixtures/be0296/`, replay it deterministically on every run; skip while none exists.
- **Key-gated live capture** (real model): run triage against a real model, assert the diagnosis
  parses, and persist it as the committed fixture — the one step that needs a credential.

No LLM ever touches the `run` / CI verdict (prime directive 1): every path here exercises the AI
*diagnosis* surface alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeBackend, FakeBlock
from real_model_triage_support import (
    FIXTURES_DIR,
    RecordingBackend,
    assert_parses_to_triage,
    diagnose_payload,
    load_fixture,
    requires_credential,
    save_fixture,
    triage_context,
)

from bajutsu.agents.claude_triage import ClaudeTriageAgent
from bajutsu.ai import create_backend
from bajutsu.ai.base import AiBackend
from bajutsu.triage.heuristic import Triage

_TRIAGE_FIXTURE = FIXTURES_DIR / "triage.json"


def _diagnose(backend: AiBackend) -> Triage:
    return ClaudeTriageAgent(backend=backend).triage(triage_context())


def _fake_diagnose_backend() -> FakeBackend:
    return FakeBackend(FakeBlock("diagnose", diagnose_payload()))


# --- Deterministic round-trip self-check (no model; always run) ---------------------------------
# Prove the capture harness records the diagnosis response, serializes it to a fixture, reloads it,
# and replays it into a valid Triage — so a live capture below persists a genuinely re-parseable
# artifact.


def test_triage_capture_replay_roundtrip(tmp_path: Path) -> None:
    recording = RecordingBackend(_fake_diagnose_backend())
    assert_parses_to_triage(_diagnose(recording))
    assert len(recording.responses) == 1, "one triage call should record exactly one response"

    fixture = tmp_path / "triage.json"
    save_fixture(fixture, recording.responses[0])
    assert_parses_to_triage(_diagnose(load_fixture(fixture)))


# --- Committed-fixture replay (signal-first: skip until a real capture lands) --------------------


@pytest.mark.skipif(not _TRIAGE_FIXTURE.exists(), reason="no captured triage fixture yet (BE-0296)")
def test_committed_triage_fixture_parses() -> None:
    assert_parses_to_triage(_diagnose(load_fixture(_TRIAGE_FIXTURE)))


# --- Key-gated live capture (real model): persist the committed fixture --------------------------


@pytest.mark.live
@requires_credential
def test_capture_triage_fixture() -> None:
    recording = RecordingBackend(create_backend())
    assert_parses_to_triage(_diagnose(recording))
    save_fixture(_TRIAGE_FIXTURE, recording.responses[0])
    # Fail the capture fast if the saved payload cannot round-trip back through the replay path.
    assert_parses_to_triage(_diagnose(load_fixture(_TRIAGE_FIXTURE)))

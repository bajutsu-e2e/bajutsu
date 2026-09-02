"""Real-model verification of the triage --ai diagnosis path (BE-0296).

Every other test of this parse path drives it with `FakeBackend(FakeBlock(...))` — a diagnosis
response shaped exactly as the test author expects — and the CLI-level `--ai` tests go further,
swapping the triage agent class itself for a hand-built `_FakeAgent`. Nothing confirms that a real
model, reasoning over a real failed run's evidence, produces a diagnosis JSON that actually parses
into the `Triage` schema, or that a proposed fix's category enum matches what the model is prompted
to choose from. This key-gated smoke test closes that gap.

It is signal-first, not a gate (the BE-0282 precedent): skipped whenever no AI credential is
configured, so the deterministic gate stays hermetic and needs no Simulator. Triage is advisory by
design (DESIGN.md M4); no LLM ever touches the `run` / CI verdict (prime directive 1) — this
exercises the AI *diagnosis* path alone and asserts only that its output parses.

The failed-scenario context is built from a committed showcase golden element tree, so the smoke
needs no Simulator; only the model call is live. The context builder and the parse-validity
assertion are checked deterministically with a `FakeBackend` below, so a live run genuinely
validates rather than passing vacuously.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeBackend, FakeBlock
from real_model_triage_support import (
    assert_parses_to_triage,
    diagnose_payload,
    requires_credential,
    triage_context,
)
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.agents.ai_config import PROVIDER_ENV
from bajutsu.common.agents.anthropic_client import ANTHROPIC_KEY_ENV
from bajutsu.common.agents.claude_triage import ClaudeTriageAgent
from bajutsu.common.ai import create_backend

# --- Deterministic harness self-checks (no model; always run) -----------------------------------
# Prove the context builder yields a screen carrying the correct id and the validity assertion
# genuinely accepts a parsed diagnosis (with a fix), so the key-gated live test below validates for
# real instead of passing on an empty result.


def test_triage_smoke_context_carries_the_renamed_id() -> None:
    context = triage_context()
    assert any(el["identifier"] == "log.intense" for el in context.elements)
    assert context.target_id == "log.intens"  # the typo the real screen never exposes


def test_triage_smoke_harness_validates_a_parsed_diagnosis() -> None:
    agent = ClaudeTriageAgent(backend=FakeBackend(FakeBlock("diagnose", diagnose_payload())))
    result = agent.triage(triage_context())
    assert_parses_to_triage(result)
    assert result.fix is not None and result.fix.kind == "renameId"


# --- Key-gated live smoke test (real model) -----------------------------------------------------


@pytest.mark.live
@requires_credential
def test_triage_diagnosis_parses_a_real_model_response() -> None:
    agent = ClaudeTriageAgent(backend=create_backend())
    assert_parses_to_triage(agent.triage(triage_context()))


# --- Real credential-gap check (always runs; no stand-in agent) ---------------------------------
# `tests/test_triage.py`'s `_stub_ai_cli` monkeypatches `_require_ai_credential` to a no-op and
# swaps the triage agent for `_FakeAgent`, so `--ai`'s actual credential check is never exercised.
# This drives the real gap-detection code path end to end through the CLI instead.


def _write_failed_run(runs: Path) -> Path:
    """A minimal failed single-scenario run dir that `triage.assemble` reads as triageable."""
    run = runs / "r"
    (run / "00-s" / "step0").mkdir(parents=True)
    manifest = {
        "runId": "r",
        "ok": False,
        "backend": "xcuitest",
        "scenarios": [
            {
                "scenario": "s",
                "ok": False,
                "backend": "xcuitest",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": False,
                        "reason": "一致なし: log.intens",
                        "artifacts": [
                            {
                                "name": "00-s/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            }
                        ],
                    }
                ],
                "expect_results": [],
                "failure": "step0 tap: 一致なし: log.intens",
                "artifacts": [],
            }
        ],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "scenario.yaml").write_text(
        "- name: s\n  steps:\n    - tap: { id: log.intens }\n", encoding="utf-8"
    )
    (run / "00-s" / "step0" / "elements.json").write_text("[]", encoding="utf-8")
    return run


def test_triage_ai_fails_closed_without_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the provider defaulted to api-key (anthropic) and its key env removed, `triage --ai` must
    # fail closed with a clean exit-2 and never construct a client (BE-0047). Deterministic no matter
    # what the host has, since the key is deleted here.
    monkeypatch.delenv(PROVIDER_ENV, raising=False)  # fall back to the api-key default
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)
    run = _write_failed_run(tmp_path / "runs")
    result = CliRunner().invoke(app, ["triage", str(run), "--ai"])
    assert result.exit_code == 2, result.output
    assert "no AI credential" in result.output
    assert ANTHROPIC_KEY_ENV in result.output

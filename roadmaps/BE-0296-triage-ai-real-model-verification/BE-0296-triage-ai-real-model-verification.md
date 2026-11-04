**English** · [日本語](BE-0296-triage-ai-real-model-verification-ja.md)

# BE-0296 — Real-model verification of the triage --ai diagnosis path

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0296](BE-0296-triage-ai-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0296") |
| Implementing PR | [#1364](https://github.com/bajutsu-e2e/bajutsu/pull/1364) |
| Topic | Self-healing triage |
<!-- /BE-METADATA -->

## Introduction

The self-heal path of `triage --ai` (`agents/claude_triage.py`) sends the
[evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) of a failed run to Claude,
including the failure screenshot. The path parses the response into a structured diagnosis and, when
one applies, a proposed fix (`renameId` / `addIndex` / `raiseTimeout`).

Every test of this parsing drives it with `FakeBackend(FakeBlock(...))`. The CLI-level test goes
further: it replaces the triage agent class with a hand-built `_FakeAgent` that returns a hardcoded
`Triage` object, so the AI backend never runs. This item adds a real-model check for the
diagnosis-parsing path itself.

## Motivation

The fakes in `tests/test_claude_triage.py` have the shape their authors expect a diagnosis
response to take. `_stub_ai_cli` in `tests/test_triage.py` backs the CLI's `--ai` tests and goes
further: it swaps `ClaudeCrossRunTriageAgent` for `_FakeAgent`. The credential-gap path
(`_require_ai_credential`) is stubbed as well, so the real check never runs.

A real model looks at a real failure screenshot and a real run's evidence. Nothing in the suite confirms
two properties of its output. First, its diagnosis JSON must parse into the `Triage` schema. Second, the
category enum of a proposed fix must match the choices the prompt offers.

Triage is advisory by design. `--apply`/`--write` always previews a diff before touching a scenario,
and a human reviews the result (M4 in the roadmap of `DESIGN.md`; prime directive 1 keeps triage off
the `run` gate entirely). That advisory framing is the reason to close this gap. A parser that
drops a real model's fix proposal without notice, or maps its category to the wrong value, degrades the one AI feature
whose value is an actionable suggestion with the right type for a human to review.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Capture a real diagnosis response as a fixture.** Run `triage --ai` against a genuinely failed
  showcase run and save the real response payload. Add it as a fixture that the existing fake-backend
  tests can also parse.
- **A key-gated live smoke test.** Add a `pytest.mark.skipif`-gated test that runs `triage --ai`
  end-to-end against a real failed run with a real credential. The test asserts that the result
  parses into a valid `Triage` object with a well-formed fix, when one is proposed.
- **Exercise the credential-gap path for real, not via a stand-in agent class.** `_stub_ai_cli` also
  monkeypatches `_require_ai_credential` itself, not merely the agent class. That test therefore
  never runs the actual credential check of `--ai`. Keep a dedicated test that drives the real
  gap-detection code path.
- **No change to triage's advisory status.** This item verifies that AI output parses correctly. It
  does not change the diff-preview-then-human-review flow of `--apply`/`--write`, and it puts no
  model call on any deterministic verdict path.

## Alternatives considered

- **Trust the fake-backend tests, since the diagnosis schema is documented.** A documented schema
  does not guarantee that a real model's free-form reasoning about a screenshot produces conforming
  output. A real-model check exists to catch exactly that risk.
- **Skip real verification since triage is advisory and human-reviewed anyway.** A human reviewing a
  diff still needs the diff to exist and be well-formed. A proposal dropped without notice, or one that is malformed,
  never reaches the review step at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Capture a real `triage --ai` diagnosis response as a regression fixture.
- [x] Add a key-gated live smoke test running `triage --ai` end-to-end against a real failed run.
- [x] Add a dedicated test exercising the real credential-gap check (not a stand-in agent class).
- [x] Confirm triage's advisory status is unchanged — this verifies output parsing only.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/agents/claude_triage.py`, `bajutsu/triage.py`, `tests/conftest.py`
  (`FakeBackend` / `FakeBlock`), `tests/test_claude_triage.py`, `tests/test_triage.py`
  (`_stub_ai_cli`, `_FakeAgent`)

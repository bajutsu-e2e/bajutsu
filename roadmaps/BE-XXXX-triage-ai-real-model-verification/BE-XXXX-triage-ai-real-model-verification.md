**English** · [日本語](BE-XXXX-triage-ai-real-model-verification-ja.md)

# BE-XXXX — Real-model verification of the triage --ai diagnosis path

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-triage-ai-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Self-healing triage |
<!-- /BE-METADATA -->

## Introduction

`triage --ai`'s self-heal path (`agents/claude_triage.py`) sends a failed run's
[evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) — including
the failure screenshot — to Claude and parses the response into a structured diagnosis and, when
applicable, a proposed fix (`renameId` / `addIndex` / `raiseTimeout`). Every test that exercises this
parsing drives it with `FakeBackend(FakeBlock(...))`; the CLI-level test goes a step further and
replaces the triage agent class itself with a hand-built `_FakeAgent` that returns a hardcoded
`Triage` object, bypassing the AI backend entirely. This item adds a real-model check for the
diagnosis-parsing path itself.

## Motivation

`tests/test_claude_triage.py`'s fakes are shaped exactly as their authors expect a diagnosis response
to look; `tests/test_triage.py`'s `_stub_ai_cli` (backing the CLI's `--ai` tests) goes further and
swaps out `ClaudeCrossRunTriageAgent` for `_FakeAgent`, so even the credential-gap path
(`_require_ai_credential`) is stubbed rather than exercised. Nothing in the suite ever confirms that
a real model, looking at a real failure screenshot and a real run's evidence, produces a diagnosis
JSON that actually parses into the `Triage` schema, or that a proposed fix's category enum matches
what the real model is prompted to choose from.

Triage is advisory by design — `--apply`/`--write` is always diff-previewed before touching a scenario,
and a human reviews the result (M4 in `DESIGN.md`'s roadmap; prime directive 1 keeps this off the
`run` gate entirely). That advisory framing is exactly why this gap is worth closing rather than
leaving alone: a parser that silently drops a real model's fix proposal, or mis-maps its category,
degrades the one AI-touching feature whose whole value proposition is producing an actionable,
correctly-typed suggestion for a human to review.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Capture a real diagnosis response as a fixture.** Run `triage --ai` against a genuinely failed
  showcase run, save the real response payload, and add it as a fixture the existing fake-backend
  tests can also parse.
- **A key-gated live smoke test.** Add a `pytest.mark.skipif`-gated test that runs `triage --ai`
  end-to-end against a real failed run with a real credential, asserting the result parses into a
  valid `Triage` object with a well-formed fix (when one is proposed).
- **Exercise the credential-gap path for real, not via a stand-in agent class.** `_stub_ai_cli`
  separately monkeypatches `_require_ai_credential` itself (not just the agent class), so `--ai`'s
  actual credential check is never exercised by that test; keep a dedicated test that drives the
  real gap-detection code path.
- **No change to triage's advisory status.** This item verifies AI output parses correctly; it does
  not change `--apply`/`--write`'s diff-preview-then-human-review flow or put a model call on any
  deterministic verdict path.

## Alternatives considered

- **Trust the fake-backend tests, since the diagnosis schema is documented.** A documented schema
  does not guarantee a real model's free-form reasoning about a screenshot reliably produces output
  conforming to it — that is exactly the risk a real-model check exists to catch.
- **Skip real verification since triage is advisory and human-reviewed anyway.** A human reviewing a
  diff still needs the diff to exist and be well-formed; a silently-dropped or malformed proposal
  never reaches that review step at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Capture a real `triage --ai` diagnosis response as a regression fixture.
- [ ] Add a key-gated live smoke test running `triage --ai` end-to-end against a real failed run.
- [ ] Add a dedicated test exercising the real credential-gap check (not a stand-in agent class).
- [ ] Confirm triage's advisory status is unchanged — this verifies output parsing only.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/agents/claude_triage.py`, `bajutsu/triage.py`, `tests/conftest.py`
  (`FakeBackend` / `FakeBlock`), `tests/test_claude_triage.py`, `tests/test_triage.py`
  (`_stub_ai_cli`, `_FakeAgent`)

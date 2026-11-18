**English** · [日本語](BE-0295-record-crawl-real-model-verification-ja.md)

# BE-0295 — Real-model verification of the record and crawl propose loops

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0295](BE-0295-record-crawl-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0295") |
| Implementing PR | [#1344](https://github.com/bajutsu-e2e/bajutsu/pull/1344), [#1357](https://github.com/bajutsu-e2e/bajutsu/pull/1357) |
| Topic | Authoring experience |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

The observe → propose → execute loop of `record` (`ClaudeAgent.next_action` in `agents/claude.py`)
and the autonomous exploration of `crawl` (`crawl/guide.py`, `crawl/tabs.py`) both depend on one step.
That step parses a real model's structured tool-use proposal into Bajutsu's own action schema.

Every test of this parsing constructs `ClaudeAgent` or the crawl agents with
`FakeBackend(FakeBlock(...))`. The response has the shape the test's author expects, not the shape a
real model actually produced. This item adds a real-model check for the parsing itself. The check is
distinct from, and complementary to, the transport-level adapter check proposed in
[PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233).

## Motivation

The proposal in [PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233) asks a transport
question: does the adapter round-trip with the real service at all? This item asks a semantic one:
given a genuine record or crawl prompt, does a real model's response parse into the action schema of
the propose loop? The question concerns the specific prompts and schemas that `record` and `crawl`
use, and a hand-built `FakeBlock` cannot exercise them by construction.

`tests/test_crawl_lanes.py` and `tests/test_crawl.py` come closest to touching a real credential, and
they do the opposite. They delete `ANTHROPIC_API_KEY` to test the credential-gap error message, and
never supply a key to test the real proposal path. No CI job runs `record` or `crawl` against a real
device with a real API key at any point.

This gap matters more here than for a generic AI call. The output of `record` is the
[scenario file](../../docs/glossary.md#scenario-authoring) itself, the one artifact that the
deterministic `run` gate trusts completely once written (prime directive 1: AI authors, never
judges). A real model might produce a proposal that the parser drops without notice or maps to the
wrong action. That would degrade what gets written into the trusted artifact, and nothing in the
current suite would notice.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Capture real responses once, replay them as regression fixtures.** Run the propose step of
  `record` and the navigation step of `crawl` against a real model on a showcase screen. Save the raw
  response payloads, and add them as fixtures that the existing fake-backend tests can also parse. A
  captured real shape then becomes part of the permanent regression suite, not a one-time check.
- **A key-gated live smoke test for each loop.** Beyond replayed fixtures, add one
  `pytest.mark.skipif`-gated test per loop (`record`, `crawl`). Each test runs the real
  propose/navigate step against a real showcase screen with a real credential, and asserts that the
  result parses into a valid action. The tests follow the same non-gating-signal-first precedent as
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
- **Keep the credential-gap tests as they are.** `tests/test_crawl_lanes.py` and `tests/test_crawl.py`
  already cover the missing-key error path. This item adds the present-key path alongside them and
  does not replace them.
- **No change to the judge boundary.** Both additions verify that AI *authoring* output parses
  correctly. Neither introduces a model call anywhere near the deterministic verdict of `run` (prime
  directive 1). The new tests live entirely in the periphery test surface that `record` and `crawl`
  already occupy.

## Alternatives considered

- **Trust the fake-backend tests as sufficient, since the schema itself is documented.** A documented
  schema does not guarantee that a real model's output conforms to it in practice. Prompt drift, model
  updates, or an edge case in the element tree of a real showcase screen can each produce a response
  that the fakes never anticipated.
- **Add a live smoke test alone, and skip capturing fixtures.** A smoke test alone proves that the path
  works today, but leaves no artifact for future regression. Captured fixtures let a real shape keep
  being checked even between live runs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Capture real `record` propose-step responses as regression fixtures.
- [x] Capture real `crawl` navigation-step responses as regression fixtures.
- [x] Add a key-gated live smoke test for `record`'s propose loop.
- [x] Add a key-gated live smoke test for `crawl`'s navigation loop.

**Log**

- Landed the two key-gated live smoke tests first, signal-first (the BE-0282 precedent):
  `tests/test_real_model_smoke.py` drives `ClaudeAgent.next_action` and `ClaudeActionProposer.propose`
  against a real model over a committed showcase golden screen (no Simulator; only the model call is
  live), asserting each real response parses into the propose loop's action schema. They skip when no
  AI credential is configured, so the gate stays hermetic. Deterministic `FakeBackend` self-checks in
  the same file keep the loader and the validity assertions honest, so a live run validates for real.
  The two fixture-capture units remain, needing a real credential to record the responses.
- Built the capture harness for the two fixture units, keyless: `tests/real_model_support.py` adds a
  `RecordingBackend` that wraps the live backend and keeps the raw tool-use a real model returns
  before the propose loop parses it away, plus `save_fixture` and `load_fixture`. `save_fixture`
  serializes all tool-use blocks from the response; `load_fixture` returns a `_FixtureReplay` backend
  that replays every captured block in one `MessageResponse` — preserving a real multi-action turn
  intact (BE-0178 batched actions). `tests/test_real_model_fixtures.py` exercises the whole
  capture → save → load → replay chain deterministically (no credential), including a multi-block
  round-trip so the `_FixtureReplay` path is validated keylessly; it replays any committed fixture
  under `tests/fixtures/be0295/` (signal-first — skipped while none exists), and carries the
  key-gated live capture that writes the committed fixture. Both boxes stayed unchecked until the
  next entry. The shared screen loader, credential gate, and parse-validity assertions moved into
  `real_model_support`, so the smoke and fixture tests share one source of truth.
- Captured both fixtures from a real model through the `claude-code` backend (BE-0176), which reaches
  a real model over the `claude` CLI's own credential — so no `ANTHROPIC_API_KEY` was needed:
  `BAJUTSU_AI_PROVIDER=claude-code uv run pytest tests/test_real_model_fixtures.py -m live -k capture`.
  `tests/fixtures/be0295/record.json` and `crawl.json` hold the genuine tool-use each loop's prompt
  produced (`crawl.json` is a single `propose_actions` block whose `input.actions` carries four real
  actions — a real fixture the fakes never produced, though it is one block, not multiple). The committed-fixture replay tests now run deterministically on
  every gate — no credential — closing both fixture units. All four units done, so the item is
  **Implemented**; the key-gated live capture stays for re-recording when a prompt or model changes.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [PR #1233 — a real-API contract smoke lane for the AI backend adapters](https://github.com/bajutsu-e2e/bajutsu/pull/1233)
- `bajutsu/agents/claude.py`, `bajutsu/crawl/guide.py`, `bajutsu/crawl/tabs.py`,
  `tests/conftest.py` (`FakeBackend` / `FakeBlock`), `tests/test_claude_agent.py`,
  `tests/test_crawl_guide.py`, `tests/test_crawl_tabs.py`, `tests/test_crawl_lanes.py`,
  `tests/test_crawl.py`

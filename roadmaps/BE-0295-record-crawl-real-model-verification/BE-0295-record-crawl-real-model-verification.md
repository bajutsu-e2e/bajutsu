**English** · [日本語](BE-0295-record-crawl-real-model-verification-ja.md)

# BE-0295 — Real-model verification of the record and crawl propose loops

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0295](BE-0295-record-crawl-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0295") |
| Topic | Authoring experience |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`record`'s observe → propose → execute loop (`agents/claude.py`'s `ClaudeAgent.next_action`) and
`crawl`'s autonomous exploration (`crawl/guide.py`, `crawl/tabs.py`) both depend on parsing a real
model's structured, tool-use proposal into Bajutsu's own action schema. Every test that exercises
this parsing constructs `ClaudeAgent`/the crawl agents with `FakeBackend(FakeBlock(...))` — a
response shaped exactly the way the test's author expects, never a response a real model actually
produced. This item adds a real-model check for the parsing itself, distinct from and complementary
to the transport-level adapter check proposed in
[PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233).

## Motivation

[PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233)'s proposal asks "does the adapter round-trip with the real service at all" — a
transport question. This item asks a different one: "given a genuine record/crawl prompt, does a
real model's response actually parse into the propose-loop's action schema" — a semantic question
about the specific prompts and schemas `record` and `crawl` use, which a hand-built `FakeBlock`
cannot exercise by construction. `tests/test_crawl_lanes.py` and `tests/test_crawl.py` come closest
to touching a real credential, and do the opposite: they delete `ANTHROPIC_API_KEY` to test the
credential-gap error message, never supply one to test the real proposal path. No CI job runs
`record` or `crawl` against a real device with a real API key at any point.

This gap matters more here than for a generic AI call, because `record`'s output is the [scenario
file](../../docs/glossary.md#scenario-authoring) itself — the one artifact the deterministic `run` gate trusts completely once written
(prime directive 1: AI authors, never judges). A real model producing a proposal that the parser
silently drops or mis-maps would degrade what gets written into that trusted artifact, and nothing
in the current suite would notice.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Capture real responses once, replay them as regression fixtures.** Run `record`'s propose step
  and `crawl`'s navigation step against a real model on a showcase screen, save the raw response
  payloads, and add them as fixtures the existing fake-backend tests can also parse — so a captured
  real shape becomes part of the permanent regression suite, not just a one-time check.
- **A key-gated live smoke test for each loop.** Beyond replayed fixtures, add one
  `pytest.mark.skipif`-gated test per loop (`record`, `crawl`) that runs the real propose/navigate
  step against a real showcase screen with a real credential, asserting the result parses into a
  valid action — following the same non-gating-signal-first precedent as
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
- **Keep the credential-gap tests as they are.** `tests/test_crawl_lanes.py` and `tests/test_crawl.py`
  already cover the missing-key error path correctly; this item adds the present-key path alongside
  them rather than replacing them.
- **No change to the judge boundary.** Both additions verify that AI *authoring* output parses
  correctly; neither introduces a model call anywhere near `run`'s deterministic verdict
  (prime directive 1) — the new tests live entirely in the periphery test surface `record`/`crawl`
  already occupy.

## Alternatives considered

- **Trust the fake-backend tests as sufficient, since the schema itself is documented.** A documented
  schema does not guarantee a real model's output conforms to it in practice — prompt drift, model
  updates, or an edge case in a real showcase screen's element tree can all produce a response the
  fakes never anticipated.
- **Add only a live smoke test, skip capturing fixtures.** A smoke test alone proves the path works
  today but leaves no artifact for future regression; captured fixtures let a real shape keep being
  checked even between live runs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Capture real `record` propose-step responses as regression fixtures.
- [ ] Capture real `crawl` navigation-step responses as regression fixtures.
- [ ] Add a key-gated live smoke test for `record`'s propose loop.
- [ ] Add a key-gated live smoke test for `crawl`'s navigation loop.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [PR #1233 — a real-API contract smoke lane for the AI backend adapters](https://github.com/bajutsu-e2e/bajutsu/pull/1233)
- `bajutsu/agents/claude.py`, `bajutsu/crawl/guide.py`, `bajutsu/crawl/tabs.py`,
  `tests/conftest.py` (`FakeBackend` / `FakeBlock`), `tests/test_claude_agent.py`,
  `tests/test_crawl_guide.py`, `tests/test_crawl_tabs.py`, `tests/test_crawl_lanes.py`,
  `tests/test_crawl.py`

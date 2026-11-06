**English** · [日本語](BE-0300-ai-backend-real-api-smoke-ja.md)

# BE-0300 — Real-API contract smoke lane for the AI backend adapters

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0300](BE-0300-ai-backend-real-api-smoke.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0300") |
| Implementing PR | [#1348](https://github.com/bajutsu-e2e/bajutsu/pull/1348) |
| Topic | AI provider configuration |
<!-- /BE-METADATA -->

## Introduction

The vendor-neutral AI backend ([BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md))
has three adapters: the direct Anthropic API adapter, the Amazon Bedrock adapter, and the `ant`-CLI
adapter ([BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)). Every test
that touches them drives them through a hand-written double (`FakeAnthropic` / `FakeBlock` in
`tests/conftest.py`), never the real service.

No test in the suite and no CI job completes a real call to the Anthropic API, Bedrock, or the `ant`
CLI through Bajutsu's own adapter code. This item adds one smoke lane that does. The lane is opt-in,
gated on a key, and non-gating. It proves that each adapter translates a real response into the
vendor-neutral request/response contract, and it keeps a model away from the `run` verdict.

## Motivation

The fakes agree with what their authors believe the real service returns. `FakeBlock` sets
`.type = "tool_use"` unconditionally, and hand-built message objects stand in for whatever
`client.messages.create(...)` would hand back. Nothing checks that belief against the real API.

A real response can differ in ways a fake cannot represent by construction:

- A `stop_reason` of `"max_tokens"` arrives mid-tool-call.
- The API silently declines to honor a `tool_choice`.
- The service rejects a `cache_control: ephemeral` block.
- The Bedrock or `ant` adapter produces a response shaped differently from what the direct API path assumes.

`test_make_client_bedrock` shows the gap. It asserts `isinstance(client, AnthropicBedrock)` with fake
AWS credentials and never calls `.messages.create`.

This item does not relax prime directive 1. The `run` / CI verdict must stay free of any model call,
and this item touches none of that path. The modules `ai/anthropic.py`, `agents/anthropic_client.py`,
and `ai/registry.py` are periphery, behind the AI extra, and the deterministic core does not import
them.

What is missing is coverage of the periphery's own contract with the vendor it wraps, at the cheapest
possible level. A real call checks transport and schema, not semantics. A minimal prompt that costs a
handful of tokens is therefore enough to prove the plumbing.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A minimal live-call test, key-gated, asserting the contract only.** Add a test that calls
  `AnthropicBackend` with a trivial prompt and a forced `tool_choice`.
  - The test is gated with `pytest.mark.skipif` on a dedicated opt-in flag (e.g.
    `BAJUTSU_LIVE_AI_SMOKE=1`), in addition to `ANTHROPIC_API_KEY` (or the Bedrock or `ant`
    equivalent credential).
  - Key presence alone is not a safe gate. Contributor sessions already export the key for `record`
    (per `CLAUDE.md`) or for `triage --ai` (`bajutsu/cli/commands/triage.py`), so an ordinary
    `make check` would fire a real, paid call.
  - The test checks only that the adapter's normalized `MessageResponse` / `ToolUseBlock` shape comes
    back populated and parses. It never checks what the model chose to say, so it stays a wire-contract
    check and not a model-quality judgment.
- **One CI lane per adapter, opt-in and non-gating.** Each adapter (direct API, Bedrock, `ant`) gets a
  workflow job. The job supplies the real credential from repository secrets and runs the live-call
  test.
  - The job is triggered only via `workflow_dispatch`, never `pull_request`. A fork-triggered run
    would expose the credential.
  - This is the hard boundary already documented in `.github/workflows/devicefarm.yml`.
  - The per-adapter lane design follows the non-gating-signal-first precedent of
    [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
- **Record the ones left uncovered.** Not every credential is realistically available in CI (e.g. a
  live Bedrock role). Where a lane cannot be wired, say so explicitly in the item's Progress log, so
  the gap does not pass as covered.

## Alternatives considered

- **VCR-style response cassettes recorded once from a real call.** Cassettes are cheaper to run
  repeatedly and deterministic. But a cassette recorded once goes stale the way today's hand-written
  fakes do, because the adapter never re-observes the live API. Only a recurring live smoke lane, even
  a small one, keeps observing reality.
- **Rely on Anthropic's own SDK test suite to cover the wire contract.** The SDK's tests cover the SDK.
  They say nothing about whether Bajutsu's adapter code translates a real response into its own
  `MessageResponse` / `ToolUseBlock` types correctly, and that is the actual gap here.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a key-gated, minimal live-call test for the direct Anthropic API adapter.
- [x] Add the same for the Bedrock adapter, or record explicitly why it can't run in CI (e.g. a live Bedrock role).
- [x] Add the same for the `ant`-CLI adapter, or record explicitly why it can't run in CI.
- [x] Wire a non-gating, opt-in CI lane for the direct Anthropic API adapter; the Bedrock and `ant` CI lanes stay deferred (no live AWS role / OAuth CLI seat as a CI secret), as the log records.

**Log**

- `tests/test_ai_backend_live_smoke.py` calls a real provider through `AnthropicBackend` with a
  trivial forced-tool prompt. It asserts only that the reply lands as a populated, parseable neutral
  `MessageResponse` carrying a `ToolUseBlock`. That is a transport-and-schema check, never a
  model-quality one.
  - The file holds one test per adapter (`api-key`, `bedrock`, `ant`).
  - `bajutsu.ai.credential_gap` skips each test when its provider has no credential.
  - Deterministic `FakeAnthropic` self-checks in the same file keep the wire-contract assertion
    honest, so a live run genuinely validates rather than passing vacuously.
- The opt-in gate is the **`live` pytest marker**, not the `BAJUTSU_LIVE_AI_SMOKE` env flag that the
  design named as an example. The marker is deselected from the fast suite by default (`addopts`
  `not live`), so `make check` stays hermetic by structure even when a contributor's
  `ANTHROPIC_API_KEY` is set.
  - The sibling BE-0295 ([#1344](https://github.com/bajutsu-e2e/bajutsu/pull/1344)) merged first.
    It added this same `live` marker and `addopts` line for its own real-model smoke, so `main`
    already carries them.
  - This item reuses that marker rather than redefining it. Opt in with `-m live`.
- `.github/workflows/ai-smoke.yml` wires the **direct Anthropic API** lane only.
  - It runs on `workflow_dispatch` only, never on push or PR, so a fork run cannot see the
    credential. This is the `devicefarm.yml` boundary.
  - The `ANTHROPIC_API_KEY` secret is scoped to an `ai-smoke` Environment, and the lane is non-gating.
  - With the secret unset, the test skips and the job is a green no-op. The lane stays dormant until
    an operator wires it up.
- **Bedrock and `ant` lanes are deliberately not wired in CI.** A live Bedrock call needs an AWS role
  (with a provider-prefixed `BAJUTSU_BEDROCK_MODEL`). The `ant` adapter needs a signed-in OAuth CLI
  seat. Neither is realistically available as a repository secret. Their `-m live` tests still run
  locally or manually when the credential is present. Only the CI lane is deferred until such a
  credential exists.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/ai/anthropic.py`, `bajutsu/agents/anthropic_client.py`, `bajutsu/ai/registry.py`,
  `tests/conftest.py` (`FakeAnthropic` / `FakeBlock`), `tests/test_ai_anthropic_adapter.py`,
  `tests/test_anthropic_client.py`, `tests/test_ai_backend.py`

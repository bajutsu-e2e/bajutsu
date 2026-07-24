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

Every test that touches the vendor-neutral AI backend
([BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)) — the direct
Anthropic API adapter, the Amazon Bedrock adapter, and the `ant`-CLI adapter
([BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)) — drives it through
a hand-written double (`FakeAnthropic` / `FakeBlock` in `tests/conftest.py`), never the real service.
No test in the suite and no CI job ever completes a real call to the Anthropic API, Bedrock, or the
`ant` CLI through Bajutsu's own adapter code. This item adds one opt-in, key-gated, non-gating smoke
lane that does — proving the adapter's translation of a real response into the vendor-neutral
request/response contract, without putting a model anywhere near the `run` verdict.

## Motivation

The fakes are internally consistent with what their authors believe the real service returns:
`FakeBlock` sets `.type = "tool_use"` unconditionally, and hand-built message objects stand in for
whatever `client.messages.create(...)` would actually hand back. Nothing checks that belief against
the real API. A real response can differ in ways a fake cannot represent by construction: a
`stop_reason` of `"max_tokens"` arriving mid-tool-call, a `tool_choice` the API silently declines to
honor, a `cache_control: ephemeral` block the service rejects, or the Bedrock/`ant` adapters producing
a differently-shaped response than the direct API path assumes. `test_make_client_bedrock` illustrates
the gap precisely: it asserts `isinstance(client, AnthropicBedrock)` with fake AWS credentials and
never calls `.messages.create`.

This item is not a request to relax prime directive 1. The `run` / CI verdict must stay free of any model
call, and this item touches none of that path — `ai/anthropic.py`, `agents/anthropic_client.py`, and
`ai/registry.py` are periphery, behind the AI extra, and the deterministic core does not import them.
What is missing is coverage of the periphery's own contract with the vendor it wraps, at the cheapest
possible level: a real call is a transport-and-schema check, not a semantic one, so a minimal prompt
that costs a handful of tokens is enough to prove the plumbing.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A minimal live-call test, key-gated, asserting the contract only.** Add a test that calls
  `AnthropicBackend` with a trivial prompt and a forced `tool_choice`, gated via `pytest.mark.skipif`
  on a dedicated opt-in flag (e.g. `BAJUTSU_LIVE_AI_SMOKE=1`) in addition to `ANTHROPIC_API_KEY` (or
  the Bedrock/`ant` equivalent credential) — key presence alone isn't a safe gate, since contributor
  sessions that already export the key for `record` (per `CLAUDE.md`) or for `triage --ai`
  (`bajutsu/cli/commands/triage.py`) would otherwise fire a real, paid call on an ordinary
  `make check`. The test only checks that the adapter's normalized
  `MessageResponse`/`ToolUseBlock` shape comes back populated and parses — never anything about what
  the model chose to say, keeping this a wire-contract check and not a model-quality judgment.
- **One CI lane per adapter, opt-in and non-gating.** A workflow job per adapter (direct API, Bedrock,
  `ant`) that supplies the real credential from repository secrets and runs the live-call test,
  triggered only via `workflow_dispatch` — never `pull_request`, which would expose the credential to
  a fork-triggered run — mirroring the hard boundary already documented in
  `.github/workflows/devicefarm.yml`. This per-adapter lane design follows the same
  non-gating-signal-first precedent as
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
- **Record the ones left uncovered.** Not every credential is realistically available in CI (e.g. a
  live Bedrock role); where a lane can't be wired, say so explicitly in the item's Progress log rather
  than let the gap pass as covered.

## Alternatives considered

- **VCR-style response cassettes recorded once from a real call.** Cheaper to run repeatedly and
  deterministic, but a cassette recorded once goes stale exactly the way today's hand-written fakes
  do — it stops the adapter from ever re-observing the live API. A recurring live smoke lane, even
  small, is the only design that keeps observing reality.
- **Rely on Anthropic's own SDK test suite to cover the wire contract.** The SDK's tests cover the
  SDK; they say nothing about whether Bajutsu's adapter code translates a real response into its own
  `MessageResponse`/`ToolUseBlock` types correctly, which is the actual gap here.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a key-gated, minimal live-call test for the direct Anthropic API adapter.
- [x] Add the same for the Bedrock adapter, or record explicitly why it can't run in CI (e.g. a live Bedrock role).
- [x] Add the same for the `ant`-CLI adapter, or record explicitly why it can't run in CI.
- [x] Wire non-gating, opt-in CI lanes per adapter.

**Log**

- `tests/test_ai_backend_live_smoke.py` calls a real provider through `AnthropicBackend` with a
  trivial forced-tool prompt and asserts only that the reply lands as a populated, parseable neutral
  `MessageResponse` carrying a `ToolUseBlock` — a transport-and-schema check, never a model-quality
  one. One test per adapter (`api-key`, `bedrock`, `ant`), each skipped by `bajutsu.ai.credential_gap`
  when its provider has no credential. Deterministic `FakeAnthropic` self-checks in the same file keep
  the wire-contract assertion honest, so a live run genuinely validates rather than passing vacuously.
- The opt-in gate is the **`live` pytest marker**, not the `BAJUTSU_LIVE_AI_SMOKE` env flag the design
  named as an example: the marker is deselected from the fast suite by default (`addopts` `not live`),
  so `make check` stays hermetic by structure even when a contributor's `ANTHROPIC_API_KEY` is set —
  the same precedent the sibling BE-0295 established for its real-model smoke. Opt in with `-m live`.
- `.github/workflows/ai-smoke.yml` wires the **direct Anthropic API** lane only: `workflow_dispatch`
  only (never push/PR, so a fork run can't see the credential — the `devicefarm.yml` boundary), the
  `ANTHROPIC_API_KEY` secret scoped to an `ai-smoke` Environment, non-gating. With the secret unset the
  test skips and the job is a green no-op, so the lane stays dormant until an operator wires it up.
- **Bedrock and `ant` lanes are deliberately not wired in CI.** A live Bedrock call needs an AWS role
  (with a provider-prefixed `BAJUTSU_BEDROCK_MODEL`) and `ant` needs a signed-in OAuth CLI seat —
  neither is realistically available as a repository secret. Their `-m live` tests still run locally /
  manually when the credential is present; only the CI lane is deferred until such a credential exists.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/ai/anthropic.py`, `bajutsu/agents/anthropic_client.py`, `bajutsu/ai/registry.py`,
  `tests/conftest.py` (`FakeAnthropic` / `FakeBlock`), `tests/test_ai_anthropic_adapter.py`,
  `tests/test_anthropic_client.py`, `tests/test_ai_backend.py`

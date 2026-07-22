**English** · [日本語](BE-XXXX-ai-backend-real-api-smoke-ja.md)

# BE-XXXX — Real-API contract smoke lane for the AI backend adapters

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ai-backend-real-api-smoke.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
  sessions that already export the key for `record` (per `CLAUDE.md`) or for `triage --agent claude`
  (`bajutsu/cli/commands/triage.py`) would otherwise fire a real, paid call on an ordinary
  `make check`. The test only checks that the adapter's normalized
  `MessageResponse`/`ToolUseBlock` shape comes back populated and parses — never anything about what
  the model chose to say, keeping this a wire-contract check and not a model-quality judgment.
- **One CI lane per adapter, opt-in and non-gating.** A workflow job per adapter (direct API, Bedrock,
  `ant`) that supplies the real credential from repository secrets and runs the live-call test,
  triggered only via `workflow_dispatch` — never `pull_request`, which would expose the credential to
  a fork-triggered run — mirroring the hard boundary already documented in
  `.github/workflows/devicefarm.yml`. This follows the same non-gating-signal-first precedent as
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

- [ ] Add a key-gated, minimal live-call test for the direct Anthropic API adapter.
- [ ] Add the same for the Bedrock adapter, or record explicitly why it can't run in CI (e.g. a live Bedrock role).
- [ ] Add the same for the `ant`-CLI adapter, or record explicitly why it can't run in CI.
- [ ] Wire non-gating, opt-in CI lanes per adapter.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/ai/anthropic.py`, `bajutsu/agents/anthropic_client.py`, `bajutsu/ai/registry.py`,
  `tests/conftest.py` (`FakeAnthropic` / `FakeBlock`), `tests/test_ai_anthropic_adapter.py`,
  `tests/test_anthropic_client.py`, `tests/test_ai_backend.py`

**English** · [日本語](BE-XXXX-ant-cli-ai-provider-ja.md)

# BE-XXXX — ant CLI as an OAuth-authenticated AI provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ant-cli-ai-provider.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
| Related | [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Add a third `AiConfig.provider` value, backed by Anthropic's official `ant` CLI, so a local
developer can authenticate every Bajutsu AI path (`record`, `triage --ai`, `run
--dismiss-alerts`, `crawl --explore`, MCP enrich) with `ant auth login` — a browser-based OAuth
flow against their own Console workspace — instead of copying a long-lived
`ANTHROPIC_API_KEY` into `.env`. This is a credential-ergonomics change, not a cost or
model-family change: `ant auth login` still bills the same Console/API workspace per token,
the same way a static key does. The value is a scoped, revocable, browser-issued credential
(`ant auth status` / `ant auth logout`) in place of a plaintext secret sitting in a
gitignored file.

## Motivation

[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) made
every AI path reach a model through one factory, `anthropic_client.make_client()`, keyed by
`AiConfig.provider` (`anthropic` | `bedrock`). Both existing providers assume a static secret:
an API key named by `ai.keyEnv`, or AWS IAM credentials for Bedrock. For a developer running
Bajutsu locally, that means creating an Anthropic Console API key, pasting it into `.env`, and
manually rotating or revoking it later — the exact kind of long-lived plaintext secret that
OAuth-based tooling exists to avoid.

Bajutsu already has *one* OAuth-based AI path: `record --agent claude-code`
([`claude_code_agent.py`](../../../../bajutsu/claude_code_agent.py)) shells out to the Claude
Code CLI (`claude -p`), authenticated by a Claude Pro/Max subscription token
(`CLAUDE_CODE_OAUTH_TOKEN`). That path is deliberately out of scope here — it is
subscription-billed, `record`-only, and text-only (screenshots are not sent; the agent reasons
from the accessibility tree). This item targets a different, complementary gap: an
OAuth-authenticated path that stays on the **API** (per-token billing, full Messages API
parity — vision, tool-use, the exact request shape the other five AI paths already send), so
credential ergonomics improve without giving up any capability the current `anthropic`
provider has.

Anthropic's `ant` CLI is built for exactly this substitution. `ant auth login` opens a
browser OAuth flow, scopes the resulting token to one Console workspace, and stores it under
`$ANTHROPIC_CONFIG_DIR` — `ant messages create` then speaks the full Messages API, including
multimodal content blocks (images, PDFs, via `@file` inlining) and repeatable `--tool` flags
for tool-use turns, with `--format json` / `--transform` for reliable machine parsing. That
covers every capability the six existing AI call sites use today.

## Detailed design

Proposal altitude. Work lands entirely inside the existing single-factory seam
(`bajutsu/anthropic_client.py`); no call site (`claude_agent.py`, `claude_triage.py`,
`alerts.py`, `claude_enrich_agent.py`, `crawl_guide.py`, `crawl_tabs.py`) changes, mirroring how
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) added
Bedrock without touching a call site. The work is MECE along four pieces:

### 1. Attribute audit of the current call sites

Enumerate exactly which `anthropic` SDK request fields the six call sites set (system prompt,
messages with text / image / tool_use / tool_result content blocks, `tools`, `model`,
`max_tokens`) and which response attributes they read (`message.content[*].type` /
`.text` / `.id` / `.input`, `message.stop_reason`). This defines the exact surface the new
adapter's shim must reproduce — not the full Anthropic SDK type, only what these six paths
touch.

### 2. `ant`-backed adapter behind `make_client()`

Add a `provider: ant-cli` branch to `make_client()` that returns a small object exposing
`.messages.create(...)` with the audited surface: it serializes the call into an `ant messages
create --format json ...` invocation (via `subprocess`), and reshapes the parsed JSON response
into lightweight objects matching the attributes piece 1 identified. Errors (non-zero exit,
`ant`'s JSON error body) map to whatever exception type the call sites already catch from the
`anthropic` SDK, so their existing error handling needs no change.

### 3. Fail-closed credential check for a binary-backed provider

`credential_gap()` today checks "is the named env var set". For `ant-cli`, presence of a
value isn't the right check — extend it to: the `ant` binary is on `PATH`, and either
`ANTHROPIC_API_KEY` is set (which `ant` also honors) or `ant auth status` reports an active
credential. Missing either fails closed with a message pointing at `ant auth login`, matching
BE-0047's existing discipline of a hard, actionable error over a silent fallback.

### 4. Config, docs, and tests

- Config: `ai: { provider: ant-cli }` in `AiSettings` / per-target `ai` overrides — no new
  fields, following the existing `provider` enum pattern.
- Docs (bilingual, `docs/` + `docs/ja/`, `README.md`, `.env.example`): document `ant-cli` as a
  **local development** option — it needs the external `ant` binary installed
  (Homebrew / release binary / `go install`, the same category of local prerequisite as
  `idb_companion` for the idb backend) and an interactive browser for `ant auth login`, so it
  is not a fit for CI or headless hosts (where `anthropic` with a static key, or Bedrock IAM,
  remains the right choice).
- Tests: unit tests that mock `subprocess.run`/`Popen` for the new adapter (no network, no
  device — runs in the fast Linux gate), asserting the serialized `ant` invocation and the
  parsed-response shape, following the precedent of the existing Bedrock adapter tests.

### Prime-directive compliance

The new provider is reached only through the existing Tier-1 AI factory; it adds no new call
site and touches nothing on the `run` / CI gate. Redaction ([BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
/ `redaction.py`) already runs before any call site invokes `.messages.create(...)`, so it
applies to the `ant-cli` provider automatically — no new redaction logic is needed. Provider
choice stays config (`targets.<name>.ai` / `defaults.ai`), so drivers and the runner stay
app-agnostic; nothing about determinism changes.

## Alternatives considered

- **Wait for [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)'s
  provider registry and land `ant-cli` as its first follow-up adapter.** Cleaner long-term
  (a registered adapter instead of another factory branch), but BE-0104 is itself an
  unimplemented design proposal with no committed timeline, and gating a small, self-contained
  credential improvement on a larger refactor isn't worth the wait. This item deliberately lands
  directly in the current `provider: anthropic | bedrock | ant-cli` factory; if/when BE-0104
  ships its registry, migrating this adapter behind it is a small, isolated follow-up (the
  adapter's internals — the audited request/response surface — carry over largely unchanged).
- **Extend `record --agent claude-code` (the Claude Code CLI path) instead.** Rejected for this
  item's goal: that path is subscription-billed and text-only by design, so widening it to the
  other five AI paths would mean adding vision support to a path built around the coding agent's
  print mode — a materially different and larger undertaking than wrapping `ant`, which already
  speaks the full Messages API. It remains a separate, valid option for someone whose goal is
  cost (subscription quota) rather than credential ergonomics, and is unaffected by this item.
- **Extract an OAuth bearer token from `ant`'s credential store and hand it to the `anthropic`
  Python SDK directly** (e.g. via `ant auth print-credentials --access-token`), instead of
  shelling out per call. Rejected: it requires Bajutsu to manage token refresh and expiry itself,
  duplicating logic `ant` already owns; shelling out to `ant messages create` per call — the same
  pattern Anthropic documents for Claude Code's own use of `ant` — keeps token lifecycle
  entirely inside the `ant` binary.
- **Do nothing / keep `anthropic` and `bedrock` only.** Rejected: it leaves local development
  with a static API key as the only zero-config option, which is exactly the friction this item
  removes.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Attribute audit — the exact `anthropic` SDK request/response surface the six AI call sites use
- [ ] `ant`-backed adapter — `provider: ant-cli` branch in `make_client()`, subprocess-backed `.messages.create(...)` shim
- [ ] Fail-closed credential check — `ant` on `PATH` + `ANTHROPIC_API_KEY` or `ant auth status`
- [ ] Config, docs (bilingual), and subprocess-mocked tests

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.

## References

`bajutsu/anthropic_client.py` (`make_client` / `resolve_model` / `credential_gap` / `provider` /
`AiConfig` — the seam this item extends), the six AI call sites
`bajutsu/claude_agent.py` · `bajutsu/claude_triage.py` · `bajutsu/alerts.py` ·
`bajutsu/claude_enrich_agent.py` · `bajutsu/crawl_guide.py` · `bajutsu/crawl_tabs.py`,
`bajutsu/claude_code_agent.py` (the existing, complementary Claude Code CLI / subscription
path for `record`), `bajutsu/redaction.py` (the guarantee this provider inherits unchanged),
`.env.example` (documents today's `ANTHROPIC_API_KEY`-only local setup),
[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
(provider-agnostic, redacted, fail-closed AI paths), [BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
(precedent for adding a provider without touching a call site),
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) (the
future registry this adapter could migrate behind), and Anthropic's `ant` CLI docs
([Quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart),
[Authentication](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication),
[Using the CLI](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/using)).

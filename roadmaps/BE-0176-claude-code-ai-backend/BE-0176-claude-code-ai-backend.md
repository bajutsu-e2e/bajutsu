**English** · [日本語](BE-0176-claude-code-ai-backend-ja.md)

# BE-0176 — Revive Claude Code as an AiBackend adapter with file-based vision

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0176](BE-0176-claude-code-ai-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0176") |
| Implementing PR | [#713](https://github.com/bajutsu-e2e/bajutsu/pull/713), [#745](https://github.com/bajutsu-e2e/bajutsu/pull/745) |
| Topic | AI provider configuration |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu will bring back a Claude Code backend, this time as a single adapter behind the
vendor-neutral `AiBackend` seam that
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) introduced. Unlike the
text-only, `record`-only detour that
[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) removed, the adapter
serves *every* AI path with full vision: `record`, `crawl`, `enrich`, `triage --ai`,
`run --dismiss-alerts`, and the SwiftUI tab locator.

Vision works by writing each screenshot to a scratch file and telling the CLI its path. Claude Code
then reads the image from disk with the `Read` tool, which is the mechanism the earlier attempt
lacked.

The backend registers under the provider name `claude-code`. Billing draws on the user's Claude Pro
/ Max / Console subscription instead of pay-per-token API credits.

## Motivation

Before BE-0163, a `ClaudeCodeAgent` let contributors author scenarios against their Claude
subscription quota instead of an `ANTHROPIC_API_KEY`. It was removed for two concrete reasons. This
item fixes the second one structurally.

- **It was text-only.** The old agent reasoned from the accessibility element list alone and never
  sent the screenshot. The vision-dependent paths could not use it at all: `ClaudeAlertLocator`,
  `ClaudeTabLocator`, and the screenshot-carrying `record` / `crawl` / `enrich` / `triage` turns.
- **It sat at the wrong layer.** The old integration implemented the high-level `Agent` protocol
  directly (`next_action` / `plan`). It therefore covered only `record`, needed a parallel
  `ClaudeCodeActionProposer` for `crawl`, and never reached `triage`, `enrich`, or the vision
  locators. Each new AI path would have needed its own Claude Code variant.

BE-0163 replaced the old backend with the `ant` provider: the official Anthropic CLI's OAuth
credential feeding the *SDK* adapter. That gives subscription billing with vision across all paths,
which solved the billing goal.

But `ant` and `claude-code` are different offerings. `ant` bills a Claude Console seat through the
Anthropic API. A genuine Claude Code backend bills the Claude Code subscription (Pro / Max) that
many contributors already hold, and it reuses the `claude` CLI they have already authenticated.

Reviving the backend is worthwhile, and now cheap, because BE-0104 created the right seam: a single
`create_message` turn (system prompt + user text/images + forced tool call → tool-use blocks). One
adapter at that layer is automatically shared by all six paths, vision included. The one real gap is
vision transport, which the file-path mechanism closes.

## Detailed design

The whole feature is one new adapter plus its registration. No AI call site changes, which is the
point of the BE-0104 seam.

### 1. `ClaudeCodeBackend` adapter (`bajutsu/ai/claude_code.py`)

The adapter implements `AiBackend.create_message(MessageRequest) -> MessageResponse` by shelling out
to `claude -p --output-format json` (print mode). It mirrors the injectable-`Runner` /
`_default_runner` pattern that the removed `claude_code_agent.py` and the current `ant` provider
both use: a single subprocess site, and a `Runner` seam for tests.

- **Forced tool call → structured output.** BE-0104's `tool_choice` is `AnyTool` or `NamedTool` over
  `request.tools`. The adapter maps it to the CLI's `--json-schema`:
  - `NamedTool`, or `AnyTool` over a single tool: pass that tool's `input_schema` directly. Wrap the
    returned `structured_output` as `ToolUseBlock(name=tool.name, input=…)`. Every path but
    `next_action` is single-tool. Each of `propose_actions` (crawl), `propose_assertions` (enrich),
    `plan`, `diagnose` (triage), `resolve_alert` (`--dismiss-alerts`), and `find_tabs` (tab locator)
    offers exactly one `ToolDef`.
  - `AnyTool` over several tools (only `ClaudeAgent.next_action`, with `tap` / `type_text` /
    `wait_for` / `finish`): wrap the schemas in a discriminator schema
    `{"tool": {"enum": [names]}, "arguments": {"oneOf": [each schema]}}`. List the per-tool schemas
    in the appended system prompt. Map `{tool, arguments}` back to `ToolUseBlock`.
  - When `structured_output` is absent or malformed, return an empty `MessageResponse` with no
    `ToolUseBlock`. Each caller already tolerates `first_tool_use()` being `None`.
- **Vision via scratch files.** For each `ImagePart` in the request, write its bytes to a PNG in a
  per-call temp directory. Append a text line to the user message that names the path (for example,
  "The current screen is at `<path>`; use Read to view it."). Invoke the CLI with
  `--add-dir <scratchdir>` and `--allowedTools Read` scoped to that directory, so Claude Code reads
  the image from disk. Delete the directory when the call returns. Text-only requests write no files
  and allow no tools.
- **Tool restriction / prompt-injection boundary (extends
  [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)).**
  On-screen text and element labels are attacker-influenced input. Everything except `Read` (scoped
  to the scratch dir) is denied via `--disallowedTools`. `--permission-mode` stays fail-closed, so
  non-interactive mode denies any unanticipated permission request instead of passing it. `Read` is
  the *only* capability the vision transport needs.
- **Subscription billing.** Strip `ANTHROPIC_API_KEY` from the child environment. The CLI's auth
  precedence puts an API key above the subscription token, which would silently bill the API. Run
  from a scratch cwd, so the CLI does not load this repo's `CLAUDE.md`, skills, or MCP servers into
  the call.
- **Redaction (BE-0047 / BE-0097).** Text is already redacted upstream before it reaches the neutral
  request. Images cannot be redacted and reach only the user-authenticated CLI. This contract does
  not change: the file is local scratch, read by the local CLI.
- **Usage pass-through.** Populate `MessageResponse.usage` best-effort from the CLI JSON envelope's
  usage/cost fields, so `bajutsu.usage` reports it. The data is for reporting only and never sits on
  the verdict path.

### 2. Registry registration (`bajutsu/ai/registry.py`)

Register `claude-code` as its own `Adapter`, with a distinct `factory` and `credential_gap`. Unlike
`ant`, it does **not** route through `AnthropicBackend` or the SDK. It is a genuinely separate
adapter, not another alias on the shared one.

`credential_gap` reports whether the `claude` binary is present and has a usable credential. The
message is actionable and points at `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN`. The path
therefore fails closed under BE-0047 instead of constructing a runner with no credential.
`known_providers()` then includes the new provider automatically.

### 3. Surfacing (`serve` + docs)

- The `serve` Settings provider selector offers `claude-code` alongside `api-key` / `bedrock` /
  `ant`.
- `doctor` reports the `claude-code` credential gap like the others.
- The AI provider docs and README document the provider in both languages, including the vision
  transport and the tool-restriction boundary.

### 4. Tests

- Adapter unit tests with an injected `Runner`:
  - `NamedTool` and single-tool `AnyTool` map straight through.
  - Multi-tool `AnyTool` round-trips the discriminator schema.
  - Missing or malformed `structured_output` yields no `ToolUseBlock`.
  - An `ImagePart` writes a scratch file, names its path in the prompt, allows `Read`, and cleans
    up.
  - The child env strips `ANTHROPIC_API_KEY`.
- Registry test: `claude-code` resolves to its own adapter and appears in `known_providers()`. Its
  `credential_gap` reports the missing-binary case.

## Alternatives considered

- **Call the Claude Agent SDK (Python `claude-agent-sdk`) instead of the CLI.** Rejected for now.
  The SDK spawns the same `claude` CLI with the same credentials, so it buys no authentication or
  billing advantage. It adds a dependency and a second process-management surface. The CLI matches
  the removed backend's proven pattern and the current `ant` provider's single-subprocess design.
  Revisit if a path ever needs multi-turn tool-result feedback, which BE-0104 deliberately does not
  model.
- **Embed the screenshot as base64 in the CLI prompt argument (no scratch file, no `Read`).**
  Rejected. It revives the earlier failure mode: the CLI's prompt-argument path is not a reliable
  image channel, and it blows up argument size. The file-path approach is the whole insight of this
  item.
- **Keep only `ant` and don't revive `claude-code`.** Rejected. `ant` bills a Console seat through
  the API. Many contributors instead hold a Claude Code Pro / Max subscription and have
  authenticated it in the `claude` CLI. The two are complementary providers, not duplicates.
- **Reinstate the old high-level `Agent`-protocol integration.** Rejected. It structurally cannot
  reach the vision locators, `triage`, or `enrich`, and it would need per-path variants. The
  `AiBackend` seam is what makes one adapter enough.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `ClaudeCodeBackend` adapter — `create_message`, structured-output mapping, vision scratch
      files, tool restriction, subscription env, usage pass-through (`bajutsu/ai/claude_code.py`).
- [x] Registry registration of the `claude-code` provider + its `credential_gap` (plus
      `resolved_provider` for surfaces).
- [x] Surfacing: `serve` Settings selector + provider handlers, `doctor` (via `ai_availability`),
      and bilingual `configuration.md`.
- [x] Tests: adapter unit tests (mapping, vision, env) and registry test.

**Log**

- Implemented the adapter behind the BE-0104 `AiBackend` seam so all six AI paths gain a
  `claude-code` option with file-path vision; registered the provider, surfaced it in `serve` /
  `doctor` / docs, and added the tests. `make check` green.
- Local `crawl` verification: the prompt was fed as the trailing `[prompt]` positional after the
  variadic `--disallowedTools` / `--allowedTools` / `--add-dir`, so `claude` swallowed it as bogus
  tool names. Fixed by feeding the prompt on stdin and passing deny tools as space-separated tokens
  with the variadic flags last; a non-zero exit now surfaces the stdout envelope so errors (e.g. an
  auth 401) are actionable.
- Follow-up ([#745](https://github.com/bajutsu-e2e/bajutsu/pull/745)): made the provider startup
  disclosure per-provider. The adapter contract gained an `announce` capability so each provider
  produces its own lines — the Anthropic SDK names only provider and model (it has no
  reasoning-effort
  knob), while `claude-code` adds the effort it honors and the forced-subscription auth line this
  item
  introduced. `record` and `crawl` now share one `announce_ai` sink, so `crawl` discloses the model
  (and, on `claude-code`, effort and auth) it previously omitted. `make check` green.

## References

- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) — the
  vendor-neutral `AiBackend` seam this adapter plugs into.
- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — removed the old
  text-only, `record`-only Claude Code backend and shipped the `ant` OAuth provider.
- [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)
  — the authoring-agent tool-restriction boundary this backend extends to the `Read`-for-vision
  case.
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — data-sovereignty /
  credential-gap semantics the adapter preserves.

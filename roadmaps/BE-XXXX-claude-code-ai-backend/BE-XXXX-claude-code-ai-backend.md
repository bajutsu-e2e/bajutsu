**English** · [日本語](BE-XXXX-claude-code-ai-backend-ja.md)

# BE-XXXX — Revive Claude Code as an AiBackend adapter with file-based vision

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-claude-code-ai-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

Bring back a Claude Code backend — but this time as a single adapter behind the vendor-neutral
`AiBackend` seam that [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
introduced, so it serves *every* AI path (`record`, `crawl`, `enrich`, `triage --ai`,
`run --dismiss-alerts`, and the SwiftUI tab locator) with full vision, rather than the text-only,
`record`-only detour that [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
removed. Vision works by writing each screenshot to a scratch file and telling the CLI its path, so
Claude Code reads the image from disk with the `Read` tool — the mechanism the earlier attempt
lacked. The backend registers under the provider name `claude-code`; billing draws on the user's
Claude Pro / Max / Console subscription instead of pay-per-token API credits.

## Motivation

Before BE-0163, a `ClaudeCodeAgent` let contributors author scenarios against their Claude
subscription quota instead of an `ANTHROPIC_API_KEY`. It was removed for two concrete reasons, and
the second is what this item fixes structurally:

- **It was text-only.** The old agent reasoned from the accessibility element list alone; it never
  sent the screenshot. So the vision-dependent paths (`ClaudeAlertLocator`, `ClaudeTabLocator`, and
  the screenshot-carrying `record` / `crawl` / `enrich` / `triage` turns) could not use it at all.
- **It sat at the wrong layer.** The old integration implemented the high-level `Agent` protocol
  directly (`next_action` / `plan`), which is why it only ever covered `record`, needed a parallel
  `ClaudeCodeActionProposer` for `crawl`, and never reached `triage` / `enrich` / the vision
  locators. Each new AI path would have needed its own Claude Code variant.

BE-0163 replaced it with the `ant` provider — the official Anthropic CLI's OAuth credential feeding
the *SDK* adapter — which does give subscription billing with vision across all paths. That solved
the billing goal. But `ant` and `claude-code` are not the same offering: `ant` bills a Claude
Console seat through the Anthropic API, whereas a genuine Claude Code backend bills the Claude Code
subscription (Pro / Max) that many contributors already hold, and reuses the `claude` CLI they
already have authenticated. Reviving it is worthwhile — and now cheap — because BE-0104 created the
right seam: a single `create_message` turn (system prompt + user text/images + forced tool call →
tool-use blocks). One adapter at that layer is automatically shared by all six paths, vision
included. The only real gap is vision transport, which the file-path mechanism closes.

## Detailed design

The whole feature is one new adapter plus its registration; no AI call site changes (that is the
point of the BE-0104 seam).

### 1. `ClaudeCodeBackend` adapter (`bajutsu/ai/claude_code.py`)

Implements `AiBackend.create_message(MessageRequest) -> MessageResponse` by shelling out to
`claude -p --output-format json` (print mode), mirroring the injectable-`Runner` /
`_default_runner` pattern the removed `claude_code_agent.py` and the current `ant` provider both
use (a single subprocess site, a `Runner` seam for tests).

- **Forced tool call → structured output.** BE-0104's `tool_choice` is `AnyTool` or `NamedTool`
  over `request.tools`. Map it to the CLI's `--json-schema`:
  - `NamedTool`, or `AnyTool` over a single tool: pass that tool's `input_schema` directly and
    wrap the returned `structured_output` as `ToolUseBlock(name=tool.name, input=…)`. Every path
    but `next_action` is single-tool — `propose_actions` (crawl), `propose_assertions` (enrich),
    `plan`, `diagnose` (triage), `resolve_alert` (`--dismiss-alerts`), and `find_tabs` (tab
    locator) each offer exactly one `ToolDef`.
  - `AnyTool` over several tools (only `ClaudeAgent.next_action`, with `tap` / `type_text` /
    `wait_for` / `finish`): wrap in a discriminator schema
    `{"tool": {"enum": [names]}, "arguments": {"oneOf": [each schema]}}`, list the per-tool schemas
    in the appended system prompt, and map `{tool, arguments}` back to `ToolUseBlock`.
  - When `structured_output` is absent or malformed, return an empty `MessageResponse` (no
    `ToolUseBlock`); each caller already tolerates `first_tool_use()` being `None`.
- **Vision via scratch files.** For each `ImagePart` in the request, write its bytes to a PNG in a
  per-call temp directory and append a text line to the user message naming the path (e.g.
  "The current screen is at `<path>`; use Read to view it."). Invoke the CLI with
  `--add-dir <scratchdir>` and `--allowedTools Read` scoped to that directory, so Claude Code reads
  the image from disk. Delete the directory when the call returns. Text-only requests write no
  files and allow no tools.
- **Tool restriction / prompt-injection boundary (extends
  [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)).**
  On-screen text and element labels are attacker-influenced input. Everything except `Read` (scoped
  to the scratch dir) is denied via `--disallowedTools`, and `--permission-mode` stays fail-closed
  so any unanticipated permission request in non-interactive mode is denied, not passed. `Read` is
  the *only* capability the vision transport needs.
- **Subscription billing.** Strip `ANTHROPIC_API_KEY` from the child environment (the CLI's auth
  precedence puts an API key above the subscription token, which would silently bill the API), and
  run from a scratch cwd so the CLI does not load this repo's `CLAUDE.md` / skills / MCP servers
  into the call.
- **Redaction (BE-0047 / BE-0097).** Text is already redacted upstream before it reaches the
  neutral request; images cannot be redacted and reach only the user-authenticated CLI. No change
  to that contract — the file is local scratch, read by the local CLI.
- **Usage pass-through.** Populate `MessageResponse.usage` best-effort from the CLI JSON
  envelope's usage/cost fields, so `bajutsu.usage` reports it (reporting only, never on the
  verdict path).

### 2. Registry registration (`bajutsu/ai/registry.py`)

Register `claude-code` as its own `Adapter` (a distinct `factory` + `credential_gap`) — unlike
`ant`, it does **not** route through `AnthropicBackend` / the SDK, so it is a genuinely separate
adapter, not another alias on the shared one. `credential_gap` reports whether the `claude` binary
is present and has a usable credential (an actionable message pointing at `claude setup-token` /
`CLAUDE_CODE_OAUTH_TOKEN`), so the path fails closed under BE-0047 rather than constructing a
runner with no credential. `known_providers()` then includes it automatically.

### 3. Surfacing (`serve` + docs)

- `serve` Settings provider selector offers `claude-code` alongside `api-key` / `bedrock` / `ant`.
- `doctor` reports the `claude-code` credential gap like the others.
- Document the provider (both languages) in the AI provider docs and README, including the vision
  transport and the tool-restriction boundary.

### 4. Tests

- Adapter unit tests with an injected `Runner`: `NamedTool` and single-tool `AnyTool` map straight
  through; multi-tool `AnyTool` round-trips the discriminator schema; missing/malformed
  `structured_output` yields no `ToolUseBlock`; an `ImagePart` writes a scratch file, names its
  path in the prompt, allows `Read`, and cleans up; the child env strips `ANTHROPIC_API_KEY`.
- Registry test: `claude-code` resolves to its own adapter and appears in `known_providers()`; its
  `credential_gap` reports the missing-binary case.

## Alternatives considered

- **Call the Claude Agent SDK (Python `claude-agent-sdk`) instead of the CLI.** Rejected for now:
  the SDK spawns the same `claude` CLI with the same credentials, so it buys no authentication or
  billing advantage, while adding a dependency and a second process-management surface. The CLI
  matches the removed backend's proven pattern and the current `ant` provider's single-subprocess
  design. Revisit if a path ever needs multi-turn tool-result feedback (which BE-0104 deliberately
  does not model).
- **Embed the screenshot as base64 in the CLI prompt argument (no scratch file, no `Read`).**
  Rejected: it revives the earlier failure mode (the CLI's prompt-argument path is not a reliable
  image channel and blows up argument size), and the file-path approach is the whole insight of
  this item.
- **Keep only `ant` and don't revive `claude-code`.** Rejected: `ant` bills a Console seat through
  the API, not the Claude Code Pro / Max subscription many contributors already hold and have
  authenticated in the `claude` CLI. The two are complementary providers, not duplicates.
- **Reinstate the old high-level `Agent`-protocol integration.** Rejected: it structurally can't
  reach the vision locators / `triage` / `enrich` and would need per-path variants. The `AiBackend`
  seam is exactly what makes one adapter enough.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `ClaudeCodeBackend` adapter — `create_message`, structured-output mapping, vision scratch
      files, tool restriction, subscription env, usage pass-through (`bajutsu/ai/claude_code.py`).
- [ ] Registry registration of the `claude-code` provider + its `credential_gap`.
- [ ] Surfacing: `serve` Settings selector, `doctor`, and bilingual docs / README.
- [ ] Tests: adapter unit tests (mapping, vision, env) and registry test.

## References

- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) — the
  vendor-neutral `AiBackend` seam this adapter plugs into.
- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — removed the old
  text-only, `record`-only Claude Code backend and shipped the `ant` OAuth provider.
- [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)
  — the authoring-agent tool-restriction boundary this backend extends to the `Read`-for-vision case.
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — data-sovereignty /
  credential-gap semantics the adapter preserves.

**English** · [日本語](BE-0163-ant-cli-oauth-provider-ja.md)

# BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0163](BE-0163-ant-cli-oauth-provider.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0163") |
| Implementing PR | [#689](https://github.com/bajutsu-e2e/bajutsu/pull/689) |
| Topic | AI provider configuration |
| Related | [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md), [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu has five Tier-1 AI entry points: `record`, `crawl`, `triage --ai`, `--dismiss-alerts`, and
`enrich`. Today they reach Claude through one of two structurally different paths.

Four of them, plus `record`'s default agent, go through the SDK-based `bajutsu.ai` registry
(BE-0104). The registry has two providers, `anthropic` (BE-0047) and `bedrock` (BE-0053). Both
authenticate with `ANTHROPIC_API_KEY` or AWS credentials.

`record` and `crawl` can instead pick `--agent claude-code`. That agent is `bajutsu/claude_code_agent.py`
plus `ClaudeCodeActionProposer` in `bajutsu/crawl_guide.py`, both reached via `bajutsu/agents.py`.
It is a second, parallel path that shells out to the Claude Code CLI (`claude -p`), so a Claude
Pro/Max subscription bills the usage instead of API credits. The docstring of
`bajutsu/ai_availability.py` already flags this as a wart: "a thin generalization of the existing
seam, not a new subsystem".

The subscription path is also narrower than it looks. It reaches `record` and `crawl` but never
`triage --ai`, `--dismiss-alerts`, or `enrich`. Even there it is text-only (no screenshot), so
`crawl`, which leans on vision, is degraded on it. The cause is that the structured-output mode of
`claude -p` is not the raw Messages API.

This item replaces the whole detour with one new AI provider, `ant`. It is backed by the official
[Anthropic CLI](https://github.com/anthropics/anthropic-cli), whose `ant auth login` runs a
browser-based OAuth/SSO flow against the Claude Console. The provider registers in the existing
`bajutsu.ai` seam exactly the way Bedrock did.

`ant messages create` is a thin, literal wrapper over the Messages API that the `anthropic` Python
SDK uses. System prompt, forced `tool_choice`, image content blocks, and the model catalog are all
the same. The existing `AnthropicBackend` translation layer (`bajutsu/ai/anthropic.py`) therefore
needs no changes. Client construction (`bajutsu/anthropic_client.py`) alone gains a third
authentication path.

Once `ai.provider: ant` covers every AI entry point uniformly, `bajutsu/claude_code_agent.py` and
the `--agent claude-code` kind are removed. One path that every AI feature shares meets the same
subscription/SSO-billing goal, in place of a bespoke `record`-only agent.

## Motivation

- Teams that want to bill Claude usage against an existing Pro/Max/Console seat can do so today
  for `record` and `crawl` only, and only text-only. `crawl` loses the vision it leans on to
  interpret a screen.
- `triage --ai` (vision-assisted root-cause), `--dismiss-alerts` (vision *is* the input), and
  `enrich` have no subscription/SSO option at all. This is a real capability gap, not a preference.
- Two structurally different "reach Claude" code paths, the `bajutsu.ai` registry and the CLI-agent
  duality, are drift that the docstring of `bajutsu.ai_availability` flags as something to
  reconcile, not a stable design. Every new AI path has to decide separately whether it "supports
  subscription billing", the way `triage --ai`, `--dismiss-alerts`, and `enrich` currently don't.
- `ant` closes the gap cleanly because it is a thin, literal wrapper over the Messages API. The
  wrapper is confirmed to expose `--system`, `--tool` / `--tool-choice`, image content blocks in
  `--message`, and the same model catalog as the SDK. Folding it in needs no parallel translation
  layer, no vision degradation, and no duplicated system-prompt or tool-schema code.
- The change has the same shape as BE-0053's change for Bedrock. It applies to a new
  *authentication* axis rather than a new *hosting* axis.
- Removing `claude_code_agent.py` once `ant` ships cuts one whole module, one `--agent` kind, and
  one denylist (`_DISALLOWED_TOOLS`) that must stay in sync with BE-0125. It also cuts the
  CLI-specific branch in `ai_availability.py` that exists for that agent alone.
- The property that made `--agent claude-code` attractive (no API key, subscription/seat billing)
  survives. It becomes a property of *every* AI path instead of one.

## Detailed design

1. **`anthropic_client.py` — a third provider.** Add `"ant"` to `PROVIDERS`, and have `provider()`
   recognize it. `make_client()` gains a branch. Instead of reading `ai.key_env` /
   `ANTHROPIC_API_KEY`, it resolves a bearer token by invoking the `ant` binary (for example
   `ant auth print-credentials --access-token`, honoring `--profile` / `ANTHROPIC_PROFILE`). It then
   constructs `anthropic.Anthropic(auth_token=token, base_url=ai.base_url or None)`.
   The SDK parameter is `auth_token` (sent as `Authorization: Bearer`), not `api_key` (sent as
   `x-api-key`). The existing `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` subscription flow
   already relies on the same parameter elsewhere in this codebase (see the docstring of
   `claude_code_agent.py`), so the mechanism is proven, not new.
2. **`credential_gap()` / `resolve_model()`.** `credential_gap()` gains an `"ant"` branch. It returns
   `None` when the `ant` binary is present and reports an active credential. Otherwise it returns a
   gap token: `"ant-cli-missing"` when the binary is absent, `"ant-cli-unauthenticated"` when it is
   present but not logged in. This mirrors the existing `CLAUDE_CODE_MISSING` pattern, which
   `ai_availability.py` retires (below). `resolve_model()` needs no change, because the model
   catalog of `ant` matches the bare Anthropic ids, unlike Bedrock's prefixed ones.
3. **`ai/registry.py`.** Register `"ant"` against the *same* `Adapter` (factory + credential_gap)
   that `anthropic` and `bedrock` already share. No new adapter class is needed, since
   `AnthropicBackend` is provider-agnostic once it holds a constructed SDK client.
4. **Profile selection needs no new config.** `ant` already resolves a named profile from
   `--profile` / `ANTHROPIC_PROFILE`. Bajutsu reuses that env var rather than inventing an
   `ai.profile` field.
5. **Remove the `claude-code` agent kind.** The removal covers these changes:
   - Delete `bajutsu/claude_code_agent.py`.
   - Drop `"claude-code"` from `agents.AGENT_KINDS` and from its branch in `make_agent`.
   - Remove `ClaudeCodeActionProposer` and its `claude-code` branch in `make_guide` in
     `bajutsu/crawl_guide.py`.
   - Remove the `--agent` option that `record` and `crawl` expose in
     `bajutsu/cli/commands/record.py` / `crawl.py`. Both commands then resolve through the
     provider registry like every other AI path.
   - Remove the CLI-specific branch in `ai_availability.py` (`CLAUDE_CODE_MISSING`, the `agent_kind`
     parameter), since every path now resolves through one `ai.provider`.
   - Update the AI-provider selector of `serve` (`bajutsu/templates/serve.html.j2` / `serve.js`,
     `bajutsu/serve/operations/config.py`) to offer `anthropic` / `bedrock` / `ant`, in place of a
     separate agent-kind toggle.
   - Update `bajutsu/capabilities.py` and `doctor` wherever the old kind is surfaced.
6. **Docs & tests.** `docs/configuration.md` (+ `docs/ja/`) documents the new provider alongside
   Bedrock. `docs/recording.md` (+ `docs/ja/`) drops the Claude Code CLI section. New tests for
   the provider branch replace the existing tests for `claude_code_agent.py`, `agents.py`, and
   `ai_availability.py`. `tests/test_anthropic_client.py`, `tests/test_ai_availability.py`, and
   `tests/test_ai_backend.py` already carry Bedrock-shaped precedent to extend.
7. **No new dependency.** `ant` is an external binary that the user installs (Homebrew, a release
   binary, or `go install`). Bajutsu invokes it via `subprocess`, exactly like the existing
   `claude` CLI probe in `ai_availability.py` (`shutil.which`). Bajutsu does not vendor or install
   it.

Open questions, left **TBD** for implementation time:

- The exact `ant` subcommand and flags to mint or read a fresh bearer token non-interactively. Also
  whether that call should happen once per `make_client()` (per run) or be cached.
- Whether the human-readable output of `ant auth status` is stable enough to parse for
  `credential_gap()`, or a machine-readable equivalent exists.

## Alternatives considered

- **Transparent fallback under the existing `"anthropic"` provider name.** This would try
  `ANTHROPIC_API_KEY`, then silently try the OAuth credential of `ant`. Rejected: an explicit
  provider name keeps `doctor`, `ai_availability`, and `serve` able to say unambiguously which
  credential path a target expects. Bedrock is likewise its own named provider rather than a
  fallback under `anthropic`.
- **Keep `--agent claude-code` alongside the new `ant` provider** (add, don't remove). Rejected:
  once every AI path can reach a subscription/SSO-billed credential through one seam, a second,
  `record`-only, text-only path is the duplicated-surface problem that `ai_availability.py`
  already flags. Removing it is the point, not an optional cleanup left for later.
- **Shell out to `ant messages create` per call**, treating `ant` as a per-turn CLI proxy the way
  `claude_code_agent.py` does for `claude -p`. Rejected: `ant auth print-credentials
  --access-token` yields a bearer token that the existing `anthropic` Python SDK already accepts
  directly. The AI paths keep using the SDK (prompt caching, the existing `AnthropicBackend`
  translation, error handling) unchanged. They need no per-call subprocess and no second
  request/response serialization to maintain in parallel with `ai/anthropic.py`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Register `ant` as a provider in `anthropic_client.py` (`PROVIDERS`, `make_client`,
      `credential_gap`, `resolve_model`)
- [x] Register `ant` in `ai/registry.py`
- [x] Remove `bajutsu/claude_code_agent.py` and the `claude-code` agent kind (`agents.py`,
      `crawl_guide.py`'s `ClaudeCodeActionProposer`, `ai_availability.py`, the `--agent` option
      in `record` / `crawl`)
- [x] Update `serve`'s AI-provider selector and settings UI
- [x] Update docs (`docs/configuration.md`, `docs/recording.md`, Japanese mirrors)
- [x] Tests for the new provider branch; remove/replace `claude_code_agent` tests

Log:

- [#689](https://github.com/bajutsu-e2e/bajutsu/pull/689) — the `ant` provider ships end to end: a third authentication path in `anthropic_client.py`
  (`auth_token` from `ant auth print-credentials`), registered on the shared Anthropic adapter, with
  the `claude-code` agent kind and its `--agent` option, denylist, and `ai_availability` CLI branch
  removed; `serve`'s Settings selector now offers `api-key` / `bedrock` / `ant`. Docs (both
  languages) and tests updated; `make check` green.
- Follow-up: renamed the direct-API provider from `anthropic` to `api-key` (the name now states the
  *auth method*, since Bedrock and `ant` are Anthropic too). The legacy `anthropic` name still
  resolves to `api-key` via `normalize_provider`, so existing configs keep working.

## References

- `bajutsu/anthropic_client.py`, `bajutsu/ai/registry.py`, `bajutsu/ai/anthropic.py`,
  `bajutsu/ai_availability.py`, `bajutsu/agents.py`, `bajutsu/claude_code_agent.py`
- [BE-0053 — Amazon Bedrock as a pluggable AI provider](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
  — the precedent this item mirrors (a new authentication axis registered the same way)
- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  — the registry this item adds a third provider to
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  — the pluggable-provider / fail-closed guarantee this item's provider must keep
- [BE-0125 — Restrict the claude-code authoring agent tools](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)
  — the tool denylist this item retires along with the agent it protects
- Anthropic CLI docs: [CLI quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart),
  [CLI authentication options](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication),
  [`ant messages create` reference](https://platform.claude.com/docs/en/api/cli/messages/create)

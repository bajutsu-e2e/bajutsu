**English** · [日本語](BE-XXXX-claude-code-oauth-token-credential-ja.md)

# BE-XXXX — Explicit CLAUDE_CODE_OAUTH_TOKEN credential for the claude-code provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-claude-code-oauth-token-credential.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
| Related | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md), [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)'s `claude-code`
provider (`bajutsu/ai/claude_code.py`) authenticates purely by inheriting whatever credential the
local `claude` binary already holds — an interactive browser login, or a prior `claude
setup-token` run on that same machine. Bajutsu itself has no explicit notion of a `claude-code`
credential: no documented environment variable, no `.env.example` entry, and no field in `serve`'s
Settings panel — unlike every other provider, which has one (`ANTHROPIC_API_KEY` / `ai.keyEnv` for
`api-key`, `ant auth login` plus a `serve`-driven login button for `ant`). This item adds
`CLAUDE_CODE_OAUTH_TOKEN` — the long-lived token `claude setup-token` is designed to mint for
exactly this non-interactive case, and which the `claude` CLI already honors when present in its
environment — as a first-class, bajutsu-managed credential: settable via `.env` / a real
environment variable, and via a new write-once secret in `serve`'s Settings panel, alongside the
existing Claude API key field.

## Motivation

- **Headless hosts have no way in today.** `claude-code`'s only credential path is a local,
  interactive one: `claude setup-token`'s browser flow or an existing terminal session's stored
  login. A continuous integration (CI) runner, a container, or a remote self-hosted `serve` instance
  ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) has no browser and no
  interactive terminal to run that flow against, so the provider is unusable there even though the
  whole point of BE-0176 was subscription billing across every AI path.
- **The token flow already exists — bajutsu just doesn't plumb it.** `claude setup-token` mints
  exactly the kind of static, long-lived token a headless environment needs, and the `claude` CLI
  already reads `CLAUDE_CODE_OAUTH_TOKEN` from its environment when present — this repo's own CI
  automation ([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)) already
  relies on that exact mechanism for the PR-review workflow's `CLAUDE_CODE_OAUTH_TOKEN` secret.
  `bajutsu/ai/claude_code.py`'s `_child_env()` does not strip the variable, so it already flows
  through *if* the ambient shell happens to export it — but that's an accident of not stripping it,
  not a supported, documented path a user can rely on and configure through bajutsu.
- **Every other provider gets a named, bajutsu-managed credential; `claude-code` doesn't.**
  `api-key` has `ANTHROPIC_API_KEY` / `ai.keyEnv` and a `serve` Settings field
  ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)); `ant` has
  `ant auth login` plus a `serve`-driven login button
  ([BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md)). Leaving
  `claude-code` as "however the host happens to be logged in" is the odd one out, and
  BE-0136's own design already anticipated this: it generalized the secret store to a *named*
  secret specifically so "a second named secret ... reuses the same store and the same write-once
  guarantee with no new plumbing" — this item is that anticipated second secret.

## Detailed design

1. **`bajutsu/ai/claude_code.py` recognizes the token explicitly.** Add a module-level constant
   naming the environment variable (`OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"`) so the adapter
   documents the credential instead of leaving it as an unstripped pass-through. No change to
   `_child_env()`'s behavior is needed — the variable is not in `_ROUTING_ENV`, so it already
   reaches the child process; this step is about making the credential a named, referenceable
   thing the rest of the codebase (docs, `serve`, `credential_gap`) can point at by the same name,
   not a behavior change to the subprocess call itself.
2. **`.env.example` documents it as an alternative to the API key**, alongside the existing
   `ANTHROPIC_API_KEY` line, framed for the case this item exists to fix: a headless host that
   can't run `claude setup-token`'s browser flow interactively, so the token is minted once
   elsewhere and copied in.
3. **`serve`'s write-once secret store gains a second named secret.** Following the generalization
   BE-0136 already designed for, add `AI_CLAUDE_CODE_TOKEN_SECRET = "aiClaudeCodeOauthToken"`
   alongside the existing `AI_API_KEY_SECRET`, with its own `describe`/`set` pair
   (`claude_code_token_info` / `set_claude_code_token`) mirroring `api_key_info` / `set_api_key` in
   `bajutsu/serve/operations/config.py`. Local `serve` materializes the stored value into
   `CLAUDE_CODE_OAUTH_TOKEN` in the process environment (mirroring how `set_api_key` materializes
   into `ai.keyEnv`); the hosted backend stores it encrypted per org like every other named secret,
   with the same explicit "consuming it in a spawned hosted-worker job is a separate follow-up"
   scope note BE-0136 already carries for `aiApiKey`.
4. **Settings UI.** `bajutsu/templates/serve.html.j2` / `serve.core.js` show a "Claude Code OAuth
   token" field — write-only, masked-preview-on-save, same shape as the existing API key field —
   only when the `claude-code` provider is selected, next to (not replacing) the existing
   API-key field, since a host can hold both credentials and switch providers without re-entering
   either.
5. **`ai_availability.py`'s gap message mentions the new path.** The existing
   `CLAUDE_CODE_CLI_MISSING` message already says "install Claude Code and sign in (`claude
   setup-token`, or an interactive login)" — extend it to name `CLAUDE_CODE_OAUTH_TOKEN` as the
   non-interactive alternative, so the `doctor` / `serve` gap message actively advertises the
   headless path this item adds, not only the interactive one. `credential_gap()`'s actual
   detection logic is unchanged: it still only checks binary presence (a deliberate BE-0176
   trade-off — probing the CLI for a live credential is unreliable, so an unauthenticated call
   still fails loud at call time, whichever way it authenticated). Perfecting that detection is
   explicitly out of scope here; see *Alternatives considered*.
6. **Docs.** `docs/configuration.md` (+ `docs/ja/`) document `CLAUDE_CODE_OAUTH_TOKEN` next to the
   existing `claude-code` provider paragraph, framed around the headless/CI/self-hosted case; the
   README's self-hosting Secrets guidance gets a one-line pointer.
7. **Tests.** `serve` operations tests for the new secret endpoint pair (set-then-describe never
   returns plaintext, mirrors the existing `aiApiKey` contract test); a `claude_code.py` test
   confirming `OAUTH_TOKEN_ENV` is exported to the child unchanged (it already is — this documents
   the contract so a future edit to `_ROUTING_ENV` can't silently start stripping it).

## Alternatives considered

- **Add real credential-gap detection (spawn a lightweight `claude` probe to check for a live
  token/session) instead of only documenting the env var.** Rejected for this item: BE-0176
  already made the deliberate call that a cheap pre-flight probe is unreliable and a genuinely
  unauthenticated call should fail loud at run time rather than guess from a probe. Changing that
  trade-off is a separate, larger discussion than "give the token a documented, configurable home"
  and would conflate two different problems in one item.
- **Fold the new secret into the existing `aiApiKey` slot instead of adding a second named
  secret.** Rejected: the two are different credentials for different providers (an API key vs. an
  OAuth token consumed by the CLI), and BE-0136's `SecretStore` was explicitly designed to add a
  second named secret without new plumbing — reusing one slot for two unrelated values would erase
  that distinction and make `serve`'s Settings panel ambiguous about which credential is set.
- **Only document the env var; skip the `serve` Settings UI entirely.** Considered as the smaller
  cut, but it leaves the exact deployment this item is motivated by — a hosted, browser-less
  `serve` instance — with no way to set the credential short of editing the container's
  environment out of band, which defeats the point for the managed/self-hosted case BE-0016
  already treats as a first-class deployment shape.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Name `CLAUDE_CODE_OAUTH_TOKEN` as a documented constant in `bajutsu/ai/claude_code.py`
- [ ] `.env.example` entry
- [ ] `serve` write-once secret: `aiClaudeCodeOauthToken` (local env-backed + hosted encrypted),
      `claude_code_token_info` / `set_claude_code_token` operations
- [ ] Settings UI field, shown when `claude-code` is the selected provider
- [ ] `ai_availability.py` gap message names the token as the non-interactive alternative
- [ ] Docs (`docs/configuration.md`, `docs/ja/`, self-hosting Secrets pointer)
- [ ] Tests: secret endpoint contract, `_child_env` pass-through contract test

## References

- `bajutsu/ai/claude_code.py` — the `claude-code` adapter this item gives an explicit credential to
- `bajutsu/serve/secrets.py`, `bajutsu/serve/operations/config.py` — the write-once `SecretStore`
  seam this item's new named secret extends
- [BE-0176 — Revive Claude Code as an AiBackend adapter with file-based vision](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)
  — the provider this item gives a bajutsu-managed credential to
- [BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
  — first documented the `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` mechanism in this codebase
- [BE-0136 — Write-once secrets store for serve](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)
  — the secret store this item's second named secret follows the design of
- [BE-0175 — Sign in to the `ant` provider from the serve Web UI](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md)
  — the precedent for a provider-specific credential affordance in the Settings UI
- [BE-0203 — Claude Code PR review workflow](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)
  — this repo's own CI automation already relies on `CLAUDE_CODE_OAUTH_TOKEN` the same way
- Anthropic docs: `claude setup-token` (long-lived OAuth token for non-interactive use)

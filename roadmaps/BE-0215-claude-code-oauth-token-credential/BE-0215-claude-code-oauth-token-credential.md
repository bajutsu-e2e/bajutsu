**English** · [日本語](BE-0215-claude-code-oauth-token-credential-ja.md)

# BE-0215 — Explicit CLAUDE_CODE_OAUTH_TOKEN credential for the claude-code provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0215](BE-0215-claude-code-oauth-token-credential.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0215") |
| Implementing PR | [#869](https://github.com/bajutsu-e2e/bajutsu/pull/869) |
| Topic | AI provider configuration |
| Related | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md), [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md) |
<!-- /BE-METADATA -->

## Introduction

The `claude-code` provider ([BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md))
lives in `bajutsu/ai/claude_code.py`. It authenticates by inheriting the credential that the local
`claude` binary already holds. That credential is an interactive browser login. It can also be a
prior `claude setup-token` run on the same machine.

Bajutsu itself has no explicit notion of a `claude-code` credential: no documented environment
variable, no `.env.example` entry, and no field in the Settings panel of `serve`.

Every other provider has such a credential. `api-key` has `ANTHROPIC_API_KEY` / `ai.keyEnv`. `ant`
has `ant auth login` plus a `serve`-driven login button.

This item adds `CLAUDE_CODE_OAUTH_TOKEN` as a first-class, bajutsu-managed credential. The command
`claude setup-token` mints this long-lived token for the non-interactive case. The `claude` command-line
interface (CLI) honors the token when the environment holds it.

A user can set the credential through `.env`, through a real environment variable, or through a new
write-once secret in the Settings panel of `serve`, beside the Claude API key field.

## Motivation

- **Headless hosts have no way in today.**
  The only credential path of `claude-code` is local and interactive.
  It is the browser flow of `claude setup-token`, or the stored login of a terminal session.
  A continuous integration (CI) runner, a container, or a remote self-hosted `serve` instance
  ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md))
  has no browser and no interactive terminal for that flow.
  The provider is unusable there.
  Yet the whole point of BE-0176 was subscription billing across every AI path.
- **The token flow already exists, and bajutsu does not plumb it.**
  `claude setup-token` mints the static, long-lived token that a headless environment needs.
  The `claude` CLI already reads `CLAUDE_CODE_OAUTH_TOKEN` from its environment.
  The CI automation of this repo already relies on that mechanism
  ([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md)).
  The PR-review workflow uses a `CLAUDE_CODE_OAUTH_TOKEN` secret.
  `_child_env()` in `bajutsu/ai/claude_code.py` does not strip the variable.
  The variable flows through *if* the ambient shell happens to export it.
  That is an accident of not stripping it.
  A user cannot rely on it, and cannot configure it through bajutsu.
- **Every other provider gets a named, bajutsu-managed credential, and `claude-code` does not.**
  `api-key` has `ANTHROPIC_API_KEY` / `ai.keyEnv` and a `serve` Settings field
  ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)). `ant` has
  `ant auth login` plus a `serve`-driven login button
  ([BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md)). Leaving
  `claude-code` as "however the host happens to be logged in" makes it the odd one out. The design
  of BE-0136 already anticipated this. It generalized the secret store to a *named* secret so that
  "a second named secret ... reuses the same store and the same write-once guarantee with no new
  plumbing". This item is that anticipated second secret.

## Detailed design

1. **`bajutsu/ai/claude_code.py` recognizes the token explicitly.**
   Add a module-level constant for the environment variable name:
   `OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"`.
   The adapter then documents the credential instead of leaving an unstripped pass-through.
   The behavior of `_child_env()` needs no change.
   The variable is not in `_ROUTING_ENV`, so it already reaches the child process.
   The step makes the credential a named thing.
   The docs, `serve`, and `credential_gap` can then point at it by the same name.
   The subprocess call itself does not change.
2. **`.env.example` documents it as an alternative to the API key**, alongside the existing
   `ANTHROPIC_API_KEY` line. The entry is framed for the case this item exists to fix: a headless
   host that cannot run the browser flow of `claude setup-token` interactively. The token is minted
   once elsewhere and copied in.
3. **The write-once secret store of `serve` gains a second named secret.**
   BE-0136 already designed for this generalization.
   Add `AI_CLAUDE_CODE_TOKEN_SECRET = "aiClaudeCodeOauthToken"` beside `AI_API_KEY_SECRET`.
   The new secret has its own `describe`/`set` pair.
   The pair is `claude_code_token_info` / `set_claude_code_token`.
   It mirrors `api_key_info` / `set_api_key` in `bajutsu/serve/operations/config.py`.
   - Local `serve` materializes the stored value into `CLAUDE_CODE_OAUTH_TOKEN` in the process
     environment. `set_api_key` does the same for `ai.keyEnv`.
   - The hosted backend stores the value encrypted per org, like every other named secret.
   - The scope note that BE-0136 carries for `aiApiKey` applies here too.
     Consuming the secret in a spawned hosted-worker job is a separate follow-up.
4. **Settings UI.** `bajutsu/templates/serve.html.j2` and `serve.core.js` show a field.
   Its label is "Claude Code OAuth token".
   The field is write-only and shows a masked preview on save.
   It has the same shape as the existing API key field.
   It appears only when the `claude-code` provider is selected.
   It sits next to the API-key field and does not replace it.
   A host can hold both credentials and switch providers without re-entering either.
5. **The gap message of `ai_availability.py` mentions the new path.** The existing
   `CLAUDE_CODE_CLI_MISSING` message already says "install Claude Code and sign in (`claude
   setup-token`, or an interactive login)". Extend it to name `CLAUDE_CODE_OAUTH_TOKEN` as the
   non-interactive alternative. The `doctor` / `serve` gap message then advertises the headless path
   that this item adds, not only the interactive one.
   - The detection logic of `credential_gap()` is unchanged. It still checks binary presence alone.
   - That is a deliberate BE-0176 trade-off. Probing the CLI for a live credential is unreliable,
     so an unauthenticated call still fails loud at call time, whichever way it authenticated.
   - Perfecting the detection is explicitly out of scope here; see *Alternatives considered*.
6. **Docs.** `docs/configuration.md` (+ `docs/ja/`) document `CLAUDE_CODE_OAUTH_TOKEN` next to the
   existing `claude-code` provider paragraph, framed around the headless/CI/self-hosted case. The
   Secrets guidance in the README's self-hosting section gets a one-line pointer.
7. **Tests.** Two tests are added.
   - `serve` operations tests for the new secret endpoint pair. Set-then-describe never returns
     plaintext, mirroring the existing `aiApiKey` contract test.
   - A `claude_code.py` test confirming that `OAUTH_TOKEN_ENV` is exported to the child unchanged.
     It already is. The test documents the contract, so a future edit to `_ROUTING_ENV` cannot
     silently start stripping the variable.

## Alternatives considered

- **Add real credential-gap detection instead of only documenting the env var.** The detection would
  spawn a lightweight `claude` probe to check for a live token or session. Rejected for this item.
  BE-0176 already made the deliberate call that a cheap pre-flight probe is unreliable. A genuinely
  unauthenticated call should fail loud at run time rather than guess from a probe. Changing that
  trade-off is a separate, larger discussion than "give the token a documented, configurable home".
  It would also conflate two different problems in one item.
- **Fold the new secret into the existing `aiApiKey` slot instead of adding a second named
  secret.** Rejected. The two are different credentials for different providers: an API key versus
  an OAuth token consumed by the CLI. BE-0136 designed its `SecretStore` explicitly to add a second
  named secret without new plumbing. Reusing one slot for two unrelated values would erase that
  distinction. It would also make the Settings panel of `serve` ambiguous about which credential is
  set.
- **Only document the env var, and skip the `serve` Settings UI entirely.** Considered as the
  smaller cut. It leaves the deployment that motivates this item, a hosted, browser-less `serve`
  instance, with no way to set the credential short of editing the container's environment out of
  band. That defeats the point for the managed/self-hosted case, which BE-0016 already treats as a
  first-class deployment shape.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Name `CLAUDE_CODE_OAUTH_TOKEN` as a documented constant in `bajutsu/ai/claude_code.py`
- [x] `.env.example` entry
- [x] `serve` write-once secret: `aiClaudeCodeOauthToken` (local env-backed + hosted encrypted),
      `claude_code_token_info` / `set_claude_code_token` operations
- [x] Settings UI field, shown when `claude-code` is the selected provider
- [x] `ai_availability.py` gap message names the token as the non-interactive alternative
- [x] Docs (`docs/configuration.md`, `docs/ja/`, self-hosting Secrets pointer)
- [x] Tests: secret endpoint contract, `_child_env` pass-through contract test

Log:

- [#869](https://github.com/bajutsu-e2e/bajutsu/pull/869) — ships the whole item: the
  `OAUTH_TOKEN_ENV` constant, the `aiClaudeCodeOauthToken` write-once secret with its
  `/api/claudecodetoken` endpoints, the provider-gated Settings field, the gap-message and
  `.env.example` / docs updates, and the endpoint + `_child_env` pass-through tests.

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

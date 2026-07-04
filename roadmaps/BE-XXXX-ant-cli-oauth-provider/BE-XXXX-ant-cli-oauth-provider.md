**English** · [日本語](BE-XXXX-ant-cli-oauth-provider-ja.md)

# BE-XXXX — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ant-cli-oauth-provider.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's five Tier-1 AI entry points (`record`, `crawl`, `triage --ai`, `--dismiss-alerts`,
enrich) reach Claude through one of two structurally different paths today. Four of them —
plus `record`'s default agent — go through the SDK-based `bajutsu.ai` registry (BE-0104),
whose only two registered providers, `anthropic` (BE-0047) and `bedrock` (BE-0053),
authenticate with `ANTHROPIC_API_KEY` or AWS credentials. `record` alone can instead pick
`--agent claude-code` (`bajutsu/claude_code_agent.py` + `bajutsu/agents.py`), a second,
parallel path that shells out to the Claude Code CLI (`claude -p`) so a Claude Pro/Max
subscription bills the usage instead of API credits. `bajutsu/ai_availability.py`'s own
docstring already flags this as a wart: "a thin generalization of the existing seam, not a
new subsystem". The duality is also narrower than it looks — only `record` gets
subscription billing, and even there the CLI agent is documented as text-only (no
screenshot), because `claude -p`'s structured-output mode is not the raw Messages API.

This item replaces the whole detour with one new AI provider, `ant`, backed by the official
[Anthropic CLI](https://github.com/anthropics/anthropic-cli) (`ant auth login` — a
browser-based OAuth/SSO flow against the Claude Console) registered in the existing
`bajutsu.ai` seam exactly the way Bedrock was. Because `ant messages create` is a thin,
literal wrapper over the same Messages API used by the `anthropic` Python SDK (system
prompt, forced `tool_choice`, image content blocks, the same model catalog), the existing
`AnthropicBackend` translation layer (`bajutsu/ai/anthropic.py`) needs no changes at all —
only client construction (`bajutsu/anthropic_client.py`) gains a third authentication path.
Once `ai.provider: ant` covers every AI entry point uniformly, `bajutsu/claude_code_agent.py`
and the `--agent claude-code` kind are removed: the same subscription/SSO-billing goal is met
by one path every AI feature shares, not a bespoke `record`-only agent.

## Motivation

- Teams that want to bill Claude usage against an existing Pro/Max/Console seat rather than
  provisioning and rotating an `ANTHROPIC_API_KEY` can currently do so only for `record`, and
  only text-only. `crawl` (which leans on vision to interpret a screen), `triage --ai`
  (vision-assisted root-cause), and `--dismiss-alerts` (vision *is* the input) offer no such
  option today — a real capability gap, not a preference.
- Maintaining two structurally different "reach Claude" code paths (the `bajutsu.ai`
  registry vs. the CLI-agent duality) is exactly the kind of drift
  `bajutsu.ai_availability`'s own docstring flags as something to reconcile, not a stable
  design: every new AI path has to separately decide whether it "supports subscription
  billing" the way `crawl` / `triage` / `alerts` currently don't.
- `ant` closes the gap cleanly because it is a thin, literal wrapper over the Messages API
  (confirmed: `--system`, `--tool` / `--tool-choice`, image content blocks in `--message`,
  the same model catalog as the SDK) — folding it in needs no parallel translation layer, no
  vision degradation, and no duplicated system-prompt/tool-schema code. It is the same shape
  of change BE-0053 already made for Bedrock, applied to a new *authentication* axis rather
  than a new *hosting* axis.
- Removing `claude_code_agent.py` once `ant` ships cuts one whole module, one `--agent` kind,
  one denylist (`_DISALLOWED_TOOLS`) to keep in sync with BE-0125, and the CLI-specific
  branch in `ai_availability.py` that exists only for it — while keeping the property that
  made `--agent claude-code` attractive (no API key, subscription/seat billing) as a property
  of *every* AI path instead of one.

## Detailed design

1. **`anthropic_client.py` — a third provider.** Add `"ant"` to `PROVIDERS`; `provider()`
   recognizes it. `make_client()` gains a branch: instead of reading `ai.keyEnv` /
   `ANTHROPIC_API_KEY`, resolve a bearer token by invoking the `ant` binary (e.g. `ant auth
   print-credentials --access-token`, honoring `--profile` / `ANTHROPIC_PROFILE`) and
   construct `anthropic.Anthropic(auth_token=token, base_url=ai.base_url or None)` —
   `auth_token` (sent as `Authorization: Bearer`) rather than `api_key` (sent as
   `x-api-key`), the same SDK parameter the existing `claude setup-token` /
   `CLAUDE_CODE_OAUTH_TOKEN` subscription flow already relies on elsewhere in this codebase
   (see `claude_code_agent.py`'s docstring) — a proven mechanism, not a new one.
2. **`credential_gap()` / `resolve_model()`.** `credential_gap()` gains an `"ant"` branch:
   `None` when the `ant` binary is present and reports an active credential, else a gap token
   (e.g. `"ant-cli-missing"` for the binary absent, `"ant-cli-unauthenticated"` for present
   but not logged in) — mirroring the existing `CLAUDE_CODE_MISSING` pattern being retired in
   `ai_availability.py` (below). `resolve_model()` needs no change: `ant`'s model catalog
   matches the bare Anthropic ids, unlike Bedrock's prefixed ones.
3. **`ai/registry.py`.** Register `"ant"` against the *same* `Adapter` (factory +
   credential_gap) that `anthropic` / `bedrock` already share — no new adapter class, since
   `AnthropicBackend` is provider-agnostic once it holds a constructed SDK client.
4. **Profile selection needs no new config.** `ant` itself already resolves a named profile
   from `--profile` / `ANTHROPIC_PROFILE`; bajutsu reuses that env var rather than inventing
   an `ai.profile` field.
5. **Remove the `claude-code` agent kind.** Delete `bajutsu/claude_code_agent.py`; drop
   `"claude-code"` from `agents.AGENT_KINDS` and its branch in `make_agent`; remove the
   CLI-specific branch in `ai_availability.py` (`CLAUDE_CODE_MISSING`, `agent_kind` parameter)
   since every path now resolves through one `ai.provider`; update `serve`'s AI-provider
   selector (`bajutsu/templates/serve.html.j2` / `serve.js`,
   `bajutsu/serve/operations/config.py`) to offer `anthropic` / `bedrock` / `ant` instead of a
   separate agent-kind toggle; update `bajutsu/capabilities.py` and `doctor` wherever the old
   kind is surfaced.
6. **Docs & tests.** `docs/configuration.md` (+ `docs/ja/`) documents the new provider
   alongside Bedrock; `docs/recording.md` (+ `docs/ja/`) drops the Claude Code CLI section.
   Existing tests for `claude_code_agent.py` / `agents.py` / `ai_availability.py` are replaced
   with tests for the new provider branch — `tests/test_anthropic_client.py`,
   `tests/test_ai_availability.py`, `tests/test_ai_backend.py` already carry Bedrock-shaped
   precedent to extend.
7. **On-demand install when `ant` is absent.** `ant` is an external binary, probed via
   `subprocess` / `shutil.which` exactly like the existing `claude` CLI check in
   `ai_availability.py`. When it is missing, Bajutsu **installs it on demand** rather than only
   erroring — mirroring how `make serve` ([scripts/serve.sh](../../../scripts/serve.sh)) installs
   the idb backend's deps on demand, and how CI installs `actionlint`. The installer fetches the
   host OS/arch release binary from the CLI's GitHub releases (the documented `curl` path),
   **pinned by version and verified by SHA** (the [BE-0133](../BE-0133-pin-actionlint-installer/BE-0133-pin-actionlint-installer.md)
   norm for the actionlint installer), into a Bajutsu-managed location on `PATH`; Homebrew /
   `go install` stay manual alternatives a user may prefer. The install is surfaced through the
   same seam as the `ant-cli-missing` gap token — a `make` target, and the `doctor` / `serve`
   readiness surfaces that already report the gap gain an install action — so a missing binary
   becomes an actionable one-step fix, not a dead end. It adds no *Python* dependency: `ant` never
   enters `pyproject.toml`; it is a runtime tool fetched on demand, the way the idb client and
   `idb_companion` already are.

Open questions, left **TBD** for implementation time:

- The exact `ant` subcommand/flags to mint or read a fresh bearer token non-interactively,
  and whether that call should happen once per `make_client()` (per run) or be cached.
- Whether `ant auth status`'s human-readable output is stable enough to parse for
  `credential_gap()`, or a machine-readable equivalent exists.

## Alternatives considered

- **Transparent fallback under the existing `"anthropic"` provider name** (try
  `ANTHROPIC_API_KEY`, else silently try `ant`'s OAuth credential). Rejected: an explicit
  provider name keeps `doctor` / `ai_availability` / `serve` able to say unambiguously which
  credential path a target expects, consistent with Bedrock being its own named provider
  rather than a fallback under `anthropic`.
- **Keep `--agent claude-code` alongside the new `ant` provider** (add, don't remove).
  Rejected: once every AI path can reach a subscription/SSO-billed credential through one
  seam, keeping a second, `record`-only, text-only path is exactly the duplicated-surface
  problem `ai_availability.py` already flags — removing it is the point, not an optional
  cleanup left for later.
- **Shell out to `ant messages create` per call**, treating `ant` as a per-turn CLI proxy the
  way `claude_code_agent.py` does for `claude -p`. Rejected: `ant auth print-credentials
  --access-token` yields a bearer token the existing `anthropic` Python SDK already accepts
  directly, so the AI paths keep using the SDK (prompt caching, the existing
  `AnthropicBackend` translation, error handling) unchanged — no per-call subprocess, no
  second request/response serialization to maintain in parallel with `ai/anthropic.py`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Register `ant` as a provider in `anthropic_client.py` (`PROVIDERS`, `make_client`,
      `credential_gap`, `resolve_model`)
- [ ] Register `ant` in `ai/registry.py`
- [ ] Remove `bajutsu/claude_code_agent.py` and the `claude-code` agent kind (`agents.py`,
      `ai_availability.py`)
- [ ] Update `serve`'s AI-provider selector and settings UI
- [ ] On-demand `ant` install when the binary is absent (pinned release binary + SHA, mirroring
      `make serve` / actionlint), wired to the `ant-cli-missing` gap in `make` / `doctor` / `serve`
- [ ] Update docs (`docs/configuration.md`, `docs/recording.md`, Japanese mirrors)
- [ ] Tests for the new provider branch; remove/replace `claude_code_agent` tests

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
- [BE-0133 — Pin the actionlint installer by SHA](../BE-0133-pin-actionlint-installer/BE-0133-pin-actionlint-installer.md)
  — the pinned-by-SHA norm the on-demand `ant` installer follows
- Anthropic CLI docs: [CLI quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart),
  [CLI authentication options](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication),
  [`ant messages create` reference](https://platform.claude.com/docs/en/api/cli/messages/create)

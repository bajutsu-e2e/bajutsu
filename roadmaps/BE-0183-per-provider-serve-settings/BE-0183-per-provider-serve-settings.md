**English** · [日本語](BE-0183-per-provider-serve-settings-ja.md)

# BE-0183 — Per-provider AI settings in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0183](BE-0183-per-provider-serve-settings.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0183") |
| Implementing PR | _pending_ |
| Topic | AI provider configuration |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md), [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's Settings panel lets an operator pick one of the registered AI providers
(`api-key` / `bedrock` / `ant` / `claude-code`, BE-0104) and, alongside it, a `model` and a
reasoning `effort`. Today those two fields are stored as a single pair shared by every
provider, even though both are properties of the chosen provider: which model ids are valid,
and whether `effort` does anything at all, differ per provider (`claude-code`'s CLI accepts
`--effort`; the SDK-backed providers ignore it). This item proposes a data structure that
scopes a provider's settings to that provider, and wires the Settings UI to read and write
through it, so switching providers stops discarding what was set for the one left behind.

## Motivation

Switching the provider dropdown in Settings today silently loses the previous selection's
`model`/`effort`: both live behind one `MODEL_ENV`/`EFFORT_ENV` pair in the serve process's
environment (`bajutsu/serve/operations/config.py::set_provider`), and saving a new provider
overwrites that pair. An operator who configures `claude-code` with `effort=high` and a
specific model, then tries `api-key` for comparison, and switches back to `claude-code`,
finds the model and effort gone — not cleared on purpose, just overwritten by whatever the
`api-key` save happened to carry (usually blank).

The current design already treats Bedrock's model as its own slot (`BEDROCK_MODEL_ENV`,
separate from the general `MODEL_ENV` the other three providers share) because a Bedrock
model id is provider-prefixed and invalid for the others. That split is the right instinct,
applied inconsistently: `effort` has no such per-provider slot, and the three non-Bedrock
providers still share one `model` slot despite `claude-code` (a CLI subprocess) and
`api-key`/`ant` (the Anthropic SDK) being different backends with no reason to share a model
choice. Generalizing the existing Bedrock split into a per-provider structure for every field
removes the ad hoc exception and fixes the shared-state loss in one move.

## Detailed design

1. **Data structure.** Introduce a settings structure keyed by provider name — e.g.
   `dict[str, ProviderSettings]` where `ProviderSettings` holds `model`, `effort`, and (for
   `bedrock` only) `region` — held for the life of the serve process, matching how
   `provider`/`model`/`effort` already live in `os.environ` today (session-only, not written
   to disk; BE-0175 already documents that constraint for the provider choice itself).
   Persisting this structure across a serve restart is out of scope here and left to a
   separate item so this one stays focused on the data shape and the UI wiring.
2. **Read API.** `GET /api/provider` (`provider_info`) returns the full per-provider map, not
   only the active provider's fields, so the Settings UI can pre-populate every provider's
   `model`/`effort`/`region` inputs once, without a round trip on every dropdown change.
3. **Write API.** `POST /api/provider` (`set_provider`) writes into the selected provider's
   slot only; the other providers' slots are untouched. The active provider's slot is what
   gets materialized into the existing `MODEL_ENV`/`EFFORT_ENV`/`BEDROCK_MODEL_ENV`/
   `AWS_REGION` environment variables spawned jobs already read, so job-spawning code needs
   no change — this item only changes how Settings arrives at the values it writes there.
4. **Web UI.** The provider `<select>`'s change handler (`bajutsu/templates/serve.js`) swaps
   the visible `model`/`effort`/region fields to the newly selected provider's own
   remembered values from the fetched map, instead of leaving the previous provider's values
   sitting in what looks like a shared textbox.
5. **Validation.** Each provider's slot keeps today's provider-specific validation (a
   required Bedrock model id, `effort` checked against `EFFORT_LEVELS`, no whitespace) —
   validation was already effectively per-provider; only the storage it validates into
   changes shape.

## Alternatives considered

- **Clear `model`/`effort` on every provider switch instead of remembering them.** Simpler,
  but it does not solve the actual complaint: an operator comparing two providers back and
  forth would have to re-enter both fields every time, which is the friction this item exists
  to remove.
- **Move `model`/`effort` into the YAML `ai:` config schema (`defaults.ai` /
  `targets.<name>.ai`, BE-0047) as a per-provider map, instead of a serve-runtime structure.**
  A static config file already commits one target to one provider, so there is no ambiguity
  to fix there — the confusion is specific to the Settings panel's interactive provider
  toggle. Scoping the fix to serve avoids widening the config schema (and its BE-0112
  deterministic-core boundary) to solve a UI-only problem. A config-file-level multi-provider
  profile remains a possible future extension if a use case for it appears.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Define the per-provider settings structure held by serve.
- [x] Return the full per-provider map from `GET /api/provider`.
- [x] Scope `POST /api/provider` writes to the selected provider's slot.
- [x] Update the Settings UI to swap fields per provider from the fetched map.

Log:

- _pending_ — Added the `ProviderSettings` dataclass and `ServeState.provider_settings`, returned
  the per-provider map as `providers` from `GET /api/provider` (active provider seeded from env),
  scoped `POST /api/provider` writes to the selected provider's slot while still materializing it
  into the env spawned jobs read, and reworked the Settings JS to cache the map and swap
  model/effort/region per provider on a dropdown change.

## References

- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
- [BE-0047 — AI data sovereignty (provider-agnostic, redacted AI path)](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
- [BE-0163 — Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
- [BE-0176 — Revive Claude Code as an AiBackend adapter with file-based vision](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)
- [BE-0175 — Sign in to the `ant` provider from the serve Web UI](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md)
- A follow-up item, "Persist serve AI provider settings across restarts" (not yet numbered),
  proposes making this structure survive a serve restart instead of living only in the
  process's memory for the session.

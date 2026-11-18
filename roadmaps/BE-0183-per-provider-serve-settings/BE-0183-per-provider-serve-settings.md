**English** · [日本語](BE-0183-per-provider-serve-settings-ja.md)

# BE-0183 — Per-provider AI settings in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0183](BE-0183-per-provider-serve-settings.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0183") |
| Implementing PR | [#762](https://github.com/bajutsu-e2e/bajutsu/pull/762) |
| Topic | AI provider configuration |
| Related | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md), [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md), [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's Settings panel lets an operator pick one of the registered AI providers
(`api-key` / `bedrock` / `ant` / `claude-code`, BE-0104). Beside the provider, the panel sets a
`model` and a reasoning `effort`.

Today every provider shares one pair of those two fields. Both fields belong to the chosen
provider, though. Valid model ids differ per provider, and so does whether `effort` has any effect:
the `claude-code` CLI accepts `--effort`, while the SDK-backed providers ignore it.

This item proposes a data structure that scopes settings to a provider. The Settings UI reads and
writes through that structure, so switching providers no longer discards the values set for the
provider left behind.

## Motivation

Switching the provider dropdown in Settings silently loses the previous selection's `model` and
`effort`. Both live behind one `MODEL_ENV`/`EFFORT_ENV` pair in the serve process's environment
(`bajutsu/serve/operations/config.py::set_provider`). Saving a new provider overwrites that pair.

Take an operator who configures `claude-code` with `effort=high` and a specific model. The operator
tries `api-key` for comparison, then switches back to `claude-code`. The model and effort are gone.
Nobody cleared them on purpose. The `api-key` save overwrote them with whatever it carried, usually
a blank.

The current design already gives Bedrock's model its own slot (`BEDROCK_MODEL_ENV`). That slot is
separate from the general `MODEL_ENV`, which the other three providers share. The split exists
because a Bedrock model id is provider-prefixed and invalid for the others. The instinct is right,
but the application is inconsistent.

`effort` has no per-provider slot. The three non-Bedrock providers still share one `model` slot,
although `claude-code` (a CLI subprocess) and `api-key`/`ant` (the Anthropic SDK) are different
backends with no reason to share a model choice.

Generalizing the Bedrock split into a per-provider structure for every field removes the ad hoc
exception. The same move fixes the shared-state loss.

## Detailed design

1. **Data structure.** Introduce a settings structure keyed by provider name, e.g.
   `dict[str, ProviderSettings]`. `ProviderSettings` holds `model`, `effort`, and, for `bedrock`
   alone, `region`. The structure lives for the life of the serve process. That matches how
   `provider`/`model`/`effort` already live in `os.environ` today: session-only, never written to
   disk. BE-0175 already documents that constraint for the provider choice itself. Persisting the
   structure across a serve restart is out of scope. A separate item covers it, so this item stays
   on the data shape and the UI wiring.
2. **Read API.** `GET /api/provider` (`provider_info`) returns the full per-provider map, not
   just the active provider's fields. The Settings UI can then pre-populate every provider's
   `model`/`effort`/`region` inputs once, with no round trip on each dropdown change.
3. **Write API.** `POST /api/provider` (`set_provider`) writes into the selected provider's slot
   and leaves the other providers' slots untouched. The active provider's slot is materialized
   into the existing `MODEL_ENV`/`EFFORT_ENV`/`BEDROCK_MODEL_ENV`/`AWS_REGION` environment
   variables, which spawned jobs already read. Job-spawning code needs no change. This item changes
   how Settings arrives at the values it writes there, and nothing else.
4. **Web UI.** The change handler of the provider `<select>` (`bajutsu/templates/serve.js`)
   swaps the visible `model`/`effort`/region fields to the newly selected provider's own
   remembered values from the fetched map. The previous provider's values no longer sit in what
   looks like a shared textbox.
5. **Validation.** Each provider's slot keeps today's provider-specific validation: a required
   Bedrock model id, `effort` checked against `EFFORT_LEVELS`, and no whitespace. Validation was
   already effectively per provider. The storage it validates into is what changes shape.

## Alternatives considered

- **Clear `model`/`effort` on every provider switch instead of remembering them.** This is
  simpler, but it does not solve the actual complaint. An operator comparing two providers back
  and forth would re-enter both fields every time. That friction is what this item exists to
  remove.
- **Move `model`/`effort` into the YAML `ai:` config schema (`defaults.ai` /
  `targets.<name>.ai`, BE-0047) as a per-provider map, instead of a serve-runtime structure.** A
  static config file already commits one target to one provider, so there is no ambiguity to fix
  there. The confusion is specific to the Settings panel's interactive provider toggle. Scoping
  the fix to serve avoids widening the config schema (and its BE-0112 deterministic-core
  boundary) to solve a UI-only problem. A config-file-level multi-provider profile remains a
  possible future extension if a use case appears.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Define the per-provider settings structure held by serve.
- [x] Return the full per-provider map from `GET /api/provider`.
- [x] Scope `POST /api/provider` writes to the selected provider's slot.
- [x] Update the Settings UI to swap fields per provider from the fetched map.

Log:

- [#762](https://github.com/bajutsu-e2e/bajutsu/pull/762) — Added the `ProviderSettings` dataclass and `ServeState.provider_settings`, returned
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

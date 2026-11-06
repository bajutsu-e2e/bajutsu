**English** · [日本語](BE-0229-per-org-provider-settings-resolution-ja.md)

# BE-0229 — Resolve serve AI provider settings per organization at runtime

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0229](BE-0229-per-org-provider-settings-resolution.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0229") |
| Implementing PR | [#955](https://github.com/bajutsu-e2e/bajutsu/pull/955) |
| Topic | AI provider configuration |
| Related | [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md), [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

Today the serve Web UI resolves the AI provider choice, the model, and the reasoning effort
*process-globally*. Saving them writes `os.environ`: `_apply_provider_env` in
`bajutsu/serve/operations/config.py` sets `PROVIDER_ENV`, `MODEL_ENV`, `EFFORT_ENV`, and the Bedrock
slots. The AI paths read the values from that one shared process environment, and spawned jobs
inherit it.

A hosted, multi-tenant serve (BE-0015) already scopes artifacts, scenarios, baselines, and secrets
*per organization*. It does so through a per-org `StoreBundle`, which the request's org selects
(`ServeState.org_of`). The AI provider selection is the one setting that stays process-wide.

This item resolves provider, model, and effort per organization at runtime. Each org's `record`,
triage, and draft paths then use that org's saved selection instead of a single shared one. BE-0184
named this as the prerequisite when it deferred its per-organization, DB-backed store.

## Motivation

BE-0184 shipped durable persistence for the provider settings. It then deferred the per-organization,
DB-backed store shape on purpose. Its reason: "in today's serve, provider/model/effort resolve
process-globally (`os.environ` + a single `ServeState.provider_settings` map), not per organization,
so a per-org store would persist values nothing reads per org." This item closes that gap.

Without per-org runtime resolution, two organizations on one hosted serve process share one
provider, model, and effort selection. Whoever saved last wins for everyone. That breaks the tenant
isolation BE-0015 gives every other stateful surface.

The per-org DB-backed store that BE-0184 deferred becomes meaningful only once a per-org read path
exists for it to feed. This item is that read path.

## Detailed design

1. **Per-organization settings state.** Replace the single `ServeState.provider_settings` map and
   the active-provider choice with an org-keyed structure. Resolve it through the same `org_of` that
   the request handlers already compute for the per-org `StoreBundle`. Local serve's single `default`
   org keeps today's shape: one bundle, one settings slot.
2. **Per-request resolution instead of a shared process env.** The AI paths that read
   provider, model, and effort from `os.environ` must resolve them from the requesting org's
   settings instead. Jobs are subprocesses that inherit the parent's environment. The resolved
   per-org values must therefore reach the spawn as that job's own environment (a per-job env overlay), and the
   shared `os.environ` stays unmutated. Otherwise one org's save would still leak into every other
   org's jobs. This is the load-bearing change; the store work below depends on it.
3. **Wire the per-organization, DB-backed store deferred by BE-0184.** With per-org resolution in
   place, add the DB-backed `ProviderSettingsStore` shape, parallel to `DbSecretStore`. It loads and
   saves per org through the repository seam that the secret store and job records already use. A
   saved selection then survives a restart *per organization* on a hosted deployment.
4. **Local parity and zero-config unchanged.** Local serve has a single `default` org and no
   database. It keeps BE-0184's file-backed `LocalProviderSettingsStore` and today's resolution
   behavior. The AI-free zero-config path (BE-0101) is likewise untouched when nothing is persisted.

## Alternatives considered

- **Keep the process-global env and accept one provider selection per serve process.** Rejected.
  It breaks tenant isolation on a hosted, multi-tenant deployment without any signal. Every other
  stateful surface (artifacts, scenarios, secrets) is already per-org. A shared provider selection
  lets one operator's save change every other org's AI runs.
- **Add the per-org DB-backed store without per-org runtime resolution first.** That would do
  BE-0184's deferred box directly. Rejected for the reason BE-0184 recorded: nothing would read the
  stored values per org, so the store would persist state with no effect. Runtime resolution is the
  prerequisite, which is why it is a separate item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Introduce per-organization provider settings state, keyed like the per-org `StoreBundle`.
- [x] Resolve provider/model/effort per request/job from the org's settings, passing them as a
  per-job environment overlay instead of mutating the shared process env.
- [x] Add the per-organization, DB-backed `ProviderSettingsStore` shape (the box BE-0184 deferred).
- [x] Confirm local parity (single `default` org, file-backed store) and the zero-config path are
  unchanged.

### Log

- Implemented per-org provider resolution + the per-job env overlay + the DB-backed store, wired
  through `ServeState.provider_settings` (keyed by org), `serve.operations.config` (`provider_env` /
  `resolve_provider_env` / lazy `_org_settings`), `_spawn_env` (managed-var strip + overlay), the
  `_register_and_dispatch` seam, the worker job spec, and migration `0011_provider_settings`. Local
  serve and the zero-config path are unchanged. ([#955](https://github.com/bajutsu-e2e/bajutsu/pull/955))

## References

- [BE-0184 — Persist serve AI provider settings across restarts](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)
- [BE-0183 — Per-provider AI settings in the serve Web UI](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md)
- [BE-0136 — Write-once secrets store for serve](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
- [BE-0101 — Legible Claude-using / Claude-free split with a zero-config non-AI path](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md)

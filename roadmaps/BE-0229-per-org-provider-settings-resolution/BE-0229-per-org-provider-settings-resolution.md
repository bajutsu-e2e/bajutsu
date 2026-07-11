**English** · [日本語](BE-0229-per-org-provider-settings-resolution-ja.md)

# BE-0229 — Resolve serve AI provider settings per organization at runtime

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0229](BE-0229-per-org-provider-settings-resolution.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0229") |
| Topic | AI provider configuration |
| Related | [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md), [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's AI provider choice, model, and reasoning effort resolve *process-globally*:
saving them writes `os.environ` (`_apply_provider_env` in `bajutsu/serve/operations/config.py`
sets `PROVIDER_ENV` / `MODEL_ENV` / `EFFORT_ENV` / the Bedrock slots), and the AI paths pick them
up from that one shared process environment, which spawned jobs inherit. A hosted, multi-tenant
serve (BE-0015) already scopes artifacts, scenarios, baselines, and secrets *per organization*
through a per-org `StoreBundle` resolved from the request's org (`ServeState.org_of`), but the AI
provider selection is the one setting that stays process-wide. This item proposes resolving
provider/model/effort per organization at runtime, so each org's `record` / triage / draft paths
use that org's saved selection rather than a single shared one — the prerequisite BE-0184 named
when it deferred its per-organization, DB-backed store.

## Motivation

BE-0184 shipped durable persistence for the provider settings and then deliberately deferred the
per-organization, DB-backed store shape, because "in today's serve, provider/model/effort resolve
process-globally (`os.environ` + a single `ServeState.provider_settings` map), not per
organization, so a per-org store would persist values nothing reads per org." That is the gap this
item closes: without per-org runtime resolution, two organizations sharing one hosted serve
process necessarily share one provider/model/effort selection — whoever saved last wins for
everyone, which breaks the tenant isolation BE-0015 gives every other stateful surface. The
per-org DB-backed store BE-0184 deferred only becomes meaningful once there is a per-org read path
for it to feed; this item is that read path.

## Detailed design

1. **Per-organization settings state.** Replace the single `ServeState.provider_settings` map and
   active-provider choice with an org-keyed structure, resolved through the same `org_of` the
   request handlers already compute for the per-org `StoreBundle`. Local serve's single `default`
   org keeps exactly today's shape — one bundle, one settings slot.
2. **Per-request resolution instead of a shared process env.** The AI paths that today read
   provider/model/effort from `os.environ` must instead resolve them from the requesting org's
   settings. Because jobs are spawned as subprocesses that inherit the parent's environment, the
   resolved per-org values must be passed to the spawn as that job's own environment (a per-job env
   overlay) rather than by mutating the shared `os.environ` — otherwise one org's save would still
   leak into every other org's jobs. This is the load-bearing change; the store work below depends
   on it.
3. **Wire the per-organization, DB-backed store deferred by BE-0184.** With per-org resolution in
   place, add the DB-backed `ProviderSettingsStore` shape (parallel to `DbSecretStore`), loaded and
   saved per org through the same repository seam the secret store and job records already use, so
   a saved selection survives a restart *per organization* on a hosted deployment.
4. **Local parity and zero-config unchanged.** Local serve (a single `default` org, no database)
   keeps BE-0184's file-backed `LocalProviderSettingsStore` and today's resolution behavior
   unchanged; the AI-free zero-config path (BE-0101) is likewise untouched when nothing is
   persisted.

## Alternatives considered

- **Keep the process-global env and accept one provider selection per serve process.** Rejected:
  it silently breaks tenant isolation on a hosted, multi-tenant deployment — every other stateful
  surface (artifacts, scenarios, secrets) is already per-org, and leaving the provider selection
  shared makes one operator's save change every other org's AI runs.
- **Add the per-org DB-backed store without per-org runtime resolution first** (i.e. do BE-0184's
  deferred box directly). Rejected for the reason BE-0184 recorded: nothing would read the stored
  values per org, so it would persist state with no effect. Runtime resolution is the prerequisite,
  which is why it is a separate item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Introduce per-organization provider settings state, keyed like the per-org `StoreBundle`.
- [ ] Resolve provider/model/effort per request/job from the org's settings, passing them as a
  per-job environment overlay instead of mutating the shared process env.
- [ ] Add the per-organization, DB-backed `ProviderSettingsStore` shape (the box BE-0184 deferred).
- [ ] Confirm local parity (single `default` org, file-backed store) and the zero-config path are
  unchanged.

## References

- [BE-0184 — Persist serve AI provider settings across restarts](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)
- [BE-0183 — Per-provider AI settings in the serve Web UI](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md)
- [BE-0136 — Write-once secrets store for serve](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
- [BE-0101 — Legible Claude-using / Claude-free split with a zero-config non-AI path](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md)

**English** · [日本語](BE-0184-persist-serve-ai-provider-settings-ja.md)

# BE-0184 — Persist serve AI provider settings across restarts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0184](BE-0184-persist-serve-ai-provider-settings.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0184") |
| Implementing PR | [#874](https://github.com/bajutsu-e2e/bajutsu/pull/874), [#955](https://github.com/bajutsu-e2e/bajutsu/pull/955) |
| Topic | AI provider configuration |
| Related | [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md), [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's AI provider choice, model, and reasoning effort live only in the serve
process's environment: they are written to `os.environ` on save and read back from there,
never to disk (BE-0175 documents this explicitly for the provider choice, "for this session
only — never to disk"). Every restart of `serve` — a redeploy, a crash, a plain restart of
the local process — resets these to whatever the launch environment (or the config file's
`ai:` block) provides, and the operator re-enters them by hand. This item proposes an actual
persistence layer so a saved choice survives a restart, the way the Claude API key already
does through the write-once secret store (BE-0136).

## Motivation

An operator who sets up a provider, model, and effort in Settings expects that choice to
stick — that expectation is exactly what BE-0136 already grants the API key, which is
encrypted per organization and outlives a restart. Provider/model/effort are not secrets, so
they do not need BE-0136's write-once, no-reveal shape, but a hosted, multi-tenant deployment
(BE-0015) needs the same per-organization durability for them that the key already has:
re-entering a provider and model after every deploy is friction with no security upside,
since none of these values are sensitive.

This item assumes the per-provider settings structure proposed in the companion item
"Per-provider AI settings in the serve Web UI" (not yet numbered): persistence is about where
that structure's values come from at boot and where they go on save, not about its shape.

## Detailed design

1. **Storage.** A local `serve` persists the per-provider settings map to a small file
   alongside serve's other local state; a hosted, multi-tenant deployment persists it per
   organization through the same durable, DB-backed org storage the secret store and job
   records already use (BE-0015 / BE-0136 precedent). Neither path reuses the secret store
   itself — see *Alternatives considered*.
2. **Boot read.** On startup, serve loads the persisted map, if one exists, before falling
   back to whatever the launch environment or the bound config's `ai:` block provides, so the
   resolved provider/model/effort match what was last saved rather than resetting.
3. **Zero-config compatibility.** An operator who has never opened Settings sees no change:
   with nothing persisted, resolution falls back to today's env-derived defaults, keeping the
   AI-free zero-config path (BE-0101) exactly as it is.
4. **Access.** Unlike the API key, these values are read back and displayed for editing —
   there is no write-once or no-reveal constraint to carry over from BE-0136.

## Alternatives considered

- **Persist by writing back into the operator's `bajutsu.config.yaml`.** Rejected: the
  Settings panel must not mutate a config file the operator owns and tracks out from under
  them. A Git-sourced or uploaded config is already treated as an external, largely read-only
  input elsewhere (e.g. the build-command trust boundary in BE-0121); writing serve-local
  preferences back into it would blur that boundary for no benefit a separate store doesn't
  already give.
- **Reuse the existing write-once secret store (BE-0136) for these values too.** Rejected:
  that store is deliberately write-once and non-readable, which fits a credential but not a
  value the UI must read back and let the operator see and edit. A plain, readable store is
  the right shape here.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a durable per-provider settings store — the local, file-backed shape
  (`LocalProviderSettingsStore`, a JSON file alongside serve's run directory).
- [x] Add the per-organization, DB-backed store shape for a hosted deployment. Shipped by
  [BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md),
  which added the per-org runtime resolution this box was waiting on and then wired the
  DB-backed `ProviderSettingsStore` this box named.
- [x] Load persisted settings on serve boot, falling back to today's env-derived defaults.
- [x] Confirm the AI-free zero-config path is unaffected when nothing is persisted.

### Log

- 2026-07-10 — Local file persistence shipped: a readable `ProviderSettingsStore` seam with a
  file-backed `LocalProviderSettingsStore` (`bajutsu/serve/provider_store.py`), wired into local
  serve construction so a saved provider/model/effort is flushed on save and restored on boot;
  a malformed file logs a visible warning and falls back to the env defaults rather than crashing.
  The hosted per-org DB-backed shape was deferred at the time (see the next log entry for when
  it shipped).
  ([#874](https://github.com/bajutsu-e2e/bajutsu/pull/874))
- 2026-07-12 — The deferred per-org DB-backed store shipped in
  [BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)
  ([#955](https://github.com/bajutsu-e2e/bajutsu/pull/955)): per-org runtime resolution plus the
  DB-backed `ProviderSettingsStore` this item's deferred box named. All boxes are now done, so
  Status flips to **Implemented**.

## References

- [BE-0136 — Write-once secrets store for serve](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)
- [BE-0175 — Sign in to the `ant` provider from the serve Web UI](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md)
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
- [BE-0101 — Legible Claude-using / Claude-free split with a zero-config non-AI path](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md)
- [BE-0229 — Resolve serve AI provider settings per organization at runtime](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)
  — shipped the per-org DB-backed store this item deferred.
- A companion item, "Per-provider AI settings in the serve Web UI" (not yet numbered),
  proposes the per-provider data structure this item persists.

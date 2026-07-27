**English** · [日本語](BE-XXXX-compose-incremental-artifact-upload-ja.md)

# BE-XXXX — Reuse the active composition when uploading only the legs that changed

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-compose-incremental-artifact-upload.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1386](https://github.com/bajutsu-e2e/bajutsu/pull/1386) |
| Topic | Configuration sourcing |
| Related | [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) already
lets Open config upload `config`, `scenarios`, and `binary` as three content-addressed artifacts
and compose them into one runnable tree. In practice the compose picker still forces a full
selection every time. The browser holds the chosen legs while the modal stays open, and a
typical iOS config's coherence check requires every referenced leg. Reloading the page, or
returning later to swap the binary alone, means re-picking every zone.

This item makes composition **incremental at the leg boundary**. Opening the compose picker
pre-fills each zone from the active composition when one is bound. A user can replace the legs
that changed and leave the rest inherited. `POST /api/compose` stays a pure function of its
request body — the server never fills missing legs from live state — and the deterministic
runner, scenario schema, and gate remain untouched.

## Motivation

BE-0268 solved the wire cost of unchanged bytes: an artifact whose sha256 is already stored is
not re-uploaded. It did not solve the **selection cost**. The Open config UI still asks the
operator to assemble a complete triple in one modal session. Two workflows suffer for it:

1. **Swap one leg after a bind.** The common hosted loop is "new CI binary, same config and
   scenarios". Today that means dropping all three again (or keeping the modal open forever). The
   binary already travels alone when its sha is new; the UI should let the other two legs ride
   along from the active bind without another pick.
2. **Resume after a reload.** Closing the modal or refreshing the page clears `composeState`. The
   content-addressed store still holds every artifact, but the picker no longer knows which triple
   is live, so the operator reconstructs the selection from memory.

The fix must not weaken BE-0268's determinism. Composition that "guesses" omitted legs from
server state would make the same POST body mean different trees depending on who last bound what
— the opposite of directive 2. The request body must stay a complete, explicit triple; the UI
is what remembers the previous legs and fills them in before the click.

## Detailed design

The work is mutually exclusive and collectively exhaustive (MECE) across three units: bind
provenance, a read application programming interface (API) for the active composition, and the
compose-picker UI.

### 1. Per-leg display names on a composed bind

A composed `Upload` already carries `artifact_shas` — one sha per supplied leg. This item adds a
sibling map of **display filenames** (`artifact_names`), recorded when `POST /api/compose`
succeeds from the request's `filename` / `scenariosName` (and a stable default for `binary`). The
names are provenance for the UI only: they never affect `materialize_composition`'s layout. They
do matter for a single-YAML `scenarios` leg, because that path salts the composition cache key
with `scenariosName`; re-composing an inherited YAML without the name would either fail the salt
or invent a different tree. Storing the name on the bind makes the resume path replay the same
body the picker sent the first time.

A legacy single-zip bind ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md))
and a Git / filesystem bind leave both maps unset. Only a composed triple exposes them.

### 2. `GET /api/compose/current` — read the active composition's legs

A new admin-gated GET returns the live composed bind as UI seed data, or an empty payload when
nothing composed is bound:

```json
{
  "artifacts": {
    "config": { "sha256": "…", "filename": "bajutsu.config.yaml" },
    "scenarios": { "sha256": "…", "filename": "scenarios.zip" },
    "binary": { "sha256": "…", "filename": "Demo.app.zip" }
  }
}
```

Rules:

- **Empty when not a composed bind.** No config, a Git/fs bind, or a legacy zip bind returns
  `{"artifacts": {}}` with HTTP 200 — never a 404 — so the UI can treat "nothing to inherit" as a
  normal empty seed.
- **Org-scoped.** The bind must belong to the caller's org (the same tenancy BE-0243 already
  applies to upload caches). Another org's active composition is invisible; the response is empty.
- **Admin-gated like `/api/artifacts/exists`.** The response discloses stored sha256 digests and
  filenames. It gets an explicit early case in `required_role` (a GET never reaches the
  POST-only `_ADMIN_PATHS` set).
- **No silent compose.** This endpoint never materializes or rebinds. It only reports what
  `state.upload` already holds.

`POST /api/compose` is unchanged in meaning: every leg the config needs must still appear in the
body. The client builds that body from inherited seed plus any zone the user overwrote.

### 3. Compose-picker UI — inherit, overwrite, clear

When Open config opens, the compose section calls `GET /api/compose/current` and fills each zone
whose `composeState` entry is still empty. A zone the user already picked in this modal session
is left alone, so a mid-session refresh of the seed cannot clobber an in-flight choice.

Each filled zone shows whether the selection is **inherited** (from the active bind) or
**uploaded / reused** in this session (the existing content-addressed skip). Every zone gains a
**Clear** control so an operator can drop a leg when switching to a config that no longer needs
it (for example, a scenarios-only target that must not keep a stale binary sha in the body).
**Compose & load** still requires a config leg; scenarios and binary remain optional at the wire
level and are still validated by `materialize_composition`'s coherence check.

Hints in the modal state that unchanged legs stay selected from the active composition, so the
incremental workflow is discoverable without reading this item.

### Determinism, the gate, and app-agnosticism

- **No LLM, no effect on the verdict.** Inheritance is acquisition UI plus a read of bind
  provenance. Pass/fail stays machine-only (directive 1).
- **Compose stays pure.** The same POST body always yields the same tree; missing legs are never
  filled from live state on the server (directive 2).
- **Linux-testable.** The new GET, the filename map, org scoping, and the "overwrite one leg over
  an inherited triple" round-trip are pure serve plumbing — unit-tested on the existing gate with
  no Simulator.
- **App-agnostic.** Layout authority stays on the config's `scenarios` / `appPath`; nothing in the
  inherit path branches per app (directive 3).

### Out of scope

- **File-level merge inside a scenarios tree.** Dropping one YAML still replaces the scenarios
  artifact as a whole (BE-0268's contract). Merging individual files into an existing tree is a
  separate feature.
- **Browsing a catalog of past artifacts.** This item seeds from the *active* composition only;
  a versioned artifact library remains out of scope as in BE-0268.
- **Reinterpreting `POST /api/upload` as decompose-to-three sugar.** Still deferred from BE-0268.

## Alternatives considered

- **Have `POST /api/compose` fill omitted legs from `state.upload`.** Rejected: the same body would
  compose different trees depending on the live bind, breaking the pure-request contract and
  making failures non-reproducible from the request alone (directive 2). The UI fills the body;
  the server does not guess.
- **Persist compose selections in `localStorage`.** Rejected as the primary mechanism: the browser
  can disagree with the server's active bind (another admin rebound the config; a different
  machine opens the UI). Seeding from `GET /api/compose/current` keeps the picker aligned with
  what Replay / Record / Crawl actually run.
- **Widen `GET /api/config` instead of a dedicated endpoint.** Rejected: `/api/config` is the
  path-and-source summary every tab polls, and stuffing per-leg sha256 into it would widen every
  client's disclosure surface. A dedicated admin GET matches `/api/artifacts/exists` and keeps the
  hot path lean.
- **Fold into BE-0268 as a Progress edit.** Rejected: BE-0268 is Implemented and stable. Incremental
  resume is a distinct UX contract with its own API and tests, cleanly `Related` rather than a
  retro-edit of a closed item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — Per-leg display names (`artifact_names`) on a composed `Upload`, recorded from the
  compose request and available to provenance / UI seed.
- [x] 2 — `GET /api/compose/current`: org-scoped, admin-gated read of the active composed legs
  (empty payload when nothing composed is bound).
- [x] 3 — Compose-picker UI: seed empty zones from the active composition on Open config, show
  inherited vs uploaded/reused, per-zone Clear, and Compose & load over the merged triple.

### Log

- 2026-07-27 — Units 1–3 implemented in the same BE-creation PR as this proposal: `Upload.artifact_names`,
  `GET /api/compose/current` (admin early case in `required_role`), and the compose picker's
  inherit / overwrite / Clear flow. `POST /api/compose` stays a pure function of its request body.

## References

- [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) (determinism first; fail rather than
  guess).
- [BE-0268 — Upload config, scenarios, and app binary as independent content-addressed artifacts](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)
  — the compose picker and `POST /api/compose` this item makes incremental.
- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
  — the legacy single-zip bind that does not expose per-leg inheritance.
- [BE-0243 — Persist uploaded zip config bundles to object storage](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)
  — org-scoped upload caches this item's read path respects.
- `bajutsu/serve/operations/upload.py` (`bind_composition` / `_compose_and_bind`),
  `bajutsu/serve/uploads.py` (`Upload.artifact_shas`),
  `bajutsu/templates/serve.panels.mjs` (compose picker),
  `bajutsu/serve/authz.py` (`required_role` early cases for admin GETs).
- [docs/configuration.md](../../docs/configuration.md), [docs/cli.md](../../docs/cli.md#serve).

**English** · [日本語](BE-XXXX-hosted-scenario-source-import-ja.md)

# BE-XXXX — Populate the hosted scenario store from a Git/zip config source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-hosted-scenario-source-import.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) |
<!-- /BE-METADATA -->

## Introduction

On the **server backend** (the hosted, multi-tenant `serve` from
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), binding a config
from a Git repository ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) or an
uploaded zip bundle ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md))
only repoints `state.config` / `state.cwd` at the fetched tree. It never copies the scenario files
that tree already contains into the hosted, per-project scenario store the server backend actually
reads from. A user who uploads a self-contained bundle, or points serve at a Git repository, sees an
empty Scenario list — the existing scenarios are on disk in the bound checkout, but nothing has ever
told the hosted store about them.

This item adds an explicit, one-shot **import** step — "copy the scenarios found in the bound
source into this project's hosted scenario store" — reachable right after binding and again on
demand, so a team's existing suite becomes runnable without hand-recreating every scenario through
`record` or the UI editor.

## Motivation

- **The reported gap.** A user uploaded a zip bundle (config + scenarios) to a `--backend=server`
  instance and the Scenario list stayed empty after the upload succeeded and the config bound
  correctly.
- **Why, structurally.** BE-0015's Phase 1 plan describes the hosted backend around a *native*
  per-project scenario store — "Scenarios + app configs stored per project in Postgres/R2" — read
  and written through `StorageScenarioStore` / `ObjectScenarioStorage`
  (`bajutsu/serve/server/scenarios.py`), which resolves content exclusively from
  `<prefix>scenarios/<app>/*.yaml` object-store keys. BE-0063 and BE-0073, by contrast, were designed
  for the self-hosted, single-tenant Tier-A `serve`, where `LocalScenarioStore`
  (`bajutsu/serve/state.py:467`) reads scenario files directly off `state.cwd` — a different
  `ScenarioStore` implementation entirely. When `bind_git_config` / `bind_upload_config` run against
  a server-backend process, they still only rebind `state.config` / `state.cwd`; that process's
  `state.scenarios` for the org stays the object-store-backed one built by `make_bundle`
  (`bajutsu/serve/__init__.py`), which never reads the bound checkout's tree. The two features work
  exactly as designed individually — the gap is that nothing bridges them.
- **Why it matters.** Bringing an existing suite — already checked into Git, or packaged for a
  one-off zip run — is precisely the "config sourcing" use case BE-0063/BE-0073 exist to serve. A
  user who successfully binds such a source reasonably expects its scenarios to show up and be
  runnable, the same way the self-hosted `serve` shows them immediately.
- **The cost of not fixing it.** Today the only way to populate the hosted scenario list is to
  author a brand-new scenario through `record`, or paste YAML into the UI editor by hand — a
  needless dead end for any team that already has a Git repository or a zip full of scenarios, and a
  surprising trap for anyone who assumes "upload" means "upload the scenarios too."

## Detailed design

**Scope.** This targets the **server backend only**
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) phase 2 onward,
`--backend=server`). The self-hosted/local backend already works correctly: `LocalScenarioStore`
resolves scenarios lazily against `state.cwd` (`bajutsu/serve/state.py:467`), so there is no
separate storage layer to reconcile there. The design covers **both** config sources named above —
a Git-sourced checkout and an uploaded zip bundle both resolve, once bound, to the same
`state.cwd`-relative `scenarios/` directory, so one implementation serves both with no per-source
branching beyond the existing `bind_git_config` / `bind_upload_config` seam.

**New operation.** `import_source_scenarios(state, org, app)` walks the bound checkout's configured
`scenarios` directory — the same resolution `_scenarios_dir_for` already performs against
`state.cwd` — reads each `*.yaml`, and calls the same `ObjectScenarioStorage.save(app, ref, text)`
seam that Record output and manual UI edits already use. No new storage seam: this only adds a new
*caller* of the existing write path.

**Conflict policy (the crux).** An import must never silently clobber a scenario someone already
authored or edited in the hosted store:

- **Default — skip existing.** Only names **not already present** in the object store for that app
  are written; an existing entry is left untouched. The response reports what happened
  (imported / skipped-already-exists / overwritten counts) so the result is legible without staring
  at the scenario list to infer it.
- **Explicit overwrite opt-in** (a checkbox, or `?overwrite=true`) lets a user deliberately re-sync
  after fixing scenarios upstream — in Git, or in a freshly re-uploaded zip — replacing the hosted
  copies with the source's.
- **One-shot, not a live link.** Import is a point-in-time copy. Afterward the hosted store is the
  record of truth for that scenario until someone re-imports or edits it in the UI — the same
  "ephemeral acquisition, durable result" framing
  [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) already uses for
  the upload itself.

**API/UI surface.** `POST /api/scenarios/import-from-source` (org/app-scoped, admin-role gated the
same way `bind_upload_config` / `bind_git_config` are), surfaced as an "Import scenarios from this
source" action both in the post-bind confirmation step of the **Open config** dialog and in the
Scenario list's toolbar, so it is discoverable right after binding and again later on demand.

**Determinism and security.** Pure file-copy plumbing — no LLM involved, satisfying prime directive
1. Reuses the existing path confinement (`Effective.rebased`) so a bound config cannot smuggle a
path outside the checkout into this new read, and reuses `valid_scenario_ref` on every candidate
`ref` before it becomes an object-store key, the same guard `StorageScenarioScope.save` already
applies, so an unsafe filename cannot traverse the object-store prefix.

## Alternatives considered

- **Layered/overlay `ScenarioStore`** — treat the git/zip tree as a read-through base layer and the
  object store as a write overlay for Record output and UI edits, unioning the two on `list()`. This
  keeps the source tree a live source of truth rather than a point-in-time snapshot, but introduces a
  staleness trap: an overlay entry sharing a name with a later-edited source file would silently keep
  serving the stale overlay copy forever. Resolving that cleanly (source-only-if-never-overridden, a
  "reset to source" affordance, or content-hash invalidation) is real added design and implementation
  weight for a benefit — always-fresh reads from a live tree — that a one-shot import mostly covers
  for the common case (import once after binding, re-import after a deliberate upstream fix). Kept as
  a future option if a team wants the hosted UI to track a fast-moving Git branch closely; not needed
  to close the reported gap.
- **Make the hosted `ScenarioStore` read straight from `state.cwd`** whenever a git/zip source is
  bound (reuse `LocalScenarioStore` in the server backend for that project). Rejected: this
  reintroduces exactly the replica-locality problem
  [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) solved
  for config — any replica serving a scenario-list request would need the checkout materialized
  locally, and unlike config (read once per job dispatch), a scenario-list read happens on every UI
  page load, making per-request re-materialization (or a distributed cache of extracted trees) a
  heavier lift than a one-shot import into storage the whole fleet already has network access to.
- **Silent, always-on auto-import on every bind, overwriting existing entries.** Rejected: it would
  clobber UI edits or Record output made since the last bind with no warning — a data-loss trap. The
  opt-in overwrite flag avoids this while still making import one click away.
- **Do nothing; document the gap instead.** Rejected as the sole fix: it leaves "bring your own
  suite" — the entire point of BE-0063/BE-0073 — non-functional on precisely the backend (server)
  most likely to be used by a team with no host filesystem access, which is BE-0073's own motivation
  #1.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `import_source_scenarios` (skip-existing default, opt-in overwrite), reusing
      `ObjectScenarioStorage.save`.
- [ ] `POST /api/scenarios/import-from-source` (admin-role gated), reporting
      imported/skipped/overwritten counts.
- [ ] Serve UI affordance: post-bind confirmation step + Scenario list toolbar action.
- [ ] Tests: Git-sourced and zip-sourced checkouts both import; skip-existing default; overwrite
      opt-in; path-confinement / `valid_scenario_ref` rejection of unsafe names.
- [ ] Docs: note in `docs/architecture.md` (both languages) on how server-backend scenario storage
      relates to a bound config's source.

## References

- [DESIGN.md §6.5](../../DESIGN.md) — scenarios are git-tracked files, Bajutsu keeps no store of its
  own for the local/CLI path; this item bridges that with the hosted project's storage-backed
  `ScenarioStore` without contradicting it — the hosted store stays the *runtime* copy a bound
  source seeds, not a replacement for Git as the team's own history.
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the hosted `server`
  backend's native per-project scenario storage (Postgres/R2) this item populates.
- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the Git config source whose
  checkout this item can import from.
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the zip bundle
  upload whose extracted tree this item can import from, and the "uploads are ephemeral" framing this
  item's one-shot import follows.
- [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) — the
  durable *raw zip bytes* storage this item's import sits alongside; distinct from the per-scenario
  object-store entries this item writes.
- `bajutsu/serve/operations/upload.py` (`bind_upload_config`), `bajutsu/serve/operations/config.py`
  (`bind_git_config`), `bajutsu/serve/server/scenarios.py` (`ObjectScenarioStorage`,
  `StorageScenarioStore`), `bajutsu/serve/state.py` (`LocalScenarioStore`, `_scenarios_dir_for`),
  `bajutsu/serve/__init__.py` (`make_bundle`, `_org_apps`) — the surfaces this item touches.

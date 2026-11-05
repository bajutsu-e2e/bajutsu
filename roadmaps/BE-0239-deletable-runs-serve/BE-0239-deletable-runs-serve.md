**English** · [日本語](BE-0239-deletable-runs-serve-ja.md)

# BE-0239 — Deletable runs and reports in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0239](BE-0239-deletable-runs-serve.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0239") |
| Implementing PR | [#985](https://github.com/bajutsu-e2e/bajutsu/pull/985) _(backend)_, [#1170](https://github.com/bajutsu-e2e/bajutsu/pull/1170) _(Web UI)_ |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

This item lets a user delete a run, and its report, individually from the `serve` Web UI. It covers
both paths: the local one (stdlib handler, filesystem-backed) and the hosted one (FastAPI, database
and object-storage-backed, BE-0015).

A soft-delete and trash window keeps the action from being instantly destructive. An audit-logged
permanent purge covers the case where a run really must be gone. Crawl runs (BE-0190) get the same
per-item deletion.

Today `serve` has **no deletion surface for runs at all**. The only `DELETE` route in the API
deregisters a project
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)). The only way to remove a
run's `runs/<id>/` tree (`report.html`, `manifest.json`, screenshots, video, network capture) is to
shell into the host and `rm -rf` it. That is impossible on the hosted backend, where runs live in
object storage and a database row (`bajutsu/serve/server/db.py`), not on a filesystem a user can
reach.

## Motivation

Runs accumulate forever today. Nothing in `serve` ever shrinks the `runs/` tree or a hosted
organization's object-storage prefix. That is fine for a demo project, but it hurts in four cases
this repo already cares about:

1. **Nothing can be done about a bad or sensitive run.** Examples are a run recorded against the
   wrong target, a flaky one-off, and a run whose evidence turns out to capture something it should
   not (a screen recording, or a leak caught after the fact, adjacent to BE-0151 and BE-0152). Only
   an operator with filesystem or bucket access can remove such a run. A viewer or editor in the Web
   UI knows the run is junk but cannot act on it.
2. **Unbounded growth on the hosted backend has a real cost.** BE-0110 moved evidence to object
   storage, and BE-0204 added Google Cloud Storage (GCS) support, because run evidence (video,
   screenshots, network capture) is not small. Every run kept forever is billed forever, and the
   product has no lever to bring that down.
3. **The list views get noisier with every run.** `GET /api/runs` and `/api/crawl/runs`
   (`bajutsu/serve/server/app.py`) already page through history for flakiness detection (BE-0220)
   and metrics (`project_metrics_view`). A project that has run continuous integration (CI) for a
   year accumulates thousands of entries. It can view more of them but cannot prune the ones nobody
   needs.
4. **Deletion is the kind of action this project handles carefully, and that care has nowhere to
   attach today.** The role-based access control (RBAC) ladder (`bajutsu/serve/authz.py`,
   `viewer < editor < admin`) and the audit log (`state.repository.record_audit`, wired for OAuth
   login and other mutations) already exist. A delete and purge feature is their natural next
   consumer, not a new subsystem.

The feature is purely a *lifecycle* feature over already-recorded data. It does not touch how a run
executes, what `assertions` or `network` decide, or the report's data contract from
[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md). No LLM is involved
anywhere in the delete, restore, and purge path, so the path sits outside the Tier-2 run/CI gate
entirely (prime directive 1). It is pure serve-mode plumbing, deterministic by construction.

## Detailed design

The work breaks into six fairly independent units.

- The trash and retention piece (3) depends on unit 1's seam existing.
- The Web UI piece (5) depends on the API existing.
- Each backend's store implementation (1) can land as a separate PR.
- The command-line companion is outside this item's scope.

1. **A `delete_run` seam on `ArtifactStore` (`bajutsu/serve/artifacts.py`), soft by default.**
   The protocol gains `soft_delete_run(run_id) -> bool`, `restore_run(run_id) -> bool`, and
   `purge_run(run_id) -> bool`. They mirror the existing `get` / `list_runs` / `archive` split
   between `LocalArtifactStore` and `ObjectStorageArtifactStore`
   (`bajutsu/serve/server/artifacts.py`). The two backends necessarily implement soft-delete
   differently, because only the hosted backend has a database (`ServeState.repository` is `None` on
   the local/loopback path, per `bajutsu/serve/state.py`).
   - `LocalArtifactStore`: soft-delete moves `runs/<id>/` under `runs/.trash/<id>/`. The move stays
     confined to `runs_dir`, so the existing path-containment guarantee in `_resolve` / `_confined`
     covers it unchanged. `list_runs` and `list_crawl_runs` never look under `.trash/`. Purge is
     `shutil.rmtree` on the trashed directory. Restore moves the directory back.
   - `ObjectStorageArtifactStore`: soft-delete writes a tombstone object (`<run_id>/.deleted`),
     alongside the existing `manifest.json` and `screenmap.json` keys that `list_runs` already
     reads. `list_runs` and `list_crawl_runs` skip any `run_id` whose tombstone key is present.
     Purge deletes every key under the run's prefix, the same key set `archive` already collects.
     Restore deletes the tombstone key.
2. **DB-backed run rows get a soft-delete column (hosted only).**
   - `Repository.list_runs` (`bajutsu/serve/server/db.py`) filters out rows with `deleted_at` set.
     The hosted backend's list is database-driven, unlike the filesystem or object-store scan that
     the two `ArtifactStore`s do for their own listings (see `db.py:108` and `:297`).
   - Deleting a run sets `deleted_at` and `deleted_by`. Restoring clears them.
   - Purge deletes the row, or converts it to a tombstone row if the audit history should survive
     the run's bytes (see *Alternatives considered*).
3. **A retention window and a purge sweep.**
   - A soft-deleted run becomes eligible for permanent purge after a configurable retention period.
     The default is 30 days, for example. A `serve` config knob alongside the other hosting settings
     sets it.
   - `serve` has no periodic-job runner today, and this item introduces no new daemon. Purge runs as
     a **lazy sweep**, checked opportunistically on the next `list_runs` or `login` call. It follows
     the precedent of `SqlSessionStore`'s expiry-on-read check in
     `bajutsu/serve/server/sessions.py`: `valid` and `identity` compare `expires_at` against now, and
     "Expiry is enforced on read". A fixed-interval background thread is not used.
4. **API surface**, alongside the existing `DELETE /api/projects/{name}`
   (`bajutsu/serve/server/app.py:418`) and its stdlib-handler counterpart (`handler.py:426`,
   `do_DELETE`):
   - `DELETE /api/runs/{run_id}` and `DELETE /api/crawl/runs/{run_id}` soft-delete a run. Both are
     org-scoped through the same `state.for_org(state.org_of(_actor(request)))` pattern that the
     `/runs/{rel:path}` reader already uses. A run in another organization's prefix therefore returns
     404 exactly as a read does, so BE-0015 multi-tenancy holds for delete too.
   - `POST /api/runs/{run_id}/restore` (and the crawl-run counterpart) undoes a soft-delete within
     the retention window.
   - `DELETE /api/runs/{run_id}?purge=true` (admin-only) skips the trash window and purges
     immediately.
   - A bulk form, `POST /api/runs/bulk-delete` with a list of ids, clears many runs at once (the
     "一括削除" case) instead of one at a time.
   - Every one of these routes goes through the unconditional CSRF Origin check and Host allowlist
     already applied to `POST` and `DELETE` (the `request.method in ("POST", "DELETE")` gate in
     `app.py`, BE-0121). A delete is exactly as CSRF-sensitive as the project-deregister `DELETE`
     next to it.
   - RBAC (`required_role` in `bajutsu/serve/authz.py`) gates the actions. Soft-delete and restore
     are **editor** actions, like triggering a run. Permanent purge is an **admin** action, matching
     how deregistering a project is gated to admin because it is similarly irreversible.
5. **Web UI.** The Web UI (`bajutsu/templates/serve.*.js`) gains four things.
   - A delete affordance per row in the run-history and crawl-history lists.
   - A multi-select toolbar action for bulk delete.
   - A confirmation dialog that states plainly what happens: the run moves to trash and is
     restorable for N days.
   - A separate, admin-only "delete forever" action with its own, more emphatic confirmation.

   A "Trash" view, or a filter toggle on the existing history list, shows soft-deleted runs with
   **Restore** and **Delete forever** actions.
6. **Audit and observability.**
   - Every soft-delete, restore, and purge goes through the same `record_audit` / `_record_audit`
     path already wired for other mutations (`bajutsu/serve/authz.py:100`,
     `bajutsu/serve/server/db.py:148`). The entry records who did what, to which run, and when.
   - Each also emits a structured `oplog` event (`EVENTS` in `bajutsu/serve/oplog.py`), for example
     `run.soft_deleted`, `run.restored`, and `run.purged`.
   - A site reliability engineer (SRE) can then grep or alert on an irreversible purge, the way
     BE-0055 already lets them grep other operational events.

## Alternatives considered

- **Hard delete only, no trash.**
  - This is simpler: one code path, no retention config, and no restore endpoint.
  - But a delete button with no undo, on evidence that may represent hours of CI history, is the
    irreversible action that the *Explicit permission required* discipline of this project's own
    tooling argues against. So does the project-deregister precedent (BE-0225).
  - Rejected in favor of soft-delete by default, with an explicit, separately gated permanent purge.
- **A uniform DB tombstone for both backends.** This would skip the filesystem `.trash/` move and
  track soft-deletes only in a database row, even on the local/loopback path. Rejected, for three
  reasons.
  - The local stdlib path has no database at all (`ServeState.repository` is `None` off the hosted
    path).
  - The local `serve` mode is deliberately a lighter-weight, filesystem-only mode
    ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)).
  - Adding a database dependency purely to support run deletion would be a much bigger footprint
    change than this feature warrants.

  The filesystem-native `.trash/` directory keeps local `serve` DB-free. It is consistent with how
  `get` and `list_runs` already diverge in implementation, but not in contract, between
  `LocalArtifactStore` and `ObjectStorageArtifactStore`.
- **Time-based or count-based automatic retention only, with no per-run delete button.**
  - It handles the "runs pile up forever" motivation.
  - It does not handle the "I want to remove *this specific* run right now" case that the user asked
    for.
  - The two are complementary, not substitutes, so both are in scope. The automatic sweep in unit 3
    acts only on runs a human already soft-deleted. It does not select runs to delete on its own.
  - A fully automatic age-based or count-based prune of *never-deleted* runs is a plausible
    follow-up. It stays out of scope here to keep the item to what was asked for.
- **On permanent purge, delete the hosted DB run row or convert it to a tombstone row.**
  - Deleting the row outright is simplest and reclaims the row. But it also erases the run from the
    audit trail's referential reach, so a later "who deleted run X, when" query has no row to join
    against.
  - Keeping a minimal tombstone row (id plus `deleted_at` and `deleted_by`, evidence bytes gone)
    preserves that history at the cost of a never-shrinking row count.
  - The proposal leans toward the tombstone row for hosted deployments, where audit history is the
    point of having a database at all. It leans toward outright deletion where no audit consumer
    exists.
  - This is a knob to settle at implementation time, not a fixed decision here. Unit 2 therefore
    flags it rather than mandating one.
- **A command-line companion (`bajutsu run rm <id>`) in the same item.** The user's ask was
  specifically "from the Web UI". A CLI-side deletion command would reuse the same
  `ArtifactStore.purge_run` seam this item introduces, so it is a natural, low-cost follow-up once
  the seam exists. It stays out of this item to keep the surface to the Web UI and its API.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `ArtifactStore.soft_delete_run` / `restore_run` / `purge_run` on `LocalArtifactStore` (trash
      directory) and `ObjectStorageArtifactStore` (tombstone object)
- [x] Soft-delete column + filtering on the hosted `Repository.list_runs` (crawl runs are
      artifact-store-driven on both backends, so no separate DB list to filter)
- [x] Retention window config + lazy purge sweep
- [x] `DELETE`/`restore`/bulk-delete API routes, CSRF + RBAC (editor soft-delete/restore, admin
      purge) + org scoping
- [x] Web UI: per-row delete, bulk-select, confirm dialogs, Trash view with restore/purge-forever
- [x] Audit-log entries + `oplog` events for soft-delete/restore/purge

**Log**

- Backend landed (units 1–4, 6): the `ArtifactStore` soft-delete/restore/purge seam on both
  backends (filesystem `.trash/` + object-store `.deleted` tombstone), the `ObjectStore`
  `delete_key`/`delete_keys` write it needed, the hosted `runs.deleted_at`/`deleted_by` column
  (migration 0012) + list filtering, the `BAJUTSU_RUN_RETENTION_DAYS` window + lazy purge sweep on
  history reads, the `DELETE`/`restore`/bulk-delete routes on both transports (CSRF + editor RBAC,
  with the admin purge gate in the operation), and audit + `oplog` (`run.soft_deleted` /
  `run.restored` / `run.purged`). The item stays `In progress`; the Web UI (unit 5) is the
  follow-up PR that flips it to `Implemented`.
- Web UI landed (unit 5), flipping the item to `Implemented`: a per-row delete and a bulk-select
  toolbar (select-all + Delete selected) on both the Replay run-history and Crawl run-history lists,
  soft-delete confirms that state the retention window, and a top-level **Trash** view listing
  soft-deleted runs with **Restore** and an admin-gated **Delete forever**. A small read seam feeds
  it — `GET /api/runs/trash` (org-scoped, sweeping expired trash first) and `retentionDays` on
  `/api/config` for the confirm/Trash copy. A Chromium smoke test drives the whole delete → trash →
  restore → bulk-delete → purge path; `docs/web-ui.md` (+ ja mirror) documents the surface.

## References

- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)
  — the `ArtifactStore` seam this item extends.
- [BE-0225 — Config project hub](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)
  — the one existing `DELETE` route and its CSRF/RBAC precedent.
- [BE-0190 — Org-scoped crawl history](../BE-0190-org-scoped-crawl-history/BE-0190-org-scoped-crawl-history.md)
  — the crawl-run listing this item's crawl-run deletion mirrors.
- [BE-0110 — Evidence store URI](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) and
  [BE-0204 — GCS support](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md)
  — why unbounded run growth has a real hosted-storage cost.
- [BE-0055 — Operational logging](../BE-0055-operational-logging/BE-0055-operational-logging.md)
  — the `oplog` event convention this item's audit events follow.

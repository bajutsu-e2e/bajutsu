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

Let a user delete a run (and its report) individually from the `serve` Web UI, on both the local
(stdlib handler, filesystem-backed) and hosted (FastAPI, DB + object-storage-backed, BE-0015)
paths, with a soft-delete/trash window so the action is not instantly destructive, plus an
audit-logged permanent purge for when a run really needs to be gone. Crawl runs (BE-0190) get the
same per-item deletion. Today `serve` has **no deletion surface for runs at all**: the only
`DELETE` route in the API is deregistering a project
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)), and the only way to
remove a run's `runs/<id>/` tree (`report.html`, `manifest.json`, screenshots, video, network
capture) is to shell into the host and `rm -rf` it — impossible on the hosted backend, where runs
live in object storage and a DB row (`bajutsu/serve/server/db.py`), not on a filesystem a user can
reach.

## Motivation

Runs accumulate forever today; nothing in `serve` ever shrinks the `runs/` tree or a hosted org's
object-storage prefix. That is fine for a demo project, but it bites in the cases this repo
already cares about:

1. **Nothing to do about a bad or sensitive run.** A run recorded against the wrong target, a
   flaky one-off, or a run whose evidence turns out to capture something it shouldn't (screen
   recording, a `BE-0151`/`BE-0152`-adjacent leak caught after the fact) has no removal path short
   of an operator with filesystem/bucket access. A viewer or editor in the Web UI — the person who
   actually knows the run is junk — cannot act on it.
2. **Unbounded growth on the hosted backend has a real cost.** BE-0110's evidence-to-object-storage
   move and BE-0204's GCS support exist because run evidence (video, screenshots, network capture)
   is not small; every run kept forever is bytes billed forever, with no lever in the product to
   bring that down.
3. **The list views get noisier with every run, forever.** `GET /api/runs` / `/api/crawl/runs`
   (`bajutsu/serve/server/app.py`) already page through history for flakiness detection
   (BE-0220) and metrics (`project_metrics_view`); a project that has run CI for a year accumulates
   thousands of entries with no way to prune the ones nobody needs, only ways to view more of them.
4. **Deletion is exactly the kind of action this project is careful about, and that carefulness
   has nowhere to attach today.** The RBAC ladder (`bajutsu/serve/authz.py`, `viewer < editor <
   admin`) and the audit log (`state.repository.record_audit`, wired for OAuth login and other
   mutations) already exist for this — a delete/purge feature is the natural next consumer, not a
   new subsystem.

This is purely a *lifecycle* feature over already-recorded data: it does not touch how a run is
executed, what `assertions`/`network` decide, or the report's data contract from
[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md). No LLM is involved
anywhere in the delete/restore/purge path, so it sits outside the Tier-2 run/CI gate entirely
(prime directive 1) — this is pure serve-mode plumbing, deterministic by construction.

## Detailed design

The work breaks into six independent-ish units; the trash/retention piece (3) depends on unit 1's
seam existing, and the Web UI piece (5) depends on the API existing, but each backend's store
implementation (1), and the CLI companion (out of this item's scope), can each land as a separate
PR.

1. **A `delete_run` seam on `ArtifactStore` (`bajutsu/serve/artifacts.py`), soft by default.**
   The protocol gains `soft_delete_run(run_id) -> bool`, `restore_run(run_id) -> bool`, and
   `purge_run(run_id) -> bool`, mirroring the existing `get`/`list_runs`/`archive` split between
   `LocalArtifactStore` and `ObjectStorageArtifactStore`
   (`bajutsu/serve/server/artifacts.py`) — the two backends necessarily implement soft-delete
   differently because only the hosted backend has a database (`ServeState.repository` is `None`
   on the local/loopback path, per `bajutsu/serve/state.py`):
   - `LocalArtifactStore`: soft-delete moves `runs/<id>/` under `runs/.trash/<id>/` (still confined
     to `runs_dir`, so the existing path-containment guarantee in `_resolve`/`_confined` covers it
     unchanged); `list_runs`/`list_crawl_runs` simply never look under `.trash/`. Purge is
     `shutil.rmtree` on the trashed directory. Restore moves it back.
   - `ObjectStorageArtifactStore`: soft-delete writes a tombstone object (`<run_id>/.deleted`,
     alongside the existing `manifest.json`/`screenmap.json` keys `list_runs` already reads);
     `list_runs`/`list_crawl_runs` skip any `run_id` whose tombstone key is present. Purge deletes
     every key under the run's prefix (the same key set `archive` already collects). Restore
     deletes the tombstone key.
2. **DB-backed run rows get a soft-delete column (hosted only).** `Repository.list_runs`
   (`bajutsu/serve/server/db.py`) filters out rows with `deleted_at` set (the hosted backend's
   list is DB-driven, unlike the filesystem/object-store scan the two `ArtifactStore`s do for
   their own listings — see `db.py:108` and `:297`); deleting a run sets `deleted_at`/`deleted_by`,
   restoring clears it, purge deletes the row (or converts it to a tombstone row if audit history
   should survive the run's bytes — see *Alternatives considered*).
3. **A retention window and a purge sweep.** A soft-deleted run is eligible for permanent purge
   after a configurable retention period (default e.g. 30 days; a `serve` config knob alongside the
   other hosting settings). There is no periodic-job runner in `serve` today, and this item does
   not introduce a new daemon: purge runs as a **lazy sweep** — checked opportunistically on the
   next `list_runs`/`login` call, matching the precedent of `SqlSessionStore`'s expiry-on-read
   check in `bajutsu/serve/server/sessions.py` (its `valid`/`identity` compare `expires_at` against
   now; "Expiry is enforced on read") — rather than a fixed-interval background thread.
4. **API surface**, alongside the existing `DELETE /api/projects/{name}`
   (`bajutsu/serve/server/app.py:418`) and its stdlib-handler counterpart (`handler.py:426`
   `do_DELETE`):
   - `DELETE /api/runs/{run_id}` / `DELETE /api/crawl/runs/{run_id}` — soft-delete, org-scoped via
     the same `state.for_org(state.org_of(_actor(request)))` pattern the `/runs/{rel:path}` reader
     already uses, so a run in another org's prefix 404s exactly like a read does (BE-0015
     multi-tenancy holds for delete too).
   - `POST /api/runs/{run_id}/restore` (and crawl-run counterpart) — undo within the retention
     window.
   - `DELETE /api/runs/{run_id}?purge=true` (admin-only) — skip the trash window and purge
     immediately.
   - A bulk form, `POST /api/runs/bulk-delete` with a list of ids, for clearing many runs at once
     (the "一括削除" case) rather than only one-at-a-time.
   - Every one of these routes goes through the unconditional CSRF Origin check + Host allowlist
     already applied to `POST`/`DELETE` (`app.py`'s `request.method in ("POST", "DELETE")` gate,
     BE-0121) — a delete is exactly as CSRF-sensitive as the project-deregister DELETE it sits next
     to.
   - RBAC (`bajutsu/serve/authz.py`'s `required_role`): soft-delete and restore are **editor**
     actions (like triggering a run); permanent purge is an **admin** action, matching how
     deregistering a project is gated admin because it is similarly irreversible.
5. **Web UI**: a delete affordance per row in the run-history and crawl-history lists
   (`bajutsu/templates/serve.*.js`), a multi-select toolbar action for bulk delete, and a
   confirmation dialog that states plainly what happens (moved to trash, restorable for N days) —
   distinct from the separate, admin-only "delete forever" action, which gets its own,
   more emphatic confirmation. A "Trash" view (or a filter toggle on the existing history list)
   shows soft-deleted runs with **Restore** and **Delete forever** actions.
6. **Audit + observability.** Every soft-delete/restore/purge goes through the same
   `record_audit`/`_record_audit` path already wired for other mutations
   (`bajutsu/serve/authz.py:100`, `bajutsu/serve/server/db.py:148`) — who did what, to which run,
   when — and emits a structured `oplog` event (`bajutsu/serve/oplog.py`'s `EVENTS`, e.g.
   `run.soft_deleted` / `run.restored` / `run.purged`) so an SRE can grep/alert on an irreversible
   purge the way BE-0055 already lets them grep other operational events.

## Alternatives considered

- **Hard delete only, no trash.** Simpler (one code path, no retention config, no restore
  endpoint), but a delete button with no undo on evidence that may represent hours of CI history
  is exactly the kind of irreversible action the *Explicit permission required* discipline this
  project's own tooling follows (and the project-deregister precedent, BE-0225) argues against.
  Rejected in favor of soft-delete-by-default with an explicit, separately-gated permanent purge.
- **A uniform DB-tombstone for both backends** (skip the filesystem `.trash/` move; track
  soft-deletes only in a database row, even for the local/loopback path). Rejected: the local
  stdlib path has no database at all (`ServeState.repository` is `None` off the hosted path) — the
  local `serve` mode is deliberately a lighter-weight, filesystem-only mode
  ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)), and adding a
  database dependency purely to support run deletion would be a much bigger footprint change than
  this feature warrants. The filesystem-native `.trash/` directory keeps local `serve` DB-free,
  consistent with how `get`/`list_runs` already diverge in implementation (not in contract)
  between `LocalArtifactStore` and `ObjectStorageArtifactStore`.
- **Time/count-based automatic retention only, no per-run delete button.** Handles the "runs pile
  up forever" motivation but not the "I want to remove *this specific* run right now" case the
  user asked for — the two are complementary, not substitutes, so both are in scope (the automatic
  sweep in unit 3 acts only on runs a human already soft-deleted, it does not auto-select runs to
  delete on its own; a fully automatic age/count-based prune of *never-deleted* runs is a
  plausible follow-up but is left out of this item's scope to keep it to what was asked for).
- **On permanent purge, delete the hosted DB run row vs. convert it to a tombstone row.** Deleting
  the row outright is simplest and reclaims the row, but it also erases the run from the audit
  trail's referential reach — a later "who deleted run X, when" query has no row to join against.
  Keeping a minimal tombstone row (id + `deleted_at`/`deleted_by`, evidence bytes gone) preserves
  that history at the cost of a never-shrinking row count. The proposal leans toward the tombstone
  row for hosted deployments (where audit history is the point of having a DB at all) and outright
  deletion where no audit consumer exists, but this is a knob to settle at implementation time, not
  a fixed decision here — hence unit 2 flags it rather than mandating one.
- **A CLI companion (`bajutsu run rm <id>`) in the same item.** The user's ask was specifically
  "from the Web UI"; a CLI-side deletion command would reuse the same `ArtifactStore.purge_run`
  seam this item introduces, so it is a natural, low-cost follow-up once the seam exists, but is
  left out here to keep this item's surface to the Web UI + its API.

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

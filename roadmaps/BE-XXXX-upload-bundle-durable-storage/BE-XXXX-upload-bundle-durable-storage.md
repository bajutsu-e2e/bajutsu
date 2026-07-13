**English** · [日本語](BE-XXXX-upload-bundle-durable-storage-ja.md)

# BE-XXXX — Persist uploaded zip config bundles to object storage for hosted serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-upload-bundle-durable-storage.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
| Related | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) lets a browser
user upload a `.zip` (config + scenarios + prebuilt app binary) and bind it as `serve`'s active
config, producing an `upload`-kind config-source record — `{kind: "upload", filename, sha256,
size}` (`bind_upload_config`, `bajutsu/serve/operations/upload.py`).
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (**Implemented**) builds
directly on that record: a registered *project* can bind to an `upload`-kind source exactly as it
binds to `git` or `file`, and durably persists the `{kind, filename, sha256, size}` record itself
(a `projects` DB row when hosted, a local JSON store otherwise).

What neither item persists is the *bytes* the `sha256` names. `POST /api/upload` extracts the zip
only into a serve-owned directory under `state.uploads_dir` — an ephemeral, per-process, local-disk
location — and BE-0073's own *Out of scope* section says so explicitly: "uploads are ephemeral;
persisting and versioning them is the Git source's job, not this one." BE-0225 already runs into
the resulting wall in shipped code: `activate_project` (`bajutsu/serve/operations/projects.py`)
refuses, with a `409` ("cannot switch to the uploaded-bundle project ...; re-upload its config to
bind it"), to reactivate an `upload`-kind project, because there is nothing left to re-extract once
the replica that received the upload is gone.

That gap doesn't matter for the single-Mac Tier-A `serve` BE-0073 targets — one process, one local
disk, no restart mid-session. It matters on the hosted, multi-tenant `server` backend
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), which is explicitly
built to run as **stateless, autoscaled replicas** behind a load balancer — BE-0015 moved sessions
and jobs to Postgres for exactly this reason ("sessions survive restarts and span replicas"). An
uploaded bundle's *content*, however, still lives only on whichever replica's local disk happened
to handle the upload. This item closes that gap: it makes the bytes behind an `upload`-kind
source's `sha256` durable and fetchable from any replica, the same way BE-0063's Git source's
content is durable and fetchable by commit SHA, so a BE-0225 project bound to an upload survives a
replica recycling instead of hitting the `409`.

## Motivation

Two deployment facts push on BE-0073's "ephemeral by design" choice from different angles:

1. **Autoscaling and pod recreation.** On the `server` backend, a replica can be recycled (a
   redeploy, a crash, a scale-down) or a request can land on a different replica than the one that
   served the upload (BE-0015's whole point in moving sessions/jobs to the database). Today, the
   extracted bundle exists only under `state.uploads_dir` on the replica that extracted it — a
   `ServeState` field, in-process, on local disk. A run request that reaches a different replica, or
   the same replica after a restart, finds nothing to materialize from.
2. **BE-0225 already hits this wall in shipped code.** BE-0225's `ProjectRegistry`
   (`bajutsu/serve/project_registry.py`) durably persists a project's config-source record — for an
   `upload`-kind project, `{kind: "upload", filename, sha256, size}` — in a `projects` DB row
   (hosted) or a local JSON store (no database). Reopening a Git-bound project later re-clones from
   its stored commit SHA on whatever replica needs it; reopening an upload-bound project has nothing
   to re-materialize from, so `activate_project` refuses it outright: `kind == "upload"` always
   returns a `409`. The `sha256` BE-0225 already stores durably is exactly the right
   content-addressed key for retrieving the bytes it names — nothing about the record needs to
   change — but nothing today writes those bytes anywhere durable for that key to resolve against.

A related operational note, not something this item needs to fix: BE-0063's own cache-root
resolution (`_default_cache_root()` in `bajutsu/config_source.py`) falls back to `Path.home()`,
which raises at runtime in a container that runs under an externally supplied UID with no matching
`/etc/passwd` entry and no `HOME` set. Setting `HOME` to any writable path avoids this entirely —
the cache directory tree is created on demand (`mkdir(parents=True, exist_ok=True)`), so it need not
already exist. A hosted deployment that enables the Git source should set `HOME` explicitly for this
reason. This item's own local materialization directory (unit 2, below) is `state.uploads_dir` —
already an explicit, `--runs`-relative path rather than one derived from `HOME` — so it does not
inherit this precondition.

The fix follows the shape the codebase already uses twice over, so it adds no new mechanism:

- **Content addressing.** BE-0073 already computes the zip's sha256 for provenance (recorded into
  `manifest.json`), and BE-0225 already persists it as part of the `upload`-kind source record.
  That hash is already the right durable, collision-resistant key — the exact role a resolved
  commit SHA plays for the Git source.
- **The object-store seam.** [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md)
  already gives the server backend a credentialed `ObjectStore` (`BAJUTSU_SERVER_STORE`,
  `s3://`/`gs://`) for exactly this shape of "must outlive a replica and be readable from any of
  them" data — today used for run artifacts, scenarios, and visual baselines. Uploaded bundle
  content is the same shape of data and belongs behind the same seam, not a fourth bespoke store.

None of this touches pass/fail, the runner, the drivers, or the scenario schema — it is acquisition
plumbing ahead of the deterministic `run`, so prime directives 1–3 ([CLAUDE.md](../../CLAUDE.md))
hold unchanged. It also does not touch AI provider/model settings persistence, a separate, already
tracked gap ([BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)'s
deferred hosted, DB-backed box).

## Detailed design

The work is MECE across three units: durable persistence at upload time, an on-demand
fetch-and-extract fallback that replaces `activate_project`'s unconditional `409` for
`upload`-kind projects, and graceful degradation with no object store configured. No new
config-source shape is needed: BE-0225 already stores `{kind: "upload", filename, sha256, size}`
durably; this item only makes the bytes behind that `sha256` resolvable.

### 1. Persist the raw zip at upload time, content-addressed and org-scoped

`bind_upload_config`'s handling of `POST /api/upload` (`bajutsu/serve/operations/upload.py`) keeps
its current behavior unchanged — stream to a temp file, validate, extract into a fresh directory
under `state.uploads_dir`, bind it as the active config — and, **when a `BAJUTSU_SERVER_STORE` is
configured**, additionally writes the raw (still-compressed) zip bytes to that store under a key
derived from the sha256 it already computes. The key nests under the same per-org prefix every
other object this seam stores already uses: `artifact_prefix`, `scenario_prefix`, and
`baseline_prefix` are each composed as `xxx_prefix(org_prefix(base, org))`
(`bajutsu/serve/server/object_store.py`, wired in `_build_server_state`), so an analogous
`upload_prefix` follows the same shape — the key becomes
`upload_prefix(org_prefix(base, org)) + f"{sha256}.zip"`. Nesting under `org_prefix` keeps
deduplication (and resolution) scoped to one tenant, matching the isolation boundary every sibling
store already enforces; a shared, org-agnostic namespace would let one org's upload dedupe
against, and potentially be resolved by, another org's identical-content upload. Writing the raw
zip, not the extracted tree, keeps the object small, keeps zip-slip/zip-bomb validation as a step
every materialization repeats locally (below) rather than something trusted once and reused, and
mirrors the shape BE-0060's export already treats a run bundle as (a single zip artifact). Content
addressing makes a repeat upload of an identical bundle a no-op write within an org: the key
already exists, so nothing changes except the local extraction, which still happens exactly as
today for the request that produced it.

### 2. Replace `activate_project`'s upload-kind `409` with a fetch-and-extract fallback

`activate_project` (`bajutsu/serve/operations/projects.py`) gains a second resolution path for
`kind == "upload"`, tried before it gives up: fetch the zip bytes from the object store by the
source record's `sha256` (scoped to the project's org), then extract locally into a fresh
directory under `state.uploads_dir` exactly as a fresh upload would, running the same
zip-slip/zip-bomb/path-confinement checks `bind_upload_config` already applies, and bind the
result the same way `bind_upload_config` does today. Only when no object store is configured, or
the key is absent from it, does today's `409` stand. The local `uploads_dir` becomes a cache in
front of the durable object store, the same relationship
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)'s local, content-addressed Git
checkout cache (`~/.cache/bajutsu/gitsrc/...`) already has to its remote origin — a resolved SHA
(there) or a sha256 (here) is what's durable; the checked-out or extracted tree on local disk is
always disposable and reproducible from it. This is what lets `activate_project` succeed on a
fresh or different replica for a bundle it never itself received.

### 3. Graceful degradation with no object store configured

A local `serve` with no `BAJUTSU_SERVER_STORE` (the default, and every non-hosted Tier-A/self-hosted
deployment) sees no behavior change at all: uploads stay exactly as ephemeral as BE-0073 and
BE-0225 ship them today, and `activate_project` keeps refusing an `upload`-kind project with the
same `409` — matching the zero-config precedent already established for optional stores
([BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md)) and settings
([BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)).
Durable, cross-replica resolution is additive and only engages when the operator has already opted
into the object-store seam for artifacts/scenarios/baselines.

### Retention

Content addressing gives natural deduplication within an org (re-uploading the same bytes is a
no-op write); actual deletion is left to the bucket's own lifecycle policy, the same retention
mechanism `bajutsu/object_store.py`'s module docstring already documents as governing every
URI-addressed store this seam backs. No new retention or garbage-collection code is introduced.

## Alternatives considered

- **Store the zip (or its extracted tree) as a BLOB in Postgres.** Rejected: BE-0015's schema
  already keeps large binary payloads out of the database (artifacts, scenarios, and baselines all
  went to the object store under BE-0204, not to a table), and a multi-megabyte app binary is exactly
  the payload shape Postgres is the wrong tool for. Consistency with the existing storage seam matters
  more than avoiding a second infrastructure dependency the deployment already has.
- **Persist the extracted tree instead of the raw zip.** Rejected: it is a larger object (many small
  keys instead of one), and it would let a future materialization skip the zip-slip/zip-bomb checks
  that only run at extraction time — re-extracting from the raw zip on every materialization keeps
  that validation live rather than trusted-once.
- **Route persistent scenario storage exclusively through the Git source, as BE-0073 originally
  intended.** This remains the right answer for a team that wants a *reusable, versioned* suite —
  nothing here changes that recommendation. It does not, however, help the case BE-0225 already
  registers: a project bound to a one-off uploaded bundle (no Git repository behind it) that still
  needs to survive a replica restart. The two sources solve different problems and stay complementary.
- **Introduce a separate `zip` config-source kind, distinct from the shipped `upload` kind.**
  Rejected: BE-0225's `_KIND_TO_SOURCE` and the frontend (`serve.core.js`, `source.kind ===
  'upload'`) already treat `upload` as the sole discriminator for this shape of source, wired
  end to end through the allowlist, the UI, and the registry. Forking a second name for the same
  shape of data would fragment that one already-wired path for no benefit.
- **Make this item responsible for BE-0225's project-registry persistence too.** Rejected as scope
  creep: BE-0225 already owns *which project points at which source record* and *where that record is
  stored* (DB row vs. local JSON); this item's job is narrower — making the bytes behind an
  already-durable `sha256` resolvable from any replica. Bundling the two would couple an
  object-storage concern to a registry concern BE-0225 already implements independently.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — Persist the raw uploaded zip to the object store at upload time, keyed by its sha256 under the project's org prefix, when `BAJUTSU_SERVER_STORE` is configured.
- [ ] 2 — `activate_project` gains a fetch-and-extract-from-object-store fallback for `kind == "upload"`, tried before the existing `409`, re-running BE-0073's zip-slip/zip-bomb/path-confinement checks on every materialization.
- [ ] 3 — Confirm zero-config behavior (and the existing `409`) is unchanged with no `BAJUTSU_SERVER_STORE` configured.

## References

- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the ephemeral upload path this item makes durable; the sha256 provenance hash this reuses as the content-addressed key.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the content-addressed, local-cache-in-front-of-a-durable-origin shape this item mirrors for uploads.
- [BE-0204 — Server storage: GCS support alongside S3](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md) — the `BAJUTSU_SERVER_STORE` / `ObjectStore` seam this item's persistence and materialization write to and read from.
- [BE-0108 — Restrict config sources to upload and Git when hosted](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) — why upload is one of the two config sources a hosted deployment must support well.
- [BE-0225 — Config project hub in serve](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (Implemented) — the consumer whose `upload`-kind project registration already hits the gap this item closes (`activate_project`'s `409`).
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the stateless, autoscaled-replica topology this item's cross-replica resolution targets.
- [BE-0184 — Persist serve AI provider settings across restarts](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md) — the sibling, separately tracked gap for settings (not scenario content), explicitly out of scope here.
- `bajutsu/object_store.py` (`ObjectStore`, `object_store_from_uri`, `upload_tree`), `bajutsu/serve/operations/upload.py` (`bind_upload_config`), `bajutsu/serve/operations/projects.py` (`activate_project`, `_KIND_TO_SOURCE`), `bajutsu/serve/project_registry.py` (`ProjectRegistry`), `bajutsu/serve/server/object_store.py` (`org_prefix`, `artifact_prefix`/`scenario_prefix`/`baseline_prefix`), `bajutsu/serve/uploads.py` (`extract_bundle`), `bajutsu/serve/state.py` (`uploads_dir`, `bind_upload`/`release_upload`).

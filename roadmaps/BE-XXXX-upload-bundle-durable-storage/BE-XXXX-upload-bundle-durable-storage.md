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

A related operational note now bears on this item directly, not just on BE-0063: the Git source's
own cache-root resolution (`_default_cache_root()` in `bajutsu/config_source.py`) resolves under
`XDG_CACHE_HOME`, falling back to `Path.home() / ".cache"` — which raises at runtime in a container
that runs under an externally supplied UID with no matching `/etc/passwd` entry and no `HOME` set.
Setting `HOME` to any writable path avoids this entirely — the cache directory tree is created on
demand (`mkdir(parents=True, exist_ok=True)`), so it need not already exist. A hosted deployment
that enables the Git source already has to set `HOME` explicitly for this reason. Unit 2, below,
moves this item's own local materialization directory onto that same cache root — a sibling of
BE-0063's `gitsrc` cache under `.../bajutsu/` — instead of leaving it the `--runs`-relative
`state.uploads_dir` default BE-0073 shipped, so it now inherits this same precondition rather than
avoiding it. That is a deliberate trade, not an oversight: a hosted `server` deployment commonly
runs with a small, explicitly allow-listed set of writable paths for security reasons, so
consolidating every piece of local materialization — Git checkouts and now upload extracts alike —
under the one cache root an operator already provisions for the Git source keeps that allow-list as
small as possible, rather than adding a second, `--runs`-relative writable directory with its own
permission story.

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
its current validation and extraction unchanged — stream to a temp file, validate, extract into a
fresh directory under `state.uploads_dir` — and, **when a `BAJUTSU_SERVER_STORE` is configured**,
inserts one new step *before* `state.bind_upload(upload)` flips `serve`'s active config: it writes
the raw (still-compressed) zip bytes to that store under a key derived from the sha256 it already
computes. The key nests under the same per-org prefix every
other object this seam stores already uses: `artifact_prefix`, `scenario_prefix`, and
`baseline_prefix` are each composed as `xxx_prefix(org_prefix(base, org))`
(`bajutsu/serve/server/object_store.py`, wired in `_build_server_state`), so an analogous
`upload_prefix` follows the same shape — the key becomes
`upload_prefix(org_prefix(base, org)) + f"{sha256}.zip"`. Nesting under `org_prefix` keeps
deduplication (and resolution) scoped to one tenant, matching the isolation boundary every sibling
store already enforces; a shared, org-agnostic namespace would let one org's upload dedupe
against, and potentially be resolved by, another org's identical-content upload. Writing the raw
zip, not the extracted tree, keeps the object small, keeps zip-slip/zip-bomb validation as a step a
replica actually runs at least once for any `sha256` it has never seen locally (unit 2, below)
rather than something trusted once, by whichever replica happened to extract it, and reused
verbatim everywhere else, and mirrors the shape BE-0060's export already treats a run bundle as (a
single zip artifact). Content addressing makes a repeat upload of an identical bundle a no-op at
both layers: the object-store write is skipped because the key already exists, and — because unit
2 also keys the local extraction by `sha256` — the local extraction is skipped for the same reason,
whether the repeat comes as a second `POST /api/upload` or as `activate_project`'s fallback.

The object-store write is synchronous and blocking, and — this is the reason it goes *before* the
bind, not after — `bind_upload_config` never calls `state.bind_upload(upload)` until it succeeds.
If the write fails (a network error, an unreachable bucket, a permission denial), the function
removes the freshly-extracted local directory exactly as its existing validation-failure paths
already do and returns an error, having touched no shared state: `serve`'s active config is still
whatever it was before the request, so there is nothing to roll back. Sequencing the write before
the bind, rather than binding first and rolling back on a later failure, is what keeps this
failure path simple — a client either gets a bound config backed by durably-persisted bytes, or an
error and no state change at all, never the two disjoint from each other. This is deliberately
unlike the post-run evidence upload's best-effort failure handling (`bajutsu/object_store.py`'s
`upload_tree`, where a finished run's already-final verdict must not depend on an artifact upload
succeeding): here nothing is final yet, and a best-effort write would let a caller register a
BE-0225 project whose already-durable `{kind: "upload", sha256, ...}` record points at a key
nothing ever wrote — silently reintroducing, for that one bundle, exactly the cross-replica gap
this item exists to close. A clear upload failure is safer than a durable project record that lies
about durability, or an active config that outruns one.

### 2. Replace `activate_project`'s upload-kind `409` with a content-addressed fetch-and-extract fallback

This unit also relocates where the local materialization cache lives. `state.uploads_dir`
(`bajutsu/serve/state.py`) defaults today to a `--runs`-relative sibling directory
(`runs_dir.parent / "uploads"` for the `local` backend; an accidental bare `Path("uploads")` for
`server`, since `_build_server_state` never overrides it). This item changes that default to a
sibling of BE-0063's Git checkout cache under the same `bajutsu` cache namespace —
`<XDG_CACHE_HOME or ~/.cache>/bajutsu/uploads/`. `_default_cache_root()`
(`bajutsu/config_source.py`) does not expose that resolution as a reusable piece today: it returns
`Path(...) / "bajutsu" / "gitsrc"` directly, with the `gitsrc` leaf baked into the function itself,
so calling it unmodified for uploads would resolve to `.../bajutsu/gitsrc/uploads/` — nested under
the Git cache, not a sibling of it. The fix factors the shared `XDG_CACHE_HOME`/`Path.home()`
prefix out into its own helper (a `_bajutsu_cache_root() -> Path` returning `.../bajutsu/`), with
`_default_cache_root()` becoming `_bajutsu_cache_root() / "gitsrc"` and the new upload-cache
default `_bajutsu_cache_root() / "uploads"` — one shared fallback rule with two siblings built on
it, instead of `_default_cache_root()` reused unmodified (wrong path) or duplicated wholesale (two
rules that could drift). `state.uploads_dir` keeps its name and its role — a serve-owned
directory distinct from `--root`, BE-0051's confinement boundary — only its default location moves;
nothing about that boundary depends on which writable path it happens to be.

`activate_project` (`bajutsu/serve/operations/projects.py`) gains a second resolution path for
`kind == "upload"`, tried before it gives up. Both this path and unit 1's own local extraction
resolve to a fixed, sha256-keyed directory under `state.uploads_dir` (`state.uploads_dir /
sha256`), mirroring the `<cache>/<host>/<owner>/<repo>/<sha>` shape `materialize()`
(`bajutsu/config_source.py`) already uses for the Git source's checkout cache: if that directory
already exists, extraction is skipped entirely and the existing tree is reused, exactly as
`materialize()`'s `if not root.exists(): _extract_into(...)` reuses a cached Git checkout instead of
re-cloning. Only on a cache miss does `activate_project`'s fallback fetch the zip bytes from the
object store by the source record's `sha256` (scoped to the project's org), extract into that
path, and run the same zip-slip/zip-bomb/path-confinement checks `bind_upload_config` already
applies — once per unique `sha256` per replica, not once per activation — then bind the result the
same way `bind_upload_config` does today. Only when no object store is configured, or the key is
absent from it, does today's `409` stand.

The exists-check alone is not enough to make this safe under concurrent access: two callers that
both miss the same `sha256` at the same moment (two simultaneous `activate_project` calls, or one
racing `bind_upload_config`'s own extraction once it is keyed by `sha256` too) would otherwise both
extract straight into `state.uploads_dir / sha256`, each seeing the other's partially-written tree.
`materialize()` avoids exactly this race for the Git source's cache by extracting into a sibling
temp directory and renaming into place (`_extract_into` in `bajutsu/config_source.py`): the rename
is atomic, so a losing caller either finds `root` already present before it starts, or has its own
`rename` fail because the winner's already landed, and in that case discards its own copy rather
than treating the failure as an error — safe because both extractions are byte-identical for the
same content-addressed key. Both extraction sites this unit introduces (`bind_upload_config`'s own
extraction and `activate_project`'s fallback) need that same temp-dir-then-rename pattern, not a
direct extract into the keyed path, to get the same guarantee.

Keying the local path by `sha256` is what makes `uploads_dir` an actual cache in front of the
durable object store, the same relationship
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)'s local, content-addressed Git
checkout cache (`~/.cache/bajutsu/gitsrc/...`) already has to its remote origin — a resolved SHA
(there) or a sha256 (here) is what's durable; the checked-out or extracted tree on local disk is
always disposable and reproducible from it. Extracting into a fresh, unkeyed directory on every
materialization — as a first draft of this unit did — would not give `uploads_dir` that property:
every activation of a project bound to a `sha256` this same replica already extracted moments
earlier would still round-trip the object store and re-run validation for content already sitting
on local disk, unlike the Git cache's cache-hit path. A `sha256`-keyed cache hit does not weaken
the check unit 1's *Alternatives considered* argues for (re-extracting from the raw zip on every
materialization rather than trusting a pre-extracted tree): that argument concerns one replica
trusting a tree a *different* replica extracted and validated, not a replica reusing a tree it
validated itself for the exact same content-addressed key — the same trust boundary `materialize()`
already relies on for a cached Git checkout.

**Consequence for `release_upload`.** `ServeState.release_upload()`
(`bajutsu/serve/state.py`) unconditionally `shutil.rmtree`s the bound upload's directory
whenever any new config is bound — the right behavior for today's one-disposable-sandbox-per-bind
design, but it would delete the `sha256`-keyed cache on every switch-away, defeating the reuse this
unit introduces: switching to a different project and back would force a full re-fetch-and-extract
of the same bundle even though nothing changed. `release_upload` needs to stop deleting the
extracted tree; it keeps clearing `state.upload`/`state.cwd` (which bundle is currently bound),
while the on-disk cache's lifetime becomes independent of any single bind — swept, if at all, by
whatever retention policy governs the local cache (see *Retention*, below), the same way nothing
binding or unbinding a config ever sweeps the Git source's `~/.cache/bajutsu/gitsrc/...` checkout
cache.

**Consequence for the startup sweep.** `serve()` (`bajutsu/serve/__init__.py`) currently
`shutil.rmtree`s the entire `uploads_dir` tree unconditionally on every launch, reasoning (per its
own comment) that nothing is bound at startup, so any tempdir left over from a prior process is
dead weight. That reasoning fit a directory of anonymous `tempfile.mkdtemp` names with no meaning
once their bind was gone; it stops fitting once every entry is instead named by its content's
`sha256` (this unit). A directory left over from a prior process is not orphaned garbage — it is
exactly as valid a cache entry as one extracted moments ago, the same trust boundary a Git checkout
cache entry already has across `serve` restarts. Wiping it at every launch would force a
same-replica restart to re-fetch and re-extract a `sha256` it already had on disk, reintroducing on
a smaller scale the very problem (losing local state to a restart) this item exists to close. This
sweep is dropped, matching the Git source's own cache, which nothing sweeps at `serve` startup
either.

This is what lets `activate_project` succeed on a fresh or different replica for a bundle it never
itself received, and lets a replica that already has a `sha256` on disk — whether from an earlier
activation or from before its own last restart — skip the object store entirely on a repeat
activation.

### 3. Graceful degradation with no object store configured

A local `serve` with no `BAJUTSU_SERVER_STORE` (the default, and every non-hosted Tier-A/self-hosted
deployment) sees no functional behavior change: uploads stay exactly as ephemeral as BE-0073 and
BE-0225 ship them today, and `activate_project` keeps refusing an `upload`-kind project with the
same `409` — matching the zero-config precedent already established for optional stores
([BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md)) and settings
([BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)).
Durable, cross-replica resolution is additive and only engages when the operator has already opted
into the object-store seam for artifacts/scenarios/baselines. Unit 2's relocation of
`state.uploads_dir`'s default onto the shared `.../bajutsu/` cache root is the one exception: it is
unconditional, since it is about which writable path an ephemeral sandbox lives under, not about
durability — a zero-config `serve` gets the smaller-allow-list benefit too, with no other change in
behavior.

### Retention

Content addressing gives natural deduplication within an org (re-uploading the same bytes is a
no-op write); actual deletion is left to the bucket's own lifecycle policy, the same retention
mechanism `bajutsu/object_store.py`'s module docstring already documents as governing every
URI-addressed store this seam backs. The local, `sha256`-keyed cache under `state.uploads_dir`
(unit 2) is left ungoverned the same way — nothing sweeps it, matching the precedent the Git
source's own local checkout cache already sets (`~/.cache/bajutsu/gitsrc/...` has no eviction
either). No new retention or garbage-collection code is introduced, locally or in the object
store.

## Alternatives considered

- **Store the zip (or its extracted tree) as a BLOB in Postgres.** Rejected: BE-0015's schema
  already keeps large binary payloads out of the database (artifacts, scenarios, and baselines all
  went to the object store under BE-0204, not to a table), and a multi-megabyte app binary is exactly
  the payload shape Postgres is the wrong tool for. Consistency with the existing storage seam matters
  more than avoiding a second infrastructure dependency the deployment already has.
- **Keep the local upload cache `--runs`-relative, matching BE-0073's shipped `state.uploads_dir`
  default.** Rejected: a hosted `server` deployment commonly runs with a small, explicitly
  allow-listed set of writable paths for security reasons. The Git source already needs one such
  writable root (`HOME`/`XDG_CACHE_HOME`, for `~/.cache/bajutsu/gitsrc/...`); keeping uploads on a
  second, `--runs`-relative root would widen that set for no benefit once uploads are
  content-addressed under the same `.../bajutsu/` cache namespace as Git's checkouts (unit 2). One
  shared cache root an operator provisions once, not two independently-provisioned ones, is the
  smaller surface to allow-list.
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

- [ ] 1 — Persist the raw uploaded zip to the object store, keyed by its sha256 under the project's org prefix, *before* `bind_upload_config` flips the active config, when `BAJUTSU_SERVER_STORE` is configured; on a write failure, remove the extracted local directory and fail the request without ever having bound it, rather than binding first and rolling back.
- [ ] 2 — Move `state.uploads_dir`'s default from a `--runs`-relative directory to a sibling of BE-0063's Git checkout cache under `.../bajutsu/` (`<XDG_CACHE_HOME or ~/.cache>/bajutsu/uploads/`), factoring the `XDG_CACHE_HOME`/`Path.home()` prefix out of `_default_cache_root()` into a shared `_bajutsu_cache_root()` helper both the Git (`gitsrc`) and upload (`uploads`) cache roots build on, rather than reusing `_default_cache_root()` unmodified (which bakes in the `gitsrc` leaf) or duplicating its fallback rule. `activate_project` gains a fetch-and-extract-from-object-store fallback for `kind == "upload"`, tried before the existing `409`. Both this fallback and `bind_upload_config`'s own local extraction resolve to a `sha256`-keyed directory under that root and skip extraction (and re-validation) on a cache hit, mirroring `materialize()`'s `if not root.exists(): _extract_into(...)` — both extract into a sibling temp dir and rename into place first, mirroring `_extract_into`'s atomicity, so two concurrent misses on the same `sha256` never race directly into the keyed path. `release_upload` stops deleting that directory on every switch-away, and `serve()`'s startup sweep (`bajutsu/serve/__init__.py`) stops wiping it on every launch, so the cache actually persists across binds and restarts.
- [ ] 3 — Confirm zero-config behavior (and the existing `409`) is unchanged with no `BAJUTSU_SERVER_STORE` configured.

## References

- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the ephemeral upload path this item makes durable; the sha256 provenance hash this reuses as the content-addressed key.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the content-addressed, local-cache-in-front-of-a-durable-origin shape this item mirrors for uploads.
- [BE-0204 — Server storage: GCS support alongside S3](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md) — the `BAJUTSU_SERVER_STORE` / `ObjectStore` seam this item's persistence and materialization write to and read from.
- [BE-0108 — Restrict config sources to upload and Git when hosted](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) — why upload is one of the two config sources a hosted deployment must support well.
- [BE-0225 — Config project hub in serve](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (Implemented) — the consumer whose `upload`-kind project registration already hits the gap this item closes (`activate_project`'s `409`).
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the stateless, autoscaled-replica topology this item's cross-replica resolution targets.
- [BE-0184 — Persist serve AI provider settings across restarts](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md) — the sibling, separately tracked gap for settings (not scenario content), explicitly out of scope here.
- `bajutsu/object_store.py` (`ObjectStore`, `object_store_from_uri`, `upload_tree`), `bajutsu/serve/operations/upload.py` (`bind_upload_config`), `bajutsu/serve/operations/projects.py` (`activate_project`, `_KIND_TO_SOURCE`), `bajutsu/serve/project_registry.py` (`ProjectRegistry`), `bajutsu/serve/server/object_store.py` (`org_prefix`, `artifact_prefix`/`scenario_prefix`/`baseline_prefix`), `bajutsu/serve/uploads.py` (`extract_bundle`), `bajutsu/serve/state.py` (`uploads_dir`, `bind_upload`/`release_upload`), `bajutsu/config_source.py` (`materialize`, `_default_cache_root`) — the Git source's content-addressed local cache this unit's `uploads_dir` cache mirrors, `bajutsu/serve/__init__.py` (`serve`, `_build_state`, `_build_server_state`) — where `uploads_dir`'s default is computed and where the startup sweep this item drops lives today.

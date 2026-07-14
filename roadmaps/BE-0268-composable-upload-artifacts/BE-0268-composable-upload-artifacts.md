**English** · [日本語](BE-0268-composable-upload-artifacts-ja.md)

# BE-0268 — Upload config, scenarios, and app binary as independent content-addressed artifacts composed per run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0268](BE-0268-composable-upload-artifacts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0268") |
| Implementing PR | [#1076](https://github.com/bajutsu-e2e/bajutsu/pull/1076), [#1097](https://github.com/bajutsu-e2e/bajutsu/pull/1097) |
| Topic | Configuration sourcing |
| Related | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) |
<!-- /BE-METADATA -->

## Introduction

Today a hosted `bajutsu serve` acquires a runnable suite as **one combined `.zip`** — a
`bajutsu.config.yaml`, its scenario tree, and the built app binary, uploaded together through a
single `POST /api/upload` and extracted as one unit
([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)). That one
artifact couples three things whose change cadences are wildly different: the **binary** is large
(tens to hundreds of MB) and changes on every build; the **scenario tree** is small text and
changes on every authoring edit; the **config** is small text and changes almost never.

This proposal decomposes that single upload into **three independently uploadable, content-addressed
artifacts** — `config`, `scenarios`, and `binary` — and makes a run **compose** a chosen triple into
exactly the coherent tree the deterministic runner already consumes. Each artifact is stored by its
sha256 in the object store [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)
already added, so re-uploading an unchanged piece is a no-op (upload only what changed) and any
`(config, scenarios, binary)` combination can be assembled without a fresh upload. It is still purely
a way to **acquire** a config-and-scenarios-and-binary tree: the scenario schema, the runner, the
drivers, and the deterministic gate are untouched, and no LLM is added anywhere.

It is a direct extension of three shipped items — it decomposes
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)'s combined bundle,
reuses [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)'s
content-addressed object store as the per-artifact backing, and widens
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s `upload`-kind config-source
record from "one zip" to "a composed triple".

## Motivation

The combined bundle is the right *floor* — the simplest thing that runs — but it forces two costs
that grow with team size:

1. **Every change re-ships the whole bundle, binary included.** Fixing a single line in one scenario
   YAML today means re-zipping and re-uploading the entire suite, including the large prebuilt
   binary that did not change. On a hosted serve over a real network, the binary dominates that
   upload; paying it again for a text-only edit is pure waste. The three pieces have independent
   lifecycles, so they should be independently uploadable — the client should send only what
   actually changed, and an unchanged binary (same sha256) should not travel the wire at all.

2. **You cannot mix and match without re-uploading.** A common testing need is a **combination
   matrix**: run the same regression scenarios against two binary builds (A/B, or last-known-good
   vs. a release candidate), or run several scenario sets against one binary. With a single opaque
   zip, every cell of that matrix is a separate full upload, even though most cells share a binary or
   a scenario set. If artifacts are content-addressed and composed at run time, a new cell is just a
   new *triple* over artifacts already stored — no upload at all.

There is a third, organizational win these two enable: the three uploads map cleanly onto **who owns
what**. A CI job pushes the binary it just built; a test author pushes the scenario tree from their
branch; an operator pins the config. Decoupling the uploads lets each side push on its own cadence,
through its own credential, without coordinating a single monolithic re-zip.

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)'s own
*Alternatives considered* rejected "upload only the config YAML, not the tree", correctly, because
`scenarios` / `appPath` are **relative paths** and a config with neither its tree nor its binary
cannot run. This proposal does not reopen that rejection — it *resolves* it: the three parts are
still all present at run time, they simply **arrive and are stored separately and are composed back
into one tree** before the run. The relative-path invariant BE-0073 protects is preserved by the
composition step (see *Detailed design*), not weakened.

This stays within the prime directives. Composition is deterministic plumbing — materialize three
content-addressed artifacts into a confined tree and resolve the config's relative paths against it;
pass/fail is still computed only by machine assertions, with no LLM anywhere on the `run`/CI path
(directive 1). A composition that cannot form a coherent tree (a config whose `appPath` no supplied
binary fills) **fails deterministically rather than guessing** (directive 2). And the mechanism is
app-agnostic: the config is the single source of truth for where each artifact lands, so nothing in
the compose step branches per app (directive 3).

## Detailed design

The work is MECE across five units: the per-artifact model, the composition step, the upload API,
the project binding, and the UI.

### 1. Three content-addressed artifact kinds

An **artifact** is one of three kinds, each stored by the sha256 of its bytes in the object store
[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)
introduced (`bajutsu/object_store.py`), under the same per-org prefix scheme every sibling store
already uses (`org_prefix` / `upload_prefix` in `bajutsu/serve/server/object_store.py`), extended
with a per-kind sub-prefix so the three never collide:

- **`config`** — a single `bajutsu.config.yaml` (its bytes; small).
- **`scenarios`** — the scenario subtree, carried as a `.zip` of the YAML tree (`scenarios/…`,
  plus `baselines/` / `setup/` when the config references them; small text).
- **`binary`** — the built app artifact (`.app.zip` / `.ipa`; large), exactly what BE-0073's bundle
  carried, now stored on its own.

Content-addressing gives the "upload only what changed" and "no-op on an unchanged binary"
properties for free: the store already dedupes by key, and `ObjectStore.exists(key)` lets a client
(or the UI) check "is this sha already stored?" and skip the upload when it is. This is the same
`exists`/`put_bytes`/`presigned_put_url` seam BE-0243 and the worker-upload path
(`bajutsu/serve/operations/worker_uploads.py`, `presign.py`) already rely on.

### 2. Composition — three artifacts materialize into one tree (the BE-0073 invariant, kept)

The crux BE-0073 and [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) both
protect is unchanged: the runner needs a **single self-contained tree** the config's relative
`scenarios` / `appPath` / `baselines` / `setup` resolve against
(`bajutsu/serve/operations/upload.py` `_validate_bundle_config` + `Effective.rebased`). So a
**composition** is a triple of artifact shas `(config_sha, scenarios_sha, binary_sha)`, and a new
`materialize_composition` step assembles them into a fresh, serve-owned, confined directory — the
sibling of BE-0073's `materialize_bundle`:

1. Write the `config` artifact's bytes as `bajutsu.config.yaml` at the tree root. **The config is
   the single source of truth for layout**: it names, via its own relative `scenarios` and
   `appPath` fields, exactly where the other two artifacts must land.
2. Extract the `scenarios` zip so that the config's `scenarios` path resolves (e.g. `./scenarios`),
   with the same zip-slip / zip-bomb bounds BE-0073 enforces on extraction.
3. Place the `binary` artifact at the config's `appPath` (a `.app` dir from a `.app.zip`, or an
   `.ipa` left as-is), the one field that already answers "where is the binary".

Then it runs BE-0073's existing `_validate_bundle_config` against the assembled root: every target's
path fields must confine to the tree, and — the new coherence check this design adds — the config's
`appPath` and `scenarios` must actually be **filled by the supplied artifacts**. A triple that names
a config whose `appPath` no binary artifact fills, or whose `scenarios` no scenarios artifact fills,
is rejected at bind time with a specific error, never run against a half-materialized tree
(directive 2). Everything downstream — `resolve()`, the runner, the drivers, the assertion
evaluator, the report — is byte-for-byte the same code path a BE-0073 bundle or a local `--config`
run takes; only the *assembly* is new.

**Provenance.** The run's `manifest.json` records the **triple of shas** (and each artifact's
display filename), extending BE-0073's single-zip-sha provenance. "What did this run execute?" is
answerable to the exact bytes of each of the three parts — a stronger guarantee than the combined
zip gave, and the audit anchor for the combination matrix.

### 3. Upload API (per-kind, additive; combined zip kept as sugar)

Three authenticated upload paths, each streaming a raw body to a temp file with bounded memory
(BE-0073's pattern) and returning the stored **sha256**:

- `POST /api/artifacts/config` — body is the YAML bytes.
- `POST /api/artifacts/scenarios` — body is the scenario-tree zip.
- `POST /api/artifacts/binary` — body is the `.app.zip` / `.ipa`.

Each is an admin-or-editor action behind [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
token auth, screened by the same [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
hosted-source allowlist that already governs upload. A companion `HEAD`/exists check (or a
`presigned_put_url` as the worker-upload path does) lets a client skip re-sending bytes already
stored — the mechanism behind "don't re-upload the unchanged binary". The existing combined
`POST /api/upload` **stays**, reinterpreted as sugar: on the server it decomposes the zip into the
same three content-addressed artifacts and forms the composition, so the one-drop flow keeps working
and every upload — combined or split — lands on one internal representation.

### 4. Project binding (widen BE-0225's `upload` source record)

[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) binds a project to a
discriminated config-source record (`kind` + locator); its `upload` kind today points at a single
bundle sha (`activate_uploaded_project` fetch-and-extracts that one zip,
`bajutsu/serve/operations/upload.py`). This item widens the `upload` locator from one sha to the
**triple** `(config_sha, scenarios_sha, binary_sha)`. `activate`/`run` then calls
`materialize_composition` instead of `materialize_bundle`. Backward compatibility is a shape check:
a legacy single-sha `upload` record still resolves through `materialize_bundle`; a triple resolves
through the new step. Updating one leg of a bound project (a new binary sha, same scenarios/config)
is a record edit, not a re-upload — this is where the combination matrix and the incremental-update
wins surface at the project level.

### 5. UI

The **Open config → Upload** surface gains **three drop zones** — Config, Scenarios, Binary — each
showing the currently-selected artifact (filename + short sha) with a **reuse last** affordance, so
a user re-drops only what changed and the client skips the upload for any zone whose sha is already
stored. A small **composition picker** lets a user assemble a triple from artifacts already uploaded
(pick config vX, scenarios vY, binary vZ) and run it — the surface that makes the combination matrix
a few clicks rather than N full uploads. Runs still flow through the same job machinery
(`bajutsu/serve/jobs.py`) and show in History; [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
export closes the round trip unchanged.

### Determinism, the gate, and app-agnosticism

- **No LLM, no effect on the verdict.** Composition is acquisition + assembly before the
  deterministic `run`; pass/fail stays machine-only. Directives 1 and 2 hold by construction.
- **Linux-testable.** Per-artifact storage, the exists/dedup check, zip-slip / zip-bomb bounds on
  the scenarios and binary artifacts, the composition assembly, and the coherence validation are all
  pure packaging/plumbing and unit-test on the existing Linux gate against fixture artifacts — no
  Simulator. Only the actual app *install + run* needs a Mac, as for any iOS run.
- **App-agnostic.** The config is the sole layout authority; the compose step reads `appPath` /
  `scenarios` from it and never branches per app. Per-app differences stay in the composed config's
  `targets.<name>`.

### Backend scope (iOS first; web noted)

The headline case is **iOS**: the `binary` artifact is a `.app.zip` / `.ipa` placed at the config's
`appPath` and installed into the Simulator, exactly as BE-0073. The web (Playwright) backend has no
"app binary"; its analogue is a bundled **static site** served via
[BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md) — the same follow-up
BE-0073 already deferred. The three-artifact split is backend-neutral in shape (it is still "a config
tree, delivered in parts"), so the mechanism does not hard-code iOS; a web variant (a `site` artifact
in place of `binary`) is a later slice.

### Out of scope

- **Building the app from source.** As in BE-0073 and [DESIGN §1](../../DESIGN.md), Bajutsu receives
  a prebuilt artifact; the `binary` artifact carries the build product, it does not build it.
- **A versioned library / retention policy for artifacts.** Content-addressed storage naturally
  dedupes and enables reuse, but *cataloguing* named artifact versions and their retention is the
  Git source's ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) and
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s territory; this item
  stores artifacts and composes triples, it does not add a browsable version registry.
- **Multi-tenant execution isolation.** Per-tenant Simulators and egress controls remain
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) /
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) territory.

## Alternatives considered

- **Keep the single combined zip (status quo, [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)).**
  Rejected as the *only* option: it couples three artifacts of very different cadence into one
  upload, forces re-shipping the large binary for a one-line scenario edit, and makes every cell of a
  binary×scenario combination matrix a separate full upload. The combined zip is kept — as sugar
  that decomposes into the three artifacts — but is no longer the only shape.
- **Client-side diff/patch of the bundle zip.** Upload a delta against the last bundle. Rejected:
  binary diffing is brittle and format-specific, it still models one opaque artifact rather than
  three with independent lifecycles, and it gives no clean path to the combination matrix (a delta is
  relative to one base, not a free choice of triple). Content-addressed whole artifacts are simpler
  and dedupe for free.
- **Upload only the config, not the tree** (BE-0073's original rejection). Not reopened: this design
  keeps all three parts present at run time and composes them, so BE-0073's relative-path objection
  is satisfied rather than dismissed.
- **A bespoke multi-part manifest format describing the layout.** Rejected for the same reason
  BE-0073 rejected one: the config *is* the layout description — its `scenarios` / `appPath` already
  say where each artifact lands. Reusing the config as the compose authority, over
  [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)'s
  object store, invents no new format.
- **Fold into BE-0073 or BE-0243.** Rejected: BE-0073 is the combined-bundle acquisition and BE-0243
  is that bundle's durable storage; both are shipped and stable. Decomposition + run-time composition
  is a distinct feature with its own API, project-record shape, and UI, and is cleanly `Related`
  rather than a retro-edit of a closed item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — Per-artifact model: three content-addressed kinds (`config` / `scenarios` / `binary`) over BE-0243's object store, with per-kind prefixes and an `exists`-based dedup check.
- [x] 2 — Composition: `materialize_composition` assembling a triple into a confined tree with the config as layout authority, reusing BE-0073's zip-slip/zip-bomb bounds and `validate_bundle_config`, plus the coherence check (`appPath`/`scenarios` filled by supplied artifacts); triple-sha provenance in `manifest.json`.
- [x] 3 (partial) — Upload API: `POST /api/artifacts/{config,scenarios,binary}` returning the sha, plus a `GET /api/artifacts/exists` dedup check, shipped. The combined `POST /api/upload` reinterpreted as decompose-to-three sugar is **not** in this PR — it is a pure internal-representation change to already-shipped, well-tested code with no new user-facing capability, deferred to a follow-up so this slice stays small and low-risk.
- [x] 4 — Project binding: widen BE-0225's `upload` source record from one sha to the triple (`{"artifacts": {"config", "scenarios", "binary"}}`, discriminated from the legacy single-`sha256` shape), with legacy single-sha records still resolving unchanged; per-leg fetch-or-cache.
- [x] 5 — UI: three drop zones (config / scenarios / binary) with a reuse/skip indicator and client-side content-addressed dedup skip (hash in-browser → `GET /api/artifacts/exists` → upload only on a miss), and a **Compose & load** composition picker backed by a new `POST /api/compose` (`bind_composition`) that assembles a stored triple into the active config — the combination matrix as a few clicks, no re-upload of unchanged parts.

### Log

- 2026-07-14 — Units 1, 2, 4, and the additive half of 3 implemented (backend core; unit 5's UI and
  the rest of unit 3 are deferred follow-ups, see the checklist above). Notable deviations from this
  document's literal *Detailed design*: the `scenarios` artifact is unzipped directly at the
  composition root (matching how a legacy combined bundle already carries its `scenarios`/
  `baselines`/`setup` tree alongside the config), not extracted per-field from the config's
  `scenarios` path — simpler, and it reuses `extract_bundle` unmodified. A composed bind's
  `manifest.json` provenance reports `compositionId` + one `<kind>Sha` entry per supplied artifact
  instead of a single top-level `sha256` — overloading that field with a synthetic composite value
  would misleadingly imply a hash verifiable against one artifact's bytes, which it isn't. Two
  review-found issues were fixed before merge: the `scenarios` artifact is now extracted *before*
  the config is written (not after), since `extract_bundle` only guards zip-slip/zip-bombs, not a
  top-level entry that happens to be named `bajutsu.config.yaml` — writing the trusted config bytes
  last means such an entry is always overwritten by the real config, never the reverse; and
  `GET /api/artifacts/exists`'s admin gate was dead code (a GET never reaches the generic
  `_ADMIN_PATHS` check, which only runs past the `POST`-only guard) — it now gets its own explicit
  early case in `required_role`, like `GET /api/config/content` already does.
- 2026-07-15 — Unit 5 (UI) implemented, plus the `POST /api/compose` (`bind_composition`) endpoint
  the composition picker needs. The compose UI adds a fourth "Open config" source — three
  content-addressed drop zones with an in-browser sha256 + `GET /api/artifacts/exists` dedup skip and
  a **Compose & load** picker — and `bind_composition` resolves a stored triple (local cache hit, or
  object-store fetch on a miss) and binds it as the active config, reusing the shipped
  `materialize_composition`. Its fetch/compose/bind core is factored out of `_activate_composed_project`
  (`_compose_and_bind` + `_collect_optional_shas`) so the reactivation and picker paths share one
  implementation; `_fetch_artifact` now returns a clean `404` (not an `assert`) on a local-cache miss
  with no object store, so the picker works on a plain `make serve`. The remaining half of unit 3 —
  reinterpreting the combined `POST /api/upload` as decompose-to-three sugar — stays a separate
  follow-up (its server-side re-zip is nondeterministic, undercutting the dedup it would exist to
  enable, and it touches the primary shipped upload path).

## References

- [CLAUDE.md](../../CLAUDE.md), [DESIGN §1](../../DESIGN.md) (Bajutsu receives a prebuilt app, does
  not build it), [DESIGN §2](../../DESIGN.md) (AI never judges; determinism first; clean environment
  per test).
- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
  — the combined bundle this decomposes; the relative-path invariant and the extraction/validation
  guards reused.
- [BE-0243 — Persist uploaded zip config bundles to object storage](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)
  — the content-addressed `ObjectStore` (`bajutsu/object_store.py`) each artifact is stored in.
- [BE-0225 — Config project hub in serve](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)
  — the `upload`-kind config-source record widened from one zip sha to a triple.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  — the sibling "materialize a tree, resolve config against its root" seam.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md),
  [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
  — the token auth + source allowlist every upload path sits behind.
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
  — the export half that closes the round trip.
- `bajutsu/object_store.py` (the content-addressed store), `bajutsu/serve/operations/upload.py`
  (`bind_upload_config` / `materialize_bundle` / `_validate_bundle_config` / `activate_uploaded_project`,
  the composition step joins these), `bajutsu/serve/uploads.py` (`materialize_bundle` /
  `find_bundle_config`), `bajutsu/serve/server/object_store.py` (`org_prefix` / `upload_prefix`),
  `bajutsu/serve/operations/worker_uploads.py` + `presign.py` (the `presigned_put_url` upload seam),
  `bajutsu/config.py` (`AppConfig.appPath` / `scenarios`) — the surfaces this touches.
- [docs/configuration.md](../../docs/configuration.md), [docs/cli.md](../../docs/cli.md#serve),
  [docs/architecture.md](../../docs/architecture.md).

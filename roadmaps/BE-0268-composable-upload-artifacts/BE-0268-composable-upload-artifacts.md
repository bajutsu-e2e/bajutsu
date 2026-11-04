**English** · [日本語](BE-0268-composable-upload-artifacts-ja.md)

# BE-0268 — Upload config, scenarios, and app binary as independent content-addressed artifacts composed per run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0268](BE-0268-composable-upload-artifacts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0268") |
| Implementing PR | [#1076](https://github.com/bajutsu-e2e/bajutsu/pull/1076), [#1097](https://github.com/bajutsu-e2e/bajutsu/pull/1097), [#1105](https://github.com/bajutsu-e2e/bajutsu/pull/1105) |
| Topic | Configuration sourcing |
| Related | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) |
<!-- /BE-METADATA -->

## Introduction

A hosted `bajutsu serve` acquires a runnable suite as one combined `.zip`: a `bajutsu.config.yaml`,
its scenario tree, and the built app binary, uploaded together through a single `POST /api/upload`
and extracted as one unit ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)).
That one artifact couples three things that change at very different rates. The binary is large (tens
to hundreds of MB) and changes on every build. The scenario tree is small text and changes on every
authoring edit. The config is small text and almost never changes.

This proposal decomposes the single upload into three independently uploadable, content-addressed
artifacts (`config`, `scenarios`, and `binary`). A run then composes a chosen triple into exactly the
coherent tree that the deterministic runner already consumes. Each artifact is stored by its sha256 in
the object store that [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)
already added. Re-uploading an unchanged piece is therefore a no-op (a client uploads only what
changed), and any `(config, scenarios, binary)` combination can be assembled without a fresh upload.
The change only affects how a config-scenarios-binary tree is acquired. The scenario schema, the
runner, the drivers, and the deterministic gate stay untouched, and no LLM is added anywhere.

The item extends three shipped items. It decomposes the combined bundle of
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), reuses the
content-addressed object store of
[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) as the
backing for each artifact, and widens the `upload`-kind config-source record of
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) from "one zip" to "a composed
triple".

## Motivation

The combined bundle is the right floor, because it is the simplest thing that runs. It also forces
two costs that grow with team size:

1. **Every change re-ships the whole bundle, binary included.** Fixing one line in one scenario YAML
   means re-zipping and re-uploading the entire suite, including the large prebuilt binary that did
   not change. On a hosted serve over a real network, the binary dominates the upload, so paying for
   it again after a text-only edit is pure waste. The three pieces have independent lifecycles and
   should be independently uploadable. The client should send only what changed, and an unchanged
   binary (same sha256) should not travel the wire at all.

2. **You cannot mix and match without re-uploading.** A common testing need is a combination matrix:
   run the same regression scenarios against two binary builds (A/B, or last-known-good against a
   release candidate), or run several scenario sets against one binary. With a single opaque zip,
   every cell of the matrix is a separate full upload, although most cells share a binary or a scenario
   set. If artifacts are content-addressed and composed at run time, a new cell is a new triple over
   artifacts that are already stored, and needs no upload at all.

These two gains enable a third, organizational one. The three uploads map onto who owns what. A CI job
pushes the binary it just built, a test author pushes the scenario tree from their branch, and an
operator pins the config. Decoupling the uploads lets each side push on its own cadence, through its
own credential, without coordinating one monolithic re-zip.

The *Alternatives considered* section of [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
rightly rejected "upload only the config YAML, not the tree": `scenarios` and `appPath` are relative
paths, and a config without its tree and binary cannot run. This proposal does not reopen that
rejection. It resolves it. All three parts are still present at run time. They arrive and are stored
separately, and the run composes them back into one tree. The composition step preserves the
relative-path invariant that BE-0073 protects (see *Detailed design*).

The change stays within the prime directives:

- **Directive 1.** Composition is deterministic plumbing. It materializes three content-addressed
  artifacts into a confined tree and resolves the config's relative paths against that tree.
  Pass/fail is still computed only by machine assertions, with no LLM anywhere on the `run`/CI path.
- **Directive 2.** A composition that cannot form a coherent tree, such as a config whose `appPath` no
  supplied binary fills, fails deterministically instead of guessing.
- **Directive 3.** The mechanism is app-agnostic. The config is the single source of truth for where
  each artifact lands, so the compose step never branches per app.

## Detailed design

Five units make up the work, and they do not overlap: the per-artifact model, the composition step, the upload API, the project binding, and the UI.

### 1. Three content-addressed artifact kinds

An **artifact** is one of three kinds. Each kind is stored by the sha256 of its bytes in the object store that [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) introduced (`bajutsu/object_store.py`). The key uses the per-org prefix scheme that every sibling store already shares (`org_prefix` / `upload_prefix` in `bajutsu/serve/server/object_store.py`), extended with a per-kind sub-prefix so the three kinds never collide.

- **`config`**: a single `bajutsu.config.yaml` (its bytes; small).
- **`scenarios`**: the scenario subtree, carried as a `.zip` of the YAML tree (`scenarios/…`, plus `baselines/` and `setup/` when the config references them; small text).
- **`binary`**: the built app artifact (`.app.zip` or `.ipa`; large). The BE-0073 bundle carried exactly this artifact, which is now stored on its own.

Content-addressing yields "upload only what changed" and "no-op on an unchanged binary" at no extra cost. The store already dedupes by key. `ObjectStore.exists(key)` lets a client (or the UI) ask whether a sha is already stored, and skip the upload when it is. BE-0243 and the worker-upload path (`bajutsu/serve/operations/worker_uploads.py`, `presign.py`) already rely on the same `exists` / `put_bytes` / `presigned_put_url` seam.

### 2. Composition of three artifacts into one tree

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) and [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) both protect one invariant, and this item keeps it. The runner needs a **single self-contained tree**, against which the config's relative `scenarios`, `appPath`, `baselines`, and `setup` paths resolve (`_validate_bundle_config` and `Effective.rebased` in `bajutsu/serve/operations/upload.py`).

A **composition** is therefore a triple of artifact shas, `(config_sha, scenarios_sha, binary_sha)`. A new `materialize_composition` step assembles the triple into a fresh, serve-owned, confined directory. It is the sibling of BE-0073's `materialize_bundle`, and it works in three steps:

1. Write the `config` artifact's bytes as `bajutsu.config.yaml` at the tree root. The config is the single source of truth for layout. Its own relative `scenarios` and `appPath` fields name where the other two artifacts must land.
2. Extract the `scenarios` zip so that the config's `scenarios` path resolves (for example `./scenarios`). Extraction uses the same zip-slip and zip-bomb bounds that BE-0073 enforces.
3. Place the `binary` artifact at the config's `appPath`: a `.app` directory from a `.app.zip`, or an `.ipa` left as is. `appPath` is the one field that already answers "where is the binary".

The step then runs BE-0073's existing `_validate_bundle_config` against the assembled root. Every target's path fields must confine to the tree. This design adds one coherence check: the config's `appPath` and `scenarios` must be **filled by the supplied artifacts**. A triple is rejected at bind time with a specific error when its config names an `appPath` that no binary artifact fills, or a `scenarios` path that no scenarios artifact fills. The run never starts against a half-materialized tree (directive 2).

Everything downstream of assembly is unchanged: `resolve()`, the runner, the drivers, the assertion evaluator, and the report. A composed run takes byte-for-byte the same code path as a BE-0073 bundle or a local `--config` run.

**Provenance.** The run's `manifest.json` records the triple of shas and each artifact's display filename. This extends BE-0073's single-zip-sha provenance. The question "what did this run execute?" now has an answer down to the exact bytes of each of the three parts. That guarantee is stronger than the combined zip gave, and it anchors audits of the combination matrix.

### 3. Upload API, per kind and additive, with the combined zip kept as sugar

Three authenticated upload paths stream a raw body to a temp file with bounded memory (the BE-0073 pattern) and return the stored **sha256**.

- `POST /api/artifacts/config`: the body is the YAML bytes.
- `POST /api/artifacts/scenarios`: the body is the scenario-tree zip.
- `POST /api/artifacts/binary`: the body is the `.app.zip` or `.ipa`.

Each path is an admin-or-editor action behind the token auth of [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md). The hosted-source allowlist of [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), which already governs upload, screens each path too.

A companion `HEAD`/exists check, or a `presigned_put_url` as the worker-upload path uses, lets a client skip bytes that are already stored. This is the mechanism behind "do not re-upload the unchanged binary".

The existing combined `POST /api/upload` stays, reinterpreted as sugar. On the server, it decomposes the zip into the same three content-addressed artifacts and forms the composition. The one-drop flow keeps working, and every upload, combined or split, lands on one internal representation.

### 4. Project binding that widens BE-0225's `upload` source record

[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) binds a project to a discriminated config-source record (`kind` plus a locator). Its `upload` kind points at a single bundle sha today: `activate_uploaded_project` fetches and extracts that one zip (`bajutsu/serve/operations/upload.py`).

This item widens the `upload` locator from one sha to the triple `(config_sha, scenarios_sha, binary_sha)`. `activate` and `run` then call `materialize_composition` instead of `materialize_bundle`. Backward compatibility rests on a shape check. A legacy single-sha `upload` record still resolves through `materialize_bundle`, and a triple resolves through the new step.

Updating one leg of a bound project (a new binary sha with the same scenarios and config) is a record edit, not a re-upload. The combination matrix and the incremental-update gains surface here, at the project level.

### 5. UI

The **Open config → Upload** surface gains three drop zones: Config, Scenarios, and Binary. Each zone shows the currently selected artifact (filename and short sha) with a **reuse last** control. A user re-drops only what changed, and the client skips the upload for any zone whose sha is already stored.

A small **composition picker** lets a user assemble a triple from artifacts that are already uploaded (pick config vX, scenarios vY, binary vZ) and run it. The combination matrix becomes a few clicks instead of N full uploads.

Runs still flow through the same job machinery (`bajutsu/serve/jobs.py`) and show in History. The export of [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) closes the round trip unchanged.

### Determinism, the gate, and app-agnosticism

- **No LLM, no effect on the verdict.** Composition is acquisition and assembly before the deterministic `run`. Pass/fail stays machine-only, so directives 1 and 2 hold by construction.
- **Linux-testable.** Per-artifact storage, the exists/dedup check, the zip-slip and zip-bomb bounds on the scenarios and binary artifacts, the composition assembly, and the coherence validation are pure packaging and plumbing. They unit-test on the existing Linux gate against fixture artifacts, with no Simulator. Only the app install and run needs a Mac, as for any iOS run.
- **App-agnostic.** The config is the sole layout authority. The compose step reads `appPath` and `scenarios` from it and never branches per app. Per-app differences stay in the composed config's `targets.<name>`.

### Backend scope

The headline case is **iOS**. The `binary` artifact is a `.app.zip` or `.ipa` that the run places at the config's `appPath` and installs into the Simulator, exactly as in BE-0073.

The web (Playwright) backend has no app binary. Its analogue is a bundled static site served through [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md), the same follow-up that BE-0073 already deferred. The three-artifact split is backend-neutral in shape, because it still means "a config tree, delivered in parts". The mechanism therefore does not hard-code iOS. A web variant, with a `site` artifact in place of `binary`, is a later slice.

### Out of scope

- **Building the app from source.** As in BE-0073 and [DESIGN §1](../../DESIGN.md), Bajutsu receives a prebuilt artifact. The `binary` artifact carries the build product and does not build it.
- **A versioned library or retention policy for artifacts.** Content-addressed storage dedupes and enables reuse. Cataloguing named artifact versions and their retention belongs to the Git source ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) and to [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md). This item stores artifacts and composes triples, and adds no browsable version registry.
- **Multi-tenant execution isolation.** Per-tenant Simulators and egress controls remain the territory of [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

## Alternatives considered

- **Keep the single combined zip (status quo, [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)).** Rejected as the only option. The zip couples three artifacts of very different cadence into one upload, forces re-shipping the large binary for a one-line scenario edit, and makes every cell of a binary-by-scenario combination matrix a separate full upload. The combined zip is kept as sugar that decomposes into the three artifacts, and is no longer the only shape.
- **Client-side diff or patch of the bundle zip.** The client would upload a delta against the last bundle. Rejected for three reasons. Binary diffing is brittle and format-specific. The delta still models one opaque artifact, not three with independent lifecycles. A delta is relative to one base, so it gives no clean path to a free choice of triple. Content-addressed whole artifacts are simpler and dedupe at no extra cost.
- **Upload only the config, not the tree** (BE-0073's original rejection). Not reopened. This design keeps all three parts present at run time and composes them, so BE-0073's relative-path objection is satisfied instead of dismissed.
- **A bespoke multi-part manifest format describing the layout.** Rejected for the reason BE-0073 gave: the config is the layout description. Its `scenarios` and `appPath` fields already say where each artifact lands. Reusing the config as the compose authority, over the object store of [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), invents no new format.
- **Fold the change into BE-0073 or BE-0243.** Rejected. BE-0073 is the combined-bundle acquisition and BE-0243 is that bundle's durable storage, and both are shipped and stable. Decomposition with run-time composition is a distinct feature with its own API, project-record shape, and UI. It links to both items as `Related`, and does not retro-edit a closed item.

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
- 2026-07-15 — Widened what the compose drop zones accept, per feedback. The `scenarios` artifact now
  takes a single `.yaml` file in addition to a `.zip`: `materialize_composition` tells the two apart
  by content (`zipfile.is_zipfile`) and writes a single file into the config's `scenarios` directory,
  its extension normalized to `.yaml` (the runner's listing globs `*.yaml`); the dropped name flows
  through `bind_composition` → `_compose_and_bind` as `scenariosName`, which also salts the
  composition cache key so the same bytes under two names compose to two distinct, correctly-named
  trees. The `binary` drop zone's accept list now includes `.app` and Android `.apk` (the byte-level
  handling already covered both — a zipped `.app` extracts, a raw `.ipa`/`.apk` is written as is,
  chosen by the config's `appPath` suffix — so this is the UI accept + hints catching up).

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

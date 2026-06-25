**English** · [日本語](BE-0073-serve-zip-bundle-upload-ja.md)

# BE-0073 — Upload a config + scenarios + app-binary bundle as a zip and run it from the web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0073](BE-0073-serve-zip-bundle-upload.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

A one-step way to take a self-contained test bundle — a `bajutsu.config.yaml`, its scenario tree,
and the **built app binary** (`.app` / `.app.zip` / `.ipa`) — packaged as a single `.zip`, **upload
it through the `bajutsu serve` web UI**, and run it, all from a browser with no file-system access to
the serve host. serve extracts the zip into a confined, ephemeral directory, binds the contained
config the same way it binds a locally-browsed one, installs the binary into the run's Simulator, and
drives the existing deterministic `run` path against the materialized tree. This is purely a new way
to **acquire** a config-and-scenarios-and-binary bundle: the schema, the runner, the drivers, and the
deterministic gate are untouched, and no LLM is added anywhere.

It is the natural counterpart to two existing items. It is the **import** mirror of
[BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) (which **exports** a
finished run as a zip); and it is the **push** sibling of
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) (which **pulls** a config and
its scenario tree from a Git repository) — both answer "where does a hosted serve get the config and
scenarios it runs?", one over Git, one over an upload. It sits on the serve hardening already shipped
in [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
(token auth + path confinement), without which uploading and running an arbitrary binary would be
unsafe to expose.

## Motivation

A team's config and scenarios live in their repository, and `bajutsu run` consumes a *local* tree
plus a *built* app artifact ([DESIGN §1](../../../DESIGN.md): "Bajutsu does not build the app — it
receives an existing `xcodebuild` product"). For a local Mac this is fine; for a **hosted or remote
serve** it leaves a gap that neither hand-placement nor a Git source fully closes:

1. **A browser user of a hosted serve has no file-system access to the host.** When serve runs on a
   remote worker or a shared Mac
   ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
   [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)), the config, scenarios,
   and `runs/` all live on *that* machine. Today's UI config picker is a **file browser confined to
   `--root`** (`bajutsu/serve/operations.py` `_confined_config_path`), so it can only choose from
   what an operator has already hand-placed on the host. A browser user cannot bring their own suite.
   This is the exact mirror of [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)'s
   motivation #3 (no file-system access to *retrieve* a run) — here the missing direction is **putting
   a suite in**.

2. **A Git source cannot carry the built binary.** [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)
   materializes a repo subtree at a ref, which is ideal for the *text* (config + YAML scenarios) but
   not for the **compiled app**: teams do not commit `.app` / `.ipa` products to Git, and BE-0063's
   own design leans on the config's `build:` command to (re)produce the binary on the host — which
   needs a full toolchain on that host. A zip is the one transport that bundles the **already-built**
   artifact together with the config and scenarios, which is precisely what
   [DESIGN §1](../../../DESIGN.md) says Bajutsu consumes. The two acquisition paths are
   complementary: Git for the versioned text, an upload for the prebuilt binary.

3. **Hand-placing files on the host is the only path today.** The self-hosting Tier-A guide
   ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) has the operator copy
   the team's config, scenarios, and binary onto the Mac and keep them in sync by hand. An upload
   turns "ask the operator to scp a build over and edit `--root`" into "drag a zip onto the page and
   press run".

No archive *import* exists in the codebase today — [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
proposes the **export** half (stdlib `zipfile`, one archiver); this proposes the **import** half on
the same stdlib foundation. Together they make a run bundle a portable unit in both directions.

## Detailed design

### The bundle is just a tree to materialize (no new layout to invent)

The crux of [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) applies unchanged:
**the config alone is not enough**, because `scenarios` / `baselines` / `setup` / `appPath` / `build`
are paths relative to the run's working directory (`bajutsu/config.py` `AppConfig`). So an uploaded
zip is treated exactly as BE-0063 treats a Git checkout — **a self-contained subtree the config lives
in** — and the config's relative entries resolve **against the extraction root**, reusing the same
"path base is an explicit value, not the process working directory" mechanism BE-0063 introduces.

A minimal bundle, therefore, is whatever a working local checkout already is:

```
my-suite.zip
├── bajutsu.config.yaml         # appPath: ./build/Sample.app   scenarios: ./scenarios
├── scenarios/
│   └── sample/…                # the YAML tree
└── build/
    └── Sample.app/             # the prebuilt binary appPath points at (a .app dir, .app.zip, or .ipa)
```

Nothing about the layout is backend-specific: it is the same tree shape a `--config <path>` run uses
today, simply delivered as a zip. The config's `appPath` is what names the binary; the bundle just
has to *contain* it. (Because `appPath` already drives install, there is no new "where is the binary"
question — the existing field answers it.)

### The acquisition seam (sibling to BE-0063)

The resolution lives behind the same seam BE-0063 establishes: a config source that **materializes a
tree into a confined directory, then loads the config from it with the extraction root as the path
base**. Where BE-0063 adds a `GitHubSource` (tarball → content-addressed cache), this adds an
**`UploadBundleSource`**: given an uploaded zip, it extracts into a confined, ephemeral directory
under serve's control and yields the same `(config, root)` pair. Everything downstream — `resolve()`,
the runner, the drivers, the assertion evaluator, the report — is identical to a local or Git run.
(If BE-0063 lands first, this reuses its `ConfigSource` Protocol verbatim; if this lands first, it
introduces the same seam. The two are designed to share it.)

### serve surface

- **Endpoint.** A new authenticated `POST` accepts the zip as a `multipart/form-data` upload (the
  first multipart handling in `bajutsu/serve/handler.py`, which today reads JSON bodies). It streams
  the upload to a temp file (bounded memory), validates it (below), extracts it into a fresh
  per-upload directory, and returns a handle the existing run path consumes — so "upload" and "run"
  reuse the same job machinery (`bajutsu/serve/jobs.py`) as a normal run, only the *source* differs.
- **Confinement.** The extraction directory is a dedicated, serve-owned sandbox (a sibling of
  `runs/`, never the browse `--root`), so an uploaded tree can never overwrite the operator's files.
  Reading run artifacts back stays on the existing `ArtifactStore` boundary
  (`bajutsu/serve/artifacts.py`).
- **UI.** An **Upload & run** panel: drop a `.zip`, pick the `--app` from the contained config's
  `apps:` (parsed after extraction), press run. The job streams logs and produces a report exactly
  like any other run, and [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)'s
  download closes the round trip (upload a suite → run → download the result).

### Security — the heart of the design (on top of BE-0051)

Uploading a binary and executing it on the host is, by construction, "run code the user supplied", so
this item is only safe **on top of** the hardening in
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
and adds upload-specific defenses. The scope here is **what runs safely today on the single-Mac
Tier-A serve**; deeper multi-tenant isolation is deferred to BE-0015 / BE-0016 (below).

- **Authentication is mandatory.** The endpoint is behind BE-0051's token auth like every other serve
  request; an unauthenticated serve must not expose upload at all. serve already refuses a non-loopback
  bind without a token, so upload inherits "no token ⇒ loopback only".
- **Zip-slip / path traversal.** Every entry is validated to resolve **strictly under** the extraction
  root before writing; absolute paths, `..` segments, and symlink entries are rejected. This is the
  same "confine to a root" invariant serve already enforces for config paths (`_confined_config_path`)
  and baselines, applied to archive extraction.
- **Resource bounds (zip-bomb defense).** Enforce a max upload size, a max number of entries, a max
  total uncompressed size, and a max per-entry compression ratio; abort extraction the moment a bound
  is crossed, rather than after filling the disk.
- **Ephemeral by default.** Each upload extracts into its own directory, the binary installs into the
  run's Simulator, and the extraction directory (and the uploaded zip) are deleted after the run —
  no uploaded code lingers. On iOS the run uses a clean/`--erase` Simulator
  ([DESIGN §2](../../../DESIGN.md)), so an uploaded app gets today's per-run execution isolation for
  free.
- **Provenance.** The uploaded filename and the **sha256 of the zip** are recorded into the run's
  `manifest.json`, mirroring how BE-0063 records the resolved commit SHA — so "what did this run
  execute?" is always answerable after the fact, preserving [DESIGN §2](../../../DESIGN.md)'s "never
  silently run an unknown revision".
- **Secrets.** The bundle carries config and scenarios but **not** secret values — `${secrets.*}`
  resolve from the serve host's environment as today ([BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md)),
  so an uploaded suite cannot smuggle or exfiltrate the host's secrets, and the run's artifact scrub
  applies unchanged.

### Determinism, the gate, and app-agnosticism

- **No LLM, no effect on the verdict.** This is acquisition + extraction before the deterministic
  `run`; pass/fail is still computed only from machine assertions. Prime directives 1 and 2
  ([CLAUDE.md](../../../CLAUDE.md)) hold by construction.
- **Linux-testable.** Extraction, zip-slip rejection, the resource bounds, and the path-base
  resolution are pure packaging/plumbing and unit-test on the existing Linux gate against fixture
  zips — no Simulator. Only the actual app *install + run* needs a Mac, exactly as for any iOS run.
- **App-agnostic.** Per-app differences stay in the bundled config (`apps.<name>`); the tool,
  drivers, and runner do not branch per bundle.

### Backend scope (iOS first; web noted)

The headline case is **iOS**: a `.app` directory, a zipped `.app`, or an `.ipa` (itself a zip), named
by the config's `appPath` and installed into the Simulator. The bundle *layout* is backend-neutral
(it is just "a config tree"), so the mechanism does not hard-code iOS. The **web (Playwright)** backend
has no "app binary"; its analogue is bundling a **static site** and serving it via
[BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)
(`launchServer`) with the config's `baseUrl` pointed at it. That web variant is **out of scope for
the first slice** and noted here so the layout stays general; it can be a follow-up once the iOS path
lands.

### Out of scope

- **Building the app from source.** [DESIGN §1](../../../DESIGN.md) is explicit that Bajutsu receives
  a prebuilt artifact; the bundle carries the build product, it does not build it. (The config's
  `build:` on-demand build remains available for the *local* / Git case, where a toolchain is present.)
- **Multi-tenant execution isolation.** Per-tenant Simulators, per-job egress controls, and
  org-scoped storage are the domain of
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) /
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md); this item targets the
  single-Mac Tier-A serve.
- **Retention / a library of uploaded bundles.** Uploads are ephemeral; persisting and versioning
  them is the Git source's job ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)),
  not this one.

## Alternatives considered

- **Git source only ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)).**
  Rejected as a complete substitute: Git carries the *text* well but not the *built binary*, which
  [DESIGN §1](../../../DESIGN.md) says Bajutsu consumes. Forcing the binary through Git means either
  committing build products or running a full build on the host — exactly what an upload avoids. The
  two are complementary, not redundant.
- **Hand-place files on the host and use the existing file-browser picker.** Works on a local Mac but
  gives a *browser* user of a hosted serve no way to bring their own suite — the same gap
  [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) closes for the
  download direction.
- **Upload only the config YAML, not the tree.** Rejected for the same reason as in BE-0063: the
  config's `scenarios` / `appPath` are relative paths, so a config without its tree (and its binary)
  cannot run.
- **A bespoke multi-part bundle format with its own manifest.** Rejected: the working local checkout
  *is* the format. Treating the zip as "a tree to materialize" reuses the config's existing relative
  paths and BE-0063's path-base mechanism, inventing nothing.
- **A tarball instead of a zip.** Rejected for symmetry and reach: `.ipa` and zipped `.app` are
  already zips, stdlib `zipfile` is the same foundation
  [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) uses for export, and a
  zip opens by double-click on every OS.
- **Persist uploads as a reusable library.** Deferred: that is versioned storage, which Git
  ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) already is. Uploads stay
  ephemeral.

## References

- [CLAUDE.md](../../../CLAUDE.md), [DESIGN §1](../../../DESIGN.md) (Bajutsu receives a prebuilt app,
  does not build it), [DESIGN §2](../../../DESIGN.md) (AI never judges; determinism first; clean
  environment per test).
- [BE-0060 — Download / export a run report as a zip](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
  — the **export** mirror; the shared stdlib `zipfile` foundation and the round-trip partner.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  — the **pull** sibling; the `ConfigSource` seam and the "materialize a tree, resolve config against
  its root" mechanism this reuses.
- [BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — token auth + path confinement this builds on; the `_confined_config_path` invariant extended to
  extraction.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — why a browser user needs upload; where deeper multi-tenant isolation lives.
- [BE-0059 — Bring up the target server for a run (`launchServer`)](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)
  — the web-backend analogue (serve a bundled static site) for a future slice.
- [BE-0032 — Secret variables](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md)
  — secrets come from the host environment, not the bundle.
- `bajutsu/config.py` (`AppConfig.appPath` / `build` / `bundleId` / `baseUrl`), `bajutsu/serve/handler.py`
  (the `do_POST` body handling that gains multipart), `bajutsu/serve/operations.py`
  (`_confined_config_path`, the config-bind path), `bajutsu/serve/jobs.py` (the run job machinery),
  `bajutsu/serve/artifacts.py` (the confined artifact store) — the surfaces this touches.
- [docs/configuration.md](../../../docs/configuration.md), [docs/cli.md](../../../docs/cli.md#serve).

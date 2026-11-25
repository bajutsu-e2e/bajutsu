**English** · [日本語](BE-0073-serve-zip-bundle-upload-ja.md)

# BE-0073 — Upload a config + scenarios + app-binary bundle as a zip and run it from the web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0073](BE-0073-serve-zip-bundle-upload.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0073") |
| Implementing PR | [#216](https://github.com/bajutsu-e2e/bajutsu/pull/216), [#890](https://github.com/bajutsu-e2e/bajutsu/pull/890) |
| Topic | Configuration sourcing |
| Related | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal adds a one-step way to run a self-contained test bundle from a browser. The bundle holds a `bajutsu.config.yaml`, its scenario tree, and the **built app binary** (`.app` / `.app.zip` / `.ipa`), packaged as a single `.zip`. The user **uploads the zip through the `bajutsu serve` web UI** and runs it. The browser needs no file-system access to the serve host.

serve extracts the zip into a confined, ephemeral directory. It binds the contained config the same way it binds a locally browsed one, installs the binary into the run's Simulator, and drives the existing deterministic `run` path against the materialized tree. The proposal is purely a new way to **acquire** a bundle of config, scenarios, and binary. The schema, the runner, the drivers, and the deterministic gate are untouched, and no large language model (LLM) is added anywhere.

The proposal pairs with two existing items:

- It is the **import** mirror of [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md), which **exports** a finished run as a zip.
- It is the **push** sibling of [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), which **pulls** a config and its scenario tree from a Git repository. Both answer the question "where does a hosted serve get the config and scenarios it runs?" BE-0063 answers over Git, and this proposal answers over an upload.

It builds on the serve hardening that [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) already shipped (token auth and path confinement). Without that hardening, exposing the upload and run of an arbitrary binary would be unsafe.

## Motivation

A team's config and scenarios live in their repository. `bajutsu run` consumes a *local* tree plus a *built* app artifact ([DESIGN §1](../../DESIGN.md): "Bajutsu does not build the app — it receives an existing `xcodebuild` product"). That works for a local Mac. A **hosted or remote serve** leaves a gap that neither hand-placement nor a Git source fully closes:

1. **A browser user of a hosted serve has no file-system access to the host.** Serve may run on a remote worker or a shared Mac ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)). Then the config, the scenarios, and `runs/` all live on *that* machine. Today's UI config picker is a **file browser confined to `--root`** (`bajutsu/serve/operations.py` `_confined_config_path`). It can choose only from what an operator has already hand-placed on the host, so a browser user cannot bring their own suite. This is the exact mirror of motivation #3 in [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): no file-system access to *retrieve* a run. Here the missing direction is **putting a suite in**.

2. **A Git source cannot carry the built binary.** [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) materializes a repo subtree at a ref. That suits the *text* (config and YAML scenarios) but not the **compiled app**. Teams do not commit `.app` or `.ipa` products to Git. BE-0063's own design leans on the config's `build:` command to (re)produce the binary on the host, which needs a full toolchain on that host. A zip is the one transport that bundles the **already-built** artifact with the config and scenarios. [DESIGN §1](../../DESIGN.md) says Bajutsu consumes exactly that artifact. The two acquisition paths complement each other: Git carries the versioned text, and an upload carries the prebuilt binary.

3. **Hand-placing files on the host is the only path today.** The self-hosting Tier-A guide ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) has the operator copy the team's config, scenarios, and binary onto the Mac and keep them in sync by hand. An upload turns "ask the operator to scp a build over and edit `--root`" into "drag a zip onto the page and press run".

No archive *import* exists in the codebase today. [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) proposes the **export** half (stdlib `zipfile`, one archiver). This proposal adds the **import** half on the same stdlib foundation. Together they make a run bundle a portable unit in both directions.

## Detailed design

### The bundle is a tree to materialize

The design invents no new layout. The crux of [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) applies unchanged: **the config alone is not enough**. The entries `scenarios`, `baselines`, `setup`, `appPath`, and `build` are paths relative to the run's working directory (`bajutsu/config.py` `AppConfig`).

An uploaded zip is therefore treated exactly as BE-0063 treats a Git checkout: **a self-contained subtree that the config lives in**. The config's relative entries resolve **against the extraction root**. The design reuses the mechanism that BE-0063 introduces, where the path base is an explicit value and not the process working directory.

A minimal bundle is whatever a working local checkout already is:

```
my-suite.zip
├── bajutsu.config.yaml         # appPath: ./build/Sample.app   scenarios: ./scenarios
├── scenarios/
│   └── sample/…                # the YAML tree
└── build/
    └── Sample.app/             # the prebuilt binary appPath points at (a .app dir, .app.zip, or .ipa)
```

Nothing about the layout is backend-specific. The tree has the same shape that a `--config <path>` run uses today, delivered as a zip. The config's `appPath` names the binary, and the bundle has to *contain* it. Because `appPath` already drives install, no new "where is the binary" question arises. The existing field answers it.

### The acquisition seam (sibling to BE-0063)

The resolution lives behind the seam that BE-0063 establishes. A config source **materializes a tree into a confined directory**, then loads the config from the tree with the extraction root as the path base. BE-0063 adds a `GitHubSource` (tarball into a content-addressed cache). This proposal adds an **`UploadBundleSource`**. Given an uploaded zip, it extracts into a confined, ephemeral directory under serve's control and yields the same `(config, root)` pair.

Everything downstream is identical to a local or Git run: `resolve()`, the runner, the drivers, the assertion evaluator, and the report. If BE-0063 lands first, this proposal reuses its `ConfigSource` Protocol verbatim. If this proposal lands first, it introduces the same seam. The two are designed to share it.

### serve surface

- **Endpoint.** A new authenticated `POST /api/upload` accepts the zip as a **raw body** (`Content-Type: application/zip`, filename via `?name=`). The single-page app (SPA) controls the request, so a streamed body needs no multipart parser. The endpoint streams the upload to a temp file with bounded memory and validates it (below). It extracts the zip into a fresh serve-owned directory and **binds the contained config as the active config**: `state.config` points at the bundle's config, and `state.cwd` points at the bundle root. `bind_git_config` binds a Git checkout in exactly the same way. Binding a config is an admin-role action, because it repoints which config the whole server serves. BE-0051 token auth guards it.
- **Confinement.** The extraction directory is a dedicated, serve-owned sandbox. It is a sibling of `runs/` and never the browse `--root`, so an uploaded tree can never overwrite the operator's files. At bind, every target's path fields are confined to the bundle (`Effective.rebased`), the same guard that the Git source applies. Reading run artifacts back stays on the existing `ArtifactStore` boundary (`bajutsu/serve/artifacts.py`).
- **UI.** The **Open config** dialog gains a third source, **Upload a bundle**, beside the file browser and the Git picker. A user drops a `.zip`, and it becomes the active config that the **Replay / Record / Crawl** tabs run from. The run uses the same job machinery (`bajutsu/serve/jobs.py`) as any other run, and only the *source* differs. Runs from a bound bundle (whose cwd is the extracted tree) write into serve's own runs store via `--runs-dir`, so they show in **History**. The download from [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) closes the round trip: upload a suite, run it, download the result. The host never executes an uploaded config's `build` command, because the bundle ships a prebuilt binary ([DESIGN §1](../../DESIGN.md)).

### Security on top of BE-0051

Uploading a binary and executing it on the host means, by construction, "run code the user supplied". This item is safe only **on top of** the hardening in [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), and it adds upload-specific defenses. The scope is **what runs safely today on the single-Mac Tier-A serve**. Deeper multi-tenant isolation is deferred to BE-0015 and BE-0016 (below).

- **Authentication is mandatory.** The endpoint sits behind BE-0051's token auth like every other serve request. An unauthenticated serve must not expose upload at all. Serve already refuses a non-loopback bind without a token, so upload inherits "no token ⇒ loopback only".
- **Zip-slip and path traversal.** Before writing, serve validates that every entry resolves **strictly under** the extraction root. It rejects absolute paths, `..` segments, and symlink entries. This applies to archive extraction the same "confine to a root" invariant that serve already enforces for config paths (`_confined_config_path`) and baselines.
- **Resource bounds (zip-bomb defense).** Serve enforces four limits: a max upload size, a max number of entries, a max total uncompressed size, and a max per-entry compression ratio. It aborts extraction the moment a bound is crossed, not after the disk fills.
- **Ephemeral by default.** Each upload extracts into its own serve-owned directory, and **only one bundle is bound at a time**. Binding another config (from any source) removes the previous bundle's sandbox. No uploaded code lingers past the session or accumulates on disk. On iOS the run uses a clean/`--erase` Simulator ([DESIGN §2](../../DESIGN.md)), so an uploaded app gets today's per-run execution isolation for free.
- **Provenance.** Serve records the uploaded filename and the **sha256 of the zip** into the run's `manifest.json`. BE-0063 records the resolved commit SHA in the same way. The question "what did this run execute?" stays answerable after the fact, which preserves [DESIGN §2](../../DESIGN.md)'s "never silently run an unknown revision".
- **Secrets.** The bundle carries config and scenarios but **no** secret values. The `${secrets.*}` references resolve from the serve host's environment as today ([BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)). An uploaded suite cannot smuggle in or exfiltrate the host's secrets, and the run's artifact scrub applies unchanged.

### Determinism, the gate, and app-agnosticism

- **No LLM, no effect on the verdict.** The change is acquisition and extraction before the deterministic `run`. Pass/fail is still computed only from machine assertions. Prime directives 1 and 2 ([CLAUDE.md](../../CLAUDE.md)) hold by construction.
- **Linux-testable.** Extraction, zip-slip rejection, the resource bounds, and the path-base resolution are pure packaging and plumbing. They unit-test on the existing Linux gate against fixture zips, with no Simulator. Only the actual app *install and run* needs a Mac, exactly as for any iOS run.
- **App-agnostic.** Per-app differences stay in the bundled config (`apps.<name>`). The tool, the drivers, and the runner do not branch per bundle.

### Backend scope (iOS first; web noted)

The headline case is **iOS**: a `.app` directory, a zipped `.app`, or an `.ipa` (itself a zip). The config's `appPath` names the binary, and the Simulator installs it. The bundle *layout* is backend-neutral, because it is just "a config tree". The mechanism therefore does not hard-code iOS.

The **web (Playwright)** backend has no "app binary". Its analogue is bundling a **static site** and serving it via [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md) (`launchServer`), with the config's `baseUrl` pointed at it. That web variant is **out of scope for the first slice**. It is noted here so the layout stays general, and it can follow once the iOS path lands.

### Out of scope

- **Building the app from source.** [DESIGN §1](../../DESIGN.md) is explicit that Bajutsu receives a prebuilt artifact. The bundle carries the build product and does not build it. The config's `build:` on-demand build remains available for the *local* and Git cases, where a toolchain is present.
- **Multi-tenant execution isolation.** Per-tenant Simulators, per-job egress controls, and org-scoped storage belong to [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md). This item targets the single-Mac Tier-A serve.
- **Retention and a library of uploaded bundles.** Uploads are ephemeral. Persisting and versioning them is the job of the Git source ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)), not of this item.

## Alternatives considered

- **Git source only ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)).** Rejected as a complete substitute. Git carries the *text* well but not the *built binary*, which [DESIGN §1](../../DESIGN.md) says Bajutsu consumes. Forcing the binary through Git means either committing build products or running a full build on the host. An upload avoids both. The two options complement each other and are not redundant.
- **Hand-place files on the host and use the existing file-browser picker.** This works on a local Mac. It gives a *browser* user of a hosted serve no way to bring their own suite — the same gap that [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) closes for the download direction.
- **Upload only the config YAML, not the tree.** Rejected for the reason given in BE-0063: the config's `scenarios` and `appPath` are relative paths, so a config without its tree and its binary cannot run.
- **A bespoke multi-part bundle format with its own manifest.** Rejected: the working local checkout *is* the format. Treating the zip as "a tree to materialize" reuses the config's existing relative paths and BE-0063's path-base mechanism, and invents nothing.
- **A tarball instead of a zip.** Rejected for symmetry and reach. An `.ipa` and a zipped `.app` are already zips. Stdlib `zipfile` is the foundation that [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) uses for export. A zip opens by double-click on every OS.
- **Persist uploads as a reusable library.** Deferred: that is versioned storage, which Git ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) already provides. Uploads stay ephemeral.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [CLAUDE.md](../../CLAUDE.md), [DESIGN §1](../../DESIGN.md) (Bajutsu receives a prebuilt app,
  does not build it), [DESIGN §2](../../DESIGN.md) (AI never judges; determinism first; clean
  environment per test).
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
  — the **export** mirror; the shared stdlib `zipfile` foundation and the round-trip partner.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  — the **pull** sibling; the `ConfigSource` seam and the "materialize a tree, resolve config against
  its root" mechanism this reuses.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — token auth + path confinement this builds on; the `_confined_config_path` invariant extended to
  extraction.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — why a browser user needs upload; where deeper multi-tenant isolation lives.
- [BE-0059 — Bring up the target server for a run (`launchServer`)](../BE-0059-launch-target-server/BE-0059-launch-target-server.md)
  — the web-backend analogue (serve a bundled static site) for a future slice.
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md)
  — secrets come from the host environment, not the bundle.
- `bajutsu/config.py` (`AppConfig.appPath` / `build` / `bundleId` / `baseUrl`), `bajutsu/serve/handler.py`
  (the `do_POST` body handling that gains a raw-zip upload path), `bajutsu/serve/operations.py`
  (`bind_config` / `bind_git_config` / `bind_upload_config`, the config-bind path), `bajutsu/serve/jobs.py`
  (the run job machinery and `ServeState.bind_upload`), `bajutsu/serve/artifacts.py` (the confined
  artifact store) — the surfaces this touches.
- [docs/configuration.md](../../docs/configuration.md), [docs/cli.md](../../docs/cli.md#serve).

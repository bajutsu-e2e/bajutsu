**English** · [日本語](BE-0506-run-code-identity-ja.md)

# BE-0506 — Record the app build and source commit a run exercised

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0506](BE-0506-run-code-identity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0506") |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

A run's `manifest.json` carries a `provenance` stamp. The stamp names the scenario and the tool. It does not name the app under test.

We propose that the runner add app facts to the stamp for every target a run drives. The facts are the app binary's hash and the app's version. They also include the Git commit of the app's source repository. The stamp also gains the commit of Bajutsu's own checkout.

The runner keeps a value it could not read as an explicit gap with a reason. A reader can then say which build of which code produced the verdicts. The reader can also see which facts the evidence cannot vouch for.

## Motivation

Today a run's evidence cannot say which code the run tested. The `provenance` stamp (`bajutsu/common/report/manifest.py:230`) holds `scenarioHash` and `toolVersion`. It also holds an optional `gitRevision` and the `configSource` of a Git-sourced config. The app leaves no trace. A report from last week names a scenario and a device, but not the build the scenario drove.

The existing `gitRevision` does not fill that gap. The runner runs `git rev-parse HEAD` in the current directory (`git_revision()`, `manifest.py:21`). The value names Bajutsu, the config repository, or the app's source. The launch directory decides which. Nothing in the manifest says which. The stamp has no flag for uncommitted changes either. A run from a modified tree looks like a run of the clean commit. When `git` errors, `git_revision()` drops the key. A missing revision then looks like a run that never tried to record one.

The cost shows up when a verdict flips between two runs. The scenario hash is the same, so the flakiness view groups the runs and calls the scenario flaky. The cause may be a rebuilt app, and no recorded field reveals it. A reviewer who wants to reproduce a problem cannot tell which binary to install.

The observable outcome has four parts.

- A run that launched a built app records `provenance.targets.<name>.app.hash` for each target.
- Two runs of the same binary report the same hash. A run after a rebuild reports a different one.
- `appSource.uncommitted` turns `true` when the source tree held uncommitted changes.
- A value the runner could not read appears as `null` beside a reason. The evidence then shows a gap, not silence.

## Detailed design

### What the stamp records

The runner adds a `targets` mapping to `provenance`, keyed by target name. It also adds a run-level `toolSource`. A run with one target has one entry. A multi-target run (BE-0428) has one entry per declared target. Each entry comes from that target's own binary and source repository.

The hash is a 256-bit Secure Hash Algorithm (SHA) digest. A single file, such as an Android package, has the kind "file". A directory, such as an iOS app bundle, has the kind "tree".

The `app` and `appSource` keys below sit under each `targets.<name>` entry.

| Key | Content |
|---|---|
| `app.hash` | `sha256:<hex>` digest of the binary at `app.path` |
| `app.hashKind` | `file` or `tree` |
| `app.version` | `CFBundleShortVersionString` or `versionName` |
| `app.buildNumber` | `CFBundleVersion` or `versionCode` |
| `appSource.commit` | Full commit hash of the app's source repository |
| `appSource.uncommitted` | `true` when the source tree holds uncommitted changes |

The `toolSource` object describes Bajutsu's own checkout. It sits at the top level of the `provenance` stamp. Whenever the object is present, it carries `commit` and `source`. The `source` value names where the identity came from. The value is either `checkout` or `build-arg`, never both. The `uncommitted` key appears when `source` is `checkout`, and never otherwise. A build-time commit has no working tree to compare against. With neither a checkout nor a `BAJUTSU_BUILD_COMMIT` value, the runner omits `toolSource` entirely.

The existing `gitRevision` keeps its meaning and its value. Every reader of it keeps working.

The serve job list is one such reader. Its function `_run_provenance` (`bajutsu/serve/jobs.py:836`) mirrors the stamp onto the database record. A reader treats a run without the new keys as an older run. The manifest `schemaVersion` rises from 18 to 19.

### How each value is obtained

**Binary hash.** For each target, the runner reads the binary at the resolved `app.path`. It reads after `build_app` (`bajutsu/common/runner/build.py:41`) has run. The hash then covers the binary the run launches.

A file hashes as a stream of its bytes. A directory hashes as a tree. The runner lists every regular file and every symbolic link in sorted order of relative paths. It never follows a link. It hashes one line per entry: `<relative path>\0<kind>\0<entry hash>\n`. The `kind` is `file` or `link`. A regular file contributes the hash of its bytes. A symbolic link contributes the hash of its target string. The tree hash includes the code-signing files, because a re-signed build is a different artifact.

**Version.** For an iOS `.app`, the runner parses the bundle's `Info.plist` with `plistlib`. For Android, it opens the `.apk` at `app.path` as a zip archive. It decodes the binary `AndroidManifest.xml` inside with a small decoder that lives in `bajutsu/common/`. The decoder reads `versionName` and `versionCode`. It adds no dependency and never calls the Android Software Development Kit (SDK). Both versions then come from the same file the hash covers. Targets that share a package each report their own `.apk`. A version the device reports after install is a different fact. This item leaves it out of the stamp. Neither read involves a Large Language Model (LLM).

**Source commit.** A new optional target key, `sourceRoot`, names the app's source repository. The runner resolves it against the config root, as it does `app.path`. It runs `git rev-parse HEAD` and `git status --porcelain` there. The commit describes the working tree, not the binary. The stamp records both separately, so a mismatch between them stays visible.

**Tool commit.** The runner reads Bajutsu's own checkout with the helper that `server_checkout()` uses. [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md) introduced it. The helper anchors Git at the package directory, not the current directory. We move it from `bajutsu/serve/operations/version.py` into `bajutsu/common/`. The runner then imports it without depending on `serve`.

A pip-installed copy has no checkout. It falls back to the build-time `BAJUTSU_BUILD_COMMIT` value. [BE-0277](../BE-0277-docker-build-commit-badge/BE-0277-docker-build-commit-badge.md) added that value. `toolSource` then records `source: build-arg` and no `uncommitted` flag. A real checkout records `source: checkout`.

The runner accepts a checkout when that checkout tracks Bajutsu's package directory. The test is `git ls-files --error-unmatch` on the package's `__init__.py`. An install inside another repository counts as no checkout. A virtualenv under the app's tree is one example. Without the test, `git` walks up from site-packages and stamps the app's commit as Bajutsu's.

### A value that could not be read

An unreadable value never changes a verdict and never stops the run. The stamp is metadata, not an assertion. No LLM touches it, so prime directive 1 holds. The gate reads machine-checkable verdicts alone. The runner records the gap and does not drop the key.

- The key stays in the stamp with the value `null`.
- A sibling `unavailable` mapping names the reason for each such key. An example is `"hash": "path-missing"`.
- The reason is a code from a fixed set, never a raw error message.

A raw message can carry a local path or a token. The run directory does not otherwise expose either. The fixed set is:

| Code | Meaning |
|---|---|
| `path-missing` | The binary does not exist at `app.path` |
| `read-error` | The runner could not read the binary |
| `plist-unreadable` | The `Info.plist` did not parse |
| `apk-unreadable` | The `.apk` did not open as a zip archive or has no manifest |
| `manifest-unparsable` | The manifest inside the `.apk` did not decode |
| `not-a-git-repo` | The `sourceRoot` is not inside a Git checkout |
| `git-unavailable` | The `git` command is missing or timed out |
| `status-read-failed` | `git status` errored |

A group the target never configured is a different case. A target with no `app.path` has no hash to attempt. An app preinstalled on the device is one example. The runner omits the `app` group for it. A target with no `sourceRoot` omits `appSource` the same way. Omission means "not configured". A `null` with a reason means "tried and could not read". A reader can tell the two apart.

### Where the stamp surfaces

`manifest.json` holds the keys above under `provenance`. The pipeline (`bajutsu/common/runner/pipeline.py:2497`) already calls `run_provenance`. The pool calls it once per scenario and once per member lease. Those calls sit at `bajutsu/common/runner/pool.py:504` and `:653`. The runner computes each target's stamp once per run. It does so after that target's `build_app` and before the first lease. It passes the mapping to every call. Each binary hashes once per run. A rebuild during the run cannot give two scenarios different values.

`ctrf.json` follows the Common Test Report Format (CTRF). Its `environment` object already reads `provenance`. It gains each target's app hash and version, each target's source commit, and the tool commit. A gap appears as an `unavailable` entry with its reason.

`report.html` shows a header under the run title with one line per target. Each line shows the app version and the short hash. An "uncommitted" marker appears when the flag is `true`. A gap reads "unknown" with its reason.

### Out of scope

- **Web targets.** A web target names a Uniform Resource Locator (URL), not a binary. Reading a deployed identifier from a response header or an endpoint needs help from the service. A separate proposal can cover it.
- **Flakiness grouping.** `bajutsu flakiness` still groups runs by `scenarioHash`. Splitting a group by `app.hash` is a follow-up that builds on the field this item records.
- **Build-time embedding.** The app's build could write a commit into its own metadata. Reading it needs a change in every app's build. This item does not ask for that.

### Work breakdown

1. Move the Git identity reader into `bajutsu/common/` and add a directory argument.
2. Add the binary hasher, the iOS version reader, and the Android manifest decoder. Each returns a value or a code from the fixed set.
3. Add the optional `sourceRoot` target key to the config schema, with a schema test.
4. Compute each target's stamp once per run. Pass the `targets` mapping to `provenance` in the pipeline and the pool. Raise `schemaVersion` to 19.
5. Surface the stamp, gaps included, in `ctrf.json` and `report.html`.
6. Document the keys in `docs/` and `docs/ja/`. Update `DESIGN.md` and `docs/architecture.md` where they describe `provenance`.

Each unit carries its own unit tests. The hasher test builds a small directory fixture. It checks that renaming a file, editing a byte, and adding a symbolic link each change the hash. It also checks that a file and a link with equal content hash differently. A decoder test feeds a small `.apk` fixture and a truncated one. The Git reader test puts the package directory inside an unrelated repository. It checks that no `toolSource` results.

A gap test points a target at a missing binary and a `sourceRoot` outside any repository. It checks that the run still passes. It checks that each key is `null` and that `unavailable` names the reason. A multi-target test checks that every declared target has its own entry.

## Alternatives considered

### Pass the commit on the command line or in an environment variable

The runner would trust the caller's claim. A forgotten flag leaves a silent blank. A value the runner reads itself cannot drift from the files on disk.

### Read a commit that the app's build embedded in its metadata

The value always matches the binary. Every app must then change its build. Bajutsu is app-agnostic (prime directive 3), so we defer this option. The stamp's keys leave room for it.

### Record the Git commit and skip the binary hash

A commit names a source state, not the artifact. A rebuild with different flags has the same commit and a different binary. A binary built elsewhere has the same problem.

### Write a separate `evidence.json`

`provenance` is already the run's identity stamp, and the flakiness view reads it. A second file would split one concept across two places.

### Hash a directory through its archive bytes

Archive metadata such as timestamps changes the hash while the binary stays the same. The sorted per-file hash depends on content alone.

### Change `gitRevision` to mean one specific repository

Existing readers depend on its current meaning. New keys with explicit names avoid a silent reinterpretation.

### Omit a key the runner could not read

A reader cannot tell a read that errored from a target that never configured the value. Recording the gap keeps the evidence honest about what it cannot vouch for.

### Stamp the first target of a multi-target run

The manifest would claim one build identity. The verdict also depended on another app's build. A per-target mapping costs one level of nesting.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Move the Git identity reader into `bajutsu/common/`
- [ ] Add the binary hasher and the version readers
- [ ] Add the `sourceRoot` target key
- [ ] Stamp every target once per run and raise `schemaVersion` to 19
- [ ] Surface the stamp, gaps included, in `ctrf.json` and `report.html`
- [ ] Update the bilingual docs, `DESIGN.md`, and `docs/architecture.md`

## References

- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md): introduced the `provenance` stamp that this item extends.
- [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md): the Git identity helper this item reuses.
- [BE-0277](../BE-0277-docker-build-commit-badge/BE-0277-docker-build-commit-badge.md): the build-time commit fallback for installs with no `.git`.
- [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md): the CTRF export that carries `provenance` into `environment`.

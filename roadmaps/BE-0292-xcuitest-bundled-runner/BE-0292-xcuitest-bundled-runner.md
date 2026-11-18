**English** · [日本語](BE-0292-xcuitest-bundled-runner-ja.md)

# BE-0292 — Bundle the XCUITest runner so testRunner is optional

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0292](BE-0292-xcuitest-bundled-runner.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0292") |
| Implementing PR | [#1221](https://github.com/bajutsu-e2e/bajutsu/pull/1221), [#1276](https://github.com/bajutsu-e2e/bajutsu/pull/1276) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md), [BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md) |
<!-- /BE-METADATA -->

## Introduction

The XCUITest backend drives every app through one generic runner. Today, though, every user must build
that runner and name its path in config before a single Simulator scenario can run.

This item ships the prebuilt Simulator runner inside the Bajutsu wheel as package data. When the
config names no runner, `xcuitest` resolves to the bundled one, so `xcuitest.testRunner` and
`xcuitest.build` both become optional. A Simulator run then needs no
`make -C demos/showcase runner-build` and no runner path, and the backend works out of the box.

An explicit `testRunner` or `build` still overrides the bundled default. Real-device runs need a signed
runner that Bajutsu cannot ship, so they stay explicit.

## Motivation

The runner is generic, but the setup it demands is not. Every target and every fresh clone pays a
build-and-configure cost for an artifact that never differs between them.

[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) established that the XCUITest
runner is a single app-agnostic XCTest target. One built `.xctestrun` drives whatever bundle id the
run targets, so the runner is identical across every app and every target. BE-0019 left its delivery
to two per-target knobs. `xcuitest.testRunner` names a prebuilt `.xctestrun`, and `xcuitest.build` is
a shell command that produces one on demand. Both put an app-agnostic artifact behind per-target
configuration.

That mismatch surfaces as friction the first time anyone reaches for XCUITest. A user must take three
steps:

- Run `make -C demos/showcase runner-build`, or write an equivalent `xcodebuild build-for-testing`
  command.
- Find the `.xctestrun` deep under a `DerivedData` products directory.
- Paste that path into the config of every target, for example
  `testRunner: ../../BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun`.

A fresh clone has no such artifact, so the path a config names does not exist until the build runs.
Each new checkout rebuilds the runner even though the bytes are the same everywhere. The per-target
`testRunner` line multiplies that one path across every XCUITest target in a config.

Bajutsu already ships non-Python assets this way for other subsystems. `bajutsu/templates/` carries
the `serve` and report HTML, CSS, and JavaScript inside the wheel, located at runtime by a path
relative to the package. The generic runner is the same kind of asset: shipped once, identical
everywhere. Its natural home is the wheel, not a build step that every user repeats.

Bundling also honors DESIGN §1, under which Bajutsu receives a prebuilt artifact rather than building
one at the user's site. The runner is built once, in Bajutsu's own release pipeline, and shipped ready
to run.

## Detailed design

The change adds a third, lowest-priority tier to runner resolution: a bundled default beneath the
existing `testRunner` and `build` knobs. It also adds the packaging that puts the runner in the wheel.
The change touches the runner-path resolution of the XCUITest environment and the build and packaging
configuration. The driver, the channel, selector resolution, the run loop, and every scenario stay
unchanged.

### Runner resolution gains a bundled default tier

Today [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py)
requires `xcuitest.testRunner`. The environment fails immediately when the target names no runner.
Once a runner path is named, `xcuitest.build`, when present, runs to produce the file if that path
does not yet exist on disk. So `build` today generates a named-but-missing runner. It does not stand
in for an absent `testRunner`.

This item makes `testRunner` optional and adds a bundled default beneath it. A Simulator run may reach
resolution with no `testRunner` and no `build`. The environment then resolves to the bundled runner
instead of failing.

The precedence stays explicit-over-default. A named `testRunner`, whether built directly or produced
by `build`, wins. The bundled runner is the fallback that lets the common case run with no config at
all.

Resolution stays a deterministic file-path decision with no LLM and no fixed sleep, so prime
directives 1 and 2 are untouched. Moving an app-agnostic artifact out of per-target config
strengthens directive 3 rather than bending it.

### The wheel carries the built Simulator runner as package data

A `.xctestrun` is not a standalone file. Its `__TESTROOT__` resolves relative to the directory of the
`.xctestrun` itself, so the test bundles beside it must ship together. The bundle therefore ships the
whole built-products directory: the `.xctestrun`, plus the runner and host `.app`/`.xctest` bundles
that `xcodebuild build-for-testing` emits. The directory lives under a package-data path such as
`bajutsu/_xcuitest_runner/`. Runtime code locates it by a path relative to the package, exactly as
for `bajutsu/templates/`.

Hatchling's default wheel packaging is VCS-aware. It silently drops any file matched by a `.gitignore`
pattern, unless `pyproject.toml` sets `artifacts` to pull the file back in. That knob differs from
`force-include`, which covers only files that live outside the package tree.

The root `.gitignore` of the repository already excludes `build/` and `DerivedData/` at any depth.
Those are the very names that the output directory of `xcodebuild` carries, as in the
`.../Runner/build/dd/Build/Products/...` path quoted above. The release pipeline must therefore do one
of two things:

- lay the products out under a package-data path with no gitignored segment
- declare an `artifacts` entry for them

Otherwise Hatchling drops the runner from the wheel without any error. The Linux install of the base
wheel never exercises XCUITest, so nothing would catch the gap.

Only the Simulator runner ships. A device runner must be signed with the operator's Apple Developer
team ([BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md)),
and Bajutsu cannot do that at release time. So `xcuitest.deviceType: device` keeps requiring an
explicit `testRunner`. When none is given, it reports a clear error. It does not fall back to a
Simulator runner, which cannot install on a device.

### The bundled runner is materialized into a writable cache before use

The package data of an installed wheel should be treated as read-only. Writing beside it pollutes the
install, and fails outright when site-packages is not writable. Yet a run already writes next to the
runner: `_patch_xctestrun_env` creates a patched copy of the `.xctestrun` in the directory of the
runner, to inject the `BAJUTSU_*` launch environment.

So resolving to the bundled runner first materializes the products directory into a writable cache,
for example `~/.cache/bajutsu/xcuitest-runner/<hash>/`. `testRunner` then resolves to the copy there.
A warm cache is reused without copying again. The existing per-run patched-copy-and-unlink operates on
the cache, never on site-packages.

The copy is keyed by a content hash of the bundled products, not by the Bajutsu version. This repo
pins `version = "0.0.0"` in `pyproject.toml`, and no release workflow bumps it today. A version-keyed
cache would never invalidate on an upgrade. Hashing the products themselves invalidates the cache
whenever the runner actually changes, independent of any version-bumping discipline.

The device pool leases multiple devices in parallel within one run set
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)
relies on that same concurrency for its per-device runner cache). Two leases can therefore reach a
cold, hash-keyed cache directory at once. Materialization copies into a temporary directory beside the
cache, and renames it into place.

Renaming a populated directory onto one that a concurrent lease already populated fails (`ENOTEMPTY`).
It does not silently no-op. The losing lease must catch that error and treat the cache of the winner
as its own result. The guarantee that matters is that a concurrent reader sees either no cache
directory yet or a fully populated one, never a partial copy.

Pruning stale hash-keyed caches left behind by earlier runner builds is out of scope for this item.
Each copy is a bounded artifact, not unbounded growth. Cache eviction can follow as a separate item if
the accumulated size becomes a real complaint.

### The runner is built and included in Bajutsu's release pipeline

The bundled products are a compiled macOS artifact. A build step produces them and places them under
the package-data directory when the wheel is built for release. The repository does not commit a
binary blob.

The runner is only ever executed on macOS against a Simulator, so the bytes are inert on any other
platform. The base wheel stays pure-Python and installable on Linux, and carries the products merely as
unused data. The deterministic `make check` gate, which must run anywhere, never touches the runner.

`doctor --target` reports which runner a target resolves to: bundled, `testRunner`, or `build`. It also
surfaces an Xcode/SDK mismatch against the bundled runner, and names the `build` and `testRunner`
overrides as the escape hatch. A toolchain that the bundled runner was not built against then degrades
to a clear message rather than an opaque `xcodebuild` failure.

### Validation

The split follows the fast-gate / on-device boundary that BE-0019 already draws.

- **Fast gate (no device).** Resolution is unit-tested with an injected fake bundled-runner directory.
  The tests check five behaviors:
  - A config with neither `testRunner` nor `build` resolves to the bundled path.
  - An explicit `testRunner` still wins over the bundled path.
  - `deviceType: device` with no `testRunner` fails with the clear device-runner error, rather than
    resolving to the bundled Simulator runner.
  - The materialize-to-cache step copies once and reuses a warm, hash-keyed cache.
  - Two concurrent materializations racing a cold cache converge on one populated directory. The
    losing lease adopts the copy of the winner rather than raising.

  No test puts a device, or an LLM, on the gate.
- **On-device (e2e path).** On the heavier `e2e.yml` path, a showcase XCUITest scenario runs with the
  `testRunner` line removed from its target config. The run proves that the backend drives the app
  through the bundled runner with no build step and no runner path.

## Alternatives considered

- **Download the runner on demand (Playwright-style).** Build the runner in CI, upload it as a
  versioned release asset, and have Bajutsu fetch and cache it on first use. This approach keeps the
  wheel free of a compiled blob and decouples the release cadence of the runner from that of the wheel.
  It adds a network dependency and a download-and-verify path to first use, and puts artifact hosting
  on the maintainers. Bundling in the wheel keeps first use offline and self-contained, at the cost of
  carrying inert bytes on non-macOS installs. Downloading remains the natural fallback if the
  wheel-size or Xcode-coupling cost of bundling proves too high.
- **Build the runner on demand from bundled source.** Ship the Swift source of the runner (already in
  `BajutsuKit/Runner/`) and run `xcodebuild build-for-testing` into a cache on first use. This
  alternative carries no compiled artifact and always matches the host Xcode. It reintroduces exactly
  the per-machine build that this item removes: the first XCUITest run on every fresh clone pays the
  full build cost. The existing `xcuitest.build` knob already covers the opt-in build case. The bundled
  default exists to avoid the build, not to relocate it.
- **Commit the built products into the repository.** Placing the prebuilt products under version
  control would put them in every sdist and wheel with no build-time step. It bloats the repository
  with a multi-megabyte binary that must be regenerated and re-committed on every runner change, and
  couples the tree to one Xcode version. Producing the artifact in the release pipeline keeps the
  binary out of history while still shipping it in the wheel.
- **Ship the runner as a separate companion package.** A macOS-only `bajutsu-xcuitest-runner` package,
  installed as an extra, would keep the base wheel byte-for-byte pure. It splits the release across two
  packages and adds an install step that the user must remember before XCUITest works. That
  reintroduces setup friction in a different place. A single wheel that carries inert bytes off macOS
  keeps installation to one step.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Runner resolution — add the bundled-default tier beneath `testRunner` / `build` in the XCUITest
  environment, with explicit config still overriding and `deviceType: device` still requiring an
  explicit runner.
- [x] Materialize-to-cache — copy the bundled products into a content-hash-keyed writable cache,
  handling the concurrent-materialization race, and resolve `testRunner` to the copy, leaving the
  per-run patched-copy step untouched.
- [x] Packaging — place the built Simulator products under the package-data directory before the wheel
  build runs and add the release-pipeline build step that produces them; keep the base wheel
  installable on Linux.
- [x] doctor / disclosure — report the resolved runner source and surface an Xcode/SDK mismatch with
  the override escape hatch named. Both halves ship: `doctor --target` prints `xcuitest runner:
  bundled (wheel-shipped Simulator runner)` / `testRunner: <path>`, and when the target resolves to
  the bundled runner it adds a `⚠` line if the host Xcode / Simulator SDK major differs from the
  toolchain `make runner-bundle` recorded (`build-info.json`), naming `xcuitest.testRunner` /
  `xcuitest.build` as the escape hatch.
- [x] Validation — fast-gate resolution tests (bundled default, override precedence, device error,
  cache reuse, concurrent-materialization race) plus the build-info / toolchain-mismatch disclosure
  are unit-tested; the on-device e2e run now exercises the bundled path — the `xcuitest (multi-touch)`
  job stages the runner with `make runner-bundle` and runs `smoke.yaml` against a config with no
  `testRunner` (`showcase.bundled-runner.config.yaml`) on both the SwiftUI and UIKit a11y apps,
  proving the app-agnostic bundled runner drives either toolkit with no runner-build and no runner
  path.

Log:

- [#1221](https://github.com/bajutsu-e2e/bajutsu/pull/1221) — implemented runner resolution,
  materialize-to-cache, and packaging, with fast-gate tests. Also added `doctor --target`'s
  resolved-runner-source disclosure (`runner_source` / `xcuitest_runner_summary`), but deliberately
  scoped the Xcode/SDK-mismatch half out as a separate follow-up.
- [#1276](https://github.com/bajutsu-e2e/bajutsu/pull/1276) — completed the two deferred halves: the
  doctor Xcode/SDK-mismatch disclosure (`build-info.json` recorded by `make runner-bundle`, compared
  against the host toolchain, escape hatch named) and the on-device bundled-path e2e (a
  `testRunner`-free `showcase.bundled-runner.config.yaml` run through `smoke.yaml` on both the SwiftUI
  and UIKit a11y apps after `make runner-bundle`). Flips the item to Implemented.

## References

- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the XCUITest backend and its
  `testRunner` / `build` runner-delivery model this item extends.
- [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md) —
  the signed device runner, which is why the bundled default is Simulator-only.
- [DESIGN §1](../../DESIGN.md) — Bajutsu receives a prebuilt artifact rather than building one at the
  user's site.

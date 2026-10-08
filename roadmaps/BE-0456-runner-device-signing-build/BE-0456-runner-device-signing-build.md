**English** · [日本語](BE-0456-runner-device-signing-build-ja.md)

# BE-0456 — Per-user signed device build of the XCUITest runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0456](BE-0456-runner-device-signing-build.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0456") |
| Topic | Device-cloud execution |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md), [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) |
<!-- /BE-METADATA -->

## Introduction

Driving a real iPhone or iPad needs an XCUITest runner signed by the person who runs it. Today that
person cannot produce one without editing the repository. The runner's bundle identifiers are
hardcoded, and the build helper accepts only an Apple Developer team. This item adds
`bajutsu runner build --device`, which builds the runner from sources shipped in the wheel, using a
per-user signing file that names the bundle identifier prefix, the team, and either automatic or
manual (certificate plus provisioning profile) signing. The result is cached under a key derived
from those inputs, and a real-device run that names no `xcuitest.testRunner` resolves to it.

## Motivation

Real-device runs already work once a signed runner exists. The missing piece is a way for each user
to make that runner from their own Apple Developer account.

- **The bundle identifiers belong to one team.** `BajutsuKit/Runner/project.yml` hardcodes
  `com.bajutsu.runner.host` and `com.bajutsu.runner.uitests`. An App ID (an identifier registered
  with Apple) can belong to only one team, so a second team that signs these identifiers fails,
  even with automatic signing. Passing a single `PRODUCT_BUNDLE_IDENTIFIER` on the `xcodebuild`
  command line does not help, because it overrides the host and the test bundle with the same value.
- **Only automatic signing exists.** `runner-build-device` in `demos/showcase/Makefile`
  ([BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md))
  reads one environment variable, `DEVELOPMENT_TEAM`, and always signs automatically. A user who
  must pin a certificate and a provisioning profile, such as a continuous-integration (CI) machine
  or a company that distributes through enterprise profiles, has no way to say so.
- **The build recipe needs a source checkout and the demo Makefile.** The wheel carries only the
  Simulator runner products ([BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)).
  A user who installed Bajutsu from the wheel has neither the runner sources nor the recipe.
- **Per-user values have no safe home.** The target config is committed, shared, and can be fetched
  from a Git repository. A team ID or bundle identifier written there reaches every other user.

The observable change: a user on a fresh machine writes one signing file, runs
`bajutsu runner build --device` once, and then `bajutsu run` drives a real device with no
`testRunner` line and no edit to any committed file. Today the same user must fork `project.yml`.

## Detailed design

The work adds a build command, a signing file, and a resolution tier. The run loop, the driver,
the channel, and every scenario stay unchanged. The build is not on the `run` verdict path, so no
LLM and no fixed sleep enter it (prime directives 1 and 2). The runner stays app-agnostic, so
per-app differences still live in `targets.<name>` (prime directive 3).

### The signing file

Signing is a property of the user, not of a target, because the runner is generic. One file per
user therefore suffices. Bajutsu looks for it in this order, and the first hit wins:

1. `--signing <path>` on `bajutsu runner build`. Only the build command has this flag.
2. The `BAJUTSU_SIGNING_FILE` environment variable.
3. `$XDG_CONFIG_HOME/bajutsu/signing.yaml`, falling back to `~/.config/bajutsu/signing.yaml`.

The run-time tier below consults entries 2 and 3 only. A user who builds with `--signing`
alone must set `BAJUTSU_SIGNING_FILE` to the same file when running, or pass the built products
through `--out` and `testRunner`.

The file lives outside the repository, so a commit cannot carry it. It holds no secret: team IDs,
certificate names, and profile names identify keys and profiles that stay in the Keychain and in
`~/Library/MobileDevice/Provisioning Profiles/`.

```yaml
bundleIdPrefix: com.acme           # runner host app = <prefix>.bajutsu.runner-host,
                                   # UI-test bundle = <prefix>.bajutsu.runner-uitests
# bundleIds:                       # instead of bundleIdPrefix: name both identifiers yourself
#   host: com.acme.e2e.runner-host
#   uitests: com.acme.e2e.runner-tests
teamId: ABCDE12345                 # required
signing: automatic                 # automatic (default) | manual
manual:                            # required when signing: manual
  identity: "Apple Development: Jane Doe (ABCDE12345)"
  profile: "Acme Bajutsu Wildcard"           # one profile that covers every identifier, or
  profiles:                                  # one profile per identifier
    host: "Acme Bajutsu Host"
    runner: "Acme Bajutsu Runner"            # the .xctrunner app of the UI-test bundle
```

The file gives either `bundleIdPrefix` or `bundleIds`, never both. The prefix form derives both
identifiers. The `bundleIds` form names them. It serves a user whose Apple Developer account already
holds explicit App IDs and profiles under other names, or whose organization restricts naming.

In either form, the UI-test bundle's `.xctrunner` app takes the identifier
`<uitests identifier>.xctrunner`. A user who signs manually with per-identifier profiles must supply
a profile for that third identifier as well.

A pydantic model validates the file at load, like the target config. Validation fails on:

- an unknown key, a missing `teamId`, or `signing: manual` without `identity`;
- a `manual` block that gives both or neither of `profile` and `profiles`;
- a file that gives both or neither of `bundleIdPrefix` and `bundleIds`;
- a `bundleIds` block that lacks `host` or `uitests`, or whose two values are equal.

There is no default prefix, because any default would recreate the collision this item removes.
The build knows no target, so it cannot compare the runner identifiers with the identifier of the
app under test. The documentation warns that a runner identifier equal to the app's identifier
makes the install overwrite the app.

### Staging the sources

The command stages the sources into a scratch directory and builds there. The checkout is never
touched, so a build leaves `git status` clean. Two source locations feed the staging step:

- In a source checkout, the repository root.
- In a wheel install, `bajutsu/_runner_source/`, a new package-data directory.

`make runner-source` fills that directory from the path list that already defines the runner's
freshness hash, `_HASH_SOURCE_PATHS` in
`bajutsu/common/platform_lifecycle/environments/_bundled_runner.py`. Reusing the list keeps the
shipped set and the hashed set identical. The directory is gitignored and pulled into the wheel
through the `artifacts` entry in `pyproject.toml`, as `_xcuitest_runner/` is today. Copying
selected paths avoids `force-include` on `BajutsuKit/`, which would drag its build output into the
wheel. `Package.swift` declares two test targets whose paths the staged tree lacks, and Swift
Package Manager (SPM) rejects a manifest with a missing target path. The staging step therefore
writes a manifest without those two targets. That manifest is derived from the committed
`Package.swift` rather than kept as a second copy of its target list. A unit test asserts that the
staged manifest still declares every non-test target the committed one does, so a target added
later cannot go missing from the device build silently.

### Generating a per-user project

XcodeGen (`xcodegen`) turns `project.yml` into the Xcode project. The command loads the staged
`project.yml` and overrides settings per target before generating, which `xcodebuild` command-line
settings cannot do. The staged copy changes, never the committed file.

| Setting | Runner host app target | UI-test target |
|---|---|---|
| `PRODUCT_BUNDLE_IDENTIFIER` | `<prefix>.bajutsu.runner-host`, or `bundleIds.host` | `<prefix>.bajutsu.runner-uitests`, or `bundleIds.uitests` |
| `DEVELOPMENT_TEAM` | `teamId` | `teamId` |
| `CODE_SIGNING_ALLOWED` | `YES` | `YES` |
| `CODE_SIGN_STYLE` | `Automatic` or `Manual` | same |
| `CODE_SIGN_IDENTITY` (manual) | `identity` | `identity` |
| `PROVISIONING_PROFILE_SPECIFIER` (manual) | `profile` or `profiles.host` | `profile` or `profiles.runner` |

The staged spec also rewrites the SPM package path from `../..` to the staged root. The build then
runs `xcodebuild build-for-testing -destination generic/platform=iOS
-skipPackagePluginValidation` (the `OpenAPIGenerator` build plugin needs that flag
non-interactively), adding `-allowProvisioningUpdates` for automatic signing only, and copies the single `*.xctestrun` to
`BajutsuRunner.xctestrun`, as `runner-build-device` does today.

### The cache and its key

The build output goes to `xcuitest-runner-device/<key>/Products/` under the shared Bajutsu cache
root, the one `_runner_cache_root()` in `_bundled_runner.py` derives from `XDG_CACHE_HOME`. A hosted
deployment that relocates caches therefore keeps the signed build inside its sandbox.

The key hashes four things:

- The hash of the pristine runner sources, taken before staging rewrites anything. In a checkout it
  is `source_hash()` over the repository root. In a wheel install it is the same function over
  `bajutsu/_runner_source/`, whose layout mirrors the repository paths. A checkout and a wheel
  install of the same release then produce the same key.
- The validated signing fields, with the two resolved identifiers.
- The Xcode build version.
- For manual signing, fingerprints of the resolved assets: the SHA-256 of each installed profile
  file the build names, and the certificate hash `security find-identity` reports for the identity.
  A profile or certificate renewed under the same name then yields a new key.

Automatic signing has no such inputs, because Xcode mints the profile during the build. Every cache
hit therefore also checks the expiration date of each profile embedded in the cached products. An
expired runner fails the run with a message to rebuild using `--force`.

A hit requires a complete directory. The build writes into a temporary directory beside the final
one, confirms that `BajutsuRunner.xctestrun` exists, and renames the directory into place, as
`materialize()` in `_bundled_runner.py` does. A reader that finds the final directory without that
file treats it as a miss, so an interrupted build or a concurrent `--force` never leaves a
half-populated hit. A losing concurrent rename keeps the directory the winner published.

The command prints the `.xctestrun` path. `--out <dir>` also copies the whole `Products` directory
there, which the Device Farm package step needs because the `.xctestrun` refers to its test
bundles by relative path.

### Command

```text
bajutsu runner build --device [--signing PATH] [--out DIR] [--force]
```

- A preflight runs before any `xcodebuild` call. It checks for macOS, `xcodebuild`, and `xcodegen`.
  For manual signing it also checks that the identity appears in `security find-identity -v -p
  codesigning` and that each named profile is installed. Each failure names the missing item and
  its fix.
- `--force` rebuilds over a matching cache entry.
- `--device` is required today. The flag leaves room to fold the Simulator runner build into the
  same command later.

### Resolution at run time

`xcuitest.deviceType: device` currently demands an explicit `testRunner`
([BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)). The environment
gains one tier: with no `testRunner`, it finds the signing file through lookup entries 2 and 3 above,
computes the key, and uses the cached `.xctestrun` if a complete directory exists. Two distinct
errors cover the misses. If no signing file is found, the run fails and names both lookup
locations. If the directory is absent, the run fails at once with the exact
`bajutsu runner build --device` command to run. The run never builds implicitly,
because a signing build is slow, can raise a Keychain prompt, and can register identifiers with
Apple, and none of that belongs in the middle of a pooled run. An explicit `testRunner` still wins
over the cache.

### The showcase Makefile

`runner-build-device` in `demos/showcase/Makefile` becomes a call to `bajutsu runner build
--device`. It appends `--signing $(SIGNING)` only when `SIGNING` is set, so an unset value falls
through to `BAJUTSU_SIGNING_FILE` and the default path. It keeps the `build/` output path the
Device Farm runbook uses. The `DEVELOPMENT_TEAM`-only invocation stops working for the runner and fails with a message that
points at the signing file. Only the runner path changes. `swiftui-archive-device` and
`swiftui-ipa-device` keep building the demo app from `DEVELOPMENT_TEAM`.

### Out of scope

- Signing or packaging the user's own app. Every project builds its app differently, so a thin
  abstraction would fit none of them.
- Device registration beyond what Xcode's `-allowProvisioningUpdates` does.
- Creating certificates or profiles, or managing the Keychain.
- Building signed artifacts in CI, which waits on an Apple Developer account wired into CI as
  described in BE-0288.
- Android, the Simulator runner, and Device Farm's own re-signing of the uploaded app.

### Verification

- Unit tests on any host (including Linux): signing-file validation (including the
  `bundleIdPrefix` and `bundleIds` exclusivity) and lookup order, spec
  override rendering for both modes, `xcodebuild` and `xcodegen` argument construction, cache-key
  stability and sensitivity (a checkout and a wheel layout of the same sources share a key; a
renewed profile changes it), atomic publication and the incomplete-directory miss, profile
expiry, the staged-manifest target check, and the run-time resolution errors. The external commands sit behind
  an injected runner, so none of these tests needs Xcode.
- A manual proof on real hardware, outside `make check`: build with automatic signing under a
  second team's identifiers, build with manual signing, and run one scenario on a device with no
  `testRunner`.

## Alternatives considered

| Option | Why it was not chosen |
|---|---|
| Environment variables only (`BAJUTSU_SIGN_*`) | Five or more variables must be set for every shell and CI job. A file groups them and validates them together. |
| Signing keys in `targets.<name>.xcuitest` | The config is committed and shareable, so it would leak team IDs. It would also repeat one value across every target although the runner is generic. |
| A signing file inside the project (`bajutsu.signing.yaml`) | A per-user file inside the repository can be committed by mistake and needs `.gitignore` and secret-scanner exceptions. A global file with an explicit override avoids both. |
| `xcodebuild PRODUCT_BUNDLE_IDENTIFIER=…` on the command line | One value overrides both targets, so the host and the test bundle collide. |
| `${VAR}` placeholders in the committed `project.yml` | The Simulator build and the unit tests would then depend on those variables being set or defaulted, and the committed spec would encode a per-user concern. |
| `sed` over `project.yml`, as BE-0288 does for `ExportOptions.plist` | Text substitution on a structured file breaks silently when the layout changes. Loading and overriding the parsed spec fails loudly. |
| Build implicitly when the cache is empty | A signing build can prompt for the Keychain and touch Apple's servers mid-run. A pooled run would hide that behind a slow first scenario. |
| Ship only prebuilt device runners | A prebuilt runner carries one team's signature and cannot install under another. |
| Source checkout only, no wheel sources | Rejected during design review: a wheel-only user could not build a device runner at all. Revisit if the shipped sources prove too large or hard to keep in step. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike: on one real device, find which profiles a manual build needs (host, the `.xctrunner`
  app, or one wildcard), confirm the `.xctrunner` identifier, and confirm the staged-manifest and
  generated-spec approach builds.
- [x] Signing file model, validation (prefix or explicit `bundleIds`), and lookup order, with tests.
- [x] Source staging: `make runner-source`, the `artifacts` entry, and the staged manifest.
- [x] Project spec override, build command construction, preflight, and cache, with tests.
- [x] `bajutsu runner build --device` command.
- [x] Run-time resolution tier in the XCUITest environment, with the missing-build error.
- [x] `demos/showcase/Makefile` wrapper for `runner-build-device`.
- [x] Docs in both languages: `docs/ios-device-cloud.md`, `docs/devicefarm.md`, `docs/cli.md`,
  `docs/configuration.md`, plus the module list in `docs/architecture.md`.
- [ ] Manual real-device proof (needs an Apple Developer account and a device).

Log:

- 2026-10-08 — Landed units 2–8: the signing file and its lookup order, source staging
  (`make runner-source` and the wheel `artifacts` entry), the per-user project spec, the build with
  its preflight and content-keyed cache, `bajutsu runner build --device`, the run-time resolution
  tier, the showcase `runner-build-device` wrapper, and the docs in both languages. The spike and
  the real-device proof stay open, because both need an Apple Developer account and a device.
  Deviations from the design, against today's tree:
  - The cache root helper the design names, `_runner_cache_root()`, did not exist. The shared root
    is now `bajutsu_cache_root()` in `_bundled_runner.py`, and the Simulator cache builds on it.
  - `_HASH_SOURCE_PATHS` and `_repo_root()` became public (`HASH_SOURCE_PATHS`, `repo_root()`), since
    staging and `make runner-source` now share them.
  - A `bajutsu runner build --device` cache hit whose embedded profile has expired rebuilds in
    place, as with `--force`. A run still fails on that same state and names the `--force` command.
  - `bajutsu doctor` names the signing file a device target would use, without keying the cache:
    the key needs `xcodebuild -version` and, for manual signing, `security`.
  - The `runner` command group needed its own help-panel classification, because the panel
    grouping previously walked plain commands alone.

## References

- [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md):
  the demo-level device signing build this item generalizes.
- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md): the bundled
  Simulator runner and the explicit-`testRunner` rule for devices.
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md): real-device
  XCUITest targeting.
- [iOS on a real device and in a device cloud](../../docs/ios-device-cloud.md).
- `BajutsuKit/Runner/project.yml`, `Package.swift`, `demos/showcase/Makefile`,
  `bajutsu/common/platform_lifecycle/environments/_bundled_runner.py`.

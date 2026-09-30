**English** · [日本語](BE-XXXX-target-config-restructure-ja.md)

# BE-XXXX — Reorganize target settings by purpose, and declare each target's runtime environment

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-target-config-restructure.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | — |
| Topic | Driver & backend architecture |
| Related | [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md), [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md), [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md), [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md) |
<!-- /BE-METADATA -->

## Introduction

A target under `targets.<name>` is a flat list of about fifty keys today. Keys for every platform
sit side by side: `browser` and `deviceMode` are read by the web backend alone, `nativeZ` by
Android alone, and `bundleId` and `xcuitest` by iOS alone. The schema does not know which keys
belong to which platform, and no key can state which device or operating system (OS) a target
expects.

This item replaces the flat list with twelve keys. Each key covers one purpose. For example, `app`
covers the app under test, `runsOn` the device it runs on, and `driver` how Bajutsu drives it.
The value of `platform` decides the shape of four of them — `app`, `runsOn`, `driver`, and `run`,
which gains one field on iOS — and the other eight keep one shape across platforms. `runsOn` declares the device, OS, and browser a
target runs on. Before the first step, a run checks the declaration against the device it got and
stops when the two disagree. The change drops backward compatibility on purpose: an old config
fails to load and names the key it no longer accepts.

## Motivation

A key that belongs to another platform loads without complaint and does nothing. An iOS target
with `browser: firefox` passes validation, and the run ignores the setting.
[`docs/configuration.md`](../../docs/configuration.md) patches over the gap by repeating "iOS
ignores it" in its key table. A reader has to look up that table to know whether a key takes effect.

The shared namespace also forces awkward names. `device` in
[`defaults.py`](../../bajutsu/common/config/schema/defaults.py) is an iOS Simulator model, yet as a
default it overlays every web and Android target too. The web backend's `deviceMode` got a
different name because `device` was already taken.

No key says where a target is meant to run. The Simulator chosen by `--udid` decides the iOS
version, and the adb serial decides the Android application programming interface (API) level. `device` takes effect in one case alone:
creating a replacement for a Simulator that vanished mid-run. A run records the OS it observed as
`device_runtime` ([BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md)).
A scenario that fails on an unintended OS therefore surfaces after the run, not before it, and the
result then lands in the flakiness history as noise.

Settings for one purpose are scattered, too. Launching the app spans `launchEnv`, `launchArgs`,
and `readyWhen`. Sourcing a device spans `deviceProvider`, `cloudBatch`, and `requires`. Steps that
run before every scenario come from two keys, `setup` and `before`, whose difference the docs keep
explaining. Deciding the platform takes a precedence chain over `platform`, `backend`, and whichever
identifier is present (`_effective_platform` in
[`resolve.py`](../../bajutsu/common/config/resolve.py)). Adding a backend such as Flutter would add
more flat keys and another branch in that chain.

Once this item ships, a reader can check two outcomes. A key written under the wrong platform fails
at config load, naming the platform's own fields. A target whose `runsOn` does not match the chosen
device stops before its first step, printing the declared and the observed values side by side.

## Detailed design

### The twelve keys

A target accepts these keys and no others.

| Key | Purpose | Shape depends on `platform` |
|---|---|---|
| `platform` | Which backend drives the target | — (the discriminator) |
| `app` | What is under test: identifier, how to obtain and launch it, readiness, and id contract | Yes |
| `runsOn` | What the target runs on: device and OS requirements, browser, and locale | Yes |
| `driver` | How Bajutsu drives the target | Yes |
| `dispatch` | Where devices come from, and where runs are sent | No |
| `services` | Which stand-in services the app talks to | No |
| `run` | The policy of a run | iOS adds one field |
| `hooks` | Steps that wrap every scenario | No |
| `evidence` | What a run keeps, and what it masks | No |
| `paths` | Where files live | No |
| `ai` | Which provider backs the AI paths | No |
| `notify` | Where results are reported | No |

A key goes into the group for its purpose, whatever platform uses it. `nativeZ`, for example, is
Android-specific, yet it answers how Bajutsu drives the device, so it lives in `driver`.

```yaml
targets:
  showcase-swiftui:
    platform: ios
    app:
      id: com.bajutsu.showcase.ios.swiftui
      path: build/Showcase.app
      build: make -C demos/showcase swiftui-build
      launch: { env: { SHOWCASE_UITEST: "1" } }
      readyWhen: { id: home.title }
    runsOn:   { model: iPhone 15, os: ">=17 <19", kind: simulator, locale: en_US }
    driver:   { runner: { testRunner: build/Runner.xctestrun } }
    dispatch: { live: { kind: local }, batch: { kind: devicefarm, budget: 2 } }
    run:      { erase: true, secrets: [LOGIN_PASSWORD], tipKitHandling: true }
    hooks:    { before: [{ use: login }] }
    paths:    { scenarios: demos/showcase/scenarios }

  site:
    platform: web
    app:    { url: "http://127.0.0.1:8787/index.html" }
    runsOn: { browser: { engine: webkit, version: ">=18" }, emulate: iPhone 13, host: { os: macos } }
    driver: { headless: false }

  showcase-android:
    platform: android
    app:    { id: com.example.showcase, grantPermissions: [android.permission.POST_NOTIFICATIONS] }
    runsOn: { avd: Pixel_8, apiLevel: ">=33" }
    driver: { nativeZ: true }
```

The same `site` target in today's notation reads as follows:

```yaml
  site:
    platform: web
    backend: [web]
    baseUrl: "http://127.0.0.1:8787/index.html"
    browser: webkit
    deviceMode: "iPhone 13"
    headless: false
```

### Platform-shaped groups

| Group | iOS | Android | Web |
|---|---|---|---|
| `app` | `id`, `path`, `build`, `deeplink`, `launch.env`, `launch.args` | `id`, `path`, `build`, `grantPermissions` | `url`, `server` |
| `app`, all platforms | `readyWhen`, `idNamespaces` | same | same |
| `runsOn` | `model`, `os`, `kind`, `locale` | `avd`, `apiLevel` | `browser.engine`, `browser.version`, `emulate`, `host.os` |
| `driver` | `runner.testRunner`, `runner.build` | `nativeZ` | `headless` |
| `run`, extra field | `tipKitHandling` | — | — |

The test-only `fake` platform registers minimal models. Its `app` takes an optional `id` and
nothing else, and its `runsOn` and `driver` take no fields, so a `fake` target declares no
requirement to check. A `fake` target still resolves to the iOS-shaped `Effective`, as it does today.

Four placements needed a judgment call:

- **`locale` sits in `runsOn`.** On iOS it pins the Simulator's own system language
  ([BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)),
  which makes it device state rather than a launch argument.
- **The web browser and `emulate` sit in `runsOn`.** For a web target the browser is what the
  target runs on. `headless` sits in `driver`: a headed run changes how Bajutsu shows the browser,
  not what the target runs on.
- **`secrets` sits in `run`.** A secret is an input injected as `${secrets.X}`; masking the value in
  evidence follows from that role.
- **`setup` is removed and folded into `hooks.before`.** Two keys serving one purpose is the
  duplication this item removes. The cost: steps that `setup` used to splice onto a scenario's
  `steps` now run as the report's own `before` phase, and a failure there counts as a `before`
  failure.

### A registry of platform schemas

Each backend registers the models for its `app`, `runsOn`, `driver`, and `run` extension in a
registry under `bajutsu/common/config/schema/platform/`. `TargetConfig` reads `platform`, looks up
the registry, and validates the four groups with the registered models. Every model forbids extra
keys, so a key from another platform fails as unknown. Pydantic's own error names the unknown
key but not the allowed ones, so `TargetConfig` rewrites that error into one that lists the model's
fields, and unit 3 pins the message in a test. The registry keeps config loading free of
Playwright and simctl imports, the same property that lets `deviceMode` resolve lazily today. The
core stops naming platforms in its schema, so adding Flutter means registering one more entry.

Explicit `platform` replaces the precedence chain. `backend` goes away: every platform has a single
actuator today, so the ordered fallback list has nothing to choose between. `_effective_platform`,
`_PLATFORM_IDENTIFIER`, and the cross-check in `Config` go with it, since each `app` model makes its
own identifier required. If a platform gains a second actuator later, `driver` gains an `actuator`
field.

`bajutsu config schema` prints a JavaScript Object Notation (JSON) Schema generated from the registry, with `platform` as the
discriminator of a `oneOf`, so an editor can complete keys per platform.

### Checking `runsOn` against the device

`runsOn` declares requirements. It never creates a device. After the environment resolves a device
or browser, and before the first step, the run compares each declared value with an observed one:

| Declared | Observed from | Comparison |
|---|---|---|
| iOS `runsOn.os` | the Simulator's runtime label, parsed by `DeviceOS` | version range on `major.minor` |
| iOS `runsOn.model` | the simctl device-type name of the chosen udid | exact |
| Android `runsOn.apiLevel` | `ro.build.version.sdk` | integer range |
| Android `runsOn.avd` | the emulator's Android Virtual Device (AVD) name | exact; a physical device never matches |
| Web `runsOn.browser.version` | Playwright's `browser.version` | version range |
| Web `runsOn.host.os` | `platform.system()`, normalized (`Darwin` → `macos`, `Linux` → `linux`, `Windows` → `windows`) | exact |

A version range is a conjunction of comparators: `>=`, `>`, `<=`, `<`, `==`, or a bare version. A
bare `18` means any 18.x release. `==` compares after zero-padding, so `==18` matches 18.0 alone;
the bare form is the one that covers the whole 18.x window. A new `VersionSpec` in `bajutsu/common/devices/version.py` parses
and compares ranges; `DeviceOS` keeps its deliberate lack of comparison operators, because this item
adds declaration checks, not per-OS branching. A mismatch raises `RunsOnRequirementError`, a new
subclass of `DeviceError`
([BE-0260](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md)), so `run`
exits non-zero on the same path as a missing device. `bajutsu doctor` runs the same check as
information when it can resolve a device.

The comparison is deterministic and involves no model call, so it adds nothing to the verdict path
beyond one more machine check. Creating a matching device on demand, or warning and continuing,
stays out of scope (see *Alternatives considered*).

### Defaults

`defaults` holds a target's keys other than `platform`. The shared groups go directly under
`defaults`. The platform-shaped groups (`app`, `runsOn`, `driver`, and the iOS field of `run`) go
under `defaults.platforms.<platform>`, which applies to targets of that platform alone, so one file
can hold defaults for several platforms at once. Dictionaries merge key by key, with the target
winning. Lists replace, except `evidence.redact` and `dispatch.requires`, which keep today's union.
`ai` keeps today's field-by-field merge.

The built-in `device: "iPhone 15"` default goes away. Kept as a `runsOn.model` default, the value
would turn into a requirement that every config without `defaults` silently enforces. When a
replacement Simulator is created with no declared `model`, the existing fallback picks the newest
iPhone, as it does today.

### Where each old key goes

| Old key | New location |
|---|---|
| `backend` | removed; `platform` decides the actuator |
| `bundleId`, `package` / `baseUrl`, `launchServer` | `app.id` / `app.url`, `app.server` |
| `appPath`, `build`, `deeplinkScheme`, `launchEnv`, `launchArgs` | `app.path`, `app.build`, `app.deeplink`, `app.launch.env`, `app.launch.args` |
| `readyWhen`, `idNamespaces`, `grantPermissions` | `app.readyWhen`, `app.idNamespaces`, `app.grantPermissions` |
| `device`, `locale`, `xcuitest.deviceType` | `runsOn.model`, `runsOn.locale`, `runsOn.kind` |
| `browser`, `deviceMode` | `runsOn.browser.engine`, `runsOn.emulate` (omitted means desktop) |
| `headless`, `nativeZ`, `xcuitest.testRunner`, `xcuitest.build` | `driver.headless`, `driver.nativeZ`, `driver.runner.*` |
| `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, `requires` | `dispatch.live`, `dispatch.batch.kind`, `dispatch.batch.budget`, `dispatch.requires` |
| `mockServer`, `mailbox` | `services.*` |
| `erase`, `network`, `visualCompare`, `secrets`, `systemAlertHandling`, `iosTipKitHandling` | `run.*` (the last as `run.tipKitHandling`) |
| `setup`, `before`, `after`, `interrupts` | `hooks.before` (absorbing `setup`), `hooks.before`, `hooks.after`, `hooks.interrupts` |
| `capture`, `redact` | `evidence.*` |
| `scenarios`, `baselines`, `schemas`, `goldens` | `paths.*` |
| `defaults.reservedNamespaces`, `defaults.doctor` | top-level `reservedNamespaces` and `doctor`; both are team-wide, not per target, so neither belongs in `defaults` |

The resolved `Effective` keeps its attribute names. Renaming them would touch several hundred call
sites and can proceed apart from the config's shape, so `resolve` builds today's `Effective` from
the new dictionary.

### Out of scope

- Reading the old keys, and a migration command.
- One target spanning several platforms; a multi-target scenario still covers that case.
- Registering a platform from an external package through an entry point.
- Recording the declared range in the run manifest, which keeps recording the observed OS.
- Picking a browser version or host OS outside what Playwright launched.
- Deriving `dispatch.requires` tags from `runsOn`; the mapping depends on how hosted workers
  advertise capabilities, which this item does not change.

### Work breakdown

1. **`VersionSpec`.** Parse and compare version ranges in `bajutsu/common/devices/version.py`.
2. **Confirm the open placements.** Find which backends read `deeplinkScheme`, `launchEnv`,
   `launchArgs`, and `locale`; confirm that the AVD name is readable (for example through
   `ro.boot.qemu.avd_name`) across supported API levels; and check whether any scenario depends on
   `setup` splicing onto `steps`. Update the placement tables before code lands.
3. **Platform registry and models.** Add the registry and the per-platform models, and pin in a test
   that the registry's keys equal `backends.PLATFORMS`.
4. **Schema switch-over.** Replace `TargetConfig`, `Defaults`, `Config`, and `resolve`, and convert
   the test fixtures and the ten `demos/` configs in one change. Splitting the switch-over would
   leave the gate red between commits, because the loader and every config break together.
5. **Fold `setup` into `hooks.before`.** Remove the `setup` path from the runner.
6. **Command-line interface (CLI).** `--backend` becomes a check that exits 2 on a mismatch with `platform`. `--browser` and
   `--headed` override `runsOn.browser.engine` and `driver.headless`.
7. **iOS requirement check** for `os` and `model`, with `RunsOnRequirementError`.
8. **Android requirement check** for `apiLevel` and `avd`.
9. **Web requirement check** for `browser.version` and `host.os`.
10. **`doctor`.** Report requirement mismatches as information when a device resolves.
11. **`bajutsu config schema`.**
12. **Docs.** Update `docs/configuration.md`, `docs/drivers.md`, `docs/cli.md`,
    `docs/architecture.md`, `DESIGN.md`, `docs/glossary.md`, and their `docs/ja/` mirrors.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Keep the flat list and reject keys from another platform | Catches typos, yet leaves the `device` name clash, adds no place for requirements, and keeps the settings for one purpose scattered |
| Make the platform name the key (`ios: {…}`, exactly one) | Nests the same word twice, as in `targets.web.web:`. A variable key name also forces the JSON Schema to express "exactly one" through a separate rule |
| Pair `platform` with a `configuration` block holding every platform-specific key | Groups keys by which platform uses them rather than by purpose, so app, device, and driver settings mix inside one block, one level deeper |
| A `driver: { kind: xcuitest \| adb \| playwright }` union | Each platform has one actuator today, so a second layer buys nothing. Revisit if a platform gains a second actuator |
| A closed union of platform models in the core | Every new backend would edit the core schema, which contradicts the backend-agnostic design |
| One shared `runsOn` shape for all platforms | Leaves fields no platform can use — a browser on iOS, an AVD on the web — which recreates the flat-list problem |
| Keep both `setup` and `before` | Preserves today's behavior, at the cost of two keys serving one purpose |
| Create a matching device on mismatch, or warn and continue | Creating a device makes Bajutsu own runtime installation, time, and cleanup. Warning lets results from the wrong environment into the flakiness history |
| Prefix matching with no ranges | Cannot express a compatibility window such as `>=17 <19` on one line |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: `VersionSpec`
- [ ] Unit 2: confirm the open placements
- [ ] Unit 3: platform registry and models
- [ ] Unit 4: schema switch-over, fixtures, and `demos/` configs
- [ ] Unit 5: fold `setup` into `hooks.before`
- [ ] Unit 6: CLI
- [ ] Unit 7: iOS requirement check
- [ ] Unit 8: Android requirement check
- [ ] Unit 9: web requirement check
- [ ] Unit 10: `doctor`
- [ ] Unit 11: `bajutsu config schema`
- [ ] Unit 12: docs

## References

- [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md): the per-platform split of the resolved `Effective`.
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md): `DeviceOS` and the recorded `device_runtime`.
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) and [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md): `deviceMode` and `browser`, which move into `runsOn`.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the `before` and `after` phases that `hooks` holds.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): `deviceProvider`, which becomes `dispatch.live`.
- `bajutsu/common/config/schema/target_config.py` and `bajutsu/common/config/resolve.py`: the schema and resolution this item replaces.

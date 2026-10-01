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
| Related | [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md), [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md), [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md), [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md), [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) |
<!-- /BE-METADATA -->

## Introduction

A target under `targets.<name>` is a flat list of about fifty keys today. Keys for every platform
sit side by side: `browser` and `deviceMode` are read by the web backend alone, `nativeZ` by
Android alone, and `bundleId` and `xcuitest` by iOS alone. The schema does not know which keys
belong to which platform, and no key can state which device or operating system (OS) a target
expects.

This item replaces the flat list with eleven keys. Each key covers one purpose. For example, `app`
covers the app under test, `runsOn` the device it runs on, and `driver` how Bajutsu drives it.
The value of `platform` decides the shape of four of them — `app`, `runsOn`, `driver`, and `run`,
which gains one field on iOS — and the other seven keep one shape across platforms. `runsOn` states the environments a target runs in:
device, OS, and browser. A scenario can state its own `runsOn` under `preconditions`, and the
scenario's value takes precedence over the target's. Bajutsu runs each scenario on an available
device that meets the merged conditions. A scenario that no available device meets is recorded as
not applicable instead of being run. The change drops backward compatibility on purpose: an old config
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

Nor can a scenario say where it applies. Take a scenario that exercises an iOS 18 feature, or a
screen that exists on iPad alone. Today the way to keep it off other devices is to split the target
into `showcase-iphone` and `showcase-ipad`, or to keep separate scenario lists by hand. Running one
suite across iOS 17 and iOS 18 then means choosing the scenarios for each run.

Settings for one purpose are scattered, too. Launching the app spans `launchEnv`, `launchArgs`,
and `readyWhen`. Where a run happens spans `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, and `requires`, although
that choice belongs to whoever operates the machines, not to the team that writes the target. Steps that
run before every scenario come from two keys, `setup` and `before`, whose difference the docs keep
explaining. Deciding the platform takes a precedence chain over `platform`, `backend`, and whichever
identifier is present (`_effective_platform` in
[`resolve.py`](../../bajutsu/common/config/resolve.py)). Adding a backend such as Flutter would add
more flat keys and another branch in that chain.

Once this item ships, a reader can check two outcomes. A key written under the wrong platform fails
at config load, naming the platform's own fields. A suite run over a device pool that
mixes iOS 17 and iOS 18 puts each scenario on a device its `runsOn` allows. A scenario with no
allowed device is reported as not applicable, with the unmet condition named.

## Detailed design

### The eleven keys

A target accepts these keys and no others.

| Key | Purpose | Shape depends on `platform` |
|---|---|---|
| `platform` | Which backend drives the target | — (the discriminator) |
| `app` | What is under test: identifier, how to obtain and launch it, readiness, and id contract | Yes |
| `runsOn` | What the target runs on: device and OS requirements, browser, and locale | Yes |
| `driver` | How Bajutsu drives the target | Yes |
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
    run:      { erase: true, secrets: [LOGIN_PASSWORD], tipKitHandling: true }
    hooks:    { before: [{ use: login }] }
    paths:    { scenarios: demos/showcase/scenarios }

  site:
    platform: web
    app:    { url: "http://127.0.0.1:8787/index.html" }
    runsOn: { browser: { engine: webkit, version: ">=18" }, emulate: iPhone 13 }
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
| `runsOn` | `model`, `os`, `kind`, `locale` | `avd`, `apiLevel` | `browser.engine`, `browser.version`, `emulate` |
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

### A scenario's own `runsOn`

A scenario declares its own conditions under `preconditions.runsOn`, keyed by platform. Each block
goes through the same registered `runsOn` model as the target's, so a misspelled field fails at
load.

```yaml
# a scenario for a screen that iPad alone has
preconditions:
  runsOn:
    ios: { model: "iPad Pro 13-inch (M4)", os: ">=18" }
```

```yaml
# one scenario run against an iOS target and against an Android target
preconditions:
  runsOn:
    ios:     { os: ">=18" }
    android: { apiLevel: ">=34" }
```

```yaml
# a multi-target scenario: showcase runs on iOS, site on the web
targets: [showcase, site]
preconditions:
  runsOn:
    ios: { os: ">=18" }
    web: { browser: { version: ">=120" } }
```

For each target a scenario drives, the effective `runsOn` starts from the target's `runsOn`. The
scenario's block for that target's platform then overrides it field by field, so the scenario wins.
A block for a platform the run does not drive has no effect, which lets one scenario serve several
platforms. Two targets of one platform on different devices share the block; a scenario that needs
different conditions for them splits into two scenarios.

A scenario block accepts the condition fields (`model`, `os`, `avd`, `apiLevel`,
`browser.engine`, and `browser.version`) and `locale`. `locale` replaces today's
`preconditions.locale`, so both levels keep it in one place. `kind` and `emulate` stay on the
target.
[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) keeps the
device mode a property of how a target is driven; a scenario that needs both faces runs under two
targets.

### Choosing where a scenario runs

`runsOn` describes the environments a scenario runs in. It never creates a device. Bajutsu reads
each available device, which means every udid of a `--udid` pool, or the browser a web lane
launched. It then compares the device with a scenario's effective `runsOn`:

| Condition | Read from | Comparison |
|---|---|---|
| iOS `runsOn.os` | the Simulator's runtime label, parsed by `DeviceOS` | version range on `major.minor` |
| iOS `runsOn.model` | the simctl device-type name of the udid | exact |
| Android `runsOn.apiLevel` | `ro.build.version.sdk` | integer range |
| Android `runsOn.avd` | the emulator's Android Virtual Device (AVD) name | exact; a physical device never matches |
| Web `runsOn.browser.version` | Playwright's `browser.version` | version range |

The runner hands each run a device that meets its conditions; *How many times a scenario runs*
below says how runs are formed and which device a run takes. The assignment is deterministic. A run
that no available device meets does not run. The run
records it with a new **not applicable** status and a reason that names the unmet field and the
devices it saw:

```
not applicable: showcase/ipad-split-view
  runsOn.ios.model  "iPad Pro 13-inch (M4)"  available: "iPhone 15" (5A3F...), "iPhone 16" (7B21...)
```

A not-applicable scenario is neither a pass nor a failure. The report lists it apart, and the
flakiness history leaves it out. When every selected scenario is not applicable, the run exits
non-zero with "no scenario ran", so an empty run never reports green. [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) rejects a skipped
status because a misconfigured worker would skip everything and still pass; this guard answers that
concern. `bajutsu doctor` lists, for each scenario, the available devices that meet it.

A version range uses npm's [node-semver range grammar](https://github.com/npm/node-semver#ranges) in full, rather than a notation of Bajutsu's own. Many teams
already write it, and adopting all of it leaves nothing Bajutsu-specific to explain:

| Form | Example | Meaning |
|---|---|---|
| comparators, space-separated for "and" | `>=17 <19` | 17.0 up to, not including, 19 |
| x-range or partial version | `18`, `18.x` | any 18.x release |
| hyphen range | `33 - 35` | 33 through 35 inclusive |
| caret | `^17` | at least 17, below 18 |
| tilde | `~17.4` | at least 17.4, below 17.5 |
| union | `17 \|\| 19` | 17 or 19 |

Versions compare on their first three components. A shorter version, such as iOS `18.2` or the
Android API level `34`, gains zeros. A longer one, such as Chrome's `130.0.6723.31`, drops the rest;
runs are formed per major version, so a dropped fourth component never changes which runs happen. A
new `VersionSpec` in `bajutsu/common/devices/version.py` parses and compares ranges. Unit 1 either
adopts a maintained Python implementation of the grammar or implements it, and pins npm's own
examples in tests. `DeviceOS` keeps its deliberate lack
of comparison operators, because this item selects devices by declaration and adds no per-OS
branching.

The comparison and the assignment are deterministic and involve no model call. Creating a matching
device on demand stays out of scope (see *Alternatives considered*).

### How many times a scenario runs

Two kinds of field multiply runs, and nothing else does.

- **A list.** One field per platform takes a list: `model` on iOS, `avd` on Android, and
  `browser.engine` on the web. The scenario runs once per listed value.
- **A version range.** `os` on iOS, `apiLevel` on Android, and `browser.version` on the web run once
  per major version the range covers. On Android each API level counts as one major version.

```yaml
preconditions:
  runsOn:
    ios:
      model: ["iPhone SE (3rd generation)", "iPad Pro 13-inch (M4)"]
      os: ">=17 <19"
```

This block makes four runs, one for each model on iOS 17 and on iOS 18. The report shows a matrix
of those runs against scenarios, the same shape `--browsers` already produces ([BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)):

```
                  SE / iOS 17   SE / iOS 18   iPad Pro / iOS 17   iPad Pro / iOS 18
login             pass          pass          pass                pass
split-view        n/a           n/a           n/a                 pass
```

The major versions a range covers come from two sources:

| Range | Major versions run |
|---|---|
| bounded above, such as `>=17 <19` | every major version in the range, 17 and 18 here; a major with no available device is not applicable |
| open above, such as `>=17` | the major versions the available devices have within the range, since an open range cannot be listed |
| a partial version or x-range, such as `18`, `18.x`, or `~18.2` | that one major version |
| a union, such as `17 \|\| 19` | the major versions of each side, 17 and 19 here |

Minor and patch releases never add runs. Within one major version, a run takes the available device
with the newest release that still falls in the range, and pool order breaks a tie. A range such as
`>=17.4 <19` therefore runs on 17 (17.4 or later) and on 18. The web backend has one version per
engine, so a `browser.version` range yields one run in practice, and an engine list never yields
not applicable because the backend installs a missing engine on demand.

A multi-target scenario runs every combination of its targets' runs. The runner schedules each run
like a scenario of its own, so `--workers` spreads them over the pool. The flakiness history keys a
verdict by scenario, value, and major version, so a layout failure on one model never reads as a
flaky scenario. `--browser` and `--browsers` keep their meaning and override the web list for one
run: the flag wins over the scenario, and the scenario wins over the target.

### Where a run happens

A target no longer says where it runs. The target states what is under test, what it runs on, and
how Bajutsu drives it. Where the run happens is a fact about the machines, which their operator
knows. [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) draws the same line when it keeps the worker capability file, `worker.yaml`, out of
`bajutsu.config.yaml`. Four of today's keys leave the target as a result:

| Old key | Where it goes | Why |
|---|---|---|
| `cloudBatchBudget` | `maxJobConcurrency` in `worker.yaml` | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) already replaces it, because the worker that reserves the device counts the budget |
| `requires` | removed | [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) abolishes free-form routing tokens; the worker advertises what its inventory has |
| `cloudBatch` | an `environment` on the run request (the serve fan-out request, and the matching CLI option) | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) runs the same target locally or on Device Farm, so the destination is a choice per run, not a property of the target |
| `deviceProvider` | an `appium` environment in `worker.yaml`, carrying the grid's `endpoint` | Which grid serves the devices is infrastructure, like a device cloud |

Two changes outside this item's schema follow. The serve fan-out request gains an `environment`
field, which feeds the `environment:<name>` routing of [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md). `worker.yaml` accepts `appium` as an
environment with an `endpoint`, which widens [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md)'s environment vocabulary beyond batch providers.
Unit 14 coordinates both with those items.

`runsOn` also supplies what [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) leaves open. With `requires` gone, a job cannot require an iOS
runtime or a device class until a later item derives that requirement. The derivation reads each
scenario's effective `runsOn`, which merges the target's and the scenario's `os` and `model`.

### Defaults

`defaults` holds a target's keys other than `platform`. The shared groups go directly under
`defaults`. The platform-shaped groups (`app`, `runsOn`, `driver`, and the iOS field of `run`) go
under `defaults.platforms.<platform>`, which applies to targets of that platform alone, so one file
can hold defaults for several platforms at once. Dictionaries merge key by key, with the target
winning. Lists replace, except `evidence.redact`, which keeps today's union.
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
| `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, `requires` | removed from the target (see *Where a run happens*) |
| `mockServer`, `mailbox` | `services.*` |
| `erase`, `network`, `visualCompare`, `secrets`, `systemAlertHandling`, `iosTipKitHandling` | `run.*` (the last as `run.tipKitHandling`) |
| `setup`, `before`, `after`, `interrupts` | `hooks.before` (absorbing `setup`), `hooks.before`, `hooks.after`, `hooks.interrupts` |
| `capture`, `redact` | `evidence.*` |
| scenario `preconditions.locale` | scenario `preconditions.runsOn.<platform>.locale` |
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
- Picking a browser version outside what Playwright launched.
- Declaring the host OS in the target. BE-0450 reads the host from the machine, lets each driver
  state which hosts it runs on, and routes on `host:<os>`, so a target-side declaration would be a
  second source for the same fact.
- Deriving a routing requirement from `runsOn`. A range such as `>=17 <19` cannot be expressed by
  the all-of token match that routing uses today, so the derivation stays with the later item that
  {B450} names.

### Work breakdown

1. **`VersionSpec`.** Parse npm range syntax and compare versions on three components in
   `bajutsu/common/devices/version.py`, and list the major versions a range covers.
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
6. **Command-line interface (CLI).** `--backend` becomes a check that exits 2 on a mismatch with `platform`. `--browser`,
   `--browsers`, and `--headed` override `runsOn.browser.engine` and `driver.headless`.
7. **Scenario `runsOn`.** Add `preconditions.runsOn` keyed by platform and validated by the
   registered models. Move `preconditions.locale` into it, and merge it over the target's `runsOn`.
8. **Reading devices.** Read the model and OS (iOS), the API level and AVD name (Android), and the
   browser version (web) from each available device.
9. **Assignment and the not-applicable status.** Hand each scenario a device that meets its
   effective `runsOn`. Record not applicable with a reason, keep it out of the flakiness history, and
   exit non-zero when nothing ran.
10. **Runs per value and per major version.** Accept a list for `model`, `avd`, and
    `browser.engine`. Run once per listed value and per major version a range covers, and run
    combinations across a multi-target scenario's targets. Print the value × scenario matrix, and
    key the flakiness history by scenario, value, and major version.
11. **`doctor`.** List the available devices that meet each scenario.
12. **`bajutsu config schema`.**
13. **Execution placement.** Remove `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, and
    `requires` from the target; add `environment` to the serve fan-out request and the CLI; and accept
    an `appium` environment with an `endpoint` in `worker.yaml`, coordinated with BE-0448 and BE-0450.
14. **Docs.** Update `docs/configuration.md`, `docs/drivers.md`, `docs/cli.md`,
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
| Keep `dispatch` (`deviceProvider`, `cloudBatch`) in the target | Duplicates what BE-0448 and BE-0450 move onto the worker and the run request, and makes the target team decide where a run happens |
| Keep both `setup` and `before` | Preserves today's behavior, at the cost of two keys serving one purpose |
| Fail the run when the device does not match | Running one suite across several OS versions would fail every OS-specific scenario on the versions it does not target |
| Create a matching device on demand | Bajutsu would own runtime installation, time, and cleanup |
| A range narrows to one device and runs once | Leaves no way to cover several OS versions in one run, which is the reason to write a range |
| Run every minor and patch release a range covers | Multiplies runs by releases that rarely differ in behavior; one run per major version keeps the count bounded |
| Treat a listed `model` as any one of the values | Matches the narrowing meaning of a range, yet cannot test several screen sizes, which is the reason to list models |
| A separate `matrix` key beside the narrowing fields | Tells the two meanings apart by key, at the cost of one more key; one list field per platform already leaves no ambiguity |
| Key a scenario's conditions by target name | The key's shape would change with whether `targets` is declared, and a scenario run against an iOS target and an Android target could not state both |
| List a scenario's condition fields flat | The file would not show which field applies to which platform, which brings back the flat config's written-but-ignored problem |
| A notation of Bajutsu's own (a subset of comparators) | Readers would have to learn which forms work; npm's grammar is already known and documented |
| PEP 440 specifiers (`>=17,<19`, `==18.*`) | Python's `packaging` handles them, including four-component versions, yet app teams rarely know them, and "the 18 line" needs `==18.*` |
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
- [ ] Unit 7: scenario `runsOn`
- [ ] Unit 8: reading devices
- [ ] Unit 9: assignment and the not-applicable status
- [ ] Unit 10: runs per value and per major version
- [ ] Unit 11: `doctor`
- [ ] Unit 12: `bajutsu config schema`
- [ ] Unit 13: execution placement
- [ ] Unit 14: docs

## References

- [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md): the per-platform split of the resolved `Effective`.
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md): `DeviceOS` and the recorded `device_runtime`.
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) and [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md): `deviceMode` and `browser`, which move into `runsOn`.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the `before` and `after` phases that `hooks` holds.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): `deviceProvider`, which moves out of the target into an `appium` environment of `worker.yaml`.
- `bajutsu/common/config/schema/target_config.py` and `bajutsu/common/config/resolve.py`: the schema and resolution this item replaces.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) and [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the worker capability file and the Device Farm worker, which take over where a run happens.

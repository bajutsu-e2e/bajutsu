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
The value of `platform` decides the shape of four of them: `app`, `runsOn`, `driver`, and `run`,
which gains fields on iOS. The other seven keep one shape across platforms.

`runsOn` states the environments a target runs in: device, OS, and browser. A scenario can state
its own `runsOn` under `preconditions`, and the scenario's value takes precedence over the
target's. A list of models, or a bounded version range, asks for one run per value or per major
version, and a run that no available device can take fails. A scenario whose open condition, such
as `>=18`, no available device meets is recorded as not applicable instead of being run. The change
drops backward compatibility on purpose: an old config fails to load and names the key it no longer
accepts.

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
version, and the adb serial decides the Android application programming interface (API) level.
`device` takes effect in one case alone: creating a replacement for a Simulator that vanished
mid-run. A run records the OS it observed as `device_runtime`
([BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md)).
A scenario that fails on an unintended OS therefore surfaces after the run, not before it, and the
result then lands in the flakiness history as noise.

Nor can a scenario say where it applies, or ask to run on several screen sizes. Take a scenario
that exercises an iOS 18 feature, or a screen that exists on iPad alone. Today the way to keep it
off other devices is to split the target into `showcase-iphone` and `showcase-ipad`, or to keep
separate scenario lists by hand. Running one suite across iOS 17 and iOS 18, or across a small and
a large phone, then means choosing devices and scenarios for each run.

Settings for one purpose are scattered, too. Launching the app spans `launchEnv`, `launchArgs`,
and `readyWhen`. Where a run happens spans `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, and
`requires`, although that choice belongs to whoever operates the machines, not to the team that
writes the target. Steps that run before every scenario come from three keys, the target's `setup`,
the scenario's `preconditions.setup`, and `before`, whose differences the docs keep explaining.
Deciding the platform takes a precedence chain over `platform`, `backend`, and whichever identifier
is present (`_effective_platform` in [`resolve.py`](../../bajutsu/common/config/resolve.py)). Adding
a backend such as Flutter would add more flat keys and another branch in that chain.

Once this item ships, a reader can check three outcomes:

- A key written under the wrong platform fails at config load, naming the platform's own fields.
- A suite run over a pool that mixes iOS 17 and iOS 18 Simulators, with a scenario that lists two
  models and a range `>=17 <19`, prints a model × OS matrix with one run per cell.
- A misspelled model fails its run with "no available device for model …", rather than quietly
  dropping coverage.

## Detailed design

### Prerequisites

This item starts after [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md)
and [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) have landed. Those items
provide the worker capability file, `worker.yaml`, and the `environment:<name>` routing that the
keys leaving the target move onto (see *Where a run happens*). Starting earlier would leave
`deviceProvider` and `cloudBatch` with no destination at the atomic schema switch-over.

### The eleven keys

A target accepts these keys and no others. `platform` is required; `defaults.platform` and the
inference from `backend` or from the identifier present go away.

| Key | Purpose | Shape depends on `platform` |
|---|---|---|
| `platform` | Which backend drives the target | — (the discriminator) |
| `app` | What is under test: identifier, how to obtain and launch it, readiness, and id contract | Yes |
| `runsOn` | What the target runs on: device and OS conditions, browser, and device state | Yes |
| `driver` | How Bajutsu drives the target | Yes |
| `services` | Which servers the run starts or stands in for | No |
| `run` | The policy of a run | iOS adds fields |
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
      startWhen: { exists: { id: home.title } }
    runsOn:   { os: ">=17", kind: simulator, locale: en_US }
    driver:   { runner: { testRunner: build/Runner.xctestrun } }
    run:      { erase: true, secrets: [LOGIN_PASSWORD], tipKitHandling: true }
    hooks:    { setup: [{ use: login }] }
    paths:    { scenarios: demos/showcase/scenarios }

  site:
    platform: web
    app:      { url: "http://127.0.0.1:8787/index.html" }
    runsOn:   { browser: { engine: webkit }, emulate: iPhone 13 }
    driver:   { headless: false }
    services: { server: { cmd: "python -m http.server 8787", readyUrl: "http://127.0.0.1:8787/" } }

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
    launchServer: { cmd: "python -m http.server 8787", readyUrl: "http://127.0.0.1:8787/" }
    browser: webkit
    deviceMode: "iPhone 13"
    headless: false
```

Top-level `notify` stays at the top level. A target's `notify` overrides it, and `[]` turns it off
for that target, as today.

### Platform-shaped groups

| Group | iOS | Android | Web |
|---|---|---|---|
| `app` | `id`, `path`, `build`, `reinstall`, `launch.env`, `launch.args`, `launch.deeplink` | `id`, `path`, `build`, `reinstall`, `launch.env`, `launch.deeplink`, `grantPermissions` | `url`, `launch.env` |
| `app`, all platforms | `startWhen`, `idNamespaces` | same | same |
| `runsOn` | `model`, `os`, `kind`, `locale`, `seedPhotos` | `avd`, `apiLevel` | `browser.engine`, `browser.version`, `emulate` |
| `driver` | `runner.testRunner`, `runner.build` | `nativeZ` | `headless` |
| `run`, extra fields | `tipKitHandling` | — | — |

Every placement follows a reader found in today's code. Android reads `launchEnv` as intent extras
and opens a scenario's `deeplink`, and the web Playwright code generator reads `launchEnv`. iOS
alone reads `launchArgs` and `locale`. `runsOn.locale` keeps today's built-in `en_US` on iOS, which
the Simulator language pin of
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
depends on.

The test-only `fake` platform registers minimal models. Its `app` takes an optional `id` and
nothing else, and its `runsOn` and `driver` take no fields, so a `fake` target declares no
condition to check. A `fake` target still resolves to the iOS-shaped `Effective`, as it does today.

`startWhen` replaces `readyWhen`. It takes a condition in the form an `interrupts` entry already
uses for its `condition`, so the config has one way to say "this element is on screen":
`startWhen: { exists: { id: home.title } }`. Before the first step, the run waits until the
condition holds; the wait polls the condition and uses no fixed sleep. Leaving `startWhen` out keeps
today's readiness chain: screen transitions first, then `idNamespaces`, then the element count. This
item accepts `exists`, which is what today's readiness gate evaluates.

Four placements needed a judgment call:

- **`locale` and `seedPhotos` sit in `runsOn`.** On iOS, `locale` pins the Simulator's own system
  language, and `seedPhotos` fills its photo library. Both are device state, not launch arguments.
- **The web browser and `emulate` sit in `runsOn`.** For a web target the browser is what the
  target runs on. `headless` sits in `driver`: a headed run changes how Bajutsu shows the browser,
  not what the target runs on.
- **`secrets` sits in `run`.** A secret is an input injected as `${secrets.X}`; masking the value in
  evidence follows from that role.
- **`launchServer` becomes `services.server`.** Today it works on every platform once it has a
  readiness URL, so it belongs with the other servers a run starts or stands in for, not with the
  web app.

### Setup and cleanup

`hooks` holds `setup`, `cleanup`, and `interrupts`. `setup` and `cleanup` replace `before` and
`after`, in the target and in the scenario alike, so one pair of words names the phases at both
levels. The report labels the two phases `setup` and `cleanup`.

`cleanup` keeps the rules `after` has today. Each entry pairs `on` with steps, and `on` takes
`always`, `success`, or `failure`, which replaces `error`. Cleanup runs on every path out of a
scenario: after a failed setup, after a failed step, and after a cancelled run. A failing cleanup
entry does not stop the others, and its failure is appended behind an earlier failure, as today.

The two levels keep today's order. The target's `setup` runs before the scenario's, and the
target's `interrupts` are checked before the scenario's. The scenario's `cleanup` runs before the
target's, so a scenario releases what it created before the app-wide teardown closes around it.

The prelude files fold into `setup` as components. Today the target's `setup` and a scenario's
`preconditions.setup` each name a prelude scenario file, whose steps are spliced onto `steps`, and
the scenario's value replaces the target's. Both keys go away:

- A target's `hooks.setup` may call a component with `use:`. Components are resolved at config
  load for target hooks, which lifts today's rule that target hooks reject `use:`. `group:` and
  `setMocks` stay rejected there.
- A scenario calls a component with `use:` inside its own `setup`, as it can today.
- A scenario that must not run the target's setup sets `preconditions.run.inheritSetup: false`.
  That replaces today's override of the target's prelude by the scenario's.
- Each prelude file is rewritten once as a component file, since the two formats differ.

The cost: prelude steps used to splice onto `steps` and run as ordinary steps. They now run as the
report's setup phase, and a failure there counts as a setup failure.

### A registry of platform schemas

Each backend registers the models for its `app`, `runsOn`, `driver`, and `run` extension in a
registry under `bajutsu/common/config/schema/platform/`. `TargetConfig` reads `platform`, looks up
the registry, and validates the four groups with the registered models. Every model forbids extra
keys, so a key from another platform fails as unknown. Pydantic's own error names the unknown key
but not the allowed ones, so `TargetConfig` rewrites that error into one that lists the model's
fields, and unit 3 pins the message in a test. The registry keeps config loading free of
Playwright and simctl imports, the same property that lets `deviceMode` resolve lazily today. The
core stops naming platforms in its schema, so adding Flutter means registering one more entry.

Explicit `platform` replaces the precedence chain. `backend` goes away: every platform has a single
actuator today, so the ordered fallback list has nothing to choose between. `_effective_platform`,
`_PLATFORM_IDENTIFIER`, and the cross-check in `Config` go with it, since each `app` model makes its
own identifier required. If a platform gains a second actuator later, `driver` gains an `actuator`
field.

`bajutsu config schema` prints a JavaScript Object Notation (JSON) Schema generated from the
registry, with `platform` as the discriminator of a `oneOf`, so an editor can complete keys per
platform.

### A scenario's preconditions

A scenario's `preconditions` uses the target's group names, so a field has one spelling at both
levels. It holds three groups: `app`, `runsOn`, and `run`. A field written in a scenario overrides
the same field in the target, with two exceptions kept from today: `launch.env` merges key by key,
and `launch.args` appends the scenario's arguments after the target's.

```yaml
preconditions:
  app:
    reinstall: overwrite
    launch: { env: { FEATURE_X: "1" }, args: ["-debug"], deeplink: "showcase://cart" }
  runsOn:
    ios: { model: iPhone 16, os: ">=18", locale: ja_JP, seedPhotos: [photos/cat.jpg] }
  run: { erase: true, tipKitHandling: true }
```

| Today's scenario field | New location |
|---|---|
| `preconditions.launchEnv`, `launchArgs`, `deeplink` | `preconditions.app.launch.env`, `.args`, `.deeplink` |
| `preconditions.reinstall` | `preconditions.app.reinstall` |
| `preconditions.erase` | `preconditions.run.erase` |
| `preconditions.locale`, `seedPhotos` | `preconditions.runsOn.ios.locale`, `preconditions.runsOn.ios.seedPhotos` |
| `preconditions.setup` | removed (see *Setup and cleanup*) |
| top-level `systemAlertHandling`, `iosTipKitHandling` | `preconditions.run.systemAlertHandling`, `.tipKitHandling` |
| top-level `before`, `after` | top-level `setup`, `cleanup` |

The scenario's top-level `network` stays where it is. It filters which requests the report's
timeline shows, an evidence setting, while the target's `run.network` turns collection on or off.

`runsOn` alone is keyed by platform, because its fields differ by platform. The fields of
`app.launch` keep one meaning wherever they apply, so `app` is not keyed. When a scenario sets an
`app.launch` field that the platform of the target it runs against does not have, such as
`launch.args` on a web target, the run fails before any device work, naming the field and the
platform. It never ignores the field. A scenario cannot set a field that identifies or builds the
app, such as `id`, `path`, `build`, `startWhen`, or `idNamespaces`; the target owns those.

`seedPhotos` paths resolve against the file that declares them: the scenario file, or the config
file for a target-level value. Seeding still needs a wiped device, so the run checks after merging
that the effective `run.erase` is true; a target-level `erase: true` satisfies it.

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

A scenario block accepts the condition fields (`model`, `os`, `avd`, `apiLevel`, `browser.engine`,
and `browser.version`) and, on iOS, `locale` and `seedPhotos`. `kind` and `emulate` stay on the
target. [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) keeps
the device mode a property of how a target is driven; a scenario that needs both faces runs under
two targets.

### Matching devices

`runsOn` describes the environments a scenario runs in. It never creates a device. Bajutsu reads
each available device, which means every udid of a `--udid` pool, every emulator serial, or the
browser a web lane launched:

| Condition | Read from | Comparison |
|---|---|---|
| iOS `runsOn.os` | the Simulator's runtime label, parsed by `DeviceOS` | version range |
| iOS `runsOn.model` | the simctl device-type name of the udid | exact |
| Android `runsOn.apiLevel` | `ro.build.version.sdk` | version range on an integer |
| Android `runsOn.avd` | the emulator's Android Virtual Device (AVD) name | exact; a physical device never matches |
| Web `runsOn.browser.version` | Playwright's `browser.version` | version range |

Matching covers devices the run drives on its own host. A target whose `runsOn.kind` is `device`
(a physical iPhone), or a run that goes to a device cloud or an Appium grid, has no pool to read
before the run. Such a target may not declare `model`, `os`, or a list. The loader rejects them for
`kind: device`, and the run rejects them before any device work for a remote environment. Reading
facts off a reserved remote device is left to a later item.

A version range uses npm's [node-semver range grammar](https://github.com/npm/node-semver#ranges)
in full, rather than a notation of Bajutsu's own. Many teams already write it, and adopting all of
it leaves nothing Bajutsu-specific to explain:

| Form | Example | Meaning |
|---|---|---|
| comparators, space-separated for "and" | `>=17 <19` | 17.0 up to, not including, 19 |
| x-range or partial version | `18`, `18.x` | any 18.x release |
| hyphen range | `33 - 35` | 33 through 35 inclusive |
| caret | `^17` | at least 17, below 18 |
| tilde | `~17.4` | at least 17.4, below 17.5 |
| union | `17 \|\| 19` | 17 or 19 |

Every version compares on three components. A shorter version gains zeros: an iOS runtime label
carries major and minor, such as `18.2`, so its patch reads as zero, and an API level such as `34`
reads as `34.0.0`. A longer one, such as Chrome's `130.0.6723.31`, drops the rest. Prerelease tags
are not used: a range that names one is rejected at load, and a beta runtime compares by its
numbers alone. A new `VersionSpec` in `bajutsu/common/devices/version.py` parses ranges, compares
versions, and lists the major versions a range covers. Unit 1 either adopts a maintained Python
implementation of the grammar or implements it, and pins npm's own examples in tests. `DeviceOS`
keeps its deliberate lack of comparison operators, because this item selects devices by
declaration and adds no per-OS branching.

### Runs and their outcomes

A scenario's effective `runsOn` decides how many runs it makes, and what happens when a run finds
no device. Two kinds of field multiply runs, and nothing else does:

| Field | Runs | When no available device fits a run |
|---|---|---|
| a list on `model` (iOS), `avd` (Android), or `browser.engine` (web) | one per listed value | the run **fails**, naming the value |
| a range bounded above on `os` (iOS) or `apiLevel` (Android), such as `>=17 <19`, `18`, `^17`, or `17 \|\| 19` | one per major version the range covers, 17 and 18 for `>=17 <19` | the run **fails**, naming the major version |
| a range open above, such as `>=18` | one per major version the available devices have within the range | when no device is in range, one **not applicable** record |
| none of the above | one | the run **fails** when a narrowing value (a single `model`, `avd`) fits no device |

On Android each API level counts as one major version. A web `browser.version` range never
multiplies runs, because Playwright brings one version per engine. It narrows instead, and it
follows the same outcome rule by its shape. The web backend installs a missing engine on demand, so
an engine list never lacks a device. A multi-target scenario runs every combination of its targets'
runs.

```yaml
preconditions:
  runsOn:
    ios:
      model: ["iPhone SE (3rd generation)", "iPad Pro 13-inch (M4)"]
      os: ">=17 <19"
```

This block makes four runs, one for each model on iOS 17 and on iOS 18. The report shows a matrix
of those runs against scenarios, the same shape `--browsers` already produces
([BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)):

```
                  SE / iOS 17   SE / iOS 18   iPad Pro / iOS 17   iPad Pro / iOS 18
login             pass          pass          pass                pass
split-view        n/a           n/a           n/a                 pass
```

The split between failure and not applicable follows what the author asked for. A list or a bounded
range names runs that must happen, so a missing device, often a misspelled model, fails rather than
quietly dropping coverage. An open range such as `>=18` says where a scenario applies, so a pool
with no iOS 18 device makes it not applicable there. A target-level bounded range multiplies every
scenario of the target and fails each run a small pool cannot serve. Target-level ranges are
therefore usually open, as in the example above.

**Which device a run takes.** A run's eligible set is every available device that fits its
conditions and carries the newest release within its major version. A run with no version
condition uses every fitting device's newest release. Any idle member of the eligible set may take
the run, so `--workers` spreads runs over the pool. The set is deterministic, while the choice
inside it is not: two devices in one set share model and release, so the choice does not change
what the run observes. `--browser` and `--browsers` override the web list for one run: the flag wins
over the scenario, and the scenario wins over the target.

**Not applicable.** A not-applicable record is neither a pass nor a failure. Its reason names the
unmet condition and the devices the run saw:

```
not applicable: showcase/ipad-split-view
  runsOn.ios.os  ">=18"  available: iOS 17.5 "iPhone 15" (5A3F...), iOS 17.5 "iPhone 16" (7B21...)
```

The report lists it apart, and the flakiness history leaves it out. When no run executes at all,
the run exits non-zero with "no run executed", so an empty run never reports green. A serve job
whose every run is not applicable reports a failed job for the same reason. Together with the
failure of a missing listed value, this answers the concern of
[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): a misconfigured pool cannot
turn a suite green by skipping it.

**History and other commands.** The flakiness history keys a verdict by the scenario's content
fingerprint, the target, and the run's coordinates: the listed values and the major version per
target. A layout failure on one model therefore never reads as a flaky scenario. Today's history is
keyed by scenario alone, so histories start fresh under the new key. `record`, `crawl`, and `repl`
never multiply runs; they take the first run's coordinates, which are the first listed value and
the newest major version available. `bajutsu doctor` lists, for each scenario, its runs and the
available devices that fit each one. With no pool to read, it reports the declared runs alone.

The comparison, the run counts, and the eligible sets are deterministic and involve no model call.
Creating a matching device on demand stays out of scope (see *Alternatives considered*).

### Where a run happens

A target no longer says where it runs. The target states what is under test, what it runs on, and
how Bajutsu drives it. Where the run happens is a fact about the machines, which their operator
knows. [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) draws the same line when
it keeps `worker.yaml` out of `bajutsu.config.yaml`. Four of today's keys leave the target:

| Old key | Where it goes | Why |
|---|---|---|
| `cloudBatchBudget` | `maxJobConcurrency` in `worker.yaml` | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) replaces it, because the worker that reserves the device counts the budget |
| `cloudBatch` | an `environment` on the run request (the serve fan-out request, and the matching CLI option) | BE-0448 runs the same target locally or on Device Farm, so the destination is a choice per run, not a property of the target |
| `deviceProvider` | an `appium` environment in `worker.yaml`, carrying the grid's `endpoint` | Which grid serves the devices is infrastructure, like a device cloud |
| `requires` | removed | BE-0450 replaces free-form routing tokens with what a worker's inventory has |

Three changes outside this item's schema follow, and unit 13 coordinates them with BE-0448 and
BE-0450. The serve fan-out request and the CLI gain an `environment`, which feeds the
`environment:<name>` routing of BE-0448. `worker.yaml` accepts `appium` as an environment with an
`endpoint`, which widens BE-0450's environment vocabulary beyond batch providers. `bajutsu run
--worker-config` accepts that `appium` environment, so a plain local run can still reach a grid;
BE-0450 rejects every other non-local environment for `run`.

Removing `requires` has a cost that this item accepts. BE-0450 keeps `requires` until a later item
derives the iOS runtime and device-class requirement for routing. This item removes it with the new
schema, so until the derivation lands, a hosted job cannot ask for an iOS runtime or a device class.
The derivation reads each scenario's effective `runsOn`, the merge of the target's and the
scenario's `os` and `model`. A range such as `>=17 <19` cannot be expressed by the all-of token
match that routing uses today, so the derivation stays with that later item.

### Defaults

`defaults` holds a target's keys other than `platform`. The shared groups go directly under
`defaults`. The platform-shaped groups (`app`, `runsOn`, `driver`, and the iOS fields of `run`) go
under `defaults.platforms.<platform>`, which applies to targets of that platform alone, so one file
can hold defaults for several platforms at once. Dictionaries merge key by key, with the target
winning. Lists replace, except two that keep today's union: `evidence.redact` and `run.secrets`.
Replacing `run.secrets` would let a target that adds one secret stop masking every team-wide one.
`ai` keeps today's field-by-field merge.

The built-in `device: "iPhone 15"` default goes away. Kept as a `runsOn.model` default, the value
would turn into a requirement that every config without `defaults` silently enforces. When a
replacement Simulator is created with no declared `model`, the existing fallback picks the newest
iPhone, as it does today. The built-in `locale: en_US` stays, as `runsOn.locale` on iOS.

### Where each old key goes

| Old key | New location |
|---|---|
| `platform` | `platform`, now required on every target |
| `backend`, `defaults.platform` | removed; `platform` decides the actuator |
| `bundleId`, `package` / `baseUrl` | `app.id` / `app.url` |
| `appPath`, `build`, `launchEnv`, `launchArgs` | `app.path`, `app.build`, `app.launch.env`, `app.launch.args` |
| `deeplinkScheme` | removed; no code reads it today |
| `readyWhen`, `idNamespaces`, `grantPermissions` | `app.startWhen` (the selector moves under `exists`), `app.idNamespaces`, `app.grantPermissions` |
| `device` | `runsOn.model`; the meaning changes from a hint for a replacement Simulator to a condition every run must meet |
| `locale`, `xcuitest.deviceType` | `runsOn.locale`, `runsOn.kind` |
| `browser`, `deviceMode` | `runsOn.browser.engine`, `runsOn.emulate` (omitted means desktop) |
| `headless`, `nativeZ`, `xcuitest.testRunner`, `xcuitest.build` | `driver.headless`, `driver.nativeZ`, `driver.runner.*` |
| `launchServer`, `mockServer`, `mailbox` | `services.server`, `services.mockServer`, `services.mailbox` |
| `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, `requires` | removed from the target (see *Where a run happens*) |
| `erase`, `network`, `visualCompare`, `secrets`, `systemAlertHandling`, `iosTipKitHandling` | `run.*` (the last as `run.tipKitHandling`) |
| `setup`, `before`, `after`, `interrupts` | `hooks.setup` (the prelude becomes a component called with `use:`), `hooks.setup`, `hooks.cleanup` (`on: error` becomes `on: failure`), `hooks.interrupts` |
| `capture`, `redact` | `evidence.*` |
| `scenarios`, `baselines`, `schemas`, `goldens` | `paths.*` |
| `ai`, `notify` | unchanged |
| `defaults.reservedNamespaces`, `defaults.doctor` | top-level `reservedNamespaces` and `doctor`; both are team-wide, not per target |
| scenario fields | see *A scenario's preconditions* |

The resolved `Effective` keeps its attribute names. Renaming them would touch several hundred call
sites and can proceed apart from the config's shape, so `resolve` builds today's `Effective` from
the new dictionary. A few readers use the raw schema instead of `Effective`, such as
`serve/operations/reads.py`, `capture.py`, and `enrich.py`, and `analysis/impact`. Unit 4 updates
them.

### Command-line flags

- `--backend` becomes a check: a value other than the target's `platform` exits with code 2. `fake`
  stays allowed as an override, so any target can still run on the fake driver. The same rule holds
  for the `--backend` of `serve`, `triage`, `audit`, and the Model Context Protocol (MCP) tools, and
  for the `backend` of a serve request body.
- `--browser`, `--browsers`, and `--headed` override `runsOn.browser.engine` and `driver.headless`.
- `--erase`, `--system-alert-handling`, and `--ios-tipkit-handling` keep their names and override
  the matching `run` fields.
- `--udid` still names the pool; matching then picks devices from it.

### Out of scope

- Reading the old keys, and a migration command.
- One target spanning several platforms; a multi-target scenario still covers that case.
- Registering a platform from an external package through an entry point.
- Recording the declared conditions in the run manifest, which keeps recording the observed OS.
- Picking a browser version outside what Playwright launched.
- Declaring the host OS in the target. BE-0450 reads the host from the machine, lets each driver
  state which hosts it runs on, and routes on `host:<os>`, so a target-side declaration would be a
  second source for the same fact.
- Matching a physical device or a remote environment by `runsOn` conditions.
- Deriving a routing requirement from `runsOn` (see *Where a run happens*).

### Work breakdown

1. **`VersionSpec`.** Parse npm range syntax, compare versions on three components, reject
   prerelease tags, and list the major versions a range covers, in
   `bajutsu/common/devices/version.py`.
2. **Confirm the open facts.** Confirm that the AVD name is readable (for example through
   `ro.boot.qemu.avd_name`) across supported API levels, and check which scenarios and demos depend
   on a prelude splicing onto `steps`. Update the tables before code lands.
3. **Platform registry and models.** Add the registry and the per-platform models, rewrite the
   extra-field error to list the allowed fields, and pin in a test that the registry's keys equal
   `backends.PLATFORMS`.
4. **Schema switch-over.** Replace `TargetConfig`, `Defaults`, `Config`, and `resolve`, keep the
   `redact` and `secrets` unions, update the raw-schema readers, and convert the test fixtures and
   the ten `demos/` configs in one change. Splitting the switch-over would leave the gate red
   between commits, because the loader and every config break together.
5. **Setup and cleanup.** Rename `before` and `after` to `setup` and `cleanup` in the target and
   the scenario, and rename `on: error` to `on: failure`. Resolve components at config load for
   target hooks, add `inheritSetup`, rewrite the prelude files as components, and relabel the report
   phases.
6. **Command-line flags.** Apply the rules of *Command-line flags* to `run`, `record`, `crawl`,
   `serve`, `triage`, `audit`, and the MCP tools.
7. **Scenario preconditions.** Regroup `preconditions` into `app`, `runsOn`, and `run`, move the
   top-level run-policy fields under `run`, key `runsOn` by platform, and merge every group over the
   target's. Check the `app.launch` fields and the `seedPhotos` erase rule against the target's
   platform at run resolution.
8. **Reading devices.** Read the model and OS (iOS), the API level and AVD name (Android), and the
   browser version (web) from each available device, and reject conditions for `kind: device` and
   remote environments.
9. **Runs and outcomes.** Form runs from lists and ranges, combine them across a multi-target
   scenario's targets, and assign each run from its eligible set. Fail a run with no device and
   record not applicable for an open range, exit non-zero when no run executed, key the flakiness
   history by run coordinates, and give `record`, `crawl`, and `repl` the first run.
10. **Reporting.** Show the run matrix and the not-applicable status in the HTML report, JUnit
    output, the run manifest, and the serve Web UI.
11. **`doctor`.** List each scenario's runs and the devices that fit them.
12. **`bajutsu config schema`.**
13. **Execution placement.** Remove `deviceProvider`, `cloudBatch`, `cloudBatchBudget`, and
    `requires` from the target, add `environment` to the serve fan-out request and the CLI, and
    accept an `appium` environment in `worker.yaml` and in `bajutsu run --worker-config`.
14. **Docs.** Update `docs/configuration.md`, `docs/scenarios.md`, `docs/drivers.md`, `docs/cli.md`,
    `docs/run-loop.md`, `docs/reporting.md`, `docs/evidence.md`, `docs/dsl-grammar.md`,
    `docs/codegen.md`, `docs/cookbook.md`, `docs/showcase.md`, `docs/devicefarm.md`,
    `docs/ios-device-cloud.md`, `docs/architecture.md`, `DESIGN.md`, `docs/glossary.md`, and their
    `docs/ja/` mirrors.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Keep the flat list and reject keys from another platform | Catches typos, yet leaves the `device` name clash, adds no place for conditions, and keeps the settings for one purpose scattered |
| Make the platform name the key (`ios: {…}`, exactly one) | Nests the same word twice, as in `targets.web.web:`. A variable key name also forces the JSON Schema to express "exactly one" through a separate rule |
| Pair `platform` with a `configuration` block holding every platform-specific key | Groups keys by which platform uses them rather than by purpose, so app, device, and driver settings mix inside one block, one level deeper |
| A `driver: { kind: xcuitest \| adb \| playwright }` union | Each platform has one actuator today, so a second layer buys nothing. Revisit if a platform gains a second actuator |
| A closed union of platform models in the core | Every new backend would edit the core schema, which contradicts the backend-agnostic design |
| One shared `runsOn` shape for all platforms | Leaves fields no platform can use — a browser on iOS, an AVD on the web — which recreates the flat-list problem |
| Keep `deviceProvider` and `cloudBatch` in the target | Duplicates what BE-0448 and BE-0450 move onto the worker and the run request, and makes the target team decide where a run happens |
| Start before BE-0448 and BE-0450, with an interim home for the removed keys | Avoids the wait, at the cost of a temporary schema that every config would migrate through twice |
| Rename the target's hooks alone | The scenario would keep `before` and `after` for the same phases, so one concept would carry two names |
| Keep both a prelude key and `setup` | Preserves today's splicing, at the cost of two keys serving one purpose |
| Keep a target-level reference to a default prelude file | Keeps the per-scenario override without component resolution in config, yet leaves the duplication with `setup` |
| Fail every run whose device does not match | Running one suite across several OS versions would fail every scenario whose open condition excludes a version |
| Record a run that lacks a listed value as not applicable | A misspelled model would quietly drop coverage while the suite stays green |
| Create a matching device on demand | Bajutsu would own runtime installation, time, and cleanup |
| A range narrows to one device and runs once | Leaves no way to cover several OS versions in one run, which is the reason to write a range |
| Run every minor and patch release a range covers | Multiplies runs by releases that rarely differ in behavior; one run per major version keeps the count bounded |
| Treat a listed `model` as any one of the values | Matches the narrowing meaning of a single value, yet cannot test several screen sizes, which is the reason to list models |
| A separate `matrix` key beside the narrowing fields | Tells the meanings apart by key, at the cost of one more key; one list field per platform already leaves no ambiguity |
| Pin each run to one device by pool order | Deterministic down to the udid, yet serializes `--workers`, since every run of a major version would wait for the same device |
| Key a scenario's conditions by target name | The key's shape would change with whether `targets` is declared, and a scenario run against an iOS target and an Android target could not state both |
| List a scenario's condition fields flat | The file would not show which field applies to which platform, which brings back the flat config's written-but-ignored problem |
| A notation of Bajutsu's own (a subset of comparators) | Readers would have to learn which forms work; npm's grammar is already known and documented |
| PEP 440 specifiers (`>=17,<19`, `==18.*`) | Python's `packaging` handles them, including four-component versions, yet app teams rarely know them, and "the 18 line" needs `==18.*` |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: `VersionSpec`
- [ ] Unit 2: confirm the open facts
- [ ] Unit 3: platform registry and models
- [ ] Unit 4: schema switch-over, fixtures, and `demos/` configs
- [ ] Unit 5: setup and cleanup
- [ ] Unit 6: command-line flags
- [ ] Unit 7: scenario preconditions
- [ ] Unit 8: reading devices
- [ ] Unit 9: runs and outcomes
- [ ] Unit 10: reporting
- [ ] Unit 11: `doctor`
- [ ] Unit 12: `bajutsu config schema`
- [ ] Unit 13: execution placement
- [ ] Unit 14: docs

### Log

- No entries yet.

## References

- [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md): the per-platform split of the resolved `Effective`.
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md): `DeviceOS` and the recorded `device_runtime`.
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md) and [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md): `deviceMode` and `browser`, which move into `runsOn`.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the `before` and `after` phases that become `setup` and `cleanup`.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): `deviceProvider`, which moves out of the target into an `appium` environment of `worker.yaml`.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) and [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the prerequisites that take over where a run happens.
- `bajutsu/common/config/schema/target_config.py` and `bajutsu/common/config/resolve.py`: the schema and resolution this item replaces.

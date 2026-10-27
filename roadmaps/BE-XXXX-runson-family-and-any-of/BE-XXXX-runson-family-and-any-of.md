**English** · [日本語](BE-XXXX-runson-family-and-any-of-ja.md)

# BE-XXXX — Let runsOn name an iOS device family and a set of device or browser kinds

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-runson-family-and-any-of.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

A target's `runsOn` names the device it needs by an exact string, and a browser by a Playwright
engine. This item adds two ways to name a target's environment by category instead.

- **`runsOn.ios.family`** names an iOS device family. `family: [iphone, ipad]` is a priority list:
  the run takes one device of the first listed family the pool has, a Simulator or a connected real
  device.
- **`kind`** names the kinds of environment a target supports: `runsOn.ios.kind: [simulator,
  device]`, or `runsOn.browser.kind: [chrome, edge, firefox]`. Without an argument, the run covers
  every listed kind, one run each. A command-line argument that names a kind runs that kind alone.

`runsOn.browser.kind` replaces `engine`, so a browser target names Chrome and Edge as well as the
three Playwright engines. A server worker checks a real-device family before it takes a job.

## Motivation

Today a config cannot say "an iPad, any iPad". It must name one device type, and Apple renames and
replaces device types with each Xcode release. A target pinned to `"iPad Pro 13-inch (M4)"` stops
matching on the first host whose Xcode lacks that type. The run then fails with the `model`
condition unmet, although the scenario only needed a tablet-sized screen. A target that names no
model avoids the failure, but it also loses the intent: the run takes the first model in pool
order, which may be an iPhone on one host and an iPad on another. A list on `model` does not help,
because it means "run once per value", so `model: [iPhone ..., iPad ...]` doubles the runs when the
author wanted either one.

A target also declares exactly one kind of device. Many apps behave the same on a Simulator and on
a real device, yet covering both takes two targets with otherwise identical settings. Every
scenario that names a target then has to pick one of them, and a scenario cannot name both without
becoming a multi-target scenario, which drives the two devices at once in one run.

Browsers have the matching gap. `engine` names a Playwright rendering engine (`chromium`, `firefox`,
or `webkit`), an implementation detail rather than the browser a user runs. A config cannot name
Chrome or Edge, which Playwright launches as the `chromium` engine with a channel. Two names for
one choice also sit side by side: `engine` in the config and `--browser` on the command line.

The outcome a reader can check after this ships is concrete. A config written as
`runsOn.ios.family: [iphone, ipad]` keeps resolving after an Xcode update renames every device type,
with no edit to the config. One target with `kind: [simulator, device]` runs each scenario on both,
and `--kind device` runs it on the real device alone. Each run's label, manifest, and `doctor`
listing show which kind, family, and model the run took.

### Scope

The item builds on the companion proposal *Group runsOn by destination and drop the scenario's
runsOn*, which lands first. That proposal moves every field under `runsOn.ios`, `runsOn.android`,
or `runsOn.browser`, and removes the scenario's `runsOn`. This item changes what some of those
fields mean and moves no field. Android gains neither `family` nor `kind`. An Android target that
omits `avd` already accepts any emulator, and Android form factor (phone or tablet) would need its
own device read, so both stay out of this item.

## Detailed design

### Two kinds of list

The item uses two list semantics, and each field has exactly one.

| Field | A list means | Selected by an argument |
|---|---|---|
| `runsOn.ios.kind` | one run per listed kind | `--kind` |
| `runsOn.browser.kind` | one run per listed browser | `--browser`, `--browsers` |
| `runsOn.ios.family` | one run, on the first listed family the pool has | no |
| `runsOn.ios.model`, `runsOn.android.avd` | one run per listed value, as today | no |

A `kind` list declares the environments the target supports, so a run covers each one, as the
cross-browser matrix of [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)
does today. A `family` list says which device will do, so one run takes the first that fits. Order
in a `family` list therefore decides the result, and the same pool always resolves the same way.

### `kind` on iOS

`runsOn.ios.kind` takes `simulator`, `device`, or a list of both. A single value behaves as today.
Without a `kind`, the target runs on a Simulator, as today.

```yaml
targets:
  app:
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios:
        kind: [simulator, device]   # one run on each, unless --kind picks one
        family: ipad
```

`bajutsu run --target app` runs each scenario twice, once on an iPad Simulator and once on a
connected iPad. `bajutsu run --target app --kind device` runs it once, on the connected iPad. A
scenario that names `app` stays one file, and the config keeps one target for both kinds.

`--kind` takes one kind or a comma-separated list. Each named kind must be one the target lists; a
kind the target does not list exits 2 and names the target's kinds. On a target with a single
`kind`, the flag may repeat that kind or be left out.

A listed kind with no device to run on fails its run and names the kind, as a listed `model` with
no device does today. A run is never skipped silently. A host without a real device runs a
two-kind target with `--kind simulator`.

### `kind` on the web

`runsOn.browser.kind` replaces `engine`. Each value names a browser and how Playwright launches it.

| `kind` | Playwright launch | Notes |
|---|---|---|
| `chromium` | the `chromium` engine | the bundled Chromium; the default when `kind` is unset |
| `chrome` | the `chromium` engine, channel `chrome` | Google Chrome installed on the host |
| `edge` | the `chromium` engine, channel `msedge` | Microsoft Edge installed on the host |
| `firefox` | the `firefox` engine | Playwright's Firefox |
| `webkit` | the `webkit` engine | Playwright's WebKit; the closest stand-in for Safari, which Playwright cannot drive |

```yaml
targets:
  site:
    platform: web
    app: { url: https://example.com }
    runsOn:
      browser:
        kind: [chrome, edge, firefox]   # one run on each, unless --browser picks one
```

`--browser <kind>` runs one browser, and `--browsers <kind>,<kind>` runs a subset, one run each. The
flags take the `kind` values, so `--browser chrome` works. On a target that lists its kinds, each
named kind must be listed; otherwise the flag exits 2. On a target that leaves `kind` unset, the
flags may name any kind, as `--browser` may name any engine today.

### `family` on iOS

| Field | Meaning |
|---|---|
| `family: iphone` | one device whose family is iPhone |
| `family: [iphone, ipad]` | one device of the first listed family the pool has |
| `family` omitted | any device of the pool, as today |

`family` takes `iphone` and `ipad`. Both are lowercase and case-sensitive. A Simulator's family is
the `productFamily` that `simctl list devicetypes` reports for its device type (`iPhone`, `iPad`). A
device type of another family (Apple TV, Apple Watch) is readable but never matches. `family`
composes with every `kind`, because both a Simulator and a real device report one.

### Rules the loader enforces

Each rule runs on the merged config block, after team defaults have folded in.

- `family` and `model` together fail the load and name both fields. A target clears an inherited
  one with `null`.
- `model` and `os` fail the load when `kind` includes `device`, because a real device cannot check
  them. A Simulator-only target keeps both, as today.
- A target that an `appium` grid or a Device Farm job serves has no pool to read, so a `family`
  there fails before any device work, as every condition does today.
- `runsOn.browser.version` with a list of browser kinds fails the load, for the reason a list of
  engines does today: each browser numbers its versions differently.
- An empty list, a repeated entry, and an unknown kind or family fail the load and name the allowed
  values.
- `runsOn.browser.engine` fails the load and names `kind`. The message states that a `kind` list
  runs once per listed browser, as an `engine` list did, so the rewrite keeps every run.

`family` composes with `os` on a Simulator. The version range multiplies runs as before. Each run of
a major version picks its own family from the devices at that version.

### How a run resolves

`form_target_runs` in `bajutsu/common/devices/runs.py` already receives the pool's device facts.

1. It forms one run per selected kind: every listed kind, or the kinds `--kind` names. A listed kind
   adds a coordinate, `kind=device`, as a listed `model` adds one today.
2. Within a run, it keeps the devices of that run's kind. For each family in order, it keeps the
   devices of that family that meet the run's `os` condition.
3. The first family with at least one such device wins. The run records it as `family`.
4. If no listed family has a device, the run fails. The reason names the kind, the listed families,
   and the available devices, as a missing `model` does today.

`eligible_set` then narrows to the winning family and applies the existing rule: the first model in
pool order, then the newest release that has enough devices. The rule does not look past the
winner when the demand exceeds the winner's device count. That run fails and names the family,
because a deterministic choice matters more here than the largest possible fit.

A run that resolved a family out of two or more listed families gains a coordinate, `family=ipad`.
The coordinate shows in the run label, in the manifest's `run_coordinates`, and in the flakiness
key beside the device OS. Without it, the same scenario would key one history across hosts where
the winner differs, and a layout difference between an iPhone and an iPad would read as flakiness
([BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md)).
A single `family: ipad` adds no coordinate, in the way a single `model` adds none.

A target that lists both kinds needs a pool that holds both. The pool is every udid of `--udid`, and
each udid belongs to the kind of the listing that shows it: `simctl` for a Simulator, `devicectl`
for a real device. A udid that neither listing shows raises `DeviceReadError`. Each run's kind sets
its XCUITest destination, which `kind` sets for the whole target today. A target whose list
includes `device` needs the signed device runner, as a `kind: device` target does.

On the web, each run's browser kind fills the existing engine slot of `TargetRun`, with the channel
beside it. `target_pool` in `bajutsu/run/device_matrix.py` reads one `DeviceFacts` per browser
through `read_web`, which launches the browser headless and raises `DeviceReadError` when it cannot.
A Chrome or Edge that the host lacks therefore fails its run loudly. The driver passes the channel
to Playwright's `launch`.

`record`, `crawl`, and `repl` never multiply runs. They take the first listed kind unless `--kind`
or `--browser` names one, the first resolvable family, and exit 2 when nothing fits.

### Reading the family

`DeviceFacts` in `bajutsu/common/devices/facts.py` gains `family: str | None`. `read_ios` reads it
from the `productFamily` of the device type it already looks up for the name. A device type with no
`productFamily` raises `DeviceReadError`, because the reader fails loudly rather than guessing.
Android and web devices carry `None`.

A real device gains a reader of its own, `read_ios_device`. It reads every udid of the pool from
`xcrun devicectl list devices` with JSON output, taking `hardwareProperties.deviceType` (`iPhone`,
`iPad`) as the family. A udid that the listing does not show, or that reports no device type, raises
`DeviceReadError`. The reader reads the family alone. A real device's model and release are
readable too, but a `model` condition would compare a product identifier such as `iPad13,8`, not
the Simulator's device-type name. Matching on that identifier needs a separate design, so `model`
and `os` stay refused on a real device. The read runs before any device work, in the same pool
read that `read_ios` serves for Simulators.

### Checking on the worker

A server worker already knows which real devices it can drive, so the worker checks a real-device
`family` before it takes a job. The check reuses the capability routing of
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md). Under that
routing, a job carries the tokens it requires, and a worker leases the job only when it advertises
all of them (`can_serve` in `bajutsu/serve/capabilities.py`). Workers already advertise `iphone`
and `ipad` from their Simulator inventory (`simctl_capabilities`).

- **What the worker advertises.** A worker adds `device:iphone` and `device:ipad` tokens for the
  real devices connected to it, read through the same `devicectl` reader as `read_ios_device`.
  Real devices come and go, so the worker refreshes these tokens before each lease attempt rather
  than once at registration. `device:` becomes a reserved prefix, like `environment:` and `host:`,
  so an operator's hand-written tokens cannot claim a device the worker lacks.
- **What a job requires.** Each run routes on its own kind, since a `kind` list forms separate runs.
  A real-device run with one family requires that token, for example `device:ipad`. A family
  priority list cannot become a subset requirement, because any one family suffices. The run's job
  instead carries an any-of group, `[device:iphone, device:ipad]`, stored beside its required
  tokens. A Simulator run's group uses the existing `iphone` and `ipad` tokens. `can_serve` accepts
  a job when the worker advertises every required token and at least one token of each group.
- **What the leasing worker does.** The worker resolves the family list over its own pool, as a
  local `run` does. The routing decides which worker runs the job; the family list decides which
  device on that worker runs it. The winner lands in the run's coordinates, so the flakiness key
  separates runs that took different families.

A job that no worker can serve stays queued, as a job with an unmet token does today. A device that
disconnects between the lease and the run fails the run when the worker reads its pool, because
the reader raises `DeviceReadError` rather than skipping the device. Routing never touches the
verdict: it decides where a job runs, and the run itself stays deterministic.

### Schema, reports, and docs

- `IosRunsOn` in `bajutsu/common/config/schema/platform/ios.py` gains `family`, and its `kind`
  accepts a list. `WebBrowser` in `web.py` replaces `engine` with `kind`, whose values gain `chrome`
  and `edge`. `WEB_ENGINES` gives way to the browser-kind table above.
- `bajutsu config schema` emits `family` and both `kind` fields as an enum or a list of the enum.
- `run`, `record`, `crawl`, and `repl` gain `--kind`. `--browser` and `--browsers` take the browser
  kinds and check them against the target's list.
- `doctor` lists each run with its kind, the winning family, and the devices that fit.
- [Configuration](../../docs/configuration.md#device-conditions-runson) and its `docs/ja/` mirror
  gain the tables and the examples. [CLI](../../docs/cli.md) documents `--kind` and the new
  `--browser` values. [Architecture](../../docs/architecture.md) updates the `common/devices/` rows
  for `facts.py` and `runs.py`.

### Prime-directive compliance

No LLM enters a run. The resolution is a pure function of the config, the arguments, and the pool,
and the same inputs yield the same runs. An ambiguous request does not tap whatever matched first:
a `family` list fixes the order in advance, a `kind` list names every run, and a pool that fits no
entry fails the run. All differences stay in config.

### Out of scope

- `model` and `os` on a real device. The reason is under *Reading the family*.
- Safari itself. Playwright drives WebKit, not Safari, so `webkit` stays the stand-in.
- Android `family` and `kind`. The *Scope* section gives the reason.
- A priority list on `emulate`. It picks a Playwright viewport profile rather than a device from a
  pool, so it has no availability to resolve.
- Glob or regular-expression matching on `model`. The alternatives table gives the reason.
- Changing the meaning of a list in `model` or `avd`. Existing configs keep their runs.

### Work breakdown

1. **Read the family.** Add `family` to `DeviceFacts` and read it in `read_ios` from
   `productFamily`; confirm on a real `simctl list devicetypes` that every iPhone and iPad type
   carries it. Add `read_ios_device` for real devices over `devicectl`, and confirm on a connected
   iPhone and iPad that `hardwareProperties.deviceType` carries the family.
2. **Schema.** Add `runsOn.ios.family`, let `runsOn.ios.kind` take a list, replace
   `runsOn.browser.engine` with `kind`, and add the load-time rules and the `engine` migration
   message. Extend the JSON Schema output.
3. **Resolution.** Form one run per selected kind, read a pool that mixes Simulators and real
   devices, resolve the family list in `form_target_runs`, narrow `eligible_set` by the winning
   family, and add the `kind` and `family` coordinates. Cover `first_run` and `declared_runs`.
   `declared_runs` has no pool to resolve against. It lists a family list as one run whose family
   depends on the pool, the way `PER_AVAILABLE_MAJOR` labels an open range.
4. **Arguments and launch.** Add `--kind`, let `--browser` and `--browsers` take the browser kinds
   and check them against the target's list, pass the channel to Playwright's `launch`, and set the
   XCUITest destination from each run's kind.
5. **Reporting.** Show the kind and the winning family in the run label, the manifest, `doctor`,
   and the flakiness key.
6. **Worker routing.** Advertise `device:iphone` and `device:ipad` from the worker's connected
   real devices, refreshed before each lease. Reserve the `device:` prefix, add the family any-of
   group to a run's requirement, and extend `can_serve` to accept it.
7. **Docs and demos.** Update the bilingual docs named above, and carry the examples into the
   configuration page's `runsOn` section in both languages. Switch one demo config from an exact
   model to `family`, and one from `engine` to `kind`.

Each unit carries its own tests: the schema rules, the migration message, the argument checks, a
fake pool for each resolution branch, and the failure messages.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| A `kind` list as a priority list, one run on the first kind that fits | It hides which kind ran behind the pool, and covering both kinds would still need two targets. Running every listed kind, narrowed by an argument, says the target's support set once and lets each invocation choose. |
| `kind: any` for "either kind" | The order between the kinds would live in the spec rather than in the config, and `any` cannot say "both, as separate runs". |
| A list of environment blocks under `runsOn.ios` | It lets each block carry its own `os` or `model`, but adds a second structure beside the field lists. The `kind` list with `family` covers the need. |
| Two targets, one per kind | It works today, but it doubles the target settings, and a scenario that names a target has to choose one of the two. |
| Keep `engine` and add a separate browser-name field | Two fields would name the same choice, and `engine` exposes Playwright's implementation to config authors. |
| Rename `anyOf` to `engine` with priority semantics | An existing `engine` list would silently drop from one run per engine to one run, and `chrome` is not an engine. |
| Let a list in `model` mean any-of | It silently changes every existing multi-model config from "run each" to "run one". The change breaks the matrices teams already rely on. |
| Glob matching, such as `model: "iPad*"` | It depends on Apple's naming, which is the brittleness this item removes. A rename of the family prefix would break the pattern the same way an exact name breaks. |
| A cross-platform `idiom: phone \| tablet` | It needs a form-factor read on every platform, so it spans Android and web emulation. The iOS and web needs here are met without it. A later item can add it on top. |
| Pool order as the tie-break for `family` | It is simpler, but the winner then depends on the host's device order. Flakiness history would key one scenario across different devices. |
| Pick the lower-priority family when the winner lacks devices for the demand | It maximizes fit but makes the family depend on `--workers` and the device count. The run's identity would change with the concurrency. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: read the family
- [ ] Unit 2: schema
- [ ] Unit 3: resolution
- [ ] Unit 4: arguments and launch
- [ ] Unit 5: reporting
- [ ] Unit 6: worker routing
- [ ] Unit 7: docs and demos

## References

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the `runsOn` conditions, device matching, and runs this item extends.
- [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md): the cross-browser matrix and the `--browser` and `--browsers` flags that the browser `kind` takes over.
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md): the device facts that the flakiness key reads.
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md): the capability routing a worker uses to check a real-device family before it leases a job.
- `bajutsu/common/devices/facts.py` and `bajutsu/common/devices/runs.py`: where the family is read and resolved.

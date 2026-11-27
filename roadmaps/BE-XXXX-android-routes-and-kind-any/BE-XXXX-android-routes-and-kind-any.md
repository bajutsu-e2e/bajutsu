**English** · [日本語](BE-XXXX-android-routes-and-kind-any-ja.md)

# BE-XXXX — Split the Android environment route and let a target run on any device kind

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-routes-and-kind-any.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md), [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location.md) |
<!-- /BE-METADATA -->

## Introduction

An environment route names the environment a run lands on. Bajutsu derives the route from the
[target](../../docs/glossary.md#target-app-device) config and the
[driver](../../docs/glossary.md#driver-backend-actuator-platform). The route selects the table of settings and capabilities
that a run may use. iOS has separate routes for the Simulator, a real device, and a live WebDriver
endpoint. Android has one route, `android`, for emulators and real devices alike.

This item makes two changes.

- Replace `android` with `android-emulator` and `android-device`. A new key, `runsOn.android.kind`,
  says which kind a target runs on. The server then hands a job to a worker that holds that kind.
- Add `kind: any` on iOS and Android. A target then accepts a virtual device or a real device.
  Bajutsu picks among the kinds that can run the whole
  [scenario](../../docs/glossary.md#scenario-authoring).

`any` is a config value, never a route. Every run still lands on one concrete route.

## Motivation

A platform says what the app under test is. A route says where a run happens and how Bajutsu drives
the device. Android has one route today, so nothing records which kind a job needs.

First, the server cannot send an Android job to the right kind of worker. A worker with a phone and
a worker with an emulator advertise the same tokens. A job meant for a phone can reach the emulator.
It then passes against an environment the author did not choose.

Second, a config cannot state the intent. iOS has `runsOn.ios.kind`. Android has no counterpart.

Third, a target cannot say "whichever device is free". A list such as `kind: [simulator, device]`
runs each scenario once per kind. No value runs it once on either kind.

[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md) left
the Android split to a later item. Android had no field that told the two kinds apart. This item
adds that field.

The change succeeds when a reader can observe both of these outcomes.

- A job for a `kind: device` Android target stays queued while the workers hold emulators alone.
- A `kind: any` target runs on a fleet that holds either kind, with no config edit.

## Detailed design

The design adds no model call and no fixed pause. Every choice follows from the config, the
scenario, and the device list.

### Config

| Key | Values | Default |
|---|---|---|
| `runsOn.ios.kind` | `simulator`, `device`, `any`, or a list of the first two | `simulator` |
| `runsOn.android.kind` | `emulator`, `device`, `any`, or a list of the first two | `emulator` |

A list keeps its current meaning: one run per listed kind. A list may not contain `any`.

The Android default changes behavior. A target that drives a real Android device today must add
`kind: device`. A run that finds no device of the target's kind stops before any device work. Its
message names `runsOn.android.kind`. The showcase Device Farm config gains the key in the same
change.

Two rules for physical devices carry over from iOS.

- A target whose `kind` is `device` or `any`, or a list with `device`, cannot declare `avd`. A real
  device has no Android Virtual Device (AVD). `apiLevel` stays allowed.
- An iOS target with `kind: any` cannot declare `model` or `os`, as a `device` target cannot today.

An iOS `kind: any` target also cannot declare `driver.runner.testRunner`. The run picks the kind
from the devices that are free. One prebuilt runner could then pass or break by chance. A list names
every kind it runs, so lists keep accepting the key.

The `--kind` flag now accepts Android targets. On an `any` target it narrows the run to one kind.

### Routes and tables

The iOS and web routes stay. `android-emulator` and `android-device` take the place of `android`. The two Android routes start with the same settings and capabilities as `android` today.
A later difference then needs one table cell, not a new route. `setLocation` still picks the emulator
console or the in-app receiver as
[BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location.md) defines.

Android device facts must mark a real device as physical, as iOS facts do. Without that mark, the
kind filter cannot tell the two apart.

### Choosing a kind under `any`

Bajutsu computes the candidate kinds for each scenario and target before it leases a device. A kind
is a candidate when its route meets both conditions.

- The route advertises every capability that the target's steps need. Lifecycle
  [hooks](../../docs/glossary.md#scenario-authoring) count as steps.
- The route supports or satisfies every setting in effect. A setting under
  `runsOn.ignoredSettings` does not count against the route.

For example, a scenario with `setLocation` leaves an iOS target with `simulator` alone. A target with
`subject.path` also keeps `simulator` alone, unless it declares `install` as ignored. An empty
candidate set stops the run. The error lists what each route lacks.

| Case | Rule |
|---|---|
| Both kinds have a free device | Take the simulator or emulator |
| One candidate kind has a device | Take that device |
| Targets that share a device | Keep the kinds that every member accepts |
| `record`, `crawl`, and `repl` | Judge the target's settings alone, since no scenario exists |
| Appium grid | Stay on `ios-live`; `kind` does not apply |

The preference works inside one device pool. The server does not rank one worker above another.

A stale declaration under `runsOn.ignoredSettings` stops the run. A declaration is stale when every
kind of the target supports or satisfies the setting. The check reads the target's whole kind set, not the
kind of one run. It replaces today's check of a single route, for lists too. Take a
target with `kind: [simulator, device]` that ignores `erase`. Its Simulator run then passes.

On a route that supports the setting, Bajutsu applies it. Evidence kinds never narrow the
candidates. The report's skipped captures still list evidence that the chosen route cannot record.
The report records the route that each run took.

An `any` run also adds the chosen kind to its run coordinates. The flakiness history groups runs by
those coordinates. Virtual and real runs then stay two histories. A difference between the two
environments does not read as a flaky scenario.

### Dispatch

Each job for an iOS or Android target requires a token that names its route, `route:<route>`. A
scenario with no capability and no setting needs the token too. A `kind: any` job holds one choice for each pair of scenario and target.
One of that pair's candidate routes meets the choice. The job needs every choice. A list-kind job needs the tokens of every listed route, since
one worker runs the whole list.

A worker must meet the tokens of one route together. A job cannot take a capability from one route and a setting
from the other. The existing any-of group works on single tokens, so `can_serve` needs a group of token sets.
An iOS set also holds the tokens that follow its kind today. A fan-out job adds the `kind:` token of
the route. A target with `runsOn.ios.family` adds family tokens. The
`ios-device` set takes `device:<family>`. The `ios-simulator` set takes `iphone` or `ipad`. A target with no family adds none.

An Android set holds no `kind:` token. No worker advertises `kind:emulator`, and the route token
already names the kind.

Device Farm raises one more case. Its iOS fan-out needs `driver.runner.testRunner`. An `any` target
cannot declare that key. A worker capability keeps such a job away from Device Farm.

Some workers can resolve the XCUITest runner themselves. Local workers that drive real devices
advertise that as `runner:resolve`. A Device Farm worker does not. An iOS target with no
`testRunner` adds that token to its `ios-device` set. An `any` job then goes to a local worker. With
no such worker, the job stays queued.

An Android fan-out job requires the same route token. The existing `kind:device` token is not
enough: a Mac with an iPhone advertises it too. A Device Farm worker advertises
`route:android-device` and `route:ios-device`. As today, that worker has no `cap:` or `setting:`
token. A job on
the fake driver drives no device, so it needs no route token.

The server never drops the route token. Today the server drops `cap:` and `setting:` needs for a worker from
before BE-0467. That drop keeps an upgrade from stranding a queue. The new token gets no such exception. A worker
without the token takes no iOS or Android job. The worker checks the token again when it claims a
job. The `route:` prefix also joins the reserved prefixes, so `--capabilities` cannot claim it.

The server still checks `cap:` and `setting:` needs as it does today.
Take a Device Farm worker, which advertises neither prefix. The server still sets those needs aside
for that worker.

Operators upgrade the server and every worker together, and drain the queue first. Queued Android
jobs hold `cap:android:` or `setting:android:` tokens. No upgraded worker advertises them. The
upgraded server stops each job left behind with a message that names the upgrade. Such a job never
waits in the queue without an error.

### Workers

A local worker combines a declaration with detection.

- `worker.yaml` declares the allowed kinds. A new key, `androidKinds`, takes `emulator` and
  `device`, and defaults to both. `realDevices` keeps its meaning for iOS.
- Before each lease, the worker reads the connected devices through Android Debug Bridge (adb). It
  advertises an Android route when the file allows the kind and a device of that kind answers.

The real-device route on iOS gets the same gate. Today a worker advertises `ios-device` whenever
`realDevices` is unset. It will advertise `route:ios-device` when `devicectl` also finds a real
device. The Simulator route does not change. An Appium grid worker advertises
`route:ios-live`. It keeps the `cap:` and `setting:` tokens of that route.

### Work units

1. Config: `kind` on Android, `any` on both platforms, load checks, and `--kind`.
2. Routes: split the table and the tokens, and mark Android real devices as physical.
3. Candidate selection: compute candidates, prefer virtual devices, and record the route.
4. Dispatch: route tokens, the group of token sets, fan-out jobs, and retired queued jobs.
5. Workers: `androidKinds`, the adb read, and the iOS `devicectl` gate.
6. Documents and demos: update both languages and the showcase configs.

Each unit ships with tests on the fake driver or on recorded device listings. The gate needs no
device.

## Alternatives considered

| Alternative | Summary | Why not adopted |
|---|---|---|
| `ios-any` and `android-any` routes | Add routes whose tables are the overlap of two routes | No run happens on such a route, and iOS would lose most Simulator features |
| Allow the overlap under `any` | Refuse whatever one kind lacks | An iOS `any` target would be close to a real-device target |
| Detect the kind at run time | Keep no config key, and read the device | The lint and the dispatch have no device to read |
| Warn for one release | Accept a real device without `kind: device` for a while | The route would be unknown at dispatch during that release |
| Declare kinds in `worker.yaml` alone | Skip the adb read | An unplugged phone keeps taking jobs |
| Write the preference order | Let a target list the preferred kind | A list already means one run per kind |

[BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md) rejected
`kind: any`. It gave two reasons. The preference between kinds would live outside the config. The
value also cannot say "both, as separate runs". A list now says the second, so `any` need not. The
first reason still holds, and this item accepts it: the preference stays virtual first.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Config keys, load checks, and `--kind`
- [ ] Unit 2 — Route split and physical mark on Android
- [ ] Unit 3 — Candidate selection under `any`
- [ ] Unit 4 — Dispatch tokens
- [ ] Unit 5 — Worker declaration and detection
- [ ] Unit 6 — Documents and demos

## References

- `bajutsu/common/capability/settings_support.py` — the settings table per route.
- `bajutsu/common/capability/route_tokens.py` — the tokens per route.
- `bajutsu/common/devices/facts.py` — the device facts that the kind filter reads.
- [Self-hosting](../../docs/self-hosting.md) — how the server routes a job.

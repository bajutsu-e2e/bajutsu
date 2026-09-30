**English** · [日本語](BE-XXXX-install-app-step-ja.md)

# BE-XXXX — Share one device among scenario targets and install a group member's build from a step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-install-app-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A Bajutsu target is one app plus the settings that drive it, and a scenario gives each target it
declares its own device. This item adds four constructs to the scenario. A nested array in
`targets` lists targets that share one device. An `installs` key names the members that install
before the first step besides the primary, which always does. An `installApp` step installs the others later, at the point where the journey
needs them, and a `setPrimaryTarget` step moves the default target to another member, such as the build an `installApp` step installed. Two journeys become expressible that are not today: a companion app, such as a
multi-factor authentication (MFA) app that shares the device with the app under test, and an app
update, where an old build creates data and a new build is installed over it. The config schema does
not change, so every existing config and scenario keeps working unchanged.

## Motivation

Today `targets.<name>.appPath` names one binary, and `run` installs it during preconditions. The
`reinstall: overwrite` precondition keeps the app's data container across that install, and the
`relaunch` step restarts the process, but nothing installs a second binary once the scenario is
running. A tester who wants to check that version 2 migrates the data version 1 wrote has no way to
say it in one scenario. The only workaround is two scenarios run back to back on the same device,
which splits one user journey in two and leaves the run order as an unwritten assumption.

Multi-target scenarios do not close the gap. A scenario's `targets` list launches every declared
target before the first step, and each target holds its own device, so two iOS targets need two
devices (`docs/scenarios.md`, "Limits"). An update needs two builds of the same bundle on one
device, and an MFA companion needs two apps on one device.

The observable outcome is a single scenario in the showcase demo that starts on an old build,
creates data, installs the current build over it, and asserts that the data survived the update.
That scenario passes on iOS and on Android, and no existing config or scenario needs an edit.

## Detailed design

### Targets stay the unit that describes an app

Nothing about the config schema changes. A target already carries what identifies and installs one
app: its bundle identifier or package, its `appPath`, its `build` command, and the settings that
drive it. Two builds of one app are two targets that share an identifier and differ in `appPath`.
A companion app is a target of its own. Settings the builds share move to the `defaults` layer the
config already merges into every target (`docs/configuration.md`, "Config layering").

```yaml
targets:
  showcase-previous:                      # the old build the scenario starts on
    backend: xcuitest
    bundleId: com.example.showcase
    appPath: build/Showcase-1.app
  showcase:                               # the current build
    backend: xcuitest
    bundleId: com.example.showcase
    appPath: build/Showcase-2.app
  authenticator:                          # a companion app
    backend: xcuitest
    bundleId: com.example.authenticator
    appPath: build/Authenticator.app
```

### Sharing a device: a nested array in `targets`

The scenario's `targets` list gains one form. An element is a target name, as today, or an array of
target names. An array is a device group: its members can run on one device, and the scenario leases
one device for the group instead of one per member. A bare name is a group of one, so a scenario
that writes no array behaves exactly as before.

The scenario model enforces three rules, and run preflight a fourth. A name appears once across the
whole list, as today. A group holds two or more names, since a one-name array adds nothing over a
bare name. `primaryTarget` still must equal the first entry, which now means the first member of the
first group. Run preflight, which sees the config, checks that the members of a group can share one
device: the same backend platform (so a web target cannot share a device with an iOS one), the same
device route (`deviceProvider`, `device`, and `xcuitest.deviceType`), and the same effective `locale`,
since iOS pins the Simulator's system locale. It rejects a group whose members disagree before any
device is acquired. The device count a run needs is the number of groups per backend, so the
up-front check that refuses a pool with too few devices counts groups, and `--udid a,b` supplies one
device per group.

Once the flattened list holds two or more names, the existing rule applies: a step that omits
`target` needs `primaryTarget`. A grouped scenario therefore sets it, as both examples below do.

Existing code that reads `Scenario.targets` (`bajutsu/common/scenario/models/scenario/_targets.py`,
`bajutsu/common/runner/pipeline.py`, `bajutsu/run/cli.py`, `bajutsu/serve/operations/reads.py`,
`bajutsu/analysis/cli/audit.py`) reads it through one flattening accessor when it needs only the
names. Validation in the scenario model, run preflight, and the lease step read the group structure
itself, since a flattened list could not reject an invalid group.

### Choosing what installs first: `installs`

A group can hold several app targets, and only some of them belong on the device at the start. The
primary, the first member of the first group, always installs and launches at the start, whether or
not `primaryTarget` is declared, since the runner treats it as the primary in either case. A
separate top-level key, `installs`, a list of target names, adds the other members that start with
it. `targets` keeps the grammar above.

| Situation | Rule |
|---|---|
| The primary (the first member of the first group) | Installs its `appPath` before the first step and launches at the start. Listing it in `installs` is optional. |
| Any other group of one member (a bare name) | The member installs at the start, as today. Listing it is optional. |
| Any other group of two or more members | The scenario must list at least one member. A group with none listed fails to load, so the choice is never implicit where the primary does not anchor it. |
| A listed member | Installs its `appPath` before the first step and launches at the start, like any declared target. |
| A member left out | The later member: it installs and launches nothing at the start. It comes alive through an `installApp` step and a `foreground` step, below. |

Every name in `installs` is a member of `targets`. Members that start in one group must not share a
bundle identifier or package, since installing both would leave one build on the device. That check
needs the config, so the run's preflight makes it. Members that start launch in reverse declared
order, so each group's first starting member is in front when the first step runs.

An update scenario starts on the old build and holds the new one back:

```yaml
- name: notes survive the 1 to 2 update
  targets: [[showcase-previous, showcase]]
  primaryTarget: showcase-previous
  steps:
    - tap: { id: notes.add }
    - type: { text: hello, into: { id: notes.field } }
    - installApp: { from: showcase }
    - setPrimaryTarget: { target: showcase }
    - foreground: {}                 # the omitted target is now showcase
    - assert:
        - exists: { id: notes.item, label: hello }
```

A companion scenario adds the authenticator to the primary's start and keeps a second device for a web
client:

```yaml
- name: read the code in the authenticator, enter it in the app
  targets:
    - [showcase, authenticator]     # one device holding both apps
    - showcase-web                  # a separate device
  primaryTarget: showcase
  installs: [authenticator]
  steps:
    - target: authenticator
      foreground: {}
    - target: authenticator
      tap: { id: code.show }
      extract: { code: { sel: { id: code.value } } }
    - target: showcase
      foreground: {}
    - target: showcase
      type: { text: "${vars.code}", into: { id: login.otp } }
```

### Preparing a group's device

A group's members share one device, so device-wide preparation runs once per group, not once per
member: booting, the erase, the system locale pin, and seeded photos. It uses the scenario's
preconditions. Each member then takes a per-member path that only installs, attaches its driver, and
launches. That path never repeats `erase`, and it never applies `reinstall: clean` to an app it did
not install itself. A starting member's install at the start follows `reinstall` for that member. A
later member's `foreground` never uninstalls or clears the build an `installApp` step just put there. In
a companion group with `erase: true`, the device is wiped once before any member installs, so the
second starting member does not wipe the first.

### The `installApp` step

| Step | Meaning |
|---|---|
| `installApp: { from: <target>, keepData?: boolean }` | Installs the build of the group member `from` onto the device the step runs against. The step's own `target` modifier picks the device through its group, and an omitted one means the primary. `keepData` defaults to `true`: the build installs over an existing one and the data container survives. `false` uninstalls the same identifier first. |

`from` names a member of the same group as the step's target. The scenario model checks that from the
scenario alone, since `targets` and the routing rule already live there, so the reader sees every
build a scenario can install in its header. An `installApp` step is refused inside a `web:` or `app:`
block. In an `interrupts` entry's recovery steps, an omitted `target` resolves the group through the
entry's own `target`, or the primary when the entry omits one. `run` preflight checks the rest against the config: the
target defines an `appPath` that exists, it is not a web target, and on a Git-sourced config the
binary builds on demand, as it does for the primary target today. The scenario names a target, never
a path, so the build artifact stays in config (prime directive 3).

The step terminates any running app with `from`'s identifier, installs the build, and leaves the
launch to the scenario. A `foreground` step for the member follows, as in the examples. When `from`
shares an identifier with a member that already runs, the install replaces that member's app, so the
older member is retired: a step addressed to it afterwards fails with a named cause instead of
reaching the new build. A step other than `installApp`, `foreground`, or `setPrimaryTarget` that is addressed to a later member before that
member's `installApp` and `foreground` fails the same way, saying the member is not installed yet.

### Moving the default target: `setPrimaryTarget`

| Step | Meaning |
|---|---|
| `setPrimaryTarget: { target: <target> }` | From this step on, a step, an `interrupts` entry, or a top-level `expect` entry that omits `target` resolves to the named target. |

After an update, the declared primary is the retired member, so without this step every later step, assertion, and
interrupt would name the installed member. The step changes routing only. `target` names any declared
target. The first member of the first group still governs leasing, evidence directories, and crash
recovery, since those are fixed at the start (BE-0428). Each member keeps its own config, driver, and
environment, so nothing is swapped mid-run: the step moves the default to a member whose `launchEnv`,
`locale`, `ready_when`, and baselines already applied to the steps that named it.

The step is allowed only among a scenario's top-level `steps`, never inside `if`, `forEach`, a target group (BE-0437),
`web:`, `app:`, or an `interrupts` entry. That lets the scenario model follow the current primary in
order and check every later omitted `target` and every `installApp.from` statically. The exception is an
`installApp` in an `interrupts` entry's recovery steps, whose group depends on when the entry fires:
once a scenario contains a `setPrimaryTarget`, such a step must name its device explicitly, on the step
or on the entry, or the load fails. Like `installApp` and
`foreground`, the step is exempt from the not-installed rule, so it can precede the member's
`foreground`. A top-level `expect` entry that omits `target` resolves to the primary in force after the
last step. An `interrupts` entry that omits `target` follows the current primary at run time, and an
entry that resolves to a later member is not polled until that member's `foreground`. The `before`
and `after` rules resolve an omitted `target` to the declared primary, since teardown runs wherever
the run stopped. In an update scenario that primary is the retired member, so its `after` steps name
their target.

### Using these steps inside components

`use:` and `group:` expand at load time, and expansion stamps the caller's `target` onto expanded
steps that omit one (BE-0446). Three rules keep the new steps consistent with that. `setPrimaryTarget`
takes its target as an argument, not as the step modifier, so it needs an explicit exemption: both
stamping points (a `use:` or `group:` caller's and a target group's) skip it, and a caller's `target`
never reaches it. The loader rejects a modifier `target` written on it by hand. The top-level-only rule for `setPrimaryTarget` is judged
after expansion: a component's `setPrimaryTarget` is valid when the `use:` or `group:` that calls it
is itself among the top-level `steps`, and refused when the call sits inside `if` or `forEach`. An
`installApp` inside a component names `from` and leaves the device to the step's `target`, stamped or
resolved like any expanded step. Whether `from` belongs to that target's group is checked after
expansion, at the call site, and the error names the component chain and the step. Expansion records the component chain on each
step it produces, so the post-expansion check can name it. Before expansion, the load-time pass
cannot see a component's `setPrimaryTarget`, so after the first top-level `use:` or `group:` step it
stops resolving omitted targets and checking `installApp.from`, and leaves both to the pass that runs
after expansion.

### Switching between apps on one device

A device shows one app at a time, so the scenario switches explicitly. The `foreground` step, given a
member's `target`, brings that member's app to the front without terminating it, and an omitted
`target` means the primary. The runner never switches on its own when the `target` of consecutive
steps changes, so every hop is a line the reader can see.

`foreground` exists on iOS today and raises "not supported" on Android, so this item adds the Android
implementation, which starts the member's launcher activity without clearing its state. The
capability preflight gates `background` and `foreground` under one token,
`deviceControl.appLifecycle`, which Android does not advertise, so the token splits in two and the
adb backend advertises `foreground` alone.

`foreground` also gains a second behavior on both platforms. When it launches an app that was not
running, as after an `installApp`, it performs `relaunch`'s launch without the terminate: the same
launch environment and arguments (config, scenario preconditions, locale, and the environment's own
extra environment such as the collector URL), the same launch-marker stamp for crash attribution
(BE-0424), the readiness wait, and, on Android, the settle-cache and exit-info resets. When the app
already runs, it only brings the app to the front. `relaunch` keeps its own meaning, including its
`env` and `args`, for scenarios that restart a running app.

On a device shared by two or more members, each member's driver checks that its own app, not another
member's, is in front before it resolves a selector, as part of that step's existing condition wait.
On Android the check reads the packages on the dump's nodes, and on iOS it reads the application
states of the other members. A step addressed to a member whose app does not come to the front within
that wait fails with a named cause pointing at `foreground`, never against another app's tree, since
resolving there would act on an element the step never meant (prime directive 2). A device that holds
one target skips the check, so existing scenarios, the `app:` block, and system dialogs behave as
today. Members that share an identifier cannot be told apart on the device, so the runner keeps its
own record of which member's build was installed last, and a step addressed to a retired member fails
by that record. The device check covers members with distinct identifiers.

### Backend behavior

| Backend | Behavior |
|---|---|
| XCUITest (iOS Simulator) | `simctl install` over the existing bundle keeps the data container, which `reinstall: overwrite` already relies on (`xcuitest_environment.py:974`). Digest skipping stays a precondition optimization and never applies to the step, since the step is explicit. An `installApp` step resets the tracked digest on every member's environment on that device, not only on the member it installed, since each member tracks device-scoped state of its own. A later `reinstall: overwrite` precondition therefore never skips installing over a build the step put there. |
| Android (adb) | `adb install -r` keeps app data. Going to an older build fails on Android, so a downgrade needs `keepData: false`, and the environment names that cause in the error. |
| Web | Rejected before any device is leased, through the capability check each target's steps already pass. |
| Device-cloud lease that hands over an installed build | Rejected with a named cause, since the provider holds the binary and the local path does not exist (BE-0236). |
| Code generation (XCUITest, UIAutomator) | Emits the generator's usual unsupported-step marker; the generated test has no equivalent. |

Whether the OS kills the running process on a same-bundle install differs by platform, and the
implementation confirms it on a device instead of assuming it. The explicit terminate makes the
outcome the same either way.

### Facts to confirm on a device first

A device group needs several facts confirmed on a device before the design is final. On iOS, the
question is whether one Simulator can run two XCUITest runners at once. On Android, it is whether one
device can serve two drivers, one per package. If either cannot, the group's members share one driver
that switches the app under test between steps, the way the `app:` block does today. The grammar
above stays the same in both cases, and only the lifecycle inside a group changes.

The same check covers four more facts: that the new Android `foreground` brings a member's app to the
front, how each platform reports which app is in front (the driver check above depends on it), how
`foreground` tells a not-running app from a backgrounded one, and when a later member's driver starts
(at its first `foreground` after the `installApp`, if the two-driver case holds). It also confirms that a companion group with `erase: true` keeps both apps, and that data
survives an `installApp` followed by `foreground`.

### Scope

An `installApp` step never moves the primary by itself. The scenario says so with `setPrimaryTarget`, so
the point where routing changes is a line the reader can see. Until then, steps that omit `target`
resolve to the primary the scenario declared, and when the `installApp` replaced that primary's own build,
the primary is the retired member. A step after the install therefore either names the installed member
or follows a `setPrimaryTarget`. This item does
not add an Android downgrade path beyond `keepData: false`. It does not remove or rename any config
field, and it does not change the `app:` block. The step never chooses a verdict, so the
deterministic gate is untouched.

### Work breakdown

1. Scenario model: the nested `targets` form, `installs`, their rules, and the flattening accessor every reader of `targets` moves to.
2. Lease and lifecycle: one device per group, the device count per backend, one device-wide preparation per group, each starting member's environment started on the shared device by a path that repeats no destructive precondition, a later member's environment started at its first `foreground`, and the current primary that `setPrimaryTarget` moves at run time. This unit opens with the on-device checks above.
3. Scenario model: the `installApp` step's shape, and its check that `from` is a member of the step target's group, including its placement rules inside `web:`/`app:` and `interrupts`, and the `setPrimaryTarget` step, with its top-level-only placement and the in-order tracking of the current primary, and how both steps behave when a component or group expands them. The tracked primary is a local of
the walk, never a write to `primary_target`, since `primaryTarget` is checked against the first entry
at several points. Top-level `expect` entries that omit `target` are resolved with it, where today
they group under the run's fixed primary. Both stamping points skip `setPrimaryTarget`, and expansion
records the component chain.
4. Run preflight: check each `installApp.from` target against the config (its `appPath` exists, building it where the run already builds one), refuse starting members of one group that share an identifier, and refuse a group whose members differ in backend platform, device route, or system locale.
5. XCUITest environment and driver: the install action, the digest reset, the terminate, `foreground`'s launch, readiness wait, and launch marker, built beside the relauncher that already holds the effective config, scenario, and driver, and the front-app check in `xcuitest_driver`.
6. Android environment and driver: the install action with the downgrade error, `foreground`'s launch, readiness wait, and launch marker built beside the relauncher, the front-app check in `adb_driver`, and the split of `deviceControl.appLifecycle`.
7. Backend handling: web and device-cloud rejection, and the code-generation marker.
8. Documentation in both languages: `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/architecture.md`.
9. Showcase demo: an older build as its own target and the update scenario, plus a companion-app scenario, on both platforms.

## Alternatives considered

| Option | Why not |
|---|---|
| An `apps` map inside each target, with `primaryApp` and a `startApp` precondition | Builds a second, per-target registry of apps beside `targets`. Identifiers belong to apps, so `bundleId` and `appPath` would move off the target, which breaks every existing config and touches more than 90 files, and it adds a primary rule beside `primaryTarget`. |
| A header list of build sources outside the groups, `installSources: [showcase]` | Cannot say which device receives the build once a scenario holds more than one. Making the install sources members of a device group ties each build to its device. |
| Members written as `{ start, later }` objects inside `targets` | Gives `targets` three shapes (a name, an array, an object) and changes every reader. A separate `installs` key leaves `targets` as the flat-or-nested list it already is. |
| An `on: <target>` key that makes one declared target share another's device | Adds a keyword beside `targets`. The nested array states the same fact with no new key. |
| Drive a companion app only through the `app: { bundleId, steps }` block | Changes nothing, but the block is iOS only and puts a bundle identifier in the scenario, against prime directive 3. Android could not drive a companion app at all. |
| `installApp: { path }` with the path written in the step | The scenario would carry a build artifact path. That leaks a per-app detail into the scenario, breaks on another machine, and violates the app-agnostic principle. |
| Two scenarios run in sequence with `reinstall: overwrite` | No DSL change, but the journey splits in two, the run order becomes an unwritten contract, and a failure no longer reads as one journey. |
| List both builds in a group and install both at the start | The second install overwrites the first, so the scenario could never begin on the old build. `installs` holds the new build back, and preflight refuses two starting members with one identifier. |
| Name the installed member on every step after an update | Correct, but every later step, assertion, and interrupt repeats the same name, and a forgotten one resolves to the retired member and fails. |
| Let `installApp` switch the primary target by itself, explicitly (`becomes: primary`) or when the identifiers match | Hides the point where routing changes inside a step that reads as an install, and an identifier match cannot express a companion. A separate `setPrimaryTarget` step names the switch on its own line, and needs no mid-run config swap because each member already owns its config, driver, and environment. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: nested `targets` form, `installs`, and flattening accessor
- [ ] Unit 2: device-group lease and lifecycle, after the on-device checks
- [ ] Unit 3: `installApp` and `setPrimaryTarget` steps
- [ ] Unit 4: run preflight and validation
- [ ] Unit 5: XCUITest environment and driver
- [ ] Unit 6: Android environment, driver, and `foreground`
- [ ] Unit 7: backend handling
- [ ] Unit 8: documentation in both languages
- [ ] Unit 9: showcase demo, update and companion scenarios

## References

- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) and [BE-0436](../BE-0436-primary-target-default/BE-0436-primary-target-default.md): multi-target scenarios and the primary target this design builds on.
- [BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md): the precedent for changing the app's state after launch.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): the provisioning profile a device-cloud lease carries.

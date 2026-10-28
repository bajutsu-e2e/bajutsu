**English** · [日本語](BE-0473-config-run-group-and-interrupts-ja.md)

# BE-0473 — Dissolve the target's run group and declare every reactive handler under interrupts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0473](BE-0473-config-run-group-and-interrupts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0473") |
| Implementing PR | [#2242](https://github.com/bajutsu-e2e/bajutsu/pull/2242) (units 3, 5, 6) |
| Topic | Driver & backend architecture |
| Related | [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md), [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md), [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md) |
<!-- /BE-METADATA -->

## Introduction

A target in `bajutsu.config.yaml` groups its settings by purpose under eleven keys. One of those
keys, `run`, holds six fields that share no purpose. Two of the six, `systemAlertHandling` and
`tipKitHandling`, clear an interruption that surfaces at an unpredictable point in a run. A third
key, `hooks.interrupts`, already exists for that purpose. A reader who wants to know how a target
handles interruptions therefore has to read two groups, written in two different forms.

This item removes `run` and gives each of its fields the group whose purpose it serves:

- `systemAlertHandling` and `tipKitHandling` become entries of `hooks.interrupts`, beside the
  existing handlers for in-app screens. The scenario's own `interrupts` accepts the same entries.
- `erase` becomes the strongest value of `app.reinstall`: `overwrite`, `clean`, or `erase`.
- `network`, `visualCompare`, and `secrets` move into `evidence`.

The item also settles three smaller points in the same file. A target-level `notify` stops
overriding the top-level list, so the top-level list alone says where results go. The documented
meaning of `services` widens to cover the mailbox it already holds. And the loader accepts top-level
keys that start with `x-`, so a file can hold YAML anchors that targets share. Like BE-0452, the
change drops backward compatibility: an old key fails to load and names its new location.

## Motivation

The `run` group (`bajutsu/common/config/schema/run_policy.py`) collects six fields under the label
"the policy of a run". The six fields answer three unrelated questions:

| Field | Question it answers |
|---|---|
| `erase` | What state does the device start in? |
| `network`, `visualCompare`, `secrets` | What does a run record, how does it compare, and what does it mask? |
| `systemAlertHandling`, `tipKitHandling` | What clears an interruption that appears mid-run? |

BE-0452 regrouped the flat target keys by purpose, and `run` is the one group that kept a mixed
list. The mixture costs readers in two places.

First, interruption handling is split across two groups. `hooks.interrupts` lists entries that match
a screen in the app's own accessibility tree and run steps to clear it, such as the Android
"Application Not Responding" dialog the showcase dismisses. `run.systemAlertHandling` answers
operating-system (OS) prompts, such as the notification permission request, by naming the prompt
and the choice. `run.tipKitHandling` turns on a built-in guard for Apple's TipKit popovers. At
runtime the three already travel together in `RunDefaults`
(`bajutsu/common/config/effective/run_defaults.py`). Only the config file keeps them apart. A
reviewer who checks "what does this target dismiss on its own?" must read `hooks` and `run`, and
must know that the two forms mean the same kind of thing.

Second, a field outside its purpose creates a dependency across groups. `runsOn.seedPhotos` seeds
the iOS photo library on the erase path alone, so the loader checks that an effective `run.erase`
exists before any device work. Meanwhile the Android lifecycle already reads `erase` and
`reinstall: clean` as one decision about what to wipe, and acts on them identically
(`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`). The schema
spreads that one decision over `app.reinstall` and `run.erase`.

Two smaller inconsistencies sit in the same file. `notify` scopes a webhook to some targets in two
ways: an endpoint's own `targets:` filter, and a target-level `notify` that replaces the top-level
list. Both answer "which results go where", and neither tells the reader the other exists. And the
`Services` docstring says `services` holds "the servers a run brings up", yet `services.mailbox` is
an external inbox that the run polls and never starts.

Finally, sibling targets repeat whole blocks. `demos/showcase/showcase.config.yaml` writes the same
`driver.runner.testRunner` path six times and the same `app.launch.env` fourteen times.
`defaults.platforms.<platform>` already removes most of that repetition, but the file does not use
it. Variants of one app, such as `showcase-swiftui-noax`, repeat most of the `app` and `driver` blocks of the target they vary.
YAML anchors would let them share the rest, yet the loader rejects any top-level key outside the
schema. A file has nowhere to park the anchor.

Once this item ships, a reader can check three outcomes:

- `targets.<name>` and `defaults` no longer accept `run`. Loading an old file fails and names the
  new location of each field.
- One list, `hooks.interrupts`, shows every handler a target applies on its own. The scenario's own
  `interrupts` shows the scenario's.
- `demos/showcase/showcase.config.yaml` states the runner path and the launch environment once.

## Detailed design

### Three kinds of interrupt entry

An entry of `hooks.interrupts` takes one of three forms. The form is decided by the key the entry
writes, so a reader never infers it from a value's type.

```yaml
hooks:
  interrupts:
    - condition: { exists: { id: [aerr_wait] } }   # an in-app screen, as today (BE-0314)
      steps:
        - tap: { id: [aerr_close] }
    - systemAlert: notifications                   # an OS prompt (was run.systemAlertHandling.rules)
      choice: grant
    - systemAlert: savePassword
      choice: deny
      frozenScreenTimeout: 20                      # tuning that belongs to this prompt alone
    - tipKit: dismiss                              # Apple's TipKit popover (was run.tipKitHandling)
```

| Form | Required keys | Optional keys | Replaces |
|---|---|---|---|
| Screen | `condition` | `steps` | unchanged |
| System alert | `systemAlert`, `choice` | `pollInterval`, `frozenScreenTimeout` | `run.systemAlertHandling` |
| TipKit | `tipKit` | none | `run.tipKitHandling` |

`systemAlert` takes the `SystemAlertPrompt` names `systemAlertHandling.rules` already uses: `notifications`, `tracking`,
`paste`, `savePassword`, and `localNetwork`. `choice` takes `grant`, `deny`, or a new value,
`leave`. `leave` tells the guard to answer nothing for that prompt. The `handleSystemAlert`
step shares the same `choice` vocabulary and rejects `leave` at load, since a step that taps no
button has no meaning. `leave` replaces
`systemAlertHandling: false`, which a scenario such as `paste_system_alert.yaml` writes today to
prove that its own `handleSystemAlert` step does the work. `tipKit` takes `dismiss` or
`leave`. A scenario writes `- tipKit: leave` to turn off a target's TipKit entry, since a tip is
sometimes the subject of the scenario's own assertion.

An entry that mixes keys from two forms fails at load, and the error names both forms. The
existing rules for an entry still hold: a target-level entry may not set `target`, and its steps may
not use `group` or `setMocks`.

### Tuning values on an entry

The guard has two tuning values today. Each one moves onto the entry it serves.

- `frozenScreenTimeout` arms only when a scenario declares an in-app prompt such as `savePassword`.
  It is therefore accepted on a `systemAlert` entry whose prompt is answered in the app's own tree,
  and rejected on any other entry. The value describes the app, such as an unlabelled loading screen
  that outlasts the default. A scenario entry for the same prompt that omits the value therefore
  inherits it from the target's entry.
- `pollInterval` sets how often the guard checks SpringBoard while a wait is pending. Each
  scenario's guard has one poll, so the value resolves layer by layer, in today's order: the
  scenario, then `--alert-poll-interval`, then the target. The first layer that sets a value wins,
  so a scenario can still widen the interval above the target's. Within one layer, two entries may
  set different values; the layer then takes the smallest. A shorter interval satisfies every entry
  that asked for a longer one, so the smallest value never weakens another entry's request. The
  built-in one second applies when no layer sets one.

### Layering between target, scenario, and command line

The scenario's own `interrupts` accepts the same three forms. The layering rules carry over from
the two keys this item merges:

| Entry form | How the target's and the scenario's entries combine |
|---|---|
| Screen | Concatenated, target entries first (BE-0314) |
| System alert | Per prompt: a scenario entry replaces the target entry's `choice` for the same prompt and inherits an omitted `frozenScreenTimeout` from it; `pollInterval` resolves by layer instead, as *Tuning values on an entry* describes; other prompts from both apply (BE-0401, BE-0406) |
| TipKit | The scenario's entry (`dismiss` or `leave`), else the target's; absent from both means off |

A scenario entry with `choice: leave` replaces the target's answer for that prompt, which is how a
scenario turns off one inherited answer. A scenario that today writes `systemAlertHandling: false`
to handle one prompt itself, such as `push.yaml`, writes `- systemAlert: notifications` with
`choice: leave` instead. Unlike `false`, the entry names the prompt it leaves. A prompt the target
adds later therefore still applies to that scenario. Two entries for the same prompt within one layer fail at
load, as two `rules` for one prompt fail today.

The command-line flags keep their names and their place in the precedence.
`--no-system-alert-handling` turns the guard off for every scenario. `--system-alert-handling` turns
it back on. `--alert-poll-interval` keeps its place between the scenario's value and the target's.
`--ios-tipkit-handling` and `--no-ios-tipkit-handling` add or remove the TipKit entry for every
scenario. Only their help text changes, to name `interrupts` instead of `run`.

### Erase as the strongest reinstall

`app.reinstall` gains a third value. The three values form one ladder of how much state a run
wipes before it starts:

| Value | What the run does before launch |
|---|---|
| `overwrite` | Install over the existing app and keep its data |
| `clean` | Uninstall, then install (the built-in default) |
| `erase` | Wipe the whole device, then install (was `run.erase: true`); on Android, which has no device-wide wipe, the same as `clean` |

`app.reinstall` exists on iOS and Android. The web platform keeps no `reinstall`, since every web
run already starts in a fresh browser context. A scenario sets the same value through
`preconditions.app.reinstall`. On a web target the value has no effect, as `run.erase` has none
today.

`--erase` makes the effective value `erase`. `--no-erase` lowers an effective `erase` to `clean`
and leaves `overwrite` and `clean` alone. The ladder changes one behavior on purpose. Today `erase`
and `reinstall` are independent, so a target with `run.erase: true` and `app.reinstall: overwrite`
under `--no-erase` keeps the app's data. After this item, such a target can state `erase` alone,
and `--no-erase` uninstalls the app. A scenario that wants to keep the data writes
`preconditions.app.reinstall: overwrite` instead of relying on the flag. `runsOn.seedPhotos` now requires an effective
`app.reinstall: erase`, and its error message names that key.

### Evidence gains three fields

| Field | Meaning | Default |
|---|---|---|
| `evidence.collectNetwork` | Collect the app's network exchanges for `request` assertions, a precondition of the `network` capture kind | `true` |
| `evidence.visualCompare` | How `visual` assertions compare, `exact` or `pixelmatch` | built-in |
| `evidence.secrets` | Environment variable names usable as `${secrets.X}`, masked in evidence | empty |

`evidence.secrets` keeps the union rule of `run.secrets`. A target adds to `defaults.evidence.secrets`
and never removes from it, and a `null` on the list fails at load. The merge code
(`bajutsu/common/config/schema/_merge.py`) moves the path in `_UNION_LISTS` and `_NO_NULL` and
drops `run` from both.

### A scenario's preconditions

`preconditions.run` goes away with the target's `run`:

| Old scenario key | New location |
|---|---|
| `preconditions.run.erase: true` | `preconditions.app.reinstall: erase` |
| `preconditions.run.erase: false` | `preconditions.app.reinstall: clean`, or `overwrite` to keep the app's data |
| `preconditions.run.systemAlertHandling` | entries in the scenario's own `interrupts` |
| `preconditions.run.tipKitHandling: true` | `- tipKit: dismiss` in the scenario's own `interrupts` |
| `preconditions.run.tipKitHandling: false` | `- tipKit: leave` in the scenario's own `interrupts` |
| `preconditions.run.inheritSetup` | `preconditions.inheritSetup` |

`inheritSetup` decides whether the targets' `hooks.setup` runs before this scenario. The flag
concerns the scenario's preconditions rather than a run policy, so it moves up one level.

### Notification scope

A target-level `notify` goes away. The top-level `notify` list alone says where results go, and an
endpoint's `targets:` filter limits it to some targets. A team that sent one target's results to
its own channel writes that channel at the top level with `targets: [<name>]`. Excluding one target
from a team-wide endpoint now means listing the other targets on that endpoint. The configs in this
repository need no such exclusion today.

### The meaning of services

The `Services` docstring and `docs/configuration.md` describe `services` as "the services a run
depends on: the ones it starts and the ones it reaches". The keys stay where they are. The stale
comment in `bajutsu/common/config/effective/effective.py` that still names `targets.<name>.mailbox`
names `services.mailbox`.

### Sharing values between targets

The schema already merges `defaults` into every target, and `defaults.platforms.<platform>` into the
targets of that platform. This item makes the showcase configs use that mechanism, and adds one
loader change so YAML anchors can cover the remaining case.

The loader ignores a top-level key whose name starts with `x-`. Such a key can host an anchor, and a
target merges it with YAML's `<<` key:

```yaml
x-swiftui-app: &swiftui-app
  launch: { env: { SHOWCASE_UITEST: "1" } }
  idNamespaces: [stable, horse, search, log, notice, perm, sys, net]

targets:
  showcase-swiftui-noax:
    platform: ios
    app:
      <<: *swiftui-app
      id: com.bajutsu.showcase.ios.swiftui.noax
      idNamespaces: []
```

YAML's `<<` merges one level deep. The anchor therefore sits at the level of the block that
differs, here `app`, and not at the level of the whole target. `docs/configuration.md` states that
limit next to the example. The `x-` prefix follows the extension-field convention of Docker
Compose files, so a reader may already know it.

### Where each old key goes

| Old key | New location |
|---|---|
| `run.erase` | `app.reinstall: erase` |
| `run.network`, `run.visualCompare`, `run.secrets` | `evidence.collectNetwork`, `evidence.visualCompare`, `evidence.secrets` |
| `run.systemAlertHandling.rules[]` | one `systemAlert` entry per rule in `hooks.interrupts` |
| `run.systemAlertHandling.pollInterval`, `.frozenScreenTimeout` | the same keys on the entries they serve |
| `run.systemAlertHandling: false` | `choice: leave` on the prompts to leave, or `--no-system-alert-handling` |
| `run.systemAlertHandling.visionInstruction` | removed; `run` already rejects it (BE-0402), and `record` / `crawl` take `--alert-vision-instruction` |
| `run.tipKitHandling: true` | `- tipKit: dismiss` in `hooks.interrupts` |
| `defaults.run.*`, `defaults.platforms.ios.run.tipKitHandling` | the same moves under `defaults` |
| `targets.<name>.notify` | top-level `notify` with `targets: [<name>]` |
| scenario fields | see *A scenario's preconditions* |

`TARGET_KEYS` drops `run` and, with unit 5, `notify`; `DEFAULTS_KEYS` drops `run`. A target
accepts nine keys as a result. `PlatformDefaults` and
the platform registry drop their `run` group, since iOS's `tipKitHandling` was its only field. The
resolved `Effective` keeps `run_defaults` as an internal grouping, filled from the new locations.

### Prime-directive compliance

- **AI never judges.** The change moves declarations; the guard's matching stays deterministic and
  model-free.
- **Determinism first.** A system-alert entry still names the prompt it expects. An alert no entry
  identifies is still left alone rather than answered by a guessed button (BE-0406).
- **App-agnostic.** Every per-app difference stays in `targets.<name>`.

### Out of scope

- **Writing both id spellings in a selector**, such as `id: [stable.row.1, stable_row_1]`. The
  dotted and underscore forms are a selector decision, not a config-structure one. BE-0221 chose the
  explicit candidate list over a driver-side rewrite, because a rewrite could conflate distinct ids
  such as `a.b` and `a-b`. The gap that matters is verification: a scenario no Views target runs
  carries underscore candidates nobody checks. A separate GitHub issue ([#2233](https://github.com/bajutsu-e2e/bajutsu/issues/2233)) tracks running `device.yaml`
  on the Views lane and deleting the candidates no lane resolves.
- **An `extends:` key** for one target to inherit another; see *Alternatives considered*.
- **Reading the old keys**, and a migration command.
- **Renaming the command-line flags.**

### Work breakdown

1. **Interrupt entries.** Extend `Interrupt` with the `systemAlert` and `tipKit` forms, the
   `leave` choice, and the per-entry tuning values. Reject mixed forms. Resolve the layering in
   `run_defaults` and the alert guard's config, keeping each flag's place in the precedence. Tests
   cover each form, each layering rule, the inheritance of an omitted tuning value, and the
   smallest value within one layer.
2. **Reinstall ladder.** Add `erase` to `Reinstall` and to the scenario's own `PreconditionsApp.reinstall` literal, map `--erase` and `--no-erase`, move the
   `seedPhotos` check, and update the iOS and Android lifecycles to read the one value.
3. **Evidence fields.** Add the three fields to `Evidence` and move the union and no-null paths in
   `_merge.py`.
4. **Remove `run`.** Drop `RunPolicy`, `run_model`, the registry's `run` group, `PreconditionsRun`,
   and `SystemAlertHandling`, with an error per removed key that names its new location. Move
   `inheritSetup`. Regenerate the JavaScript Object Notation (JSON) Schema that `bajutsu config
   schema` prints.
5. **Notification scope.** Remove `TargetConfig.notify` and its resolution in `resolve.py`.
6. **Anchor host keys.** Make `Config` ignore top-level keys starting with `x-`, with a test that a
   merged anchor validates like a written-out block.
7. **Configs and scenarios.** Rewrite `demos/` configs and scenarios to the new keys. Move the
   showcase's shared values into `defaults.platforms` and anchors.
8. **Documentation.** Update `docs/configuration.md`, `docs/scenarios.md`, `docs/cli.md`,
   `docs/architecture.md`, and `DESIGN.md`, with their `docs/ja/` mirrors. Correct the `services`
   wording and the stale `effective.py` comment.

## Alternatives considered

- **Keep `run` with `erase`, `network`, and `visualCompare` alone.** Moving the two handlers and
  `secrets` out would shrink `run`, but its definition would stay "whatever else a run does". The
  next field without an obvious home would land there, and the group would grow mixed again.
- **Keep the handlers apart in a new `hooks.guards` block.** This keeps the screen form and the
  built-in guards in two lists with distinct shapes. A reader still has two places to read for one
  question. The three forms differ in their keys, so one list stays unambiguous.
- **Keep the tuning values in one guard-wide block.** A `hooks.systemAlertGuard: { pollInterval,
  frozenScreenTimeout }` block would mirror the runtime, where both values are guard-wide. It would
  also bring back a second place to read. `frozenScreenTimeout` already serves one kind of prompt,
  and the layer-then-smallest rule gives `pollInterval` one meaning, so the values sit on the
  entries.
- **Add a whole-guard switch for one scenario.** A `- systemAlert: all` entry with `choice: leave`
  would keep the full power of today's `systemAlertHandling: false`. It would also add a special
  prompt name, and an entry that silences prompts the scenario never names. Leaving each prompt by
  name keeps every answer a scenario turns off visible in the file, and the run-wide
  `--no-system-alert-handling` remains for a whole run.
- **Move `erase` into `runsOn`.** `erase` is device state, like `locale` and `seedPhotos`. But the
  scenario's `preconditions.runsOn` is keyed by platform, so a multi-platform scenario would repeat
  `erase` once per platform. The Android lifecycle already reads `erase` and `reinstall` as one
  decision, so `app.reinstall` fits the behavior.
- **Move `secrets` to the top level of a target.** `secrets` also feeds `${secrets.X}`, which is
  input rather than evidence. As a separate key it would bring the target to ten keys. The field
  exists because of masking, though, and `evidence.redact` already holds the masking rules beside
  it.
- **Keep a target-level `notify` and drop the endpoint filter.** This would layer `notify` like the
  other groups, with `defaults.notify` and a target override. The full set of sinks would then be
  spread across every target. A team reads one list to answer "who hears about failures".
- **Add an `extends:` key.** A target could inherit another target with a deep merge. This would
  reach nested fields that YAML's one-level merge cannot. It would also add a tenth key back,
  plus rules for cycles and chains. Should the showcase's variants still repeat nested blocks once
  anchors are in use, this alternative becomes worth revisiting.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: interrupt entries
- [ ] Unit 2: reinstall ladder
- [x] Unit 3: evidence fields
- [ ] Unit 4: remove `run`
- [x] Unit 5: notification scope
- [x] Unit 6: anchor host keys
- [ ] Unit 7: configs and scenarios
- [ ] Unit 8: documentation

Log:

- [#2242](https://github.com/bajutsu-e2e/bajutsu/pull/2242): Landed units 3, 5, and 6. `evidence` gained `collectNetwork`, `visualCompare`, and
  `secrets`, and `run` rejects those three keys with an error that names the new location. A target
  no longer accepts `notify`, and the loader ignores top-level `x-` keys. The demo config and the
  documents that name these keys moved in the same change, so units 7 and 8 stay open for the keys
  later units move. One deviation from *Notification scope*: an endpoint's `targets:` filter matches
  scenario names, not config target names, so no config-level replacement exists for a target's own
  `notify`. The maintainer chose to remove it as written regardless.

## References

- [`bajutsu/common/config/schema/run_policy.py`](../../bajutsu/common/config/schema/run_policy.py):
  the group this item removes.
- [`bajutsu/common/config/schema/hooks.py`](../../bajutsu/common/config/schema/hooks.py) and
  [`bajutsu/common/scenario/models/steps/interrupt.py`](../../bajutsu/common/scenario/models/steps/interrupt.py):
  the interrupt list this item extends.
- [`bajutsu/common/scenario/models/scenario/system_alert_handling.py`](../../bajutsu/common/scenario/models/scenario/system_alert_handling.py):
  the declaration that becomes interrupt entries.
- [`bajutsu/common/config/schema/_merge.py`](../../bajutsu/common/config/schema/_merge.py): the
  union and no-null rules.
- [`docs/configuration.md`](../../docs/configuration.md): the target's keys as documented today.

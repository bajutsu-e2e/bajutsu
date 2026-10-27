**English** · [日本語](BE-XXXX-runson-destination-blocks-ja.md)

# BE-XXXX — Group runsOn by destination and drop the scenario's runsOn

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-runson-destination-blocks.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

A target's `runsOn` limits where the target can run. This item groups that limit by destination:
`runsOn.ios`, `runsOn.android`, and `runsOn.browser`. The browser block already names its
destination; the iOS and Android fields sit bare beside it today. The item also removes
`preconditions.runsOn` from the scenario, because where a run can happen belongs to the target. A
scenario's device state (`locale`, `seedPhotos`) moves to `preconditions.device.ios`. No field
changes meaning: every condition matches devices exactly as it does today, from its new path.

## Motivation

The shape of `runsOn` is inconsistent. `runsOn.browser` names a browser explicitly, while
`runsOn.model` implies a mobile device without saying which platform. A reader of `runsOn.os: "18"`
must look at the target's `platform` to learn that the range means iOS. The gap grows with every
field a platform adds, and the companion proposal *Let runsOn.ios name a device family and
runsOn.browser any of several engines* adds two. Grouping by destination makes every field say what
it limits: `runsOn.ios.os` reads as an iOS version, and `runsOn.browser.engine` as a browser.

A scenario's `preconditions.runsOn` mixes two concerns. Where a run can happen is a property of the
target, yet a scenario may override it field by field. The scenarios that use the block in this
repository set no condition at all. Four demo scenarios set `locale` or `seedPhotos`, which are
device state rather than a limit on where a run happens. An override also needs a cross-layer rule
each time the target gains two fields that exclude each other, because a scenario's block folds in
as flat conditions after the target's own validation. Removing the scenario's block leaves one
place that says "where", and gives device state a block named for it.

The outcome a reader can check after this ships is concrete. Every `runsOn` field in a config sits
under the key of its destination, so `bajutsu config schema` shows each platform's limits in one
block. No scenario in the repository carries `runsOn`, and an old spelling fails the load with the
path that replaces it.

## Detailed design

### The shape of `runsOn`

Each target writes one destination block. Today each platform allows exactly one destination.

| Platform | Block | Fields |
|---|---|---|
| iOS | `runsOn.ios` | `model`, `os`, `kind`, `locale`, `seedPhotos` |
| Android | `runsOn.android` | `avd`, `apiLevel` |
| web | `runsOn.browser` | `engine`, `version`, `emulate` |
| fake | none | none |

`platform` and `runsOn` say different things. `platform` names the kind of app under test, which
decides how the app is given (`app.id` or `app.url`) and which driver runs it. `runsOn` limits
where that app can run. The two coincide for every platform today, but they need not. A web app
that runs in mobile Safari on an iOS Simulator has `platform: web` and an iOS destination, and a
Flutter app would run on both iOS and Android. The loader therefore checks that the pair is an
allowed one, not that the two names are equal. The allowed pairs are the four rows above. A
`platform: ios` target with `runsOn.browser`, for example, fails and names the destinations that
`ios` allows. A later proposal can widen a platform's destinations without reshaping `runsOn`. Team
defaults may hold several blocks under `defaults.runsOn`. Each target takes the block for its own
platform, so a team default for iOS no longer reaches a web target.

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) rejected the
platform name as a key at the target level. That alternative nested the same word twice, as in
`targets.web.web:`, and its key stood in for `platform`. The key here sits one level down, inside
`runsOn`, and names where the run happens, as `runsOn.browser` already does. It does not stand in
for `platform`: the platform still decides the shape of `app` and `driver`.

`locale` and `seedPhotos` move with the iOS fields for now. They are device state, not a limit on
where a run happens. A separate proposal decides whether a target's device state leaves `runsOn`
altogether; this item moves the scenario's device state alone.

### A scenario no longer declares `runsOn`

`preconditions.runsOn` leaves the scenario schema. Each of its two kinds of field gets its own path.

- **Conditions** (`model`, `os`, `avd`, `apiLevel`, `browser.engine`, `browser.version`) have no
  scenario-level replacement. A scenario that needs an iPad names a target that runs on one, such as
  `targets: [app-ipad]`. The condition stays where every other limit on the destination lives. A
  scenario that multiplied its runs through a list or a range gets the same runs from a target that
  declares the list or the range.
- **Device state** (`locale`, `seedPhotos`) moves to `preconditions.device.ios`. The semantics stay
  the same. A scenario's locale replaces the target's and forces a cold respawn when it differs
  ([BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)).
  A scenario's `seedPhotos` replaces the target's list and needs an effective `run.erase`.

With the scenario's block gone, a target's conditions reach `form_target_runs` in
`bajutsu/common/devices/runs.py` alone, apart from the `--browser` flag. `effective_conditions` in
`bajutsu/common/config/preconditions.py` no longer merges a scenario. `runs_on_problems` gives way
to a check of the new device block. A multi-target scenario still runs every combination of its
targets' runs.

### Migration of existing configs and scenarios

An old spelling fails the load and names where the key went, as BE-0452 did for its own moves. The
loader never accepts both spellings, so a config has one way to say each thing.

| Old key | New key |
|---|---|
| `runsOn.model`, `os`, `kind`, `locale`, `seedPhotos` | `runsOn.ios.<same field>` |
| `runsOn.avd`, `apiLevel` | `runsOn.android.<same field>` |
| `runsOn.emulate` | `runsOn.browser.emulate` |
| `preconditions.runsOn.ios.locale`, `seedPhotos` | `preconditions.device.ios.<same field>` |
| `preconditions.runsOn.<platform>.<condition>` | a target that declares the condition |

### Examples by destination

```yaml
targets:
  app-sim:                     # a Simulator
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios: { model: "iPhone 16", os: "18" }

  app-device:                  # a real device, taken from --udid
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios: { kind: device }

  site:                        # a browser
    platform: web
    app: { url: https://example.com }
    runsOn:
      browser: { engine: firefox, emulate: "iPhone 15" }
```

A scenario names the target it needs, and sets device state under `preconditions.device`.

```yaml
targets: [app-sim]
preconditions:
  device:
    ios: { locale: ja_JP }
```

### Schema and docs

- The platform registry in `bajutsu/common/config/schema/platform/` wraps each platform's `runsOn`
  model in its destination key. `emulate` moves from `WebRunsOn` into the `browser` block.
- The scenario schema drops `preconditions.runsOn` and gains `preconditions.device.ios`.
- `bajutsu config schema` emits the destination blocks.
- [Configuration](../../docs/configuration.md#device-conditions-runson) and its `docs/ja/` mirror
  take the new shape, the migration table, and the examples. [CLI](../../docs/cli.md) drops the
  scenario's `runsOn` section. [Architecture](../../docs/architecture.md) updates the
  `common/config/` row.

### Prime-directive compliance

No LLM enters a run, and no condition changes how it matches. The item moves fields to new paths
and removes an override, so every difference stays in config, now in one place per target.

### Out of scope

- Moving a target's device state (`locale`, `seedPhotos`, `emulate`) out of `runsOn`. A separate
  proposal decides its home.
- A platform with more than one destination, such as a web app in mobile Safari. This item keeps
  one destination per platform and leaves room to add more.
- New conditions, such as a device family or a set of browser engines. The companion proposal
  *Let runsOn.ios name a device family and runsOn.browser any of several engines* adds them on top
  of this shape.

### Work breakdown

1. **Destination blocks.** Wrap each platform's `runsOn` model in `ios`, `android`, or `browser`,
   check that `platform` allows the key, take per-platform team defaults, move `emulate` under
   `browser`, and fail each old key with its new path. Rewrite the `demos/` configs and the test
   fixtures. Extend the JSON Schema output.
2. **Scenario block.** Remove `preconditions.runsOn`, add `preconditions.device.ios` for `locale`
   and `seedPhotos`, and fail the old block with the replacement for each field. Drop the scenario
   merge from `effective_conditions`, and rewrite the four demo scenarios that set device state.
3. **Docs.** Update the bilingual docs named above with the new shape, the migration table, and
   the examples.

Each unit carries its own tests: the allowed-pair rule, each migration message, and the device
block's replace semantics.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Keep `runsOn` flat | `runsOn.model` implies a mobile device without naming its platform, while `runsOn.browser` names its destination. The shape grows less consistent with each new field. |
| Key the web block `runsOn.web` | It names the platform rather than what runs the page. `runsOn.browser` already names the destination, and the iOS and Android keys follow it. |
| Keep the scenario's `runsOn` and add cross-layer rules | Where a run can happen belongs to the target. The override needs a rule for each pair of exclusive fields, and the block's real uses in this repository were device state. |
| Drop `platform` and infer it from the `runsOn` key | `platform` names the app under test and `runsOn` its destination, which can differ, as a web app in mobile Safari shows. Inferring would also make `runsOn` mandatory with exactly one key, an empty block just to say "any iOS device", and a key for the fake platform. |
| Accept the old spelling for a grace period | Two spellings would coexist in the loader, the JSON Schema, and the docs. A failing load that names the new key moves a config in one edit. |
| Move the target's device state out of `runsOn` in the same item | The right home for device state is a separate decision, and folding it in would make this breaking change larger. The scenario's device state moves now because removing the scenario's block leaves it no place. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: destination blocks
- [ ] Unit 2: scenario block
- [ ] Unit 3: docs

## References

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the `runsOn` groups and the scenario `runsOn` this item reshapes.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md): the scenario locale whose semantics move to `preconditions.device.ios`.
- `bajutsu/common/config/schema/platform/` and `bajutsu/common/config/preconditions.py`: the schema and the scenario merge this item changes.

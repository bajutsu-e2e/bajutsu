**English** · [日本語](BE-XXXX-runson-family-and-any-of-ja.md)

# BE-XXXX — Let runsOn name a device family or any of several browsers instead of one exact model

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

A target's `runsOn` names the device it needs by an exact string. On iOS, `model` must equal the
Simulator's device-type name, such as `"iPad Pro 13-inch (M4)"`. On the web, `browser.engine` is
one engine, and a list of engines multiplies the runs. This item adds two ways to say "any of
these" without naming a model. On iOS, `family: [iphone, ipad]` takes one Simulator of the first
listed family that the pool has. On the web, `browser: { anyOf: [chromium, firefox, webkit] }` takes
the first listed engine that launches. Both resolve to one run, and the manifest records what
each run took.

## Motivation

Today a config cannot say "an iPad, any iPad". It must name one device type, and Apple renames and
replaces device types with each Xcode release. A target pinned to `"iPad Pro 13-inch (M4)"` stops
matching on the first host whose Xcode lacks that type. The run then fails with `runsOn.ios.model`
unmet, although the scenario only needed a tablet-sized screen. A target that names no model avoids
the failure, but it also loses the intent: the run takes the first model in pool order, which may be
an iPhone on one host and an iPad on another.

The list syntax does not fill the gap. A list on `model` or `browser.engine` means "run once per
value", so `model: [iPhone ..., iPad ...]` doubles the runs when the author wanted either one. The
config has no spelling for "one of these will do".

The outcome a reader can check after this ships is concrete. A config written as
`family: [iphone, ipad]` keeps resolving after an Xcode update renames every device type, with no
edit to the config. Each run's label, manifest, and `doctor` listing then show which family and
model the run took.

### Scope

The item covers iOS (`family`) and the web (`browser.anyOf`). It leaves Android alone. An Android
target that omits `avd` already accepts any emulator, so "any" has a spelling there today. Android
form factor (phone or tablet) is a separate fact that would need its own device read, so it stays
out of this item.

## Detailed design

### What the config says

| Platform | Field | Meaning |
|---|---|---|
| iOS | `runsOn.family: iphone` | one Simulator whose device-type family is iPhone |
| iOS | `runsOn.family: [iphone, ipad]` | one Simulator of the first listed family the pool has |
| iOS | `runsOn.family` omitted | any Simulator, as today |
| web | `runsOn.browser.anyOf: [chromium, firefox]` | the first listed engine that launches |

A list in `family` and in `anyOf` is a priority list. The first entry wins when it fits, and the
run falls to the next entry only when no device or engine fits the earlier one. Order in the config
therefore decides the result, and the same pool always resolves the same way.

`family` takes `iphone` and `ipad`. Both are lowercase and case-sensitive. The reader maps the
`productFamily` that `simctl list devicetypes` reports for each device type (`iPhone`, `iPad`).
A device type of another family (Apple TV, Apple Watch) is readable but never matches.

The web has one spelling for "any engine": `anyOf` with every engine listed. Omission cannot
serve, because the engine has a built-in default, `chromium`. iOS has no default model, so omission
already means "any", and a target clears an inherited `family` with `family: null`.

### Rules the loader enforces

All rules run on the merged block, after team defaults and a scenario's `preconditions.runsOn`
have folded in, as the existing `kind: device` rule does.

- `family` and `model` together fail the load and name both fields. A target clears an inherited
  one with `null`.
- `family` on a target with `kind: device` fails the load, like `model` and `os` today. A physical
  device has no pool to match.
- `anyOf` and an explicit `engine` together fail the load. A target clears an inherited `engine`
  with `engine: null`; the built-in `chromium` then applies only when `anyOf` is also absent.
- `anyOf` with `browser.version` fails the load, for the reason a list of engines already does:
  each engine numbers its versions differently.
- An empty list, a repeated entry, and an unknown family or engine fail the load and name the
  allowed values.

`family` stays a condition that composes with `os`. The version range multiplies runs as before.
Each run of a major version picks its own family from the devices at that version.

### How a run resolves

`form_target_runs` in `bajutsu/common/devices/runs.py` already receives the pool's device facts. It
resolves the priority list there, once per run, and stores the winner on the `TargetRun`.

1. For each family in order, keep the devices of that family that meet the run's `os` condition.
2. The first family with at least one such device wins. The run records it as `family`.
3. If no listed family has a device, the run fails. The reason names the listed families and the
   available devices, as a missing `model` does today. It is never `not applicable`, because only
   the version part of a run can be that.

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

On the web, `anyOf` resolves the same way against the engines. The lane launches engines in listed
order, and the first launch that succeeds wins. When none launches, the run fails and names each
engine with its launch error. The chosen engine fills the existing `engine` slot, and the
`browser.engine` coordinate appears for any-of runs, as it does for a list.

`--browser` on `run`, `record`, `crawl`, and `repl` replaces the whole `runsOn.browser` engine
choice. It clears `anyOf` as it already clears a listed `engine`.

`record`, `crawl`, and `repl` never multiply runs. They take the first resolvable family or
engine in priority order, and exit 2 when none fits.

### Reading the family

`DeviceFacts` in `bajutsu/common/devices/facts.py` gains `family: str | None`. `read_ios` reads it
from the `productFamily` of the device type it already looks up for the name. A device type with no
`productFamily` raises `DeviceReadError`, because the reader fails loudly rather than guessing.
Android and web devices carry `None`.

### Schema, reports, and docs

- `IosRunsOn` in `bajutsu/common/config/schema/platform/ios.py` gains `family`. `WebBrowser` in
  `web.py` gains `anyOf`. The platform registry supplies the same fields to a
  scenario's `preconditions.runsOn`, so a scenario may narrow its target's choice.
- `bajutsu config schema` emits `family` as an enum or a list of the enum, and `anyOf` as a list
  of engines.
- `doctor` lists each run with the winning family or engine and the devices that fit.
- [Configuration](../../docs/configuration.md#device-conditions-runson) and its `docs/ja/` mirror
  gain the table above. [CLI](../../docs/cli.md) documents `--browser`'s effect on `anyOf`.
  [Architecture](../../docs/architecture.md) updates the `common/devices/` rows for `facts.py` and
  `runs.py`.

### Prime-directive compliance

No LLM enters a run. The resolution is a pure function of the config and the pool, and the same
inputs yield the same winner. An ambiguous request does not tap whatever matched first: a priority
list fixes the order in advance, and a pool that fits no entry fails the run. All differences stay
in config.

### Out of scope

- Android form factor. A tablet-or-phone fact needs `ro.build.characteristics` read on an
  emulator, which this item leaves to its own proposal.
- `runsOn.emulate`. It picks a Playwright viewport profile rather than a device from a pool, so
  it has no availability to resolve. A list there would mean coverage across profiles, a separate
  question from "any one of these".
- Glob or regular-expression matching on `model`. The alternatives table gives the reason.
- Changing the meaning of a list in `model`, `avd`, or `engine`. Existing configs keep their runs.

### Work breakdown

1. **Read the family.** Add `family` to `DeviceFacts` and read it in `read_ios` from
   `productFamily`. Confirm on a real `simctl list devicetypes` that every iPhone and iPad type
   carries it.
2. **Schema.** Add `IosRunsOn.family`, `WebBrowser.anyOf`, and the load-time
   rules above. Extend the JSON Schema output.
3. **Resolution.** Resolve the priority lists in `form_target_runs`, narrow `eligible_set` by the
   winning family, and add the coordinate for any-of runs. Cover `first_run` and `declared_runs`.
4. **Wiring.** Carry the fields through `_conditions` and `unpack_conditions`, make `--browser`
   clear `anyOf`, and thread the winning engine into the web lane.
5. **Reporting.** Show the winner in the run label, the manifest, `doctor`, and the flakiness key.
6. **Docs and demos.** Update the bilingual docs named above, and switch one demo config from an
   exact model to `family` as a worked example.

Each unit carries its own tests: the schema rules, a fake pool for each resolution branch, and
the failure messages.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Let a list in `model` mean any-of | It silently changes every existing multi-model config from "run each" to "run one". The change breaks the matrices teams already rely on. |
| Let `model` take `{ anyOf: [...] }` | The field becomes a string, a list, or an object, and the JSON Schema and the error messages grow with it. A separate `family` field keeps each form single-purpose. |
| Glob matching, such as `model: "iPad*"` | It depends on Apple's naming, which is the brittleness this item removes. A rename of the family prefix would break the pattern the same way an exact name breaks. |
| A cross-platform `idiom: phone \| tablet` | It needs a form-factor read on every platform, so it spans Android and web emulation. The iOS and web needs here are met without it. A later item can add it on top. |
| A shorthand `engine: any` for all engines | It gives the same request a second spelling, and readers must learn both. Listing the three engines in `anyOf` costs one line and keeps the priority visible. |
| Pool order as the tie-break | It is simpler, but the winner then depends on the host's device order. Flakiness history would key one scenario across different devices. |
| Pick the lower-priority family when the winner lacks devices for the demand | It maximizes fit but makes the family depend on `--workers` and the device count. The run's identity would change with the concurrency. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: read the family
- [ ] Unit 2: schema
- [ ] Unit 3: resolution
- [ ] Unit 4: wiring
- [ ] Unit 5: reporting
- [ ] Unit 6: docs and demos

## References

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the `runsOn` conditions, device matching, and runs this item extends.
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md): the device facts that the flakiness key reads.
- [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md): the web engines this item makes selectable by priority.
- `bajutsu/common/devices/facts.py` and `bajutsu/common/devices/runs.py`: where the family is read and resolved.

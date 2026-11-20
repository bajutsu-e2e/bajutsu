**English** · [日本語](BE-0358-device-os-as-a-first-class-fact-ja.md)

# BE-0358 — Carry the device OS version as a parsed fact, so a cross-version run is not scored as flaky

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0358](BE-0358-device-os-as-a-first-class-fact.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0358") |
| Implementing PR | [#1610](https://github.com/bajutsu-e2e/bajutsu/pull/1610) |
| Topic | Platform support |
| Related | [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) |
<!-- /BE-METADATA -->

## Introduction

This item turns the device's operating-system (OS) version from a display string into a **parsed
fact that travels with the run**, and makes the two determinism surfaces group by it. A run already
records the version per scenario as `device_runtime` (the string `"iOS 18.6"`). Only the report
reads that string. The same scenario executed on two OS versions therefore lands in one history,
and a verdict that differs *because the versions differ* is scored as flakiness.

We propose three changes:

- a small parsed type;
- its use as part of the grouping key in both flakiness surfaces;
- one channel that hands the parsed version to the driver, so that a decision that must differ by
  OS has somewhere to read the version instead of having no access to it.

## Motivation

Bajutsu's determinism audit exists to tell a genuine flake, a verdict that flips with nothing
changed, apart from a reproducible failure. Across OS versions the audit cannot do that today, and
the misclassification is easy to produce.

Consider two runs of one scenario file, unedited between them, on two Simulators:

| Run | Device | Verdict | `provenance.scenarioHash` |
|---|---|---|---|
| `20260812-013907` | iOS 18.6 | pass | `sha256:c6b97d3d1…` |
| `20260812-014335` | iOS 26.5 | fail | `sha256:c6b97d3d1…` |

Feeding both manifests to [`longitudinal`](../../bajutsu/analysis/audit.py) reports:

```
flaky          runs=2 passed=1  native alert can be tapped
flaky          runs=2 passed=1  native alert cancel leaves a distinct result
```

Neither scenario is flaky. Each is perfectly deterministic on the OS it ran on: it passed every
time on iOS 18.6 and failed every time on iOS 26.5. The cause has since been traced to a concrete
difference in how the two OS versions register an alert button's accessibility node.

The audit calls the result flakiness because its grouping key is `(scenarioHash, scenario name)`.
Its classification rule, in the words of `classify_stability`, is that "a mix of pass and fail at
the *same* fingerprint is true flakiness". The fingerprint is the content hash of the scenario
file. The device that the file ran against is not part of it.

The cost goes beyond a cosmetic label. A team uses the determinism audit to decide what to chase,
and the audit ranks flaky findings first so that they get chased first. A reproducible OS gap
presented as a flake sends the team hunting a race that does not exist. The real finding, that the
scenario does not work on that OS, is the one thing the report does not say.

The hosted surface has the same problem. `rank_flakiness` in
[`flakiness.py`](../../bajutsu/serve/flakiness.py) groups on `scenario_hash` alone. A fleet that
runs one suite across a device matrix therefore scores every genuine OS difference as flakiness,
and running a suite across a device matrix is the case a fleet exists for.

Beneath the misclassification sits a plainer gap: **nothing below the report can read the OS
version at all.** The version travels through four stages:

1. `simctl.runtime_label` (and its adb counterpart) produces the string.
2. The pool stamps the string onto the `Lease`.
3. The pipeline copies it to the `RunResult` after the scenario has already run.
4. The report (its Environment tab and the CTRF export) and the manifest consume it.

No driver takes the version, no parsed form of it exists, and the repository holds no
version-comparison helper of any kind. A driver that hits a real, measured OS difference today
cannot name which OS it is on, whether to branch on it or merely to say so in a failure.

## Detailed design

The design has three units, smallest first. The first unit is what the other two consume.

**A parsed device OS.** The unit is a small frozen dataclass plus a parser. The dataclass holds the
platform (`ios` / `android`), `major`, `minor`, and the raw label it came from. The parser reads the
label that `runtime_label` already produces (`"iOS 18.6"`, `"Android 14"`).

The parser is pure and needs no device, so it is unit-testable on the fast gate. It lives beside
[`device_id.py`](../../bajutsu/device_id.py), the existing home for small device-identity helpers.

**An unrecognized or absent label parses to `None`, not to a guess.** The web backend and the
WebDriver grid both return an empty device catalog today, so absence is a normal state, and every
consumer below must already handle it.

The type carries no comparison operators in this item. Nothing here compares versions, and an
ordering that nobody uses invites a version gate that this item deliberately does not build (see
*Alternatives considered*).

**The grouping key in both flakiness surfaces.** `longitudinal` keys its groups by
`(scenarioHash, name)`. The key gains the parsed OS, so two runs of one scenario on two OS versions
form two histories, each classified on its own evidence.

A run whose label is missing or unparseable groups under a distinct "unknown" key, and does not
join any version's history. Merging the run into a version's history would reintroduce the
blending that this item removes. Dropping the run would lose a run that the audit can still
classify.

The manifest already carries `device_runtime` on each scenario entry, so `longitudinal` needs no
new recorded field.

`rank_flakiness` on the hosted side keys on `scenario_hash` alone and gains the same component.
That side needs a newly recorded field to do it:

- `RunRecord` carries no runtime.
- `_run_summary` builds the `summary` mapping from the manifest. It keeps only the run id, verdict,
  report, scenario names, and pass count, and drops `device_runtime` at that seam.

Whether the field is an addition to `_run_summary` or a column of its own is for the unit to settle
against the real schema.

`rank_flakiness` also takes records reduced from manifests. `records_from_manifests` feeds it for
the file-backed `bajutsu flakiness` command and for a database-free serve. That reduction must fill
the same field from each manifest's per-scenario `device_runtime`, under the same unknown-key
rule. Otherwise those two callers would group every run under "unknown" and keep today's
misclassification, while the other two surfaces are fixed.

The record is per *run*, while the label is per *scenario*. A run whose scenarios span OS versions
therefore has no single OS. Such a run groups under the same "unknown" key that an unparseable
label gets, because a mixed run cannot speak for any one version.

The two surfaces stay deliberately different in granularity: the database one is per run, and the
file one is per scenario. They must gain the *same* OS component and the *same* unknown-key rule.
That keeps them labelling a scenario identically, which is why they share `classify_stability` in
the first place.

**The OS has to reach the output, not merely the key.** Splitting the groups is half the fix.
Today the output carries no OS:

- `ScenarioHistory` carries `scenario_hash`, `name`, and counts.
- `render_longitudinal` prints the name and the classification.
- `FlakyScenario` is the same shape on the hosted side.

Two histories split by OS would therefore render as two rows with one name, one hash, and nothing
between them. The `--json` output would be just as ambiguous. Both sort keys also tie for such a
pair, which leaves their order to the input order.

So the OS joins each record and each rendering. It also joins both sort keys ahead of the name,
which makes the order deterministic for the pair. This turns "this scenario is flaky" into the
finding that *Motivation* wants: this scenario does not work on that OS.

**Rows recorded before the field exists.** Every run that `serve` has already stored carries no
runtime. Those runs would group under `unknown`, while runs recorded afterwards group per OS, so a
scenario's history would split at the deploy boundary.

A genuine flake that spans the boundary is then masked twice over. An old passing run and a new
failing run land in different groups, and each group is `unproven` for having fewer than two runs.
This is the misclassification of this item with the sign reversed, so the unit must not leave it
silent.

The preferred remedy is to backfill the field from each run's stored manifest, where the
per-scenario `device_runtime` already sits. Where a deployment no longer holds the manifest, the
row stays `unknown`, and the report discloses the split rather than passing it off as evidence.

**The parsed OS reaches the one driver that needs it.** The OS does not become a member of
`Driver`. That Protocol is `@runtime_checkable` with no shared base class. A data member there
would be a declaration that every backend and every inline test double must repeat, and
`isinstance` checks against it would start failing on the ones that did not.

The OS travels instead as a `make_driver` keyword. The XCUITest branch reads the keyword, and every
other actuator ignores it. This is the shape that `runner_port`, `runner_alive`, and `act` already
take. The XCUITest environment derives the value from the runtime identifier it already captures
for device cloning, at both driver-construction sites.

That placement is also the more correct one. A device replacement can move a lease onto a different
Simulator mid-run, and both construction sites build a fresh driver afterwards, so the OS follows
the swap. A fact stamped once onto the `Lease` would go stale.

Nothing in this item branches on the value, because the first branch is a separate item. A
driver-level failure can already name the OS it happened on, and a future per-OS decision has one
route to read the value instead of inventing its own.

**What this item does not build.** The item builds no mechanism for *declaring* per-OS behaviour:

- no version comparison;
- no "on iOS below N do X" table in the drivers;
- no OS predicate in the scenario schema or in `targets.<name>`.

The repository's existing position is that an OS difference is better absorbed than branched on.

- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md)
  treats an iOS release that reshapes the element tree as something a golden assertion should
  catch.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) and
  [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  both cite OS-version variation as the reason to avoid coordinates and hardcoded labels.

This item does not overturn that position. It supplies the fact that the position needs in order
to be checked, and leaves each behavioural difference to be fixed on its merits.

One difference has since been measured against that bar and did not clear it: the `back` action,
whose identifier the navigation bar of iOS 18 does not carry. A separate item therefore gives iOS
its back control as one implementation per major version behind an interface, selected in a single
factory. That is one method on one driver, argued from a measurement. It is the shape that the fact
of this item exists to make possible, and it is not the declaration mechanism ruled out above.

**Capability preflight stays out of scope, and cannot host this.** The preflight that decides
whether a scenario is supported runs in `pipeline.py` *before* a device is leased. `capabilities_for`
reads its token set without constructing a driver at all, and its docstring gives the reason: the
preflight then needs no Simulator. A device's OS version does not exist at that moment, so no
capability token can depend on it without inverting that order. This item does not attempt the
inversion.

**Tests.** The parser is pure, so its test is a table of labels to parsed values, including the
unparseable and absent cases.

Both grouping changes are pure functions over manifests and records. The decisive test is the case
that *Motivation* measures: one scenario, one fingerprint, two OS versions, and opposite verdicts.
It asserts two `deterministic` histories where today there is one `flaky` history. A second test
covers a same-OS mix that must still classify as `flaky`, so the change narrows nothing that it
should not.

The driver attribute is covered by asserting that the lease sets it, and that a driver constructed
without a lease reports `None`.

## Alternatives considered

**Add the OS to the scenario fingerprint itself.** The fingerprint is defined as the content hash
of the executed scenario file, and the audit relies on that definition: an edited scenario gets a
new fingerprint and a fresh group, so "an edit can't look like a flake". Folding a device property
into a content hash would make the stamp mean two things at once and break that guarantee. Keeping
the fingerprint as content, and adding the OS as a separate key component, keeps both readings
intact.

**Group by the raw `device_runtime` string instead of parsing it.** This needs no new type and
would fix the misclassification today. It also makes `"iOS 18.6"` and `"iOS 18.6.1"` different
groups. That splits one scenario's history across patch releases that no observed difference
distinguishes. It also leaves the third unit with nothing better than a string to hand a driver.
Parsing once, in one place, costs little and gives both surfaces the same notion of "same OS".

**Let the parsed type compare versions, ready for a future gate.** An ordering would make
`os >= (26, 0)` writable the day it lands. Nothing in this item needs it. A comparison operator
sitting unused in a driver-visible type is a standing invitation to the per-OS branch table that
this item argues against building before a case survives the version-agnostic alternative. Adding
the operator later, with the first case that justifies it, costs one function.

**Build the per-OS behaviour mechanism now, with the back button and the stepper as its first
users.** Both are real, measured iOS 18 differences. `back` taps the identifier `BackButton`, which
the navigation bar of that OS does not carry. A SwiftUI stepper reports itself non-hittable there.

Branching on the OS where a version-agnostic fix would do buys a matrix that grows with every
release, and nobody re-tests the older half of it. Each case has to earn its branch. Measurement
since settled both cases, in opposite directions.

- The stepper needs no branch. Its container is refused on one OS and accepted on the other, but
  the fix lies in the actuation path, not in the version. The apparent version-agnostic fix is not
  the one to reach for either. Classifying `.stepper` in the runner's `typeName`, which today falls
  through to `other`, would change the trait token and leave the frame and `isHittable` as they
  are, so the tap stays refused.
- The back button did earn a branch, for the reason given in *Detailed design*.

Both fixes belong in their own items.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The parsed type and its parser, beside `device_id.py`: platform, major, minor, raw label;
      `None` for an absent or unrecognized label; no comparison operators. Unit-tested from a table
      of labels.
- [x] Carry the parsed OS on `ScenarioHistory` and `FlakyScenario`, print it in the rendered and
      `--json` output, and put it in both sort keys ahead of the name.
- [x] Group by the parsed OS in `longitudinal` (`bajutsu/analysis/audit.py`), with an "unknown" key
      for a run whose label is missing or unparseable, and the same component in `rank_flakiness`
      (`bajutsu/serve/flakiness.py`), reading the label from the field this unit adds to the record —
      `_run_summary` keeps none today — filling that field in `records_from_manifests` too, and
      grouping a run whose scenarios span OS versions under the "unknown" key. Both surfaces must gain the same component and the same unknown-key rule.
- [x] Backfill the field on rows recorded before it existed, from each run's stored manifest; where
      a deployment no longer holds the manifest, leave the row `unknown` and disclose the split in
      the report rather than letting a boundary-spanning flake read as two `unproven` histories.
- [x] Hand the parsed OS to the XCUITest driver as a `make_driver` keyword, derived in the
      environment from the runtime identifier it already captures, at both construction sites. No
      `Driver` protocol member and no test double changes. Nothing branches on it in this item.
- [x] Documentation: record in `docs/` (and its `docs/ja/` mirror) that a flakiness history is now
      per OS version, and that the parsed OS is available to a driver but is not a licence to
      branch — a behavioural OS difference is fixed version-agnostically unless an item argues
      otherwise.

## References

- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md):
  defined the fingerprint and the classification this item leaves intact, adding only a key
  component beside them.
- [BE-0220 — Flaky-test suggestion and cross-run fix proposals from DB run history](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md):
  the hosted surface that shares `classify_stability` with the file-backed audit, and must therefore
  gain the same grouping component.
- [BE-0166 — Capability-routed job queues](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md):
  the one shipped place an installed OS version influences anything — worker *routing*, by an
  `iosNN` token derived from the device catalog, deliberately walled off from the verdict. Its
  regex over the same label is the closest thing to a parser today.
- [`bajutsu/analysis/audit.py`](../../bajutsu/analysis/audit.py): `longitudinal` and
  `classify_stability` — the grouping this item extends and the rule it does not touch.
- [`bajutsu/serve/flakiness.py`](../../bajutsu/serve/flakiness.py): `rank_flakiness` — the hosted twin
  of that grouping, per run where the file-backed one is per scenario.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py): `runtime_label` and `device_catalog` — where the
  label this item parses is produced; [`bajutsu/adb.py`](../../bajutsu/adb.py) holds the Android
  counterpart.
- [`bajutsu/runner/pool.py`](../../bajutsu/runner/pool.py): where the label is stamped onto the
  `Lease`, and therefore where the driver attribute is set.

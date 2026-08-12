**English** · [日本語](BE-XXXX-device-os-as-a-first-class-fact-ja.md)

# BE-XXXX — Carry the device OS version as a parsed fact, so a cross-version run is not scored as flaky

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-os-as-a-first-class-fact.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) |
<!-- /BE-METADATA -->

## Introduction

This item turns the device's operating-system version from a display string into a **parsed fact
that travels with the run**, and makes the two determinism surfaces group by it. A run already
records the version per scenario as `device_runtime` — the string `"iOS 18.6"` — but nothing reads
that string except the report, so the same scenario executed on two OS versions lands in one
history, and a verdict that differs *because the versions differ* is scored as flakiness. We
propose a small parsed type, its use as part of the grouping key in both flakiness surfaces, and
one channel that hands the parsed version to the driver, so a decision that genuinely must differ
by OS has somewhere to read it instead of having no access to the version at all.

## Motivation

Bajutsu's determinism audit exists to tell a genuine flake — a verdict that flips with nothing
changed — apart from a reproducible failure. It currently cannot do that across OS versions, and
the misclassification is easy to produce.

Two runs of one scenario file, unedited between them, on two Simulators:

| Run | Device | Verdict | `provenance.scenarioHash` |
|---|---|---|---|
| `20260812-013907` | iOS 18.6 | pass | `sha256:c6b97d3d1…` |
| `20260812-014335` | iOS 26.5 | fail | `sha256:c6b97d3d1…` |

Feeding both manifests to
[`longitudinal`](../../bajutsu/analysis/audit.py) reports:

```
flaky          runs=2 passed=1  native alert can be tapped
flaky          runs=2 passed=1  native alert cancel leaves a distinct result
```

Neither scenario is flaky. Each is perfectly deterministic on the OS it ran on — it passed every
time on iOS 18.6 and failed every time on iOS 26.5, for a reason since traced to a concrete
difference in how the two OS versions register an alert button's accessibility node. The audit
calls it flakiness because its grouping key is `(scenarioHash, scenario name)` and its
classification rule, in `classify_stability`'s own words, is that "a mix of pass and fail at the
*same* fingerprint is true flakiness". The fingerprint is the scenario file's content hash. The
device the file ran against is not in it.

The cost is not a cosmetic label. The determinism audit is the instrument a team uses to decide
what to chase, and it ranks flaky findings first precisely so they get chased first. A
reproducible OS gap presented as a flake sends that team hunting a race that does not exist, while
the real finding — this scenario does not work on that OS — is the one thing the report does not
say. The same reading reaches the hosted surface: `rank_flakiness` in
[`flakiness.py`](../../bajutsu/serve/flakiness.py) groups on `scenario_hash` alone, so a fleet
running one suite across a device matrix scores every genuine OS difference in it as flakiness,
which is exactly the case a fleet exists to have.

Underneath the misclassification sits a plainer gap: **nothing below the report can read the OS
version at all.** `simctl.runtime_label` (and its adb counterpart) produces the string, the pool
stamps it onto the `Lease`, and the pipeline copies it to the `RunResult` after the scenario has
already run — the report (its Environment tab and the CTRF export) and the manifest are its only
consumers. No driver
takes it, no parsed form of it exists, and the repository holds no version-comparison helper of
any kind. A driver that hits a real, measured OS difference today has no way to name which OS it
is on, whether to branch on it or merely to say so in a failure.

## Detailed design

Three units, smallest first. The first is what the other two consume.

**A parsed device OS.** A small frozen dataclass — platform (`ios` / `android`), `major`, `minor`,
and the raw label it came from — plus a parser over the label `runtime_label` already produces
(`"iOS 18.6"`, `"Android 14"`). It is pure and needs no device, so it is unit-testable on the fast
gate, and it lives beside [`device_id.py`](../../bajutsu/device_id.py), the existing home for small
device-identity helpers. **An unrecognized or absent label parses to `None`, not to a guess**: the
web backend and the WebDriver grid both return an empty device catalog today, so absence is a
normal state and every consumer below must already handle it. The type carries no comparison
operators in this item — nothing here compares versions, and an ordering nobody uses is an
invitation to a version gate this item deliberately does not build (see *Alternatives*).

**The grouping key in both flakiness surfaces.** `longitudinal` keys its groups by
`(scenarioHash, name)`; the key gains the parsed OS, so two runs of one scenario on two OS versions
form two histories, each classified on its own evidence. A run whose label is missing or
unparseable groups under a distinct "unknown" key rather than joining any version's history —
merging it into one would reintroduce exactly the blending this item removes, and dropping it would
lose a run the audit can still classify. The manifest already carries `device_runtime` on each
scenario entry, so this unit needs no new recorded field. `rank_flakiness` on the hosted side keys on
`scenario_hash` alone and gains the same component. That side needs a newly recorded field to do
it: `RunRecord` carries no runtime, and `_run_summary` — which builds the `summary` mapping it
mirrors from the manifest — keeps only the run id, verdict, report, scenario names, and pass
count, dropping `device_runtime` at that seam. Whether the field is an addition to `_run_summary` or a column of
its own is the unit's to settle against the real schema. `rank_flakiness` also takes records reduced
from manifests: `records_from_manifests` feeds it for the file-backed `bajutsu flakiness` command and
for a database-free serve. That reduction must fill the same field from each manifest's per-scenario
`device_runtime`, under the same unknown-key rule, or those two callers would group every run under
"unknown" and keep today's misclassification while the other two surfaces are fixed. Its record is
also per *run* while the
label is per *scenario*, so a run whose scenarios span OS versions has no single OS: such a run
groups under the same "unknown" key an unparseable label gets, since a mixed run cannot speak for
any one version. The two surfaces stay deliberately different in granularity — the DB one is per
run, the file one per scenario — but must gain the *same* OS component and the *same* unknown-key
rule, so they keep labelling a scenario identically, which is why they share `classify_stability`
in the first place.

**The OS has to reach the output, not only the key.** Splitting the groups is half the fix: today
`ScenarioHistory` carries `scenario_hash`, `name`, and counts, `render_longitudinal` prints the name
and the classification, and `FlakyScenario` is the same shape on the hosted side — so two histories
split by OS would render as two rows with one name, one hash, and nothing between them, and `--json`
would be as ambiguous. Both sort keys tie for such a pair, leaving their order to input order. So the
OS joins each record and each rendering, and joins both sort keys ahead of the name, which also makes
the order deterministic for the pair. That is what turns "this scenario is flaky" into the finding
*Motivation* actually wants: this scenario does not work on that OS.

**Rows recorded before the field exists.** Every run `serve` has already stored carries no runtime,
so it would group under `unknown` while runs recorded afterwards group per OS, and a scenario's
history would split at the deploy boundary. A genuine flake spanning that boundary is then masked
twice over: an old passing run and a new failing run land in different groups and each is
`unproven` for having fewer than two runs. That is this item's own misclassification with the sign
reversed, so the unit must not leave it silent. Preferred: backfill the field from each run's stored
manifest, which is where the per-scenario `device_runtime` already sits. Where a deployment no longer
holds the manifest, the row stays `unknown` and the split is disclosed in the report rather than
passed off as evidence.
**The parsed OS reaches the one driver that needs it.** Not as a member of `Driver`: that Protocol
is `@runtime_checkable` with no shared base class, so a data member there is a declaration every
backend and every inline test double must repeat, and `isinstance` checks against it would start
failing on the ones that did not. It travels instead as a `make_driver` keyword the XCUITest branch
reads and every other actuator ignores, which is the shape `runner_port`, `runner_alive`, and `act`
already take. The XCUITest environment derives it from the runtime identifier it already captures
for device cloning, at both driver-construction sites.

That placement is also the more correct one. A device replacement can move a lease onto a different
Simulator mid-run, and both construction sites build a fresh driver afterwards, so the OS follows the
swap; a fact stamped once onto the `Lease` would go stale. Nothing in this item branches on the
value — the first branch is a separate item — but a driver-level failure can already name the OS it
happened on, and a future per-OS decision has one route to read instead of inventing its own.

**What this item does not build.** No mechanism for *declaring* per-OS behaviour — no version
comparison, no "on iOS below N do X" table in the drivers, no OS predicate in the scenario schema
or in `targets.<name>`. The repository's existing position is that an OS difference is better
absorbed than branched on: [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md)
treats an iOS release reshaping the element tree as something a golden assertion should catch,
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) and
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
both cite OS-version variation as the reason to avoid coordinates and hardcoded labels. This item
does not overturn that position; it supplies the fact that position needs in order to be checked,
and leaves each behavioural difference to be fixed on its merits. One difference has since been
measured against that bar and did not clear it — the `back` action, whose identifier iOS 18's
navigation bar does not carry — so a separate item gives iOS its back control as one implementation
per major version behind an interface, selected in a single factory. That is one method on one
driver, argued from a measurement, and it is the shape this item's fact exists to make possible; it
is not the declaration mechanism ruled out above.

**Capability preflight stays out of scope, and cannot host this.** The preflight that decides
whether a scenario is supported runs in `pipeline.py` *before* a device is leased, and
`capabilities_for` reads its token set without constructing a driver at all — its docstring gives
the reason: the preflight then needs no Simulator. A device's OS version
does not exist at that moment, so no capability token can depend on it without inverting that
order. This item does not attempt the inversion.

**Tests.** The parser is pure: a table of labels to parsed values, including the unparseable and
absent cases. Both grouping changes are pure functions over manifests and records: the decisive
test is the case *Motivation* measures — one scenario, one fingerprint, two OS versions, opposite
verdicts — asserting two `deterministic` histories where today there is one `flaky` history, plus
a same-OS mix that must still classify as `flaky` so the change narrows nothing it should not. The
driver attribute is covered by asserting the lease sets it and that a driver constructed without a
lease reports `None`.

## Alternatives considered

**Add the OS to the scenario fingerprint itself.** The fingerprint is defined as the content hash
of the executed scenario file, and the audit relies on that definition: an edited scenario gets a
new fingerprint and a fresh group, so "an edit can't look like a flake". Folding a device
property into a content hash would make the stamp mean two things at once and break that
guarantee. Keeping the fingerprint as content and adding the OS as a separate key component keeps
both readings intact.

**Group by the raw `device_runtime` string instead of parsing it.** This needs no new type and
would fix the misclassification today. It also makes `"iOS 18.6"` and `"iOS 18.6.1"` different
groups, splitting one scenario's history across patch releases that no observed difference
distinguishes, and it leaves the third unit with nothing better than a string to hand a driver.
Parsing once, in one place, costs little and gives both surfaces the same notion of "same OS".

**Let the parsed type compare versions, ready for a future gate.** An ordering would make
`os >= (26, 0)` writable the day it lands. Nothing in this item needs it, and a comparison operator
sitting unused in a driver-visible type is a standing invitation to the per-OS branch table this
item argues against building before a case survives the version-agnostic alternative. Adding the
operator later, with the first case that justifies it, costs one function.

**Build the per-OS behaviour mechanism now, with the back button and the stepper as its first
users.** Both are real, measured iOS 18 differences: `back` taps the identifier `BackButton`, which
that OS's navigation bar does not carry, and a SwiftUI stepper reports itself non-hittable there.
Branching on the OS where a version-agnostic fix would do buys a matrix that grows with every
release and that nobody re-tests on the older half, so each case has to earn its branch. Measurement
since settled both, in opposite directions. The stepper needs no branch: its container is refused on
one OS and accepted on the other, but the fix is in the actuation path, not the version. Its
apparent version-agnostic fix is not the one to reach for either — classifying `.stepper` in the
runner's `typeName`, which today falls through to `other`, would change the trait token and leave
the frame and `isHittable` exactly as they are, so the tap stays refused. The back button did earn
a branch, for the reason given in *Detailed design*. Both fixes belong in their own items.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] The parsed type and its parser, beside `device_id.py`: platform, major, minor, raw label;
      `None` for an absent or unrecognized label; no comparison operators. Unit-tested from a table
      of labels.
- [ ] Carry the parsed OS on `ScenarioHistory` and `FlakyScenario`, print it in the rendered and
      `--json` output, and put it in both sort keys ahead of the name.
- [ ] Group by the parsed OS in `longitudinal` (`bajutsu/analysis/audit.py`), with an "unknown" key
      for a run whose label is missing or unparseable, and the same component in `rank_flakiness`
      (`bajutsu/serve/flakiness.py`), reading the label from the field this unit adds to the record —
      `_run_summary` keeps none today — filling that field in `records_from_manifests` too, and
      grouping a run whose scenarios span OS versions under the "unknown" key. Both surfaces must gain the same component and the same unknown-key rule.
- [ ] Backfill the field on rows recorded before it existed, from each run's stored manifest; where
      a deployment no longer holds the manifest, leave the row `unknown` and disclose the split in
      the report rather than letting a boundary-spanning flake read as two `unproven` histories.
- [ ] Hand the parsed OS to the XCUITest driver as a `make_driver` keyword, derived in the
      environment from the runtime identifier it already captures, at both construction sites. No
      `Driver` protocol member and no test double changes. Nothing branches on it in this item.
- [ ] Documentation: record in `docs/` (and its `docs/ja/` mirror) that a flakiness history is now
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

**English** · [日本語](BE-XXXX-xcuitest-default-ios-backend-ja.md)

# BE-XXXX — Make XCUITest the default iOS backend and retire idb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-default-ios-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) |
<!-- /BE-METADATA -->

## Introduction

Make XCUITest the default iOS backend and remove idb entirely, in a single pull request that carries
the migration through its Simulator verification. Because retiring idb reverses a decision
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) took deliberately, the proposal
argues the reversal in full: XCUITest is the more capable backend, and once the sibling runner-reuse
proposal removes its per-scenario startup cost it is no more expensive across a run set, so the one
advantage idb keeps — running without an Xcode toolchain — no longer justifies a second permanent
backend. The one PR flips the default, migrates every fixture and CI lane, deletes idb and its
supporting surface, and confirms on the Simulator that every scenario still runs. The destination is
a single iOS backend, reached in one change rather than staged across several.

## Motivation

The trigger is a concrete gap a user hits in the web UI. The report and `serve` element-tree view
cannot show the children of a `trait: group` container on iOS — a tab bar renders as one opaque
group with nothing under it. The cause is idb's design: `idb ui describe-all` reads the
VoiceOver-facing accessibility tree, in which a container that is itself one accessibility element
(an `AXGroup`) is a leaf that hides its children, so idb drops every element beneath it (idb issue
767). No option to idb recovers those children, and the limitation is intrinsic to reading that
tree.

XCUITest has no such limitation, and it is the more capable backend on every other axis too. It
reads the XCTest automation snapshot, which descends through those containers and enumerates each
child, so it alone can render a faithful, fully expanded element tree. Its capability set is a
strict superset of idb's — it adds semantic tap, native condition waiting, multi-touch, and text
selection — so the capability-aware resolver
([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md))
already escalates to XCUITest whenever a scenario needs any of them. A reader might expect XCUITest
to demand per-app integration in exchange, but it does not: the runner is a generic host that
launches the target by bundle id (`XCUIApplication(bundleIdentifier:)`), so it drives an arbitrary
app without touching that app's source.

With capability and fidelity both favoring XCUITest, only one reason kept idb the default: cost.
The XCUITest runner paid a cold `xcodebuild test-without-building` startup once per scenario, which
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) cited when it rejected replacing
idb outright and kept the two backends in a cost-ordered ladder with idb on the cheap rung. The
sibling proposal *Reuse the XCUITest runner across scenarios* removes that cost by keeping one
runner resident per device and restarting only the app between scenarios, so the startup is paid
once per device rather than once per scenario. Once that amortization lands, the cost argument for
idb no longer holds, and idb's only remaining justification is operating where no Xcode toolchain is
available — idb needs only its command-line tool and companion; XCUITest needs `xcodebuild`. This
proposal treats that single-environment benefit as not worth a second permanent backend, flips the
default, and removes idb outright.

## Detailed design

All five units land in a single pull request. They are a work breakdown within that one PR, not a
sequence of separate changes: leaving `main` half-migrated — the default flipped but idb still
present, or idb deleted but a fixture still pinned to it — is a broken state no merge should land on.
Unit 5, the Simulator run that confirms every scenario still passes, is the PR's merge gate.

### Unit 1 — Flip the default to XCUITest

Change the iOS default so a run resolves to XCUITest unless it opts out. `Defaults.backend` moves
off the hard `["idb"]` pin (`bajutsu/config/schema.py`), and the cost-ordered selection that puts
idb first (`COST_ORDER["ios"]` and `select_actuator_for_scenario` in `bajutsu/backends.py`) is
retired for iOS, leaving the stability order (`PLATFORMS["ios"] = ("xcuitest", "idb")`) as the sole
order. Capability escalation
([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md))
becomes a no-op for iOS once XCUITest — the superset — is already the default, so the resolver
simplifies rather than grows.

### Unit 2 — Migrate the fixtures and CI lanes

The showcase's `-noax` targets are pinned to idb today because no runner is wired for them; wire the
generic runner so they run on XCUITest, and re-point the required `smoke (idb)` and `E2E` lanes onto
XCUITest with the runner prebuilt (`xcuitest.testRunner`) so CI does not pay a per-run build. This
unit is where the migration is proven in CI, not just asserted.

### Unit 3 — Update the idb-assuming surfaces and docs

Retire the code paths that assume idb is the default: the cost-order branch, the doctor fallback
that degrades XCUITest to idb, and the capability preflight's idb-first assumptions. Then update the
documentation that presents idb as the iOS default (`docs/drivers.md` and `docs/getting-started/ios.md`,
with their Japanese mirrors) to lead with XCUITest.

### Unit 4 — Remove idb entirely

Delete idb, not deprioritize it. This unit removes `IdbDriver` and the idb-only surface around it.
That surface is the companion-version monitor
([BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md)),
the crawl vision tab locator that exists only because idb cannot address tabs, the idb branches in
the shared coordinate-tree read path, and the idb executable's availability wiring. The unit also
drops the `smoke (idb)` lane, which XCUITest now covers. idb is referenced across roughly 60 modules
and as many tests, so this unit's own breakdown is large; the work is mechanical, though, following
the failing imports outward from the deleted driver. The proposal fixes the outcome — no idb backend
remains in the tree — not a line-by-line removal list.

### Unit 5 — Confirm every scenario runs on the Simulator

On a named environment (the Simulator model and the Xcode version), run every scenario the idb
`smoke` and `E2E` lanes cover today on XCUITest, with the runner-reuse amortization in place, and
confirm each passes on the Simulator. This Simulator run is the PR's merge gate: the change does not
merge until every scenario is green on XCUITest. Record the per-suite wall-clock so the migration's
cost is measured, not assumed.

## Alternatives considered

- **Keep idb as a permanent fallback rather than removing it.** Leaving idb in place as a
  never-default opt-out is the lowest-risk option, and it is rejected here. Maintaining two iOS
  backends indefinitely — two read paths, two actuators, two CI lanes — is the standing cost this
  migration exists to end, and idb's one advantage (running without an Xcode toolchain) does not earn
  a second permanent backend once XCUITest is both more capable and no more expensive across a run
  set. Removal is the goal, not a fallback state.
- **Flip the default without the runner-reuse enabler.** Making XCUITest the default while it still
  restarts the runner per scenario would pay the full per-scenario cold start on every iOS run — the
  regression [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) named when it kept
  idb. The runner-reuse proposal is a hard prerequisite for this reason, and must land before this
  PR.
- **Split the migration across several PRs.** Flipping the default, migrating the fixtures, and
  deleting idb could each be a separate PR. This is rejected because every intermediate point leaves
  `main` in a broken state — a flipped default with idb still half-wired, or a deleted idb with a
  fixture still pinned to it. Doing the whole migration in one PR, gated on the Simulator run in Unit
  5, keeps `main` correct at every merge; the cost is one large review, which the unit breakdown
  above is meant to make navigable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs. All five units land in one PR, gated on Unit 5.

- [ ] Unit 1 — Flip the iOS default to XCUITest; retire the cost-ordered idb-first selection.
- [ ] Unit 2 — Migrate the `-noax` fixtures and the `smoke` / `E2E` lanes onto XCUITest.
- [ ] Unit 3 — Retire the idb-assuming code paths and update the docs to lead with XCUITest.
- [ ] Unit 4 — Remove idb entirely (`IdbDriver`, the idb-only modules, and the `smoke (idb)` lane).
- [ ] Unit 5 — Confirm every scenario passes on the Simulator (the PR's merge gate).

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the backend
  and the cost-ordered ladder, including the *Alternatives considered* entry this proposal revisits.
- [BE-0240 — Capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
  — the per-scenario resolver this proposal simplifies.
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb's accessibility tree drops the
  children of a group-role container, the fidelity gap that motivates leading with XCUITest.

**English** · [日本語](BE-0290-xcuitest-default-ios-backend-ja.md)

# BE-0290 — Make XCUITest the default iOS backend and retire idb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0290](BE-0290-xcuitest-default-ios-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0290") |
| Implementing PR | [#1279](https://github.com/bajutsu-e2e/bajutsu/pull/1279) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md), [BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal makes XCUITest the default iOS backend and removes idb entirely. A single pull request carries the migration through its Simulator verification.

Retiring idb reverses a decision that [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) took deliberately, so the proposal argues the reversal in full:

- XCUITest is the more capable backend.
- The sibling runner-reuse proposal removes the per-scenario startup cost of XCUITest. XCUITest is then no more expensive across a run set.
- The one advantage that idb keeps is running without an Xcode toolchain. That advantage no longer justifies a second permanent backend.

The one PR flips the default, migrates every fixture and CI lane, and deletes idb with its supporting surface. It confirms on the Simulator that every scenario still runs. The destination is a single iOS backend, reached in one change, not staged across several.

## Motivation

The trigger is a concrete gap that a user hits in the web UI. The report and the element-tree view of `serve` cannot show the children of a `trait: group` container on iOS. A tab bar renders as one opaque group with nothing under it.

The cause is the design of idb. `idb ui describe-all` reads the VoiceOver-facing accessibility tree. In that tree, a container that is itself one accessibility element (an `AXGroup`) is a leaf that hides its children. idb therefore drops every element beneath the container (idb issue 767). No option of idb recovers those children, because the limitation is intrinsic to reading that tree.

XCUITest has no such limitation, and it is the more capable backend on every other axis too:

- **Fidelity.** XCUITest reads the XCTest automation snapshot. The snapshot descends through those containers and enumerates each child. Only XCUITest can therefore render a faithful, fully expanded element tree.
- **Capability.** The capability set of XCUITest is a strict superset of the set of idb. XCUITest adds semantic tap, native condition waiting, multi-touch, and text selection. The capability-aware resolver ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)) already escalates to XCUITest whenever a scenario needs any of these.
- **Integration.** A reader might expect XCUITest to demand per-app integration in exchange. It does not. The runner is a generic host that launches the target by bundle id (`XCUIApplication(bundleIdentifier:)`), so it drives an arbitrary app without touching the source of that app.

With capability and fidelity both favoring XCUITest, only one reason keeps idb the default: cost. The XCUITest runner pays a cold `xcodebuild test-without-building` startup once per scenario. For this reason, the cost-ordered actuator resolver ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)) puts idb first as the cheap default and treats XCUITest as the escalation.

The sibling proposal *Reuse the XCUITest runner across scenarios* removes that cost. It keeps one runner resident per device and restarts only the app between scenarios. The startup is then paid once per device, not once per scenario.

Once that amortization lands, the cost argument for idb no longer holds. The only remaining justification for idb is the reason that [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) gave for keeping it in the first place: running where no full Xcode toolchain is available. idb needs only its command-line tool and companion, while XCUITest needs `xcodebuild`. This proposal treats that single-environment benefit as not worth a second permanent backend. It flips the default and removes idb outright.

## Detailed design

All five units land in a single pull request. They are a work breakdown within that one PR, not a sequence of separate changes. Leaving `main` half-migrated is a broken state, and no merge should land on it. Two examples of a half-migrated `main` are a flipped default with idb still present, and a deleted idb with a fixture still pinned to it. Unit 5, the Simulator run that confirms every scenario still passes, is the merge gate of the PR.

### Unit 1 — Flip the default to XCUITest

Change the iOS default so that a run resolves to XCUITest unless it opts out.

- `Defaults.backend` moves off the hard `["idb"]` pin (`bajutsu/config/schema.py`).
- The cost-ordered selection that puts idb first (`COST_ORDER["ios"]` and `select_actuator_for_scenario` in `bajutsu/backends.py`) is retired for iOS. The stability order (`PLATFORMS["ios"] = ("xcuitest", "idb")`) remains as the sole order.
- Capability escalation ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)) becomes a no-op for iOS, because XCUITest, the superset, is already the default. The resolver simplifies and does not grow.

### Unit 2 — Migrate the fixtures and CI lanes

The `-noax` targets of the showcase are pinned to idb today, because no runner is wired for them. This unit does three things:

- Wire the generic runner so that the `-noax` targets run on XCUITest.
- Re-point the required `E2E` lane onto XCUITest.
- Stand up an XCUITest smoke lane alongside the existing `smoke (idb)`.

All three use the prebuilt runner (`xcuitest.testRunner`), so CI does not pay a per-run build. This unit adds the XCUITest coverage and proves the migration in CI. Retiring the now-duplicated `smoke (idb)` lane is the job of Unit 4, once idb itself is gone.

### Unit 3 — Update the idb-assuming surfaces and docs

Retire the code paths that assume idb is the default:

- The cost-order branch.
- The doctor fallback that degrades XCUITest to idb.
- The idb-first assumptions of the capability preflight.

`doctor` needs explicit attention. It routes its own iOS query to idb, precisely because idb reads the accessibility tree without a resident runner. `doctor` also runs outside the runner-reuse pool, so it cannot lean on that amortization. This unit gives `doctor` a lightweight XCUITest query path: a short-lived runner that `doctor` starts and tears down for the preflight. The check then does not regress into a full per-run startup.

Then update the documentation that presents idb as the iOS default, so that it leads with XCUITest. The documents are `docs/drivers.md` and `docs/getting-started/ios.md`, with their Japanese mirrors.

### Unit 4 — Remove idb entirely

Delete idb, do not deprioritize it. This unit removes `IdbDriver` and the idb-only surface around it:

- The companion-version monitor ([BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md)).
- The crawl vision tab locator, which exists only because idb cannot address tabs.
- The idb branches in the shared coordinate-tree read path.
- The availability wiring of the idb executable.

The unit also drops the now-redundant `smoke (idb)` lane. The XCUITest smoke lane that Unit 2 stood up already provides its coverage.

idb is referenced across roughly 60 modules and as many tests, so the breakdown of this unit is large. The work is mechanical, though: follow the failing imports outward from the deleted driver. The proposal fixes the outcome, which is that no idb backend remains in the tree. It does not fix a line-by-line removal list.

Two of these deletions carry a caveat:

- The crawl vision tab locator covers a narrower case than the opaque-group argument of the Motivation. The case is custom image tabs with no accessibility identifier and no `tab` trait. Removing the locator is therefore gated on Unit 5 confirming that XCUITest addresses this case, not only the opaque-group case.
- BE-0005 (companion-version monitoring) is `Implemented` today. The roadmap taxonomy has no "implemented-then-removed" status. This unit marks BE-0005 `Superseded by` this item, with the reciprocal link, and records the disposition in both Progress logs. BE-0005 then stops reading as still true once idb is gone.

### Unit 5 — Confirm every scenario runs on the Simulator

Run every scenario that the idb `smoke` and `E2E` lanes cover today on XCUITest, on a named environment (the Simulator model and the Xcode version). The runner-reuse amortization must be in place. Confirm that each scenario passes on the Simulator.

This Simulator run is the merge gate of the PR. The change does not merge until every scenario is green on XCUITest.

The run must also exercise the custom tab-bar shape that the crawl vision tab locator handles today: a tab bar with no accessibility identifier and no `tab` trait. Unit 4 deletes the locator, and the deletion is a regression unless XCUITest is confirmed to address that shape.

Record the per-suite wall-clock, so that the cost of the migration is measured, not assumed.

## Alternatives considered

- **Keep idb as a permanent fallback rather than removing it.** Leaving idb in place as a never-default opt-out is the lowest-risk option, and it is rejected here. Maintaining two iOS backends indefinitely means two read paths, two actuators, and two CI lanes. That is the standing cost this migration exists to end. The one advantage of idb (running without an Xcode toolchain) does not earn a second permanent backend once XCUITest is both more capable and no more expensive across a run set. Removal is the goal, not a fallback state.
- **Flip the default without the runner-reuse enabler.** Making XCUITest the default while it still restarts the runner per scenario would pay the full per-scenario cold start on every iOS run. This is exactly the cost that the cost-ordered actuator resolver ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)) weighs when it keeps idb the cheap default today. The runner-reuse proposal is therefore a hard prerequisite, and it must land before this PR.
- **Split the migration across several PRs.** Flipping the default, migrating the fixtures, and deleting idb could each be a separate PR. This is rejected, because every intermediate point leaves `main` in a broken state: a flipped default with idb still half-wired, or a deleted idb with a fixture still pinned to it. Doing the whole migration in one PR, gated on the Simulator run in Unit 5, keeps `main` correct at every merge. The cost is one large review, which the unit breakdown above is meant to make navigable.
- **Land the idb deletion of Unit 4 as a fast-follow after Units 1–3.** A softer split would flip the default and migrate the fixtures first. It would then delete the roughly 60-module surface of idb in a later PR. The argument is that idb sits unused but present in between, so `main` stays correct. This is rejected for two reasons:
  - First, idb unused-but-present is precisely the *keep idb as a permanent fallback* state that the first alternative already rejects. Until the follow-up lands, `--backend idb` keeps idb selectable, and the `smoke (idb)` lane keeps exercising it. A deprioritized follow-up tends never to land, so the split risks making the rejected fallback state permanent by default.
  - Second, the Simulator parity evidence of Unit 5 justifies the deletion. idb is removed because XCUITest is proven to cover every scenario that idb runs. Separating the deletion from that evidence either strands the deletion in a PR without the proof or duplicates the Simulator run. Bundling keeps "remove idb because parity is proven" a single evidence-gated step.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs. All five units land in one PR, gated on Unit 5.

- [x] Unit 1 — Flip the iOS default to XCUITest; retire the cost-ordered idb-first selection.
- [x] Unit 2 — Migrate the `-noax` fixtures and the `smoke` / `E2E` lanes onto XCUITest.
- [x] Unit 3 — Retire the idb-assuming code paths and update the docs to lead with XCUITest.
- [x] Unit 4 — Remove idb entirely (`IdbDriver`, the idb-only modules, and the `smoke (idb)` lane).
- [ ] Unit 5 — Confirm every scenario passes on the Simulator (the PR's merge gate).

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the backend
  and its stability-ordered ladder, including the *Alternatives considered* entry (idb kept for
  toolchain-free CI-host availability) this proposal revisits.
- [BE-0240 — Capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
  — the per-scenario resolver this proposal simplifies.
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb's accessibility tree drops the
  children of a group-role container, the fidelity gap that motivates leading with XCUITest.

**English** · [日本語](BE-0290-xcuitest-default-ios-backend-ja.md)

# BE-0290 — Make XCUITest the default iOS backend and retire idb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0290](BE-0290-xcuitest-default-ios-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0290") |
| Implementing PR | [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md), [BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) |
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

With capability and fidelity both favoring XCUITest, only one reason keeps idb the default: cost.
The XCUITest runner pays a cold `xcodebuild test-without-building` startup once per scenario, which
is why the cost-ordered actuator resolver
([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md))
puts idb first as the cheap default and treats XCUITest as the escalation. The sibling proposal
*Reuse the XCUITest runner across scenarios* removes that cost by keeping one runner resident per
device and restarting only the app between scenarios, so the startup is paid once per device rather
than once per scenario. Once that amortization lands, the cost argument for idb no longer holds, and
idb's only remaining justification is the reason
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) gave for keeping it in the first
place: running where no full Xcode toolchain is available, since idb needs only its command-line
tool and companion while XCUITest needs `xcodebuild`. This proposal treats that single-environment
benefit as not worth a second permanent backend, flips the default, and removes idb outright.

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
generic runner so they run on XCUITest, re-point the required `E2E` lane onto XCUITest, and stand up
an XCUITest smoke lane alongside the existing `smoke (idb)` — all with the runner prebuilt
(`xcuitest.testRunner`) so CI does not pay a per-run build. This unit adds the XCUITest coverage and
proves the migration in CI; retiring the now-duplicated `smoke (idb)` lane is Unit 4's job, once idb
itself is gone.

### Unit 3 — Update the idb-assuming surfaces and docs

Retire the code paths that assume idb is the default: the cost-order branch, the doctor fallback
that degrades XCUITest to idb, and the capability preflight's idb-first assumptions. `doctor` needs
explicit attention: it routes its own iOS query to idb precisely because idb reads the accessibility
tree without a resident runner, and `doctor` runs outside the runner-reuse pool, so it cannot lean on
that amortization. This unit gives `doctor` a lightweight XCUITest query path — a short-lived runner
it starts and tears down for the preflight — so the check does not regress into a full per-run
startup. Then update the documentation that presents idb as the iOS default (`docs/drivers.md` and
`docs/getting-started/ios.md`, with their Japanese mirrors) to lead with XCUITest.

### Unit 4 — Remove idb entirely

Delete idb, not deprioritize it. This unit removes `IdbDriver` and the idb-only surface around it.
That surface is the companion-version monitor
([BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md)),
the crawl vision tab locator that exists only because idb cannot address tabs, the idb branches in
the shared coordinate-tree read path, and the idb executable's availability wiring. The unit also
drops the now-redundant `smoke (idb)` lane, whose coverage the XCUITest smoke lane Unit 2 stood up
already provides. idb is referenced across roughly 60 modules
and as many tests, so this unit's own breakdown is large; the work is mechanical, though, following
the failing imports outward from the deleted driver. The proposal fixes the outcome — no idb backend
remains in the tree — not a line-by-line removal list.

Two of these deletions carry a caveat. The crawl vision tab locator covers a narrower case than the
Motivation's opaque-group argument — custom image tabs with no accessibility identifier and no `tab`
trait — so its removal is gated on Unit 5 confirming XCUITest addresses that case, not only the
opaque-group one. And BE-0005 (companion-version monitoring), `Implemented` today, has no
"implemented-then-removed" status in the roadmap taxonomy; this unit marks it `Superseded by` this
item (with the reciprocal link) and records the disposition in both Progress logs, so BE-0005 stops
reading as still-true once idb is gone.

### Unit 5 — Confirm every scenario runs on the Simulator

On a named environment (the Simulator model and the Xcode version), run every scenario the idb
`smoke` and `E2E` lanes cover today on XCUITest, with the runner-reuse amortization in place, and
confirm each passes on the Simulator. This Simulator run is the PR's merge gate: the change does not
merge until every scenario is green on XCUITest. The run must also exercise the custom tab-bar shape
the crawl vision tab locator handles today — a tab bar with no accessibility identifier and no `tab`
trait — since Unit 4's deletion of the crawl vision tab locator is a regression unless XCUITest is
confirmed to address it. Record the per-suite wall-clock so the migration's cost is measured, not
assumed.

## Alternatives considered

- **Keep idb as a permanent fallback rather than removing it.** Leaving idb in place as a
  never-default opt-out is the lowest-risk option, and it is rejected here. Maintaining two iOS
  backends indefinitely — two read paths, two actuators, two CI lanes — is the standing cost this
  migration exists to end, and idb's one advantage (running without an Xcode toolchain) does not earn
  a second permanent backend once XCUITest is both more capable and no more expensive across a run
  set. Removal is the goal, not a fallback state.
- **Flip the default without the runner-reuse enabler.** Making XCUITest the default while it still
  restarts the runner per scenario would pay the full per-scenario cold start on every iOS run —
  exactly the cost the cost-ordered actuator resolver
  ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md))
  weighs when it keeps idb the cheap default today. The runner-reuse proposal is a hard prerequisite
  for this reason, and must land before this PR.
- **Split the migration across several PRs.** Flipping the default, migrating the fixtures, and
  deleting idb could each be a separate PR. This is rejected because every intermediate point leaves
  `main` in a broken state — a flipped default with idb still half-wired, or a deleted idb with a
  fixture still pinned to it. Doing the whole migration in one PR, gated on the Simulator run in Unit
  5, keeps `main` correct at every merge; the cost is one large review, which the unit breakdown
  above is meant to make navigable.
- **Land Unit 4's idb deletion as a fast-follow after Units 1–3.** A softer split: flip the default
  and migrate the fixtures first, then delete idb's roughly 60-module surface in a later PR, on the
  grounds that idb is unused-but-present in between so `main` stays correct. This is rejected for two
  reasons. First, idb unused-but-present is precisely the *keep idb as a permanent fallback* state
  the first alternative already rejects: until the follow-up lands, idb stays selectable by `--backend
  idb` and the `smoke (idb)` lane keeps exercising it, and a deprioritized follow-up tends never to
  land — so the split risks making the rejected fallback state permanent by default. Second, the
  deletion is justified *by* Unit 5's Simulator parity evidence: idb is removed because XCUITest is
  proven to cover every scenario it runs, so separating the deletion from that evidence either strands
  it in a PR without the proof or duplicates the Simulator run. Bundling keeps "remove idb because
  parity is proven" a single evidence-gated step.

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

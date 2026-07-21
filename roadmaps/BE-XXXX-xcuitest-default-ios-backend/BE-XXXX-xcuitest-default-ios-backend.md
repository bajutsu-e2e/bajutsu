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

Make XCUITest the default iOS backend, and remove idb once its one remaining advantage — running
without an Xcode toolchain — is no longer needed. Because retiring idb reverses a decision
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) took deliberately, the change is
staged: first flip the default so every iOS run uses XCUITest unless it opts out, then remove idb
outright once on-device evidence shows XCUITest covers every scenario idb runs today and the
toolchain-free path is confirmed unnecessary. The destination is a single iOS backend; the
intermediate state keeps idb only as an explicit opt-out.

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
available — idb needs only its command-line tool and companion; XCUITest needs `xcodebuild`.
This proposal flips the default on that basis and sets the condition under which idb is removed
entirely.

## Detailed design

The work is staged so the default flip and idb's removal are separable, and so the removal is gated
on evidence rather than taken on faith.

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

### Unit 4 — Decide and execute idb's end state

Choose between removing idb entirely and keeping it as an explicit opt-out, on the evidence from
Units 2 and 5 and from a decision on whether the toolchain-free path must survive. Full removal
deletes `IdbDriver` and the idb-only surface around it — the companion-version monitor
([BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md)),
the crawl vision tab locator that exists only because idb cannot address tabs, and the idb branches
in the shared coordinate-tree read path — and drops the `smoke (idb)` lane. Because idb is
referenced across many modules, this unit is itself broken down when it is scheduled; the proposal
records the destination and the gate, not a line-by-line removal plan.

### Unit 5 — On-device parity verification

Removal is gated on evidence. On a named environment (the Simulator model and the Xcode version),
confirm that every scenario the idb `smoke` and `E2E` lanes cover today passes on XCUITest with the
runner-reuse amortization in place, and record the per-suite wall-clock so the migration's cost is
measured rather than assumed. A scenario that only idb can run today, if any is found, is a blocker
that this unit surfaces before Unit 4 proceeds.

## Alternatives considered

- **Keep idb as a permanent fallback rather than removing it.** Leaving idb in place as a
  never-default opt-out is the lowest-risk option and is exactly the intermediate state this proposal
  passes through. It is rejected as the *destination* because maintaining two iOS backends
  indefinitely — two read paths, two actuators, two CI lanes — is the standing cost the migration
  exists to end. The proposal keeps this state as a waypoint, not the goal.
- **Flip the default without the runner-reuse enabler.** Making XCUITest the default while it still
  restarts the runner per scenario would pay the full per-scenario cold start on every iOS run — the
  regression [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) named when it kept
  idb. The runner-reuse proposal is a hard prerequisite for this reason.
- **Remove idb immediately, without staging.** Deleting idb in one step would drop the toolchain-free
  path and the required `smoke (idb)` lane before any on-device evidence shows XCUITest covers every
  idb scenario. Staging the flip ahead of the removal is what makes the removal safe to take.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Flip the iOS default to XCUITest; retire the cost-ordered idb-first selection.
- [ ] Unit 2 — Migrate the `-noax` fixtures and the `smoke` / `E2E` lanes onto XCUITest.
- [ ] Unit 3 — Retire the idb-assuming code paths and update the docs to lead with XCUITest.
- [ ] Unit 4 — Decide and execute idb's end state (full removal or permanent opt-out).
- [ ] Unit 5 — On-device parity verification with the runner-reuse amortization in place.

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the backend
  and the cost-ordered ladder, including the *Alternatives considered* entry this proposal revisits.
- [BE-0240 — Capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
  — the per-scenario resolver this proposal simplifies.
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb's accessibility tree drops the
  children of a group-role container, the fidelity gap that motivates leading with XCUITest.

**English** · [日本語](BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)

# BE-0291 — Reuse the XCUITest runner across scenarios to amortize cold startup

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0291](BE-0291-xcuitest-runner-reuse-across-scenarios.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0291") |
| Implementing PR | [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) |
<!-- /BE-METADATA -->

## Introduction

Keep one XCUITest runner resident per device across a run set, and restart only the app between
scenarios, so a run pays the runner's cold startup once per device rather than once per scenario.
The channel, the way elements are addressed, and the per-scenario device reset are all unchanged; the change
is only how long the runner process lives. This amortization removes the largest fixed cost of the
XCUITest backend, which is what makes the backend a candidate to become the iOS default rather than
the escalation-only actuator it is today.

## Motivation

The XCUITest backend actuates from a resident runner on the device — an `xcodebuild
test-without-building` process that launches the app, then serves a loopback HTTP endpoint the
Python driver drives ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)). Starting
that runner is expensive: the XCTest host boots and the app launches before the runner answers its
first health check, and a cold start on a loaded continuous-integration (CI) host routinely exceeds
10 seconds — the driver allows up to 120 seconds for it (`_RUNNER_STARTUP_TIMEOUT` in
`bajutsu/platform_lifecycle/environments/xcuitest.py`).

Today a run pays that cold start for every scenario. The device pool leases a device per scenario
(`bajutsu/runner/pool.py`), and each lease builds a fresh `XcuitestEnvironment` whose `start()`
spawns a new runner and whose teardown terminates it on release. A suite of 20 scenarios
therefore starts and tears down 20 runners, spending several minutes on runner startup alone
before any assertion runs. This per-scenario cost is the concrete reason the actuator resolver keeps
idb the cheap default and treats XCUITest as the escalation
([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)):
idb has no resident runner and no build step, so it starts nothing between scenarios.

The cost is not intrinsic to XCUITest; it is intrinsic to restarting the runner. Within a single
lease, the `relaunch` step already reuses the runner: `device_relauncher`
(`bajutsu/platform_lifecycle/relaunchers.py`) terminates and relaunches only the app process, never
the runner, and waits until the app is ready again. The runner drives whichever app is currently
launched, so it carries no scenario-specific state of its own. Generalizing that same app-only
restart from within a lease to across leases on one device is the whole of this proposal.

Amortizing the startup matters beyond raw speed, because the per-scenario cost is the standing
argument for a real capability gap. The idb backend reads the VoiceOver-facing accessibility tree,
in which a container that is itself one accessibility element — an `AXGroup` such as a tab bar —
is a leaf that hides its children, so idb's element tree drops every element under such a group
(idb issue 767). The XCUITest backend reads the XCTest automation snapshot instead, which
descends through those containers and enumerates each child, so it alone can show a faithful,
fully expanded element tree. Making that faithful tree the default iOS behavior — in a run's
captured evidence and in the web UI that renders it — is blocked today only by the startup cost this
item removes. This proposal is therefore the enabler; flipping the iOS default from idb to XCUITest
is a separate follow-on that depends on it, and is deliberately out of scope here.

## Detailed design

The change is confined to the device pool's environment lifecycle and the XCUITest environment's
process ownership. The Python↔runner channel, the snapshot-handle addressing
([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)), the single-snapshot query
([BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)), and
every per-scenario reset (`erase` / `relaunch` / permission grants) stay exactly as they are.

### Unit 1 — A per-device warm-runner cache in the pool, keyed by actuator

The pool holds one resident runner per device, keyed by `(udid, actuator)`, and reuses it across
leases the way it already reuses the per-device network collector (`bajutsu/runner/pool.py` starts
one collector per device up front and reuses it across leases). A lease whose resolved actuator
matches the cached runner's actuator takes over the running process instead of spawning one. The key
includes the actuator because the actuator is resolved per scenario
([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)):
a scenario that resolves to idb must not inherit a warm XCUITest runner, and an XCUITest scenario must not inherit a warm idb runner.

The runner handle lives on the pool, not on a fresh per-lease `XcuitestEnvironment`. Today
`pool.py`'s `lease()` builds a new environment each time, and `XcuitestEnvironment.start()` inlines
both the simctl device prep and the runner spawn
(`bajutsu/platform_lifecycle/environments/xcuitest.py`). This unit separates those two steps: a lease
with a cached runner runs the device prep and the app relaunch but skips the spawn, taking over the
process the pool holds, and the spawn happens only on a cache miss. Unit 3 covers the matching
ownership move — the pool, not the lease, now terminates that process.

### Unit 2 — App-only handover between same-actuator scenarios

When a lease reuses a warm runner, scenario setup restarts the app rather than the runner. It uses
the existing app-only path `device_relauncher` follows within a lease: terminate the app; re-apply
the scenario's launch environment, launch arguments, and locale; launch again; and wait until ready.
Preconditions that reset device state (`erase`) and permission grants continue to run through simctl
before the app launches, so a reused runner never weakens the per-scenario isolation the single-lease
path gives today.

### Unit 3 — Runner ownership, teardown, and actuator switches

The warm runner outlives the lease that created it, so ownership moves from the lease to the pool.
The pool terminates a device's runner when the run set finishes, when the next scenario on that
device resolves to a different actuator (the cached runner is torn down before the new actuator's
environment starts), and when a fault forces a fresh runner. This preserves the invariant that the
instance owning the runner process is the instance that terminates it — the invariant the current
per-lease teardown states explicitly — moved up one level from the lease to the pool.

### Unit 4 — Crash recovery across the reused runner

A runner that crashes or wedges mid-run must not carry over into the next scenario that would reuse it. The
pool treats an unsuccessful health check on a warm runner as a cache miss: it discards the unresponsive runner and
starts a fresh one for the next lease, reusing the driver's existing bounded health-poll recovery
rather than adding a second recovery path. A single scenario's fault therefore costs one extra cold
start, not the loss of the run.

### Unit 5 — Measure the amortization on a named environment

Establish the saving the same way
[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) fixed
its query baseline. On a named environment — the Simulator model, the Xcode version, and a
multi-scenario showcase suite — record the suite's total runner-startup time before and after the
change. Then confirm it drops from once-per-scenario to once-per-device. The acceptance number is the
per-suite startup time, not a single scenario's, because the amortization only shows across
scenarios.

## Alternatives considered

- **Flip the iOS default to XCUITest without amortizing startup.** Making XCUITest the default while
  it still restarts the runner per scenario would pay the full per-scenario cold start on every iOS
  run — exactly the cost the cost-ordered actuator resolver
  ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md))
  weighs when it keeps idb the cheap default today. Amortizing the startup first is what lets that
  default be reconsidered honestly rather than by absorbing a known regression.
- **Prebuild the `.xctestrun` and call the problem solved.** Naming a prebuilt test runner in
  `xcuitest.testRunner` removes the `xcodebuild build-for-testing` step from the run path, and this
  item assumes it, but it does not remove the runner's cold *startup* — the XCTest host boot and app
  launch that dominate the per-scenario cost remain. Prebuilding is a complementary operational
  choice, not a substitute for reuse.
- **Keep one warm runner regardless of actuator.** A single per-device runner that ignored the
  resolved actuator would let an idb scenario run against a warm XCUITest runner, silently changing
  the actuator the run manifest records and violating the one-actuator-per-scenario rule
  ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)).
  Keying the cache on the actuator keeps the resolver in charge of the choice.
- **Reuse the runner across devices.** The runner is bound to one Simulator through its `xcodebuild
  -destination`, so there is no cross-device runner to share; parallelism across devices is the
  pool's existing concern and is orthogonal to reusing a runner on one device.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Per-device warm-runner cache in the pool, keyed by `(udid, actuator)`.
- [x] Unit 2 — App-only handover between same-actuator scenarios via the existing relaunch path.
- [x] Unit 3 — Runner ownership moved to the pool; teardown on run-set end, actuator switch, and fault.
- [x] Unit 4 — Crash recovery treats an unresponsive warm runner as a cache miss.
- [x] Unit 5 — Amortization proven deterministically (the runner spawns once per device across a
  multi-scenario suite, `tests/runner/test_pool.py` / `tests/test_xcuitest_environment.py`); the
  wall-clock per-suite startup saving on a named Simulator + Xcode environment is the on-device
  confirmation, which needs hardware the fast gate lacks.

**Log**

- The reuse landed as a two-method extension of the `Environment` seam (`has_reusable_resident` /
  `end_lease`, both defaulting to "no warm resident"), a per-device warm-runner cache in
  `runner/pool.py` keyed by `(udid, actuator)`, and a resume-aware `XcuitestEnvironment.start` that
  relaunches only the app when the runner is healthy — so the pool stays actuator-agnostic and every
  non-XCUITest backend is unchanged. Scoped to the Simulator path (real-device XCUITest and the live
  WebDriver environment stay cold per lease). See the implementing PR.

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the resident
  runner and the loopback channel whose per-scenario startup this item amortizes.
- [BE-0105 — Single-snapshot element query for XCUITest](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)
  — the query-latency follow-on this item mirrors in method.
- [BE-0240 — Capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
  — the per-scenario actuator resolution the cache key respects.
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb's accessibility tree
  drops the children of a group-role container, the capability gap this amortization ultimately
  helps close.

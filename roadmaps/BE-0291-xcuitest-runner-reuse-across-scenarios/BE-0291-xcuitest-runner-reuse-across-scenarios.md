**English** · [日本語](BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)

# BE-0291 — Reuse the XCUITest runner across scenarios to amortize cold startup

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0291](BE-0291-xcuitest-runner-reuse-across-scenarios.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0291") |
| Implementing PR | [#1226](https://github.com/bajutsu-e2e/bajutsu/pull/1226) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal keeps one XCUITest runner resident per device across a run set. Between scenarios, only the app restarts. A run then pays the cold startup of the runner once per device, not once per scenario.

The channel, the way elements are addressed, and the per-scenario device reset are all unchanged. The change affects only how long the runner process lives.

This amortization removes the largest fixed cost of the XCUITest backend. The removal makes the backend a candidate to become the iOS default. Today the backend is an actuator used only on escalation.

## Motivation

The XCUITest backend actuates from a resident runner on the device. The runner is an `xcodebuild test-without-building` process. It launches the app, then serves a loopback HTTP endpoint that the Python driver drives ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)).

Starting that runner is expensive. The XCTest host boots and the app launches before the runner answers its first health check. A cold start on a loaded continuous-integration (CI) host routinely exceeds 10 seconds. The driver allows up to 120 seconds for it (`_RUNNER_STARTUP_TIMEOUT` in `bajutsu/platform_lifecycle/environments/xcuitest.py`).

Today a run pays that cold start for every scenario:

- The device pool leases a device per scenario (`bajutsu/runner/pool.py`).
- Each lease builds a fresh `XcuitestEnvironment`. Its `start()` spawns a new runner, and its teardown terminates the runner on release.
- A suite of 20 scenarios therefore starts and tears down 20 runners. It spends several minutes on runner startup alone before any assertion runs.

This per-scenario cost is the concrete reason why the actuator resolver keeps idb the cheap default and treats XCUITest as the escalation ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)). idb has no resident runner and no build step, so it starts nothing between scenarios.

The cost is not intrinsic to XCUITest. It is intrinsic to restarting the runner. Within a single lease, the `relaunch` step already reuses the runner. `device_relauncher` (`bajutsu/platform_lifecycle/relaunchers.py`) terminates and relaunches only the app process, never the runner, and waits until the app is ready again. The runner drives whichever app is currently launched, so it carries no scenario-specific state of its own. This proposal generalizes that same app-only restart. The restart now applies across leases on one device, not only within a lease. That generalization is the whole of the proposal.

Amortizing the startup matters beyond raw speed, because the per-scenario cost is the standing argument for a real capability gap.

- The idb backend reads the VoiceOver-facing accessibility tree. In that tree, a container that is itself one accessibility element, such as the `AXGroup` of a tab bar, is a leaf that hides its children. The element tree of idb therefore drops every element under such a group (idb issue 767).
- The XCUITest backend reads the XCTest automation snapshot instead. The snapshot descends through those containers and enumerates each child. Only XCUITest can show a faithful, fully expanded element tree.

Making that faithful tree the default iOS behavior would improve the captured evidence of a run. It would also improve the web UI that renders the evidence. Only the startup cost that this item removes blocks that change today. This proposal is therefore the enabler. Flipping the iOS default from idb to XCUITest is a separate follow-on that depends on this item. It is deliberately out of scope here.

## Detailed design

The change is confined to the device-pool lifecycle of environments and to the process ownership of the XCUITest environment. The following stay exactly as they are:

- The Python↔runner channel.
- The snapshot-handle addressing ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)).
- The single-snapshot query ([BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)).
- Every per-scenario reset (`erase`, `relaunch`, and permission grants).

### Unit 1 — A per-device warm-runner cache in the pool, keyed by actuator

The pool holds one resident runner per device, keyed by `(udid, actuator)`. The pool reuses the runner across leases, the way it already reuses the per-device network collector. (`bajutsu/runner/pool.py` starts one collector per device up front and reuses it across leases.) A lease whose resolved actuator matches the actuator of the cached runner takes over the running process, instead of spawning one.

The key includes the actuator, because the actuator is resolved per scenario ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)). A scenario that resolves to idb must not inherit a warm XCUITest runner. An XCUITest scenario must not inherit a warm idb runner.

The runner handle lives on the pool, not on a fresh per-lease `XcuitestEnvironment`. Today `lease()` in `pool.py` builds a new environment each time. `XcuitestEnvironment.start()` inlines both the simctl device prep and the runner spawn (`bajutsu/platform_lifecycle/environments/xcuitest.py`). This unit separates those two steps:

- A lease with a cached runner runs the device prep and the app relaunch. It skips the spawn and takes over the process that the pool holds.
- The spawn happens only on a cache miss.

Unit 3 covers the matching ownership move. The pool, not the lease, now terminates the process.

### Unit 2 — App-only handover between same-actuator scenarios

Suppose a lease reuses a warm runner. Scenario setup then restarts the app, not the runner. It uses the existing app-only path that `device_relauncher` follows within a lease:

1. Terminate the app.
2. Re-apply the launch environment, the launch arguments, and the locale of the scenario.
3. Launch the app again.
4. Wait until the app is ready.

Preconditions that reset device state (`erase`) and permission grants continue to run through simctl before the app launches. A reused runner therefore never weakens the per-scenario isolation that the single-lease path gives today.

### Unit 3 — Runner ownership, teardown, and actuator switches

The warm runner outlives the lease that created it, so ownership moves from the lease to the pool. The pool terminates the runner of a device in three cases:

- The run set finishes.
- The next scenario on the device resolves to a different actuator. The pool tears down the cached runner before the environment of the new actuator starts.
- A fault forces a fresh runner.

This preserves an invariant that the current per-lease teardown states explicitly: the instance that owns the runner process is the instance that terminates it. The invariant moves up one level, from the lease to the pool.

### Unit 4 — Crash recovery across the reused runner

A runner that crashes or wedges mid-run must not carry over into the next scenario that would reuse it. The pool treats an unsuccessful health check on a warm runner as a cache miss. It discards the unresponsive runner and starts a fresh one for the next lease. The pool reuses the existing bounded health-poll recovery of the driver, and adds no second recovery path. The fault of a single scenario then costs one extra cold start, not the loss of the run.

### Unit 5 — Measure the amortization on a named environment

Establish the saving the same way that [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) fixed its query baseline. Use a named environment: the Simulator model, the Xcode version, and a multi-scenario showcase suite. Record the total runner-startup time of the suite before and after the change. Then confirm that the time drops from once per scenario to once per device.

The acceptance number is the startup time per suite, not the startup time of a single scenario. The amortization shows only across scenarios.

## Alternatives considered

- **Flip the iOS default to XCUITest without amortizing startup.** Making XCUITest the default while it still restarts the runner per scenario would pay the full per-scenario cold start on every iOS run. This is exactly the cost that the cost-ordered actuator resolver ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)) weighs when it keeps idb the cheap default today. Amortizing the startup first lets the team reconsider that default honestly, without absorbing a known regression.
- **Prebuild the `.xctestrun` and call the problem solved.** Naming a prebuilt test runner in `xcuitest.testRunner` removes the `xcodebuild build-for-testing` step from the run path, and this item assumes the prebuild. The prebuild does not remove the cold *startup* of the runner, though. The XCTest host boot and the app launch dominate the per-scenario cost, and they remain. Prebuilding is a complementary operational choice, not a substitute for reuse.
- **Keep one warm runner regardless of actuator.** A single per-device runner that ignored the resolved actuator would let an idb scenario run against a warm XCUITest runner. That would silently change the actuator that the run manifest records. It would also violate the one-actuator-per-scenario rule ([BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)). Keying the cache on the actuator keeps the resolver in charge of the choice.
- **Reuse the runner across devices.** The runner is bound to one Simulator through its `xcodebuild -destination`, so there is no cross-device runner to share. Parallelism across devices is the existing concern of the pool. It is orthogonal to reusing a runner on one device.

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

- The reuse landed in three parts:
  - A two-method extension of the `Environment` seam: `has_reusable_resident` and `end_lease`. Both default to "no warm resident".
  - A per-device warm-runner cache in `runner/pool.py`, keyed by `(udid, actuator)`.
  - A resume-aware `XcuitestEnvironment.start`, which relaunches only the app when the runner is healthy.
- The pool therefore stays actuator-agnostic, and every non-XCUITest backend is unchanged.
- The scope is the Simulator path. Real-device XCUITest and the live WebDriver environment stay cold per lease.
- See the implementing PR.

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

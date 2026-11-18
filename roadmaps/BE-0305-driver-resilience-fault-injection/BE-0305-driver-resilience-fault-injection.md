**English** · [日本語](BE-0305-driver-resilience-fault-injection-ja.md)

# BE-0305 — Real-device fault-injection coverage for driver resilience paths

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0305](BE-0305-driver-resilience-fault-injection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
| Implementing PR | [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461), [#1724](https://github.com/bajutsu-e2e/bajutsu/pull/1724) |
| Topic | Driver & backend architecture |
| Related | [BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

Two resilience mechanisms exist specifically to survive real-device fault conditions. CI never
triggers either one with a real fault.

- **Transient-empty retry.** `CoordinateTreeDriver`'s retry
  ([BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base.md)) exists
  for the near-empty element tree that idb and adb return mid-transition. Its tests fabricate a
  synthetic count sequence (`[3, 1, 3]`) with the backoff zeroed out.
- **Crash recovery and retry.** The XCUITest channel's path
  ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md),
  [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md))
  exists for a killed or frozen resident runner. Its tests raise a synthetic exception from a nested
  closure.

The on-device conformance suite does run for real, but it exercises neither path. Its screens are
pre-seeded and waited-for-ready, so the transient-empty branch is never hit. No job deliberately kills
the runner mid-action. This item adds real-fault-injection coverage for both.

## Motivation

A synthetic count sequence or a raised exception proves that the retry or recovery *code path* runs
when triggered. That is real and useful coverage of the control flow. It cannot prove that the
mechanism survives the real condition it was built for:

- the real shape and timing of idb's and uiautomator's mid-transition near-empty response;
- the real socket-level failure mode of a killed XCUITest resident runner (a clean RST, a hung
  connection, or a partial write), and its real relaunch latency.

A regression could break the real detection heuristic: `_is_transient_empty`'s threshold, or the crash
classifier's exception matching. If the synthetic-fixture tests stay green, that regression ships
unnoticed. Nothing in CI recreates the condition these mechanisms exist to survive.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Real transient-empty fault injection (idb/adb).** Add an on-device conformance or E2E case. The
  case deliberately drives a screen transition known to produce a real near-empty intermediate tree,
  or adds artificial contention that reproduces the condition. It asserts that `CoordinateTreeDriver`'s
  retry recovers without a false "element not found."
- **Real crash-recovery fault injection (XCUITest).** Add an on-device case that deliberately kills or
  freezes the resident BajutsuRunner process mid-scenario. It asserts that the driver's crash-recovery
  path relaunches the runner. The scenario then either recovers, or fails with the correct
  `XcuitestRunnerCrashError`-derived diagnosis, not an unrelated timeout.
- **Land both as non-gating signal first.** Fault-injection lanes carry more inherent flakiness risk
  than the existing conformance suite. Follow the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), and
  promote each lane once it proves stable.
- **Keep the existing synthetic-fixture unit tests.** They remain the fast, deterministic check of the
  control-flow logic itself. This item adds the real-condition layer underneath them and does not
  replace them.

## Alternatives considered

- **Trust the synthetic fixtures, since the control-flow logic is unit-tested.** The control flow can
  be correct for a fabricated count sequence or a raised exception. That says nothing about whether
  the detection heuristic fires on the real condition it targets, and firing on that condition is the
  property these mechanisms exist to guarantee.
- **Wait for the mechanisms to fail in production before adding real coverage.** A retry or recovery
  path that fails silently in the field is exactly the outcome that fault injection in CI exists to
  catch before a user does.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add real transient-empty fault injection for idb/adb, non-gating first.
- [x] Add real crash-recovery fault injection for XCUITest, non-gating first.
- [x] Promote each to required once stable.
- [x] Keep the existing synthetic-fixture unit tests as the fast, deterministic control-flow check.

Log:

- [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461) — landed both lanes as non-gating signals.
  - **adb:** `fault-injection (adb)` puts the emulator's display to sleep. That was *measured* to make
    the real read source return 0 elements against the 2-element floor, with no error to mistake for
    something else. The retry then rides over the condition, and the known element resolves. A second
    case holds the display down so the budget runs out, and pins that outcome as a loud
    `ElementNotFound`.
  - Both adb cases were mutation-checked. Zeroing `_READY_MIN` breaks the detection heuristic exactly
    as the Motivation describes, and it reddens both cases. The fabricated count sequence cannot do
    that.
  - **XCUITest:** `fault-injection (xcuitest)` signals the runner's own host process. `SIGSTOP` leaves
    its socket accepting while nothing answers. A short freeze is absorbed by BE-0207's retry
    (measured 15.5s, one attempt). A freeze past the retry budget is ridden out by BE-0287's crash
    recovery (measured 46.6s, "recovered from a mid-run crash; re-issuing"). `SIGKILL` ends the read
    on a `BackendCrashError` naming a mid-run runner fault.
  - Neither lane guesses a duration. Each lifts its fault on the driver's own log record that it
    reached the layer under test (`tests/fault_injection.py`). For that reason `CoordinateTreeDriver`'s
    retry, silent until now, gained a log line, with fast-suite tests of its own.
  - The synthetic fixtures stay as the fast control-flow check.
  - One measurement is worth recording. A killed runner does **not** take crash recovery's
    process-exited fast path, because `_runner_alive` reads the `xcodebuild` parent, which outlives the
    killed host process. The diagnosis is correct, but it arrives after the full 60s recovery window
    rather than immediately. This item leaves that as-is, since the verdict is right and loud. A
    tighter liveness check is a separate item.
- [#1724](https://github.com/bajutsu-e2e/bajutsu/pull/1724) — promoted both lanes into their lanes'
  required aggregate checks, closing the item. The promotion condition was "once stable".
  - The first attempt to settle stability by counting got the answer wrong. `gh run list --limit 150`
    returns the newest 150 runs. At this repository's run density, that reaches back only about four
    days. A `--jq 'select(.createdAt > "2026-08-04")'` filter therefore matched every row and bounded
    nothing. Those four days fell entirely on the clean side of a regime change, and that is how the
    lanes came to look flawless.
  - The re-measurement paginated the Actions API over every run from 2026-08-04 to 2026-08-22. The
    record is `fault-injection (xcuitest)` 116 passes against **78 failures**, and
    `fault-injection (adb)` 378 against 4.
- What justifies the promotion is a found cause, not a low failure rate.
  - Every one of those 78 iOS failures precedes 2026-08-13T12:58Z. At that time
    [#1609](https://github.com/bajutsu-e2e/bajutsu/pull/1609) merged
    `fix(xcuitest): keep the runner HTTP server alive through abandoned connections`. The last failure
    landed 30 minutes before the merge.
  - The lane holds the runner under `SIGSTOP`, which is exactly how a connection comes to be
    abandoned. The lane therefore tripped that SIGPIPE far more often than any suite driving a healthy
    runner. The lane found a real runner-channel defect, which is what it is for.
  - Since that commit, `fault-injection (xcuitest)` has run 64 times without a failure. Over the same
    span, `conformance (xcuitest)` had 58 passes and 2 failures.
  - `fault-injection (adb)` ran 73 times for one failure. That failure was the emulator never coming
    up (`Unable to connect to adb daemon on port: 5037`), not anything the lane asserts. Over the same
    span, `conformance (adb)` had four failures in 67 runs.
  - Both lanes now sit at or better than a suite that has been on the gate throughout. The promotion
    rests on that comparison.
- Promotion costs the gate no wall clock. Each lane's fault-injection job finishes well inside its
  `conformance` sibling (3min vs 6min on adb, 9min vs 22min on xcuitest), so neither is on the
  critical path. No branch-protection change was needed. The ruleset pins the `E2E (iOS)` and
  `E2E (android)` aggregators, so promotion is a `needs:` edit.
- `tests/test_e2e_gate_needs.py` is the new regression net. It has three layers, because the first two
  proved insufficient on their own.
  - Layer one pins which jobs each gate depends on.
  - Layer two pins that every one of those jobs has its result read.
  - Layer three runs the verdict script under `bash`, once per dependency per result. It pins that
    each of those results actually reddens the check.
- Layer two alone found a live defect while being written. `E2E (iOS)` listed `changes` in `needs:`
  but never read `needs.changes.result`. A failed `changes` job would then skip every macOS job as a
  *dependency* failure, and the required check would count that as the path-skip pass. It would report
  green having run nothing. Android and web both guarded against it, and iOS did not. The same change
  fixes it, since the fix and the promotion edit the same handful of lines in that one job.
- The outer layers exist because a review pass showed two gaps.
  - Agreement between `needs:` and the step reads survives dropping a job from both, which reinstates
    the same defect.
  - Rewriting the loop to compare against a value no job reports leaves a permanently green required
    check.
- All three layers were mutation-checked. Each of these mutations reddens: removing `changes` from
  every half, narrowing a gate, dropping the `cancelled` arm, and neutering the verdict.
- Reviewing the net itself added two pins beside those three layers. They cover routes that an
  addition, rather than an edit, opens.
  - A lane whose aggregator this file never reads.
  - A `continue-on-error` that disarms a red. On a gated job, it hands `needs:` a `success` that its
    job never earned. On the aggregator's own verdict step, it reports the required check green over a
    script that exited non-zero.

## References

- [BE-0254 — Extract a shared CoordinateTreeDriver base for idb and adb](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base.md)
- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/drivers/coordinate_tree.py` (`_read_settled_tree`, `_is_transient_empty`,
  `_empty_backoff`), `tests/test_coordinate_tree.py`, `bajutsu/drivers/xcuitest.py`
  (`_with_retry`, `_with_crash_recovery`, `XcuitestRunnerCrashError`), `tests/test_xcuitest.py`,
  `tests/driver_conformance.py`

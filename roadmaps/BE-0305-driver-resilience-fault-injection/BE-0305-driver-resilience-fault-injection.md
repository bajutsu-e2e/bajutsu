**English** · [日本語](BE-0305-driver-resilience-fault-injection-ja.md)

# BE-0305 — Real-device fault-injection coverage for driver resilience paths

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0305](BE-0305-driver-resilience-fault-injection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
| Implementing PR | _(pending)_ |
| Topic | Driver & backend architecture |
| Related | [BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

Two resilience mechanisms exist specifically to survive real-device fault conditions, and neither is
ever triggered by a real fault in CI. `CoordinateTreeDriver`'s transient-empty retry
([BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base.md)) exists for
idb's and adb's mid-transition near-empty element tree; its tests fabricate a synthetic count
sequence (`[3, 1, 3]`) with the backoff zeroed out. The XCUITest channel's crash-recovery and retry
path ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md),
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md))
exists for a killed or frozen resident runner; its tests raise a synthetic exception from a nested
closure. The on-device conformance suite, which does run for real, never exercises either path: its
screens are pre-seeded and waited-for-ready, so the transient-empty branch is never hit, and no job
deliberately kills the runner mid-action. This item adds real-fault-injection coverage for both.

## Motivation

A synthetic count sequence or a raised exception proves the retry/recovery *code path* runs when
triggered — real and useful coverage of the control flow. It cannot prove the mechanism actually
survives the real condition it was built for: the real shape and timing of idb's/uiautomator's
mid-transition near-empty response, or the real socket-level failure mode (a clean RST, a hung
connection, a partial write) and real relaunch latency of a killed XCUITest resident runner. A
regression that broke the real detection heuristic (`_is_transient_empty`'s threshold, or the crash
classifier's exception matching) while leaving the synthetic-fixture tests green would ship
unnoticed, because nothing in CI ever recreates the condition these mechanisms exist to survive.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Real transient-empty fault injection (idb/adb).** Add an on-device conformance or E2E case that
  deliberately drives a screen transition known to produce a real near-empty intermediate tree (or
  adds artificial contention that reproduces the condition), asserting `CoordinateTreeDriver`'s retry
  recovers without a false "element not found."
- **Real crash-recovery fault injection (XCUITest).** Add an on-device case that deliberately kills or
  freezes the resident BajutsuRunner process mid-scenario, asserting the driver's crash-recovery path
  relaunches it and the scenario either recovers or fails with the correct
  `XcuitestRunnerCrashError`-derived diagnosis, not an unrelated timeout.
- **Land both as non-gating signal first.** Fault-injection lanes carry more inherent flakiness risk
  than the existing conformance suite; follow the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) and
  promote each once it proves stable.
- **Keep the existing synthetic-fixture unit tests.** They remain the fast, deterministic check of the
  control-flow logic itself; this item adds the real-condition layer underneath them, not a
  replacement.

## Alternatives considered

- **Trust the synthetic fixtures, since the control-flow logic is unit-tested.** Control flow being
  correct for a fabricated count sequence or a raised exception says nothing about whether the
  detection heuristic actually fires on the real condition it targets — the property these mechanisms
  exist to guarantee.
- **Wait for the mechanisms to fail in production before adding real coverage.** A retry/recovery path
  failing silently in the field is exactly the outcome fault injection in CI is meant to catch before
  a user does.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add real transient-empty fault injection for idb/adb, non-gating first.
- [x] Add real crash-recovery fault injection for XCUITest, non-gating first.
- [ ] Promote each to required once stable.
- [x] Keep the existing synthetic-fixture unit tests as the fast, deterministic control-flow check.

Log:

- _(pending)_ — landed both lanes as non-gating signals. **adb:** `fault-injection (adb)` puts the
  emulator's display to sleep, which was *measured* to make the real read source return 0 elements
  against the 2-element floor, with no error to mistake for something else; the retry then rides over
  it and the known element resolves. A second case holds the display down so the budget runs out and
  pins that as a loud `ElementNotFound`. Both were mutation-checked: zeroing `_READY_MIN` (breaking
  the detection heuristic exactly as the Motivation describes) reddens both, which the fabricated
  count sequence cannot do. **XCUITest:** `fault-injection (xcuitest)` signals the runner's own host
  process — `SIGSTOP` leaves its socket accepting while nothing answers, so a short freeze is absorbed
  by BE-0207's retry (measured 15.5s, one attempt) and a freeze past the retry budget is ridden out by
  BE-0287's crash recovery (measured 46.6s, "recovered from a mid-run crash; re-issuing"); `SIGKILL`
  ends the read on a `BackendCrashError` naming a mid-run runner fault. Neither lane guesses a
  duration: each lifts its fault on the driver's own log record that it reached the layer under test
  (`tests/fault_injection.py`), which is why `CoordinateTreeDriver`'s retry — silent until now — gained
  a log line, with fast-suite tests of its own. The synthetic fixtures stay as the fast control-flow
  check. Measured on the way and worth recording: a killed runner does **not** take crash recovery's
  process-exited fast path, because `_runner_alive` reads the `xcodebuild` parent, which outlives the
  killed host process — so the diagnosis is correct but arrives after the full 60s recovery window
  rather than immediately. Left as-is here (the verdict is right and loud); a tighter liveness check is
  a separate item.

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

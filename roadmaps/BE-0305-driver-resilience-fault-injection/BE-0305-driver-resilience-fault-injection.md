**English** · [日本語](BE-0305-driver-resilience-fault-injection-ja.md)

# BE-0305 — Real-device fault-injection coverage for driver resilience paths

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0305](BE-0305-driver-resilience-fault-injection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
| Implementing PR | [#1447](https://github.com/bajutsu-e2e/bajutsu/pull/1447) |
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
  *(adb only: [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
  retired idb, so `AdbDriver` is the sole `CoordinateTreeDriver` subclass left to cover.)*
- [x] Add real crash-recovery fault injection for XCUITest, non-gating first.
- [ ] Promote each to required once stable.
  *(A repository ruleset change — adding `fault (adb)` / `fault (xcuitest)` to the aggregators'
  `needs:` and the required-check list — not a code PR, so it stays open past this item's
  implementation, as it did for [BE-0309](../BE-0309-serve-postgres-ci-lane/BE-0309-serve-postgres-ci-lane.md).)*
- [x] Keep the existing synthetic-fixture unit tests as the fast, deterministic control-flow check.

### Log

- The two fault-injection lanes land together. On Android, `fault (adb)` contends the tab bar and
  reads the tree with no readiness wait in between until UI Automator really answers mid-transition
  with no hierarchy, then asserts the retry recovered the real screen without a false "element not
  found". It needs animations left on — the one job in the lane that keeps them. The tap is a raw
  `input tap`, not the driver's actuator, because that actuator settles the screen and a settled
  screen no longer shows the transient.
- **The read must go over `uiautomator dump`, not the resident channel — the reverse of this item's
  first assumption, and the reason the lane reproduced nothing on its first five CI runs.** The
  transient-empty tree is a *stock* dump artifact, and the resident server (BE-0245) exists partly to
  eliminate that class of artifact; BE-0332 Unit 4 completed it, so `respondSource` now runs
  `waitForIdle()` and `settledDump()` — re-dumping until two consecutive dumps match — before it
  answers, whether or not a `since` mark rides on the request. The device therefore settles the
  transient away on its own side, and no host-side tap timing can make a read through that channel
  observe one. The suite forces the dump path with `BAJUTSU_ADB_RESIDENT=0` and asserts it got it.
  Forcing it through the environment (not by blanking the driver's channel afterwards) is what keeps
  the fault honest: a live resident server holds the device's single UiAutomation session, so a dump
  taken beside one reads empty for the whole lease — a wedge that would satisfy a naive "did it come
  back empty?" check while proving nothing. The final recovery assertion is the second guard, since a
  wedge never recovers. Because a dump costs seconds to start, far longer than one transition, the
  contention is sustained rather than raced: a background thread taps continuously while the main
  thread reads, and the loop is bounded by wall clock rather than a round count. Moving to the dump
  path is what finally reached the branch: the first run on it fired the retry (six re-reads), where
  five runs on the resident channel had never fired it once.
- The contention stops the moment the fault lands, not when the read that caught it returns. The
  retry under test is what runs *after* the first empty read, and it rides over a transition that is
  *ending* — so it has to see the screen calming, as it would on a device left alone. Tapping through
  those attempts denies the mechanism the only condition it can recover in: one run's retry fired
  fifteen times and never came back, against six and a clean recovery when left alone, and the
  never-recovering empty read as a wedged accessibility bridge when nothing was wedged. The tapper
  therefore watches the retry counter and returns as soon as it moves, mid-read if that is when it
  lands.
- Recovery is checked by a bounded poll, not by the single read that follows the contention.
  `query`'s own retry budget (~0.75 s) answers "is this read a mid-transition blip"; whether the
  accessibility bridge survived being deliberately hammered is a different question, and one the
  device can legitimately take seconds to answer. A single read there failed a device that recovers a
  moment later. The poll is a condition wait, never a fixed sleep — it returns the instant the screen
  resolves — and a genuine wedge still fails loudly when its window expires.
- Proving the branch fired needed the mechanism to be observable, so `CoordinateTreeDriver` now
  counts its transient-empty re-reads. Without the counter the suite could only assert that a read
  came back intact — which also holds when the contention never reproduced the condition, so a green
  run would have proved nothing. A round that exhausts its bound now fails with exactly that
  diagnosis.
- On iOS, `fault (xcuitest)` needs no Swift change: the fault is a host-side signal to the process
  holding the runner's loopback port, found by port rather than by name so a rename cannot leave the
  suite signalling nothing. `SIGSTOP` produces the hung-connection failure mode a refused connection
  cannot. The freeze is held for the transport's own retry budget, which the channel now derives
  beside its constants (`_retry_budget_seconds`, pinned on the fast gate against the sleeps
  `_with_retry` really takes) so a re-tune of the retry loop re-tunes the fault rather than leaving
  a caller's copy of the arithmetic to drift. A background thread then releases it. Killing the
  runner and its `xcodebuild` host then drives BE-0319's process-exited branch, asserting an
  `XcuitestRunnerCrashError` that is also a `base.BackendCrashError` — the classification the run
  pipeline needs to lease a fresh device and re-run.
- The XCUITest freeze runs the transport at a 2 s per-attempt socket timeout instead of the shipped
  15 s, so the fault has to outlast ~7.5 s of retry budget rather than ~46.5 s. At the shipped value
  the freeze had to hold ~51.5 s, and a real XCTest host stopped that long did not reliably come
  back: two of three runs lost the runner outright, the frozen case failing "did not recover" and the
  killed case then finding nothing on the port. Shrinking the budget avoids racing whatever reclaims
  a long-stopped process, and changes nothing about the mechanism under test — a real `SIGSTOP` still
  hangs a real socket, the retry loop still exhausts on it for real, and recovery still re-issues.
  The override is a module attribute the request path reads per call, so it moves the actual socket
  timeout, not merely the arithmetic the freeze is sized from.
- Both jobs stay outside their lane's `E2E (…)` aggregator's `needs:`, so neither can block a merge
  while it proves itself (BE-0282's precedent). The synthetic-fixture suites are untouched: they
  remain the fast control-flow check, and this item adds the real-condition layer beneath them.
- The 2 s freeze (~7.5 s budget, ~12.5 s held) still lost the runner on a later CI run: `health`
  caught it alive for one poll — logged as recovered — and the immediately following re-issue found
  the port refused, not merely unreachable. That rules out the retry loop or the classifier: a process
  that answers `/health` and then refuses a connection within the same synchronous call has exited,
  not hung, and no amount of polling logic changes that outcome. The freeze itself is still the
  variable most under this suite's control, so it shrinks again: 0.5 s per attempt (~3 s budget), with
  the release margin now a named constant (`_RELEASE_MARGIN_S = 1.5`, replacing the unlabelled
  `+ 5.0`), holding the freeze for ~4.5 s total rather than ~12.5 s. Nothing about the mechanism
  changes — the shorter timeout still forces three genuine socket timeouts before the classifier
  fires — only the window a resumed process has to be reclaimed before this suite observes it.

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

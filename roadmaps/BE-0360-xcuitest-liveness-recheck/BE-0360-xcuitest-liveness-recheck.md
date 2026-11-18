**English** · [日本語](BE-0360-xcuitest-liveness-recheck-ja.md)

# BE-0360 — Re-ask the runner's liveness while waiting out a mid-run crash

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0360](BE-0360-xcuitest-liveness-recheck.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0360") |
| Implementing PR | [#1601](https://github.com/bajutsu-e2e/bajutsu/pull/1601) |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu drives an iOS Simulator through a resident test runner. The runner is an XCUITest process that `xcodebuild test-without-building` starts on the device. It serves the [driver](../../docs/glossary.md#driver-backend-actuator-platform)'s requests over a loopback HTTP port.

When the runner crashes in the middle of a scenario, the driver does not fail the run outright. It waits up to 60 seconds for the runner to answer on its port again. If the runner comes back, the driver re-issues the call.

Waiting is worth doing only while the runner can still come back. A runner whose process has exited, or whose XCTest run has already ended, will never bind that port again. So before it starts waiting, the driver asks the Simulator lifecycle for a liveness verdict. On a negative verdict, the driver fails immediately.

This item makes the driver **keep re-asking that same question while the wait runs**. Today it asks only once, before the wait begins. The liveness verdict changes during the 60-second window, and that is when a crashing runner's death becomes observable. Sampling the verdict once, at the earliest possible moment, keeps the fast-fail from firing on the failure it was built for.

## Motivation

The liveness verdict became trustworthy only recently. [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) widened it from "the `xcodebuild` process is still running" to a conjunction. The second condition reads the runner's captured output for the markers that XCTest writes when a test suite reports its result.

The widening closed a real blind spot. After a mid-run crash, XCTest restarts the in-Simulator test host and re-runs zero tests. The suite then reports a result while the `xcodebuild` parent lives on. The process handle alone reads that state as healthy.

The verdict is still consulted only once: immediately after the crash is declared, before the 60-second wait begins. Nothing re-reads it while the wait runs. The polling loop that runs the wait probes only the runner's `/health` endpoint, and it does not receive the liveness callback at all.

A runner whose death becomes observable *during* the wait is therefore waited out in full, exactly as before the verdict was widened. This is the ordinary case. The `xcodebuild` exit and the suite's result line are both consequences of the crash, so both follow it.

Continuous integration (CI) measurements show the fast-fail missing its target in practice. Across every `ios-e2e` failure recorded between 2026-08-09 and 2026-08-12, eight failing jobs reported `did not recover within 60s`. None reported a genuine assertion, build, or infrastructure failure.

One of those jobs ran on a commit that already carried the widened verdict. It spent the full window five times in a single scenario. Its own captured output named the state the verdict looks for: the runner had exited on its own with code 65 after executing zero tests.

The widened verdict does fire when a runner is already demonstrably gone at the moment the crash is declared. So the two shapes call for separate accounts. The mechanism is right, and the sampling is too early.

Each miss costs a full 60 seconds. Worse, it reports a diagnosis that misleads the reader. "The runner crashed mid-run and did not recover within 60s" describes a runner that was given a fair chance to return. A runner that stopped serving the moment it crashed was never coming back. The message that says so is already written and already correct, but it never reaches the reader.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1, and unit 3 depends on unit 2.

1. **Let the readiness wait read the liveness verdict.** The bounded wait polls `GET /health` until the runner answers `ready`. Today it takes a transport, a timeout, and a poll interval. Add an optional liveness callback to that signature.

   The parameter is optional, so the startup caller stays unchanged. A cold spawn's readiness wait already runs under a separate liveness check owned by the spawn retry. It passes nothing and behaves as it does now.

2. **Break the wait on a negative verdict.** Inside the polling loop, ask the callback. Stop waiting as soon as it reports the runner gone.

   Ask after the `/health` probe, not before. A runner that answers `ready` on the very poll where its capture first shows the marker is then still treated as recovered. A runner that is serving is serving, whatever its log says.

   Distinguish the two ways the wait can end: the deadline passed, or the runner was found gone. The two deserve different diagnostics. A caller that learned only "the wait failed" would have to re-ask the callback to find out which.

   Throttle the liveness question instead of asking it at the poll interval. The `/health` probe runs every 100 milliseconds, while the liveness check reads the runner's capture file from a private offset. Reading it 600 times across one window would cost 600 file reads. That catches the death at most 900 milliseconds sooner than reading it once a second, which does not matter against a 60-second window. Once a second bounds the wasted wait to about a second beyond the moment the runner's death becomes observable.

3. **Report which end the wait reached.** The crash-recovery layer already carries two diagnostics: one for a runner found gone before the wait, and one for a wait that reached its deadline. Route the newly distinguishable outcome to the first. A runner that dies during the window is then reported the way a runner that died just before it already is.

   Keep the existing check before the wait as an early exit. Do not delete it in favour of the in-loop check. It costs one call, and it catches a runner already gone when the crash is declared. Removing it would make that case pay a poll interval it does not need.

The fault-injection suite exercises this behavior on a real device. It freezes a live runner and asserts on how the recovery layer responds. A case that freezes the runner past the point where XCTest's own watchdog restarts the test host has the shape this item changes. A regression would surface there.

## Alternatives considered

- **Shorten the 60-second recovery window.** The window is not the defect. A runner that genuinely recovers needs the time. The failures measured here would fail at any window length, because the runner is already gone. A shorter window would cut the cost of the miss without ever letting the fast-fail fire. It would also erode the recoverable case that the window exists for.
- **Have the environment push a notification when the runner dies, instead of polling the verdict.** A callback fired from the process-reaping path would report the death at the instant it happens, with no poll interval to round up. Rejected as more machinery than the problem needs. The driver and the Simulator lifecycle communicate through callables passed at construction. A push channel between them would add state that must stay correct across a warm runner's reuse and a device replacement. Polling an existing callable once a second reaches the same outcome within a second.
- **Ask the liveness callback only after the first few polls.** A runner that crashes and returns quickly is the case the wait exists for, and skipping the early polls would spare it the check entirely. Rejected because the check is already cheap enough not to need the exemption. A threshold expressed in polls would also be one more number to justify and keep aligned with the poll interval.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — add an optional liveness callback to the readiness wait's signature.
- [x] Unit 2 — ask the callback once per second inside the polling loop and break on a negative
      verdict, distinguishing that outcome from a deadline that passed.
- [x] Unit 3 — route the new outcome to the "runner is gone" diagnostic, keeping the pre-wait check
      as an early exit.

## References

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — widened the liveness verdict to read the run-ended markers, the mechanism this item re-samples.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
  — introduced the fast-fail on a dead runner that this item extends into the wait.
- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — introduced the mid-run crash recovery and its 60-second window.

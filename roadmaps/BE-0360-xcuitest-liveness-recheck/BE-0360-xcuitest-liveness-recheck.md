**English** · [日本語](BE-0360-xcuitest-liveness-recheck-ja.md)

# BE-0360 — Re-ask the runner's liveness while waiting out a mid-run crash

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0360](BE-0360-xcuitest-liveness-recheck.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0360") |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu drives an iOS Simulator through a resident test runner: an XCUITest process that
`xcodebuild test-without-building` starts on the device, which then serves the driver's requests
over a loopback HTTP port. When that runner crashes in the middle of a scenario, the driver does not
fail the run outright. It waits up to 60 seconds for the runner to answer on its port again, and
re-issues the call if the runner comes back.

Waiting is worth doing only while the runner can still come back. A runner whose process has exited,
or whose XCTest run has already ended, will never bind that port again, so the driver asks the
Simulator lifecycle for a liveness verdict before it starts waiting and fails immediately on a
negative one. This item makes the driver **keep re-asking that same question while the wait runs**,
instead of asking it only once before the wait begins. The liveness verdict is a fact that changes
during the 60-second window — that is precisely when a crashing runner's death becomes observable —
and sampling it once, at the earliest possible moment, is what keeps the fast-fail from firing on the
failure it was built for.

## Motivation

The liveness verdict became trustworthy only recently.
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
widened it from "the `xcodebuild` process is still running" to a conjunction that also reads the
runner's captured output for the markers XCTest writes when a test suite reports its result. The
widening closed a real blind spot: after a mid-run crash, XCTest restarts the in-Simulator test host
and re-runs zero tests, so the suite reports a result while the `xcodebuild` parent lives on. The
process handle alone reads that state as healthy.

The verdict is nonetheless still consulted once, immediately after the crash is declared and before
the 60-second wait begins. Nothing re-reads it while the wait runs, because the polling loop that
runs the wait probes only the runner's `/health` endpoint and does not receive the liveness
callback at all. So a runner whose death becomes observable *during* the wait — the ordinary case,
since the `xcodebuild` exit and the suite's result line are both consequences of the crash and
therefore follow it — is waited out in full, exactly as it was before the verdict was widened.

Continuous integration measurements show the fast-fail missing its target in practice. Across every
`ios-e2e` failure recorded between 2026-08-09 and 2026-08-12, eight failing jobs reported
`did not recover within 60s`. None reported a genuine assertion, build, or infrastructure failure.
One of those jobs ran on a commit that already carried the widened verdict, and spent the full
window five times in a single scenario while its own captured output named the state the verdict
looks for: the runner had exited on its own with code 65 after executing zero tests. The widened
verdict does fire when a runner is already demonstrably gone when the crash is declared, which is
why the two shapes call for separate accounts rather than one: the mechanism is right and the
sampling is too early.

Each miss costs a full 60 seconds and, worse, reports a diagnosis that misdirects whoever reads it.
"The runner crashed mid-run and did not recover within 60s" describes a runner that was given a fair
chance to return. A runner that stopped serving the moment it crashed was never coming back, and the
message that says so is already written and already correct — it never reaches the reader.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1; unit 3 depends on unit 2.

1. **Let the readiness wait read the liveness verdict.** The bounded wait that polls `GET /health`
   until the runner answers `ready` takes a transport, a timeout, and a poll interval today. Add an
   optional liveness callback to that signature. Leaving the parameter optional keeps the startup caller
   unchanged: a cold spawn's readiness wait already runs under a separate liveness check owned by
   the spawn retry, so it passes nothing and behaves as it does now.

2. **Break the wait on a negative verdict.** Inside the polling loop, ask the callback and stop
   waiting as soon as it reports the runner gone. Ask it after the `/health` probe rather than
   before, so a runner that answers `ready` on the very poll where its capture first shows the
   marker is still treated as recovered: a runner that is serving is serving, whatever its log says.
   Distinguish the two ways the wait can end so the caller can tell them apart — the deadline
   passed, or the runner was found gone — because they deserve different diagnostics, and a caller
   that only learned "the wait failed" would have to re-ask the callback to find out which.

   Throttle the liveness question rather than asking it at the poll interval. The `/health` probe
   runs every 100 milliseconds, while the liveness check reads the runner's capture file from a
   private offset. Reading it 600 times across one window would cost 600 file reads to catch the
   death at most 900 milliseconds sooner than reading it once a second — a difference that does not
   matter against a 60-second window. Once a second bounds the wasted wait to about a second beyond
   the moment the runner's death becomes observable.

3. **Report which end the wait reached.** The crash-recovery layer already carries two diagnostics:
   one for a runner found gone before the wait, and one for a wait that reached its deadline. Route
   the newly distinguishable outcome to the first, so a runner that dies during the window is
   reported the way a runner that died just before it already is. Keep the existing check before the
   wait as an early exit rather than deleting it in favour of the in-loop one: it costs one call, it
   is what catches a runner already gone when the crash is declared, and removing it would make that
   case pay a poll interval it does not need.

The fault-injection suite is where this behavior is exercised on a real device: it freezes a live
runner and asserts on how the recovery layer responds. A case that freezes the runner past the point
where XCTest's own watchdog restarts the test host is the shape this item changes, so the suite is
where a regression would surface.

## Alternatives considered

- **Shorten the 60-second recovery window.** The window is not the defect. A runner that genuinely
  recovers needs the time, and the failures measured here would fail at any window length, because
  the runner is already gone — a shorter window would cut the fast-fail's cost without ever letting
  the fast-fail fire, and it would erode the recoverable case the window exists for.
- **Have the environment push a notification when the runner dies, instead of polling the verdict.**
  A callback fired from the process-reaping path would report the death at the instant it happens,
  with no poll interval to round up. Rejected as more machinery than the problem needs: the driver
  and the Simulator lifecycle communicate through callables passed at construction, and adding a
  push channel between them would mean new state that has to stay correct across a warm runner's
  reuse and a device replacement. Polling an existing callable once a second reaches the same
  outcome within a second.
- **Ask the liveness callback only after the first few polls.** A runner that crashes and returns
  quickly is the case the wait exists for, and skipping the early polls would spare it the check
  entirely. Rejected because the check is already cheap enough not to need the exemption, and a
  threshold expressed in polls would be one more number to justify and keep aligned with the poll
  interval.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — add an optional liveness callback to the readiness wait's signature.
- [ ] Unit 2 — ask the callback once per second inside the polling loop and break on a negative
      verdict, distinguishing that outcome from a deadline that passed.
- [ ] Unit 3 — route the new outcome to the "runner is gone" diagnostic, keeping the pre-wait check
      as an early exit.

## References

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — widened the liveness verdict to read the run-ended markers, the mechanism this item re-samples.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
  — introduced the fast-fail on a dead runner that this item extends into the wait.
- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — introduced the mid-run crash recovery and its 60-second window.

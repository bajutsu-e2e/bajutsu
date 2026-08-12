**English** · [日本語](BE-XXXX-runner-http-queue-qos-ja.md)

# BE-XXXX — Give the runner's HTTP server queues an explicit quality of service

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-runner-http-queue-qos.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's iOS backend talks to a small HTTP server that runs inside the Simulator, hosted by an
XCUITest process. That server dispatches its work onto two queues built with Apple's Dispatch
library: a serial queue that runs the socket accept loop, and a concurrent queue that handles each
accepted connection. Neither queue declares a quality of service (QoS) — the priority class Dispatch
uses to decide how the system schedules that work against everything else running.

This item declares one. A queue built without a QoS does not therefore run at a fixed low priority.
Dispatch propagates the submitting thread's QoS through `async` onto such a queue, capped at
`.userInitiated`. What the omission actually costs is a guarantee. The priority of the whole request
path is inherited from whichever context happened to start the accept loop, and it stays correct only
as long as that submission chain does. Declaring `.userInitiated` on both queues pins the priority of
the channel the driver's readiness and health probes travel, instead of leaving it to be re-derived —
and silently re-derived differently — by anyone who later moves where the server is started.

## Motivation

Today's priority is a consequence rather than a decision, and the chain that produces it is long
enough to be fragile. The server is started from an XCUITest test method on the main thread, which
runs at a high QoS. The accept loop is submitted with `async` onto the unspecified-QoS serial queue
and so inherits that QoS, capped at `.userInitiated`. Each connection is then submitted with `async`
from the accept loop onto the unspecified-QoS concurrent queue and inherits in turn. The effective
priority is therefore probably already `.userInitiated` — which is exactly the value this item
proposes to write down. Nothing in the code says so, so nothing preserves it. Start the server from a
background helper, hop the submission through another queue, or adopt a Swift concurrency context
with a different QoS: every handler's priority moves with no diagnostic and no test failure.

The path this governs is the one that decides whether the runner is alive. The driver's readiness
wait polls `GET /health` until the runner answers, and its mid-run crash recovery polls the same
endpoint against a deadline. Both conclude, when the deadline passes, that the runner has stopped
serving. A scheduling delay on that path is therefore not merely slow; on a contended host it is
indistinguishable from a dead runner. Bajutsu's iOS jobs run on GitHub-hosted macOS runners, whose
Simulator resource pressure under Xcode 26 is a documented and still-open upstream problem, so
contention is the expected condition rather than the exceptional one.

The connection path also blocks in two places, which is what makes its priority worth pinning rather
than merely tidy. A connection handler waits on a counting semaphore that bounds concurrent handlers
to eight ([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)),
and an XCUITest operation takes a mutual-exclusion lock before hopping to the main thread, holding it
until the main-thread work returns
([BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)).
A blocking wait whose holder runs below its waiter is the shape Apple's Thread Performance Checker
reports as a priority inversion, and both waits have that shape whenever the inherited priorities of
two handlers differ.

Whether such an inversion has actually fired here is not settled, and this item does not pretend
otherwise. A Thread Performance Checker report naming the accept loop was observed in a failing job's
captured output during an earlier investigation, but the run was not recorded and cannot now be
produced: searching the 12 most recent failing `ios-e2e` runs found no such warning, and every
failure between 2026-08-09 and 2026-08-12 reported a mid-run crash-recovery timeout instead. Deriving
the inversion from the code alone does not close the gap either — the only threads that wait on the
semaphore or the lock are other connection handlers, which inherit their QoS from the same accept
loop and so normally match. Re-establishing that baseline is the first step of the work below rather
than a premise of it.

What justifies the change without that baseline is narrower and still sufficient. An explicit QoS is
the correct declaration for work a caller is blocked on, it is what Apple's tooling recommends for a
queue on a latency-sensitive path, and it converts an inherited property into a stated one at a cost
of two lines with no behavioural surface beyond scheduling. This item claims no measured reduction in
flake rate, and it would be wrong to imply one.

## Detailed design

Two units. Unit 1 is a measurement that unit 2 does not depend on, and either may land first.

1. **Re-establish the Thread Performance Checker baseline.** Determine whether the runner still
   provokes a priority-inversion report, and if so, what the report's stacks name. The checker writes
   into the runner's captured output, which the fault-injection and visual jobs already upload as a
   job artifact, so the measurement needs no new plumbing: run those jobs and grep the capture. Record
   the finding in this item — including a negative one, which is the outcome the reasoning above
   expects. A report that does fire names the waiting and holding threads, and that is what would turn
   the inversion from a shape the code permits into a mechanism worth describing.

   Do not close such a report as already handled by unit 2, because unit 2 addresses only one of the
   two shapes it could take. Declaring one level on both queues removes a *divergence* — two handlers
   submitted from contexts at different QoS, which inheritance permits and a declared value forbids —
   but it leaves every handler equal to every other, so it cannot remove a differential between a
   handler and a waiter outside the handler pool. The accept loop's own listen-descriptor lock is that
   second shape: `stop()` takes it from the test method's thread, above whatever the accept loop
   inherits, and `.userInitiated` on the accept-loop queue still sits below a `.userInteractive`
   caller. Nothing in the code makes that hold long enough to matter today, which is why this item
   does not act on it; a report that named it would call for raising the holder to the waiter's class
   or removing the wait, neither of which unit 2 does.

2. **Declare `.userInitiated` on both queues.** Pass `qos: .userInitiated` when constructing the
   serial accept-loop queue and the concurrent connection queue in the runner's HTTP server. Every
   request the server handles exists because the driver is blocked on its reply, which is what
   user-initiated denotes. Add a comment recording that the level is declared to pin what QoS
   propagation supplies implicitly today, so a later reader neither reads the explicit value as
   decoration nor assumes it changed the priority the code runs at today.

   `.userInitiated` rather than `.userInteractive`, on two grounds. First, `.userInteractive` is the
   main thread's own class, reserved for work that must complete within a frame to keep a user
   interface responsive; socket reading and request parsing are not that, and labelling them so
   misdescribes the requirement. Second, up to eight handlers run concurrently, and their unblocked
   work — reading bytes off a socket and parsing them — would then contend at equal priority with the
   main-thread work those same handlers later wait for. `.userInitiated` sits directly below the
   main thread's class and above the default, which is the relationship this path wants.

   The change is confined to two constructor calls and touches no request handling, so the runner's
   behaviour is unchanged apart from scheduling. The Swift package builds as part of the iOS toolchain
   the end-to-end jobs already run, and those jobs' first step depends on the runner answering
   `/health` at all, so a mistake here fails loudly and immediately rather than subtly.

## Alternatives considered

- **Leave both queues undeclared and rely on QoS propagation.** The status quo, and defensible on
  today's code, since propagation from a main-thread start already yields `.userInitiated`. Rejected
  because propagation makes the priority a property of the call site rather than of the server: it
  holds until someone moves the start, and it fails silently when they do. Two lines buy a guarantee
  in place of a coincidence.
- **Remove the blocking waits instead of declaring a priority for the threads that hold them.** The
  deeper fix, since an inversion cannot happen where nothing blocks. Rejected as a much larger change
  against deliberate design. The semaphore bounds concurrent handlers so health polls cannot pile up
  during a long gesture, and the lock serialises XCUITest operations before they reach the main thread
  because the re-entrancy it prevents aborts the test host outright. Both have reasons this item does
  not reopen.
- **Declare `.userInteractive` to put the handlers at the top class.** Tempting on a starved host,
  where more priority looks strictly better. Rejected on the two grounds given in unit 2: the class
  misdescribes socket handling as frame-deadline work, and eight handlers competing at the main
  thread's own priority would contend with the main-thread work they depend on.
- **Wait for a reproducible inversion before changing anything.** The strictest reading of the
  evidence, and the reason unit 1 exists as its own unit. Rejected as the whole of the item, because
  the case for declaring a QoS does not rest on the inversion: an inherited priority on the
  liveness-deciding path is worth pinning even where it has yet to cost a run.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — grep the fault-injection and visual jobs' runner captures for a Thread Performance
      Checker report, and record the finding here, including a negative one.
- [ ] Unit 2 — pass `qos: .userInitiated` to both of the runner HTTP server's queue constructors, with
      a comment recording that the level pins what propagation supplies implicitly.

## References

- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — introduced the concurrent connection queue and the handler semaphore, two of the pieces whose
  scheduling this item declares.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
  — introduced the main-thread serialisation lock (`Router.actuationLock`), the second blocking wait
  on the connection path.
- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) — bundles the
  runner that carries this server, so the change ships through the same build the end-to-end jobs use.

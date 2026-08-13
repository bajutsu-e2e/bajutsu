**English** · [日本語](BE-XXXX-xcuitest-screenshot-capture-deadline-ja.md)

# BE-XXXX — Capture the iOS runner's screenshot from the screen and bound it with a runner-side deadline

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-screenshot-capture-deadline.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1624](https://github.com/bajutsu-e2e/bajutsu/pull/1624) |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's iOS backend drives an application through a resident XCUITest process — a test host that
serves HTTP on a loopback port inside an iOS Simulator — and asks that process for a screenshot
(`GET /screenshot`) to record the [evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage)
each [step](../../docs/glossary.md#scenario-authoring) leaves behind. That one endpoint is the
dominant cause of the iOS continuous integration (CI) lanes' `backend crashed mid-run` verdicts, and
at the moment such a verdict is reached the test host is usually still alive — parked inside a capture
that runs far longer than the client waits, answering `/health` all the while.

This item does not remove the underlying cause, which is host exhaustion and belongs to other work.
It removes two things layered on top of it. The runner captures from the screen rather than from the
application element, dropping an element-resolution round trip that has no bearing on the resulting
image and that degrades — silently, at `HTTP 200` — into a 1×1 pixel image when the app is unhealthy. The screenshot handler then
gains a deadline shorter than the client's socket timeout, so a capture that still runs long answers
with an explicit, attributable failure instead of the silence the channel is left to interpret today.
The first change addresses the capture we can see failing. The second does not free the main thread
— the abandoned capture still holds it — but it names the failure on the request that caused it, and
it answers while the client is still listening, which also removes the late write into a closed socket
that can kill the test host outright.

## Motivation

`GET /screenshot` accounts for essentially every runner-channel failure the iOS lanes record. In the
`run (xcuitest)` job of workflow run
[31683047665](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31683047665), **48 of 48** channel
failures were `GET /screenshot`; not one was an element-tree read (`GET /elements`) or an actuation.
Three sibling jobs in the same run logged 18, 21, and 51. The correlation holds across lanes as well
as within them: `run (xcuitest)` has not passed once in the last 40 workflow runs, while
`conformance (xcuitest)` passed 14 of 14 — and the conformance suite requests no screenshots at all.
Even the lanes that go green log the failure and merely recover in budget, at ten occurrences per
job.

The runner's own captured output names the failing call. A `visual (xcuitest)` job's uploaded runner
log reports the failure at the exact line that takes the screenshot:

```
XcuitestElementProvider.swift:219: error: Failed to get screenshot: Timed out while requesting screenshot.
XcuitestElementProvider.swift:219: error: Element Application '…' cannot request screenshot data because it does not exist
```

Line 219 is `app.screenshot().pngRepresentation`. The second message is the one that identifies the
mechanism, because it can only come from the application-element path: `app.screenshot()` is an
element-scoped capture, so XCUITest resolves the application element before it requests any image,
and that resolution can fail or hang on its own. The activity log confirms the runner was inside
exactly that resolution when the client gave up — `Find the Application` opens at `t = 29.02s`
(09:37:40.70), and the client's first timeout lands at 09:37:55.75, **15.05 seconds** later, which is
the client's own 15-second read window to the hundredth of a second.

None of that makes the XCUITest request path the *cause*. The measured cause lies underneath it:
BE-0361's diagnostics established that GitHub's 3-core, 7 GiB macOS runner, saturated by the 257
guest processes a booted Simulator brings up, drives Apple's `SimRenderServer` into trapping on its
`com.apple.display.captureservice` queue — the one queue that serves `simctl io screenshot`,
`recordVideo`, and the XCUITest screenshot alike, which is why all three symptoms appear together.
This item does not fix that, and nothing here should be read as claiming otherwise: host exhaustion
needs the guest population shrunk, which is its own work. What this item changes is how the runner
behaves while that shared queue is degrading — one capture form is measurably worse than the other,
and the handler's response to a stall is measurably worse than it needs to be.

The damage is disproportionate to a failed screenshot because two budgets are mismatched. XCUITest
gives its own screenshot request roughly 90 seconds, while the client's read window is 15 — so a slow
capture can never be absorbed, and every one of them becomes a failure the client must classify. The
handler holds the runner's serialization lock across that whole wait, which parks the channel, while
`/health` keeps answering `ready` because it deliberately takes no lock. BE-0354 reads that
combination as a wedged automation session, correctly by its own definition, and the scenario retry
then spends the run-level recovery budget of 900 seconds on a device that was never broken. One slow
screenshot therefore ends the entire run: later scenarios report
`an earlier scenario already exhausted the run-level crash-recovery budget`, and never lease a device
at all.

## Detailed design

The work splits into two independent units. The first changes what the capture asks for, the second
changes how long the handler will wait for it.

### Unit 1 — Capture from the screen instead of from the application element

`XcuitestElementProvider.screenshot()` returns `app.screenshot().pngRepresentation`. Replace the
element-scoped capture with the screen-scoped `XCUIScreen.main.screenshot().pngRepresentation`, which
reaches the display directly and never resolves an element.

The substitution is pixel-neutral on the devices Bajutsu's iOS lanes pin, because the application
window already fills the screen and the element crop is therefore an identity operation. Two
independent measurements agree: the committed iOS baseline image is 1206×2622, and every screenshot
`app.screenshot()` produces against a locally booted iPhone 17 is 1206×2622 as well — the device's
full native resolution at 402×874 points and a scale factor of 3. Verification must confirm rather
than assume this, by capturing the same screens before and after the change and comparing the images,
because a drift here would silently invalidate every committed baseline and the element-scoped crops
that [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md)
computes in screenshot pixels.

The element-scoped capture also fails *silently*, which is the sharper reason to drop it. Measured on
the `visual (xcuitest)` artifact of run 31670480559: when the app under test degrades, the runner
answered `HTTP 200` carrying a **1×1 pixel** PNG, 4232 bytes, which the run stored as ordinary step
evidence. The Python client guards only against a non-PNG or empty body, so a valid 1×1 image passes
every check. Ten recorded XCTest failures sat in the same runner log, all of them screenshot requests
(`cannot request screenshot data because it has an empty frame`), and none of them became a reply the
client could see — `caughtOnMain` catches a *raised* `NSException`, not a *recorded* `XCTIssue`. So
evidence disappears at exactly the moment it is wanted, because the app degrading is the thing under
investigation. The screen-scoped capture has no such degenerate case: it captures the display whatever
state the app is in, including a system alert or SpringBoard, which the app-scoped form cannot see at
all.

The element-tree read is deliberately left alone. `queryElements()` calls `app.snapshot()`, an
accessibility-tree query that performs no element resolution, which is why the tree reads stay
healthy in precisely the jobs whose screenshots hang. That asymmetry is the evidence for this unit,
so preserving it matters.

### Unit 2 — Bound the screenshot handler with a deadline below the client's

The screenshot handler waits for the main thread indefinitely. Give it a deadline strictly shorter
than the client's 15-second read window, so the runner answers first and says why.

Only `GET /screenshot` may take a deadline, and the reason is worth stating precisely, because
applying one to an actuation would be a correctness bug rather than a conservative choice. A
screenshot is a pure read, so a reply that reports failure cannot be contradicted by a side effect
that lands afterwards. An actuation can: a handler that reported failure at 12 seconds while the tap
it synthesized landed at 40 would tell the client the screen was untouched when it had in fact
changed. Every actuation therefore keeps its unbounded wait.

The serialization invariant survives unchanged, and preserving it is the delicate part of this unit.
The runner serializes every XCUITest-touching operation because two of them running concurrently on
the main thread abort the test host — one call's internal run-loop spin drains the main queue and
re-enters the other, and XCUITest is not re-entrant. So the deadline may release the *client*, but it
must not release the *lock*: the handler dispatches the capture to the main thread, waits on a
semaphore until the deadline, and replies without waiting further, while ownership of the lock passes
to the dispatched block, which releases it only once the capture actually returns. At most one
XCUITest operation is enqueued on the main queue at any moment, exactly as before. A consequence
follows that the implementation must respect: because the lock is acquired and released on different
threads, the primitive becomes a counting semaphore rather than an `NSLock`, whose contract requires
the locking thread to unlock it.

The late completion must also write nothing. The waiting handler has already sent the only reply that
connection will receive, and a second write into a socket the client has closed raises `SIGPIPE`,
whose default disposition terminates the whole test host.

The deadline's value is coupled to the client's read window, and the coupling is enforced by comment
on both sides rather than mechanically — the two constants live in different languages, and no check
spans them. Twelve seconds against the client's fifteen leaves the reply time to reach the client
while keeping the runner's answer first.

### Machine-checkable outcome

Two of unit 2's three properties are covered by the fast Swift suite, because the routing layer and
the provider protocol both live in the SwiftPM target that `swift test --package-path BajutsuKit`
builds. Against a provider whose capture runs past the deadline, `GET /screenshot` must reply *before*
that capture returns — removing the deadline fails this — and the abandoned capture must hand the lock
on when it finally returns, so the next operation still runs; dropping the hand-off leaks the lock and
deadlocks the suite. Both were confirmed by mutation, not merely observed to pass.

The third property — that no second operation re-enters XCUITest while the capture holds the main
thread — is deliberately left unasserted, because it cannot be observed in this harness. A fake
capture that spins the run loop does not drain the main dispatch queue, so the capture's hold on the
main thread serializes everything with or without the lock, and a test claiming the invariant would
pass even against an implementation that released the lock early. Only the real XCUITest run loop
drains that queue. Writing the assertion anyway would buy false confidence, so that property rests on
the on-device lanes and on the argument recorded beside the code.

Unit 1 carries no unit test either, for a plainer reason: `XcuitestElementProvider` lives in the
xcodebuild-only test target, which neither `make check` nor `swift test` compiles. Its net is the
on-device lanes plus the before-and-after image comparison the unit describes above.

## Alternatives considered

**Capture out of band through `simctl`.** `xcrun simctl io <udid> screenshot` reaches the display
without entering the XCUITest process at all, and the command already exists in the codebase
(`bajutsu/simctl.py`). Taking evidence off the actuation channel entirely is the strongest structural
answer, and a slow capture could then never park the channel or produce a false crash verdict. It is
not this item, because it moves a capability the driver protocol locates in the backend out
to a Simulator-only side channel, which needs its own design: the XCUITest backend also serves
physical devices through a different lease path, where no `simctl` exists. Worth its own item.

**Raise the client's read window above XCUITest's ~90 seconds.** This would stop the client
misreading a slow capture as a wedge, and it is the wrong direction: a step would then be able to
stall for a minute and a half before anything reported it, and the run-level budgets that bound a
failing job would lose their meaning. Bounding the capture is the point; widening the window abandons
it.

**Release the serialization lock at the deadline.** Replying and releasing the lock together would
free the channel sooner, and it would reintroduce the test-host abort the lock exists to prevent — a
second operation would enqueue onto the main queue while the first capture is still pumping the run
loop. Ruled out for that reason; the lock's release stays tied to the capture's completion.

**Detect and escalate instead of preventing.** BE-0354 already took this path, classifying the wedge
in seconds and escalating a repeated retry to a replacement device — and BE-0361's measurements
vindicate that escalation, because a trap that takes `backboardd` with it leaves no guest to respawn
into, so replacing the device is the only rung that can work. This item is not an alternative to that
layer and does not reduce its value; it removes a capture form that fails silently and a wait that
reports nothing, both of which sit above the exhaustion BE-0361 identifies.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — capture from the screen (`XCUIScreen.main.screenshot()`), verified pixel-neutral by a
  before-and-after image comparison on a booted iPhone 17.
- [x] Unit 2 — bound the screenshot handler with a deadline below the client's read window, keeping
  the serialization invariant and writing exactly one reply per connection.

## References

- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
  — the provider whose `screenshot()` unit 1 changes.
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)
  — the handler and serialization lock unit 2 changes.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the client whose 15-second read
  window the runner's deadline must stay below.
- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — the detection-and-escalation layer this item complements rather than replaces.
- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
  — the diagnostics whose measurements identify the host exhaustion underneath these stalls, which
  this item deliberately does not address.
- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — the item that made the runner answerable during a long operation, and the origin of the
  serialization invariant unit 2 must preserve.
- [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)
  — the single-`app.snapshot()` element read whose health, next to the failing screenshot, is this
  item's evidence.

**English** · [日本語](BE-0363-simctl-subprocess-timeout-ja.md)

# BE-0363 — Bound every simctl call with a timeout so a wedged CoreSimulator fails with a named cause

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0363](BE-0363-simctl-subprocess-timeout.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0363") |
| Implementing PR | [#1602](https://github.com/bajutsu-e2e/bajutsu/pull/1602) |
| Topic | Platform support |
| Related | [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

Every operation Bajutsu performs on an iOS Simulator goes through `simctl`, the command-line tool Apple ships for controlling Simulator devices. Booting a device, erasing it, and installing an app all run as subprocesses that Bajutsu waits for. It waits without a deadline.

One helper of about three lines runs every one-shot `simctl` command in the Simulator lifecycle. The helper passes no `timeout` to `subprocess.run`.

Two kinds of call sit outside that helper, and both stay outside this item:

- The clipboard write already carries its own deadline.
- The evidence captures stream video and device logs through a long-running process that is meant to outlive the call which started it.

This item gives that helper a deadline. A `simctl` call that never returns is the observable symptom of a wedged CoreSimulator, the macOS service that owns Simulator devices. Today such a call hangs until the continuous integration (CI) job's own `timeout-minutes` cancels the whole job.

A cancelled job names no cause. No scenario reported a verdict and no error was raised, so the reader learns only that sixty minutes elapsed. A bounded call turns that into a `simctl` command that exceeded its deadline, which is a diagnosis.

## Motivation

The gap is already recorded as a known follow-up rather than a discovery. [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) made a crash-triggered retry force the `erase` precondition. That sends the retry through `shutdown`, `erase`, and `boot`.

BE-0353's detailed design names the consequence and defers it. Those calls carry no timeout, so the sequence "can still hang rather than raise". Bounding it needs a deliberately chosen timeout value and failure mode, shared with the other `simctl` calls. A local fix to one retry loop would not do. This item is that follow-up.

Deferring was right, and it also enlarged the exposure. Before BE-0353, the `shutdown` → `erase` → `boot` sequence ran only for a scenario that asked for it. Now every crash-triggered retry runs it. The three longest-running unbounded calls therefore sit on the path taken right after the failure that a retry exists to absorb.

The recovery ladder from [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) has the same property and says so. Its recovery budget is checked only *between* rungs, because each rung's `simctl` steps are blocking calls with no subprocess-level timeout of their own. A genuinely wedged CoreSimulator is caught only by the run's outer timeout.

One `simctl` call is already bounded, which settles whether bounding one is acceptable. Clipboard writes go through a helper with a 30-second deadline and a bounded retry. It was added after `simctl pbcopy` was measured returning exit code 60, its own `ETIMEDOUT`, on a loaded host ([PR #1363](https://github.com/bajutsu-e2e/bajutsu/pull/1363), which carries no roadmap item of its own). That precedent covers one command out of the thirty-odd the module builds. Generalising it is what remains.

A single deadline for every command will not do. That is why this item needs a design rather than a one-line change:

- `simctl bootstatus` blocks until a device finishes booting. That took 39.94 seconds on this project's own hardware, and the iOS end-to-end workflow estimates roughly 80 seconds on a CI runner.
- `simctl list` returns in well under a second.

A deadline that lets `bootstatus` finish would let `list` hang for a minute and a half before anyone noticed. A deadline tight enough for `list` would kill every boot.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1, and unit 3 depends on unit 2.

1. **Give the runner helper a deadline, and classify the commands by how long they may legitimately take.** Pass a `timeout` to `subprocess.run` in the one helper that every `simctl` call goes through. Define two values rather than one:

   - A short default, for the commands that query or mutate state without waiting on the device.
   - A long bound, for the commands that legitimately block on the device: `bootstatus` above all, with `erase` and `boot` beside it.

   Choose which bound applies from the command itself, not from a caller-supplied argument. A new call site then inherits the right bound without its author having to know one exists.

   Size the long bound against the workflow's own published estimate rather than the local measurement. CI is the slower environment, and the one where the hang matters. Leave generous headroom above the estimate, because the bound exists to catch a call that will never return, not to police a slow one. Size the short bound well above the observed cost of the commands it covers, for the same reason.

   Put both values, and the reasoning for each, in a comment beside them. A bare number invites a later reader to tighten it.

2. **Raise a timeout as a device fault, not as a new exception the callers have never seen.** A `subprocess.TimeoutExpired` is not a `subprocess.CalledProcessError`, and the distinction matters more than it looks:

   - The module suppresses `CalledProcessError` at four call sites where a failure is the ordinary case. Examples are shutting down a device that is already off and uninstalling an app that was never installed.
   - The Simulator lifecycle converts `CalledProcessError` into the module's own device-fault type at five more call sites.

   A raw `TimeoutExpired` would escape every one of those sites. A hang would then stop being a job-level cancellation only to become an unhandled exception in a different place. Convert a timeout into the same device-fault type inside the helper, carrying the command and the deadline it exceeded. The handlers that convert a failure keep working, and the message names what happened.

   Two groups of caller need a decision rather than a translation, and they want opposite answers.

   The four suppressed calls deliberately ignore failure. A timeout on shutdown, boot, uninstall, or terminate is not the ordinary "already in that state" outcome those sites exist to absorb. It is the wedge this item exists to surface. Let a timeout propagate from them rather than widening the suppression to cover it. A hung `shutdown` is exactly the signal the recovery ladder needs, and today's silence hides it.

   For `terminate`, propagating out of the `simctl` module is not enough. This item settles that here rather than at implementation time, because it changes teardown behaviour. Two callers terminate an app on the runner-discard path: the app under test and the XCTRunner app. Both already suppress the module's device-fault type alongside `CalledProcessError`, so that an app that is not running does not fail a teardown. Converting a timed-out `terminate` into that same type would leave the discard path as silent as today. Narrow those two suppressions to the failure they were written for. A timeout then surfaces, and "not running" stays absorbed.

   The module's own best-effort probes want the opposite. About ten readers catch a failure and return a documented fallback instead: an empty list, or a third value meaning "could not tell". They cover these operations:

   - resolving the `booted` alias and listing booted devices
   - asking whether a device is booted, available, or of a known type
   - reading the device catalogue, checking an install, and comparing the system locale

   Those three-valued contracts exist to model the state a timeout indicates. The recovery ladder of [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) depends on the distinction. A probe that returns "unknown" leaves the device alone, while a probe that raises would turn a diagnostic read into a run-visible fault.

   Fold a timeout into each probe's existing fallback rather than raising out of it, and log it at the probe. The ladder keeps choosing on what it observed, and the timeout still leaves a trace. A device fault is then raised only from the calls that were going to raise on a failure anyway.

3. **Keep the test fakes working.** The helper's type is a two-argument callable. The test suite substitutes it throughout, to run the Simulator lifecycle with no device present. Adding a parameter to that type would touch every fake. Resolve the bound inside the real helper instead, and leave the callable's shape unchanged. The fakes then need no edit, and tests that drive the real helper against a command that sleeps exercise the timeout.

Verification is unusually direct for a change of this kind. A fake command that sleeps past its bound asserts the device-fault translation. A fake that returns immediately asserts that the healthy path is untouched. Neither needs a Simulator, so both run in the deterministic gate rather than only on macOS.

## Alternatives considered

- **Bound only the calls that the recovery paths make.** This is narrower, and enough for the two paths that motivated the item. Rejected because it would leave the same hang reachable from the ordinary cold bring-up. A rule expressed as "these call sites are bounded" is also one that a new call site silently escapes. Bounding the shared helper makes the property hold by construction.
- **Add the timeout as an argument on the runner callable.** This is the obvious shape, and it would let each call site state its own bound. Rejected on the cost it imposes for the benefit. It changes a type that the test suite substitutes throughout. Per-call bounds are also exactly the knowledge a new call site's author would have to acquire before writing a correct call. A classification that the helper applies needs no such knowledge.
- **Retry a timed-out call, as the clipboard helper retries a timed-out `pbcopy`.** This is symmetrical with the existing precedent. Rejected because the two failures differ in kind. `pbcopy`'s exit code 60 is a transient pasteboard timeout that the same device serves correctly moments later. A `simctl` call that never returns indicates that the daemon serving it has stopped serving, so a retry would hang the same way. Recovery from a wedged daemon is a device-level decision, which the recovery ladder already owns. This item's job is to let that ladder hear about the wedge.
- **Kill and restart the CoreSimulator daemon when a call times out.** This is the remedy a timeout invites, and it is cheap to perform. Rejected as out of scope here, and not yet justified on the evidence. No failure among those reviewed for this item showed the daemon-level signature that the restart would target. A bounded call is the prerequisite for ever making that decision from data, because today the wedge is not detected at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — pass a `timeout` in the shared `simctl` runner helper, with a short default and a long
      bound for the commands that legitimately block on the device, chosen from the command itself.
      Shipped with `install` in the long class beside `bootstatus` / `boot` / `erase`, which the
      design above names but does not enumerate exhaustively: `simctl install` transfers the whole
      app bundle, so its duration is set by the app under test rather than by simctl, and a short
      bound would leave an app-agnostic tool carrying an app-size-dependent ceiling. The short bound
      also lands on `pbpaste`, the read half of the one command family this module already records as
      stalling transiently, and the two halves stay bounded differently on purpose: the write keeps
      its own deadline and retry outside the helper, because re-feeding the same stdin is safe, while
      the read raises, because its result is the scenario's own data and retrying it is the
      device-level decision this item's *Alternatives considered* defers to the recovery ladder. A
      read that raises at a named deadline already improves on the unbounded hang it replaced.
- [x] Unit 2 — translate a timeout into the module's device-fault type inside the helper; let a
      timeout propagate from the four deliberately suppressed calls, narrow the two discard-path
      `terminate` suppressions so it is not re-absorbed there, and fold it into the existing fallback
      of each best-effort probe. Shipped with four callers that catch the now-propagating timeout
      instead of letting it out: the `serve` job's parallel boot, where an uncaught one dies on its
      worker thread and leaves the job reporting a boot it never got; `guarded_teardown`, where a
      `mid_run=False` re-raise surfaces inside `run`'s `finally: shutdown()` and would replace a
      finished run's verdicts with an exception; and the two calls the crash retry's replacement rung
      makes on the device it is abandoning — the runner discard and the quarantine `shutdown` — both
      of which run past the recovery ladder's decision point with a replacement already confirmed
      creatable, and the first of which cannot be retried, since the escalation request is consumed
      before it. Each logs the wedge, so the diagnosis this item exists to produce survives. The
      design above states where a timeout is raised from, not that every caller must let it reach the
      top — a timeout that destroys what the run already produced, or that spends the one remedy left
      for the device that caused it, diagnoses no better than the hang it replaced.
- [x] Unit 3 — resolve the bound inside the real helper so the substituted test callable keeps its
      two-argument shape, and cover both the timeout and the healthy path in the deterministic gate.

## References

- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — names this gap as an explicit follow-up, and forces the erase sequence that enlarged it.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the recovery
  ladder whose budget can only be checked between rungs because the rungs' own calls are unbounded.
- [PR #1363](https://github.com/bajutsu-e2e/bajutsu/pull/1363) — bounded the one `simctl` call that
  carries a deadline today, the precedent this item generalises.

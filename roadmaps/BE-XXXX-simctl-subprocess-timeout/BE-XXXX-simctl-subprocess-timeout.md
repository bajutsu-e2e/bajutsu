**English** · [日本語](BE-XXXX-simctl-subprocess-timeout-ja.md)

# BE-XXXX — Bound every simctl call with a timeout so a wedged CoreSimulator fails with a named cause

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-simctl-subprocess-timeout.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

Every operation Bajutsu performs on an iOS Simulator goes through `simctl`, the command-line tool
Apple ships for controlling Simulator devices. Booting a device, erasing it, and installing an app all
run as subprocesses that Bajutsu waits for. It waits without a deadline. One helper of about three
lines runs every one-shot `simctl` command in the Simulator lifecycle, and it passes no `timeout` to
`subprocess.run`.

Two kinds of call sit outside that helper, and both stay outside this item. The clipboard write
already carries its own deadline. The evidence captures stream video and device logs through a
long-running process that is meant to outlive the call which started it.

This item gives that helper a deadline. A `simctl` call that never returns — the observable symptom
of a wedged CoreSimulator, the macOS service that owns Simulator devices — hangs today until the
continuous integration (CI) job's own `timeout-minutes` cancels the whole job. A cancelled job names
no cause: no scenario reported a verdict, no error was raised, and the person reading it learns only
that sixty minutes elapsed. A bounded call turns that into a `simctl` command that exceeded its
deadline, which is a diagnosis.

## Motivation

The gap is already recorded as a known follow-up rather than a discovery.
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
made a crash-triggered retry force the `erase` precondition, which sends the retry through
`shutdown`, `erase`, and `boot`. Its own detailed design names the consequence and defers it: those
calls carry no timeout, so the sequence "can still hang rather than raise", left as a follow-up
because bounding it needs a deliberately chosen timeout value and failure mode shared with the other
`simctl` calls rather than a local fix to one retry loop. This item is that follow-up.

Deferring it was right, and it also enlarged the exposure. Before
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md),
the `shutdown` → `erase` → `boot` sequence ran only for a scenario that asked for it. Now every
crash-triggered retry runs it, which puts the three longest-running unbounded calls on the path taken
immediately after the failure a retry exists to absorb. The recovery ladder from
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) has the same
property and says so: its recovery budget is checked only *between* rungs, because each rung's
`simctl` steps are blocking calls with no subprocess-level timeout of their own, so a genuinely
wedged CoreSimulator is caught only by the run's outer timeout.

One `simctl` call is already bounded, which settles the question of whether bounding one is
acceptable. Clipboard writes go through a helper with a 30-second deadline and a bounded retry, added
after `simctl pbcopy` was measured returning exit code 60 — its own `ETIMEDOUT` — on a loaded host
([PR #1363](https://github.com/bajutsu-e2e/bajutsu/pull/1363), which carries no roadmap item of its
own). That precedent covers one command out of the thirty-odd the module builds. Generalising it is
what remains.

A single deadline for every command will not do, and that is the reason this item needs a design
rather than a one-line change. `simctl bootstatus` blocks until a device finishes booting, which took
39.94 seconds on this project's own hardware and which the iOS end-to-end workflow estimates at
roughly 80 seconds on a CI runner. `simctl list` returns in well under a second. A deadline that lets
`bootstatus` finish would let `list` hang for a minute and a half before anyone noticed, and a
deadline tight enough for `list` would kill every boot.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1; unit 3 depends on unit 2.

1. **Give the runner helper a deadline, and classify the commands by how long they may legitimately
   take.** Pass a `timeout` to `subprocess.run` in the one helper every `simctl` call goes through.
   Rather than one value, define two: a short default for the commands that query or mutate state
   without waiting on the device, and a long one for the commands that legitimately block on the
   device — `bootstatus` above all, with `erase` and `boot` beside it. Choose which applies from the
   command itself rather than from a caller-supplied argument, so a new call site inherits the right
   bound without its author having to know one exists.

   Size the long bound against the workflow's own published estimate rather than the local
   measurement, since CI is the slower environment and the one where the hang matters, and leave
   generous headroom above it: the bound exists to catch a call that will never return, not to police
   a slow one. Size the short bound well above the observed cost of the commands it covers for the
   same reason. Both values, and the reasoning for each, belong in a comment beside them — a bare
   number invites a later reader to tighten it.

2. **Raise a timeout as a device fault, not as a new exception the callers have never seen.** A
   `subprocess.TimeoutExpired` is not a `subprocess.CalledProcessError`, and the distinction matters
   here more than it looks: the module suppresses `CalledProcessError` at four call sites where a
   failure is the ordinary case (shutting down a device that is already off, uninstalling an app that
   was never installed), and callers across the Simulator lifecycle catch it in eight more places to
   convert a `simctl` failure into the module's own device-fault type. A raw `TimeoutExpired` would
   escape every one of those, so a hang would stop being a job-level cancellation only to become an
   unhandled exception in a different place. Convert a timeout into the same device-fault type inside
   the helper, carrying the command and the deadline it exceeded, so the handlers that convert a
   failure keep working and the message names what happened.

   Two groups of caller need a decision rather than a translation, and they want opposite answers.
   The four suppressed calls deliberately ignore failure. A timeout on shutdown, boot, uninstall, or
   terminate is not the ordinary "already in that state" outcome those sites exist to absorb; it is
   the wedge this item exists to surface. Let a timeout propagate from them rather than widening the
   suppression to cover it, since a hung `shutdown` is precisely the signal the recovery ladder above
   needs and the thing today's silence hides.

   The module's own best-effort probes want the opposite. About ten readers catch a failure and return
   a documented fallback instead — an empty list, or a third value meaning "could not tell". They
   resolve the `booted` alias and list booted devices. They ask whether a device is booted or available
   or of a known type. They read the device catalogue, check an install, and compare the system
   locale.
   Those three-valued contracts exist to model exactly the state a timeout indicates, and
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)'s recovery ladder
   depends on the distinction — a probe that returns "unknown" leaves the device alone, where one that
   raises would turn a diagnostic read into a run-visible fault. Fold a timeout into each probe's
   existing fallback rather than raising out of it, and log it at the probe, so the ladder keeps
   choosing on what it observed while the timeout still leaves a trace. A device fault is then raised
   only from the calls that were going to raise on a failure anyway.

3. **Keep the test fakes working.** The helper's type is a two-argument callable, and the test suite
   substitutes it throughout to run the Simulator lifecycle with no device present. Adding a
   parameter to that type would touch every fake. Resolve the bound inside the real helper instead,
   leaving the callable's shape unchanged, so the fakes need no edit and the timeout is exercised by
   tests that drive the real helper against a command that sleeps.

Verification is unusually direct for a change of this kind: a fake command that sleeps past its bound
asserts the device-fault translation, and a fake that returns immediately asserts the healthy path is
untouched. Neither needs a Simulator, so both run in the deterministic gate rather than only on macOS.

## Alternatives considered

- **Bound only the calls that the recovery paths make.** Narrower, and enough for the two paths that
  motivated the item. Rejected because it would leave the same hang reachable from the ordinary cold
  bring-up, and because a rule expressed as "these call sites are bounded" is one a new call site
  silently escapes. Bounding the shared helper makes the property hold by construction.
- **Add the timeout as an argument on the runner callable.** The obvious shape, and it would let each
  call site state its own bound. Rejected on the cost it imposes for the benefit: it changes a type
  the test suite substitutes throughout, and per-call bounds are exactly the knowledge a new call
  site's author would have to acquire before writing a correct call. A classification the helper
  applies needs no such knowledge.
- **Retry a timed-out call, as the clipboard helper retries a timed-out `pbcopy`.** Symmetrical with
  the existing precedent. Rejected because the two failures differ in kind: `pbcopy`'s exit code 60
  is a transient pasteboard timeout that the same device serves correctly moments later. A `simctl`
  call that never returns instead indicates the daemon serving it has stopped serving, so a retry
  would hang the same way. Recovery from a wedged daemon is a device-level decision, which the
  recovery ladder already owns; this item's job is to let that ladder hear about the wedge.
- **Kill and restart the CoreSimulator daemon when a call times out.** The remedy a timeout invites,
  and cheap to perform. Rejected as out of scope here, and not yet justified on the evidence: no
  failure among those reviewed for this item showed the daemon-level signature the restart would
  target. A bounded call is the prerequisite for ever making that decision from data, because today
  the wedge is not detected at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — pass a `timeout` in the shared `simctl` runner helper, with a short default and a long
      bound for the commands that legitimately block on the device, chosen from the command itself.
- [ ] Unit 2 — translate a timeout into the module's device-fault type inside the helper; let a
      timeout propagate from the four deliberately suppressed calls, and fold it into the existing
      fallback of each best-effort probe.
- [ ] Unit 3 — resolve the bound inside the real helper so the substituted test callable keeps its
      two-argument shape, and cover both the timeout and the healthy path in the deterministic gate.

## References

- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — names this gap as an explicit follow-up, and forces the erase sequence that enlarged it.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the recovery
  ladder whose budget can only be checked between rungs because the rungs' own calls are unbounded.
- [PR #1363](https://github.com/bajutsu-e2e/bajutsu/pull/1363) — bounded the one `simctl` call that
  carries a deadline today, the precedent this item generalises.

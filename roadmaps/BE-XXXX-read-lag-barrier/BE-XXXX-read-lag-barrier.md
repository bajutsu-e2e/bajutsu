**English** · [日本語](BE-XXXX-read-lag-barrier-ja.md)

# BE-XXXX — Apply a read-lag barrier to every read path that mistakes a stable tree for a current one

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-read-lag-barrier.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

Four places in Bajutsu decide that the screen has settled. Each one watches the accessibility tree
stop changing. All four are wrong on Android, for the same reason. A read taken shortly after an
action can still describe the screen from before that action. Two such reads then agree with each
other. The stability test passes on a tree that is merely late.

This item replaces "the tree stopped changing" with "the tree postdates the action," at each of
those four places. The device-side barrier gets the real fix. The resident Android reader publishes
a mark. The mark identifies the last accessibility update it has seen. A read is trusted once that
mark postdates the actuation, and not before. The two host-side read paths cannot see such a mark.
They keep a bounded budget as their fallback instead.

One of the four is already fixed in isolation. Pull request
[#1391](https://github.com/bajutsu-e2e/bajutsu/pull/1391) added the `scroll` action. Pull request
[#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) stopped it from reporting a late tree as
the end of the content. The narrow protocol that fix introduced is the seam this item builds on.

## Motivation

The symptom that opened this item is an `extract` step copying out a value from before its own tap.
The showcase scenario [`extract.yaml`](../../demos/showcase/scenarios/extract.yaml) taps a counter
three times. It captures the counter's tree value on the third tap. The following
[step](../../docs/glossary.md#scenario-authoring) asserts on the captured value. On the Android lane
the `smoke (adb)` job fails it about one run in three, with `step 4 (assert_): expected equals='2'
but actual='3'`. The arithmetic names the defect precisely. The counter reads 1, 2, and 3 across the
three taps. `extract` captured `2`, the value from before the tap it ran on. The assertion that
followed saw the true `3`. The assertion is correct, and the scenario is correct. The captured value
is one action stale.

Two reads on the same tree, taken a beat apart, disagree about which one to trust. The difference is
which question each poll asks. `_poll_asserts` (`bajutsu/orchestrator/loop.py:94`) polls until the
assertion passes. A late tree costs it only another read, and it rides over the lag without
noticing. `_settle_extract_read` (`bajutsu/orchestrator/loop.py:147`) has no assertion to satisfy.
`extract` copies a value out; it does not check one. So this poll instead runs until two consecutive
reads agree on the projection the extract reads. On Android the accessibility update naming the new
value is published after the tap has already taken effect. Two reads taken inside that window are
both stale. They agree with each other, and they pass the stability test. Nothing about that outcome
looks like a failure from inside the poll. The poll got exactly the agreement it asked for.

The same mistake sits in three further places. That is what makes a local patch the wrong shape.
`AdbDriver._settle` (`bajutsu/drivers/adb.py:325`) returns as soon as a fresh read's
identifier-and-frame projection equals the previous read's. It can then hand an actuator the
coordinates of a pre-gesture layout. That is the leading explanation for `smoke (adb)` intermittently
failing the `gestures` scenario with `expected equals='pressed' but actual='idle'`. The long press
aims at where an element used to be. `scroll_to_target`
(`bajutsu/orchestrator/actions/handlers/scroll.py:128`) inferred the end of the content from one
unchanged region. That turned a late tree into a hard `ElementNotFound`, rather than a retry.
`stableHierarchy`
(`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt:102`)
is the device-side reader the other three all read through. It re-dumps the hierarchy until two
dumps are byte-identical. Its own docstring names the barrier it installs: "the tree stopped
changing." That barrier is the deepest of the four, and it explains why the other three see late
trees at all.

Measurement, not inference, identified the `scroll` instance. Instrumenting every `scroll` step
whose region looked unchanged with a framebuffer digest, before and after, showed that **14 of 14**
such steps had moved the screen's pixels. The list scrolled every time; only the tree said
otherwise. A re-read two seconds later saw the change in 12 of those 14. The same investigation ruled
out the innocent explanations, with evidence. Animation scale is not the cause: disabling animations
reproduces nothing locally. Nor is a slow read: six reads over seven seconds all agreed with each
other. Nor is the gesture's interpolation shape: a chained motion-event pan failed at an identical
rate. The lag is real, specific to Android, and not a slow read. It is a read of an older screen.

Pull request [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) fixed the `scroll` instance
alone, and left the seam this item needs. It added `base.ReadLagProvider`, a narrow opt-in protocol.
Its single `read_lag()` method returns the number of seconds a backend admits its reads may trail an
action it has already applied. `AdbDriver` returns a non-zero budget. Every other backend implements
nothing, reports no lag, and keeps its behavior unchanged. `scroll` spends that budget on a region
that looks stopped, and nowhere else. Three read paths sharing one defect argues for making the
barrier a property of the read, not a local patch in each caller. A fourth instance living in the
device-side reader argues for fixing it there, not only above it.

## Detailed design

The design separates the barrier's question from its fallback. The question is ordering: whether
this read reflects a device state later than the action just performed. Where the reader can answer
that question, it does, and the barrier releases the moment the device publishes an update. Where no
answer is available — a `uiautomator dump` invocation carries no such marker — a bounded budget
stands in. It is spent only while the answer would have been "no."

Two constraints shape every unit below. Prime directive 2 forbids a fixed `sleep`. A budget may only
ever be a ceiling on a condition that can release early, never an unconditional delay. Unit 1 needs
the most care over that distinction. Unit 3 is what shrinks the budget to almost never spent.
Prime directive 3 keeps this app-agnostic: the barrier is a property of a backend's read channel,
declared by the backend, and no part of it is tuned per app.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Give `_settle_extract_read` an actuation-anchored barrier.** Today the poll accepts the first
   pair of agreeing reads. Require also that the agreeing read postdate the step's
   actuation by the backend's `read_lag()` budget. On a backend reporting no lag, the behavior stays
   byte-for-byte what it is now. On Android, the poll can no longer settle inside the window where
   both reads are stale. The budget becomes a ceiling rather than a delay only once unit 3 lands.
   Unit 3 supplies the mark that releases it early. Until then, this unit holds an `extract` step on
   Android for the budget. The budget's value, and the number of `extract` steps a run performs, both
   belong in the review of this unit.
2. **Give `AdbDriver._settle` the same barrier.** `_settle` releases on the first read whose
   projection matches the previous one; its first call, with no cached key, returns at once. Apply
   the same ordering requirement, so a gesture's target coordinates come from a layout the device
   published after the previous actuation. This unit closes the `gestures` long-press flake described
   in Motivation.
3. **Publish a read mark from the resident Android reader, and require reads to postdate the
   actuation.** The resident reader already runs as a long-lived instrumentation session. It can
   observe accessibility events directly and record the timestamp of the most recent one. Expose that
   timestamp with every hierarchy response. Add an endpoint that returns the device's current clock,
   so the host can take a mark before it actuates. `AdbDriver` then requires a read whose reported
   event timestamp postdates the mark it took before the gesture. Both values come from the device's
   own clock, so no host-to-device clock skew enters. This is a genuine condition wait: it releases
   as soon as the device publishes an update. That turns the budgets in units 1 and 2 into ceilings
   rarely reached, and it holds for reads taken through the resident channel — whether the caller is
   `extract`, `_settle`, or `scroll`.
4. **Replace `stableHierarchy`'s two-identical-dumps barrier with the mark.** Once unit 3's event
   timestamp is available inside the reader, the device side can stop inferring stability from two
   matching dumps. It can instead return a hierarchy that postdates the requested mark. This removes
   the defect at its source, rather than compensating for it in every caller. It also retires a
   second dump per read on the path where the two dumps agreed: the cost the current barrier pays on
   every settled screen.
5. **Cover the barrier in the deterministic suite and the driver conformance suite.** The
   deterministic tests need a `FakeDriver` that reports a lag and serves a scripted stale read. That
   lets the barrier's behavior be checked without a device. A stale-but-stable read must not be
   accepted. A backend reporting no lag must keep its current single-read behavior. The budget's
   expiry must return the latest read, not raise. The conformance suite (BE-0114) checks the
   marked-read contract against each real backend.
6. **Document the read contract.** [`docs/architecture.md`](../../docs/architecture.md) and its
   Japanese mirror describe the read path, so the barrier and the mark belong there
   ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)). A note in
   [`docs/drivers.md`](../../docs/drivers.md) states what a backend takes on by declaring a read lag.

### Verification

The lag does not reproduce on a developer machine. An Apple-silicon `bajutsu_api34` emulator runs
fast enough that every read is current, and the whole Android lane passes locally. Verification
therefore runs on the continuous-integration (CI) x86_64 emulator, dispatched with `gh workflow run
android-e2e.yml --ref <branch>`. The measurement that proves a fix is the one that proved the
defect. Log the value each `extract` would copy out on every poll. Log a digest of the device's
framebuffer alongside it. No accepted read may then differ from the screen the framebuffer shows.
The flake's about one-in-three rate means a single green run is not evidence. The branch under
measurement narrows the lane to the affected scenario, repeated, so one run samples the barrier
several times.

## Alternatives considered

**Wait for the tree to differ from the pre-step read, as `scroll` does.** `scroll`'s fix compares
each post-step read against the region's pre-step signature, and re-reads until the two differ. That
is sound there, because the baseline came from the previous iteration's already-confirmed-changed
read: the chain corrects itself. That property does not carry over to `extract`. A step's `before`
snapshot is an ordinary single read, taken right after the previous action, so it can be stale too.
In the failing `extract.yaml` run, the baseline would have read `1` while the screen showed `2`. The
post-step read would have differed from it, and the barrier would have released early on the same
stale `2` the defect already returns. A barrier anchored on an untrustworthy baseline inherits that
baseline's staleness. That is why units 1 and 2 anchor on the actuation instead.

**Require N consecutive unchanged reads instead of two.** Raising the stability threshold buys
margin without changing the question being asked. A long enough lag still defeats it, and every
settled screen pays the extra reads. The `scroll` investigation rejected the same idea, for a second
reason specific to that path: re-scrolling to confirm doubles a step's travel.

**Wait out the lag in the driver's `query()` for every read.** Making every Android read pay the
budget would close all four instances in one place, and that is the reason to reject it. `query()`
is the dominant per-step cost on adb, which is why BE-0234 exists. A barrier belongs where an
ordering guarantee is actually needed: after an actuation, not on reads that follow no action.

**Leave `extract` as it is, and let authors assert instead of extract.** This asks scenario authors
to work around a tool defect. `extract` exists precisely to carry a value the author cannot know in
advance. The scenario in Motivation is correct as written, so weakening it would hide the defect
rather than fix it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — actuation-anchored barrier in `_settle_extract_read`.
- [ ] Unit 2 — the same barrier in `AdbDriver._settle`.
- [ ] Unit 3 — read mark published by the resident Android reader, required by `AdbDriver`.
- [ ] Unit 4 — `stableHierarchy` returns a marked read instead of two matching dumps.
- [ ] Unit 5 — deterministic and conformance coverage for the barrier.
- [ ] Unit 6 — read contract documented in `docs/architecture.md` and `docs/drivers.md`, both
      languages.

## References

- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the `scroll` action, and the
  `base.ReadLagProvider` protocol this item extends to the remaining read paths.
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) — made
  mid-scenario `assert` and `extract` reads condition waits, and introduced
  `_settle_extract_read`.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident Android reader, and the first diagnosis of a read racing a gesture's accessibility
  update.
- [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) — why an extra Android
  read is expensive enough to be worth avoiding.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance suite where unit 5 checks the contract against each real backend.
- Pull request [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) — the `scroll` instance of
  this defect, fixed in isolation.

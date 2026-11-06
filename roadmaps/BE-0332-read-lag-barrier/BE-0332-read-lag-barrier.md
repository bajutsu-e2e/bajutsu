**English** · [日本語](BE-0332-read-lag-barrier-ja.md)

# BE-0332 — Apply a read-lag barrier to every read path that mistakes a stable tree for a current one

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0332](BE-0332-read-lag-barrier.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0332") |
| Implementing PR | [#1442](https://github.com/bajutsu-e2e/bajutsu/pull/1442), [#1445](https://github.com/bajutsu-e2e/bajutsu/pull/1445), [#1449](https://github.com/bajutsu-e2e/bajutsu/pull/1449), [#1454](https://github.com/bajutsu-e2e/bajutsu/pull/1454), [#1455](https://github.com/bajutsu-e2e/bajutsu/pull/1455) (withdraw the `extract` early release), [#1519](https://github.com/bajutsu-e2e/bajutsu/pull/1519) (Unit 4 follow-up: scope the `/act` tearing settle to the resolved element) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

Four places in Bajutsu decide that the screen has settled. Each place watches the accessibility tree
stop changing. All four are wrong on Android, for the same reason. A read taken shortly after an
action can still describe the screen from before that action. Two such reads then agree with each
other, and the stability test passes on a tree that is merely late.

This item replaces "the tree stopped changing" with "the tree postdates the action" at each of those
four places. The device-side barrier gets the real fix. The resident Android reader publishes a mark
that identifies the last accessibility update the reader has seen. A read is trusted once that mark
postdates the actuation, and not before. Either host-side path gets the same guarantee once its read
reaches the resident channel. A one-shot `uiautomator dump` carries no such mark, so it alone falls
back to a bounded budget.

One of the four is already fixed in isolation. Pull request
[#1391](https://github.com/bajutsu-e2e/bajutsu/pull/1391) added the `scroll` action. Pull request
[#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) stopped `scroll` from reporting a late tree
as the end of the content. The narrow protocol that fix introduced is the seam this item builds on.

## Motivation

The symptom that opened this item is an `extract` step copying out a value from before its own tap.
The showcase scenario [`extract.yaml`](../../demos/showcase/scenarios/extract.yaml) taps a counter
three times. The scenario captures the counter's tree value on the third tap. The following
[step](../../docs/glossary.md#scenario-authoring) asserts on the captured value. On the Android lane
the `smoke (adb)` job fails the scenario about one run in three, with `step 4 (assert_): expected
equals='2' but actual='3'`. The arithmetic names the defect precisely. The counter reads 1, 2, and 3
across the three taps. `extract` captured `2`, the value from before the tap it ran on. The assertion
that followed saw the true `3`. The assertion is correct, and the scenario is correct. The captured
value is one action stale.

The two polls disagree about which read to trust because each poll asks a different question.
`_poll_asserts` (`bajutsu/orchestrator/loop.py:94`) polls until the assertion passes. A late tree
costs `_poll_asserts` only another read, and the poll rides over the lag without noticing.
`_settle_extract_read` (`bajutsu/orchestrator/loop.py:147`) has no assertion to satisfy, because
`extract` copies a value out and does not check one. This poll instead runs until two consecutive
reads agree on the projection the extract reads. On Android, the accessibility update naming the new
value is published after the tap has already taken effect. Two reads taken inside that window are
both stale. They agree with each other, and they pass the stability test. From inside the poll,
nothing about that outcome looks like a failure. The poll got exactly the agreement it asked for.

The same mistake sits in three further places, which is what makes a local patch the wrong shape.
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

Measurement, not inference, identified the `scroll` instance. The investigation instrumented every
`scroll` step whose region looked unchanged, taking a framebuffer digest before and after. **All 14 of
14** such steps had moved the screen's pixels. The list scrolled every time, and only the tree said
otherwise. A re-read two seconds later saw the change in 12 of those 14. The same investigation ruled
out the innocent explanations, with evidence:

- Animation scale is not the cause: disabling animations reproduces nothing locally.
- A slow read is not the cause: six reads over seven seconds all agreed with each other.
- The gesture's interpolation shape is not the cause: a chained motion-event pan failed at an
  identical rate.

The lag is real, specific to Android, and not a slow read. It is a read of an older screen.

Pull request [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) fixed the `scroll` instance
alone, and left the seam this item needs. The pull request added `base.ReadLagProvider`, a narrow
opt-in protocol. Its single `read_lag()` method returns the number of seconds a backend admits its
reads may trail an action it has already applied. `AdbDriver` returns a non-zero budget. Every other
backend implements nothing, reports no lag, and keeps its behavior unchanged. `scroll` spends the
budget on a region that looks stopped, and nowhere else. Three read paths sharing one defect argue for
making the barrier a property of the read, not a local patch in each caller. A fourth instance living
in the device-side reader argues for fixing the defect there, not only above it.

## Detailed design

The design separates the barrier's question from its fallback. The question is ordering: whether the
read reflects a device state later than the action just performed. Where the reader can answer that
question, it does, and the barrier releases the moment the device publishes an update. Where no answer
is available (a `uiautomator dump` invocation carries no such marker), a bounded budget stands in. The
budget is spent while the answer would have been "no."

Two constraints shape every unit below. Prime directive 2 forbids a fixed `sleep`. A budget must be a
ceiling on a condition that can release early, never an unconditional delay. Unit 1 needs the most care
over that distinction, and Unit 3 shrinks the budget to almost never spent. Prime directive 3 keeps the
design app-agnostic. A backend declares the barrier as a property of its read channel, and no part of
the barrier is tuned per app.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Give `_settle_extract_read` an actuation-anchored barrier.** Today the poll accepts the first
   pair of agreeing reads. Require also that the agreeing read postdate the step's actuation by the
   backend's `read_lag()` budget. On a backend reporting no lag, the behavior stays byte-for-byte what
   it is now. On Android, the poll can no longer settle inside the window where both reads are stale.
   The budget becomes a ceiling rather than a delay once unit 3 lands, because unit 3 supplies the mark
   that releases the budget early. Until then, this unit holds an `extract` step on Android for the
   budget. The review of this unit covers the budget's value and the number of `extract` steps a run
   performs.
2. **Give `AdbDriver._settle` the same barrier.** `_settle` releases on the first read whose
   projection matches the previous one. Its first call, with no cached key, returns at once. Apply the
   same ordering requirement, so a gesture's target coordinates come from a layout the device published
   after the previous actuation. This unit closes the `gestures` long-press flake described in
   Motivation.
3. **Publish a read mark from the resident Android reader, and require reads to postdate the
   actuation.** The resident reader already runs as a long-lived instrumentation session. The reader
   can observe accessibility events directly and record the timestamp of the most recent event. Expose
   that timestamp with every hierarchy response. Add an endpoint that returns the device's current
   clock, so the host can take a mark before it actuates. `AdbDriver` then requires a read whose
   reported event timestamp postdates the mark it took before the gesture. Both values come from the
   device's own clock, so no host-to-device clock skew enters. This is a genuine condition wait, and it
   releases as soon as the device publishes an update. The wait turns the budgets in units 1 and 2 into
   ceilings rarely reached. It holds for reads taken through the resident channel, whether the caller
   is `extract`, `_settle`, or `scroll`.
4. **Re-anchor `stableHierarchy`'s barrier on the mark.** Once unit 3's event timestamp is available
   inside the reader, the device stops inferring *staleness* from two matching dumps. Instead, the
   device returns a hierarchy that postdates the requested mark. This removes the defect at its source,
   rather than compensating for it in every caller. The two-dump settle is not dropped. It narrows to
   what it alone can prove: *tearing*, a dump caught mid-republish, where Android has republished some
   node bounds but not the rest. The mark decides freshness, and a bounded settle after the mark
   decides wholeness. Splitting the two keeps both guarantees the single barrier gave. A settled-but-late
   tree, which is the read-lag defect itself, can no longer pass as current.
5. **Cover the barrier in the deterministic suite and the driver conformance suite.** The
   deterministic tests need a `FakeDriver` that reports a lag and serves a scripted stale read. The
   `FakeDriver` lets the tests check the barrier's behavior without a device. A stale-but-stable read
   must not be accepted. A backend reporting no lag must keep its current single-read behavior. The
   budget's expiry must return the latest read, not raise. The conformance suite (BE-0114) checks the
   marked-read contract against each real backend.
6. **Document the read contract.** [`docs/architecture.md`](../../docs/architecture.md) and its
   Japanese mirror describe the read path, so the barrier and the mark belong there
   ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)). A note in
   [`docs/drivers.md`](../../docs/drivers.md) states what a backend takes on by declaring a read lag.

### Verification

The lag does not reproduce on a developer machine. An Apple-silicon `bajutsu_api34` emulator runs
fast enough that every read is current, and the whole Android lane passes locally. Verification
runs on the continuous-integration (CI) x86_64 emulator instead, dispatched with `gh workflow run
android-e2e.yml --ref <branch>`. The measurement that proves a fix is the measurement that proved the
defect. Log the value each `extract` would copy out on every poll, and log a digest of the device's
framebuffer alongside it. No accepted read may then differ from the screen the framebuffer shows.
The flake's rate of about one in three means a single green run is not evidence. The branch under
measurement narrows the lane to the affected scenario, repeated, so one run samples the barrier
several times.

## Alternatives considered

**Wait for the tree to differ from the pre-step read, as `scroll` does.** The `scroll` fix compares
each post-step read against the region's pre-step signature, and re-reads until the two differ. The
comparison is sound there, because the baseline came from the previous iteration's
already-confirmed-changed read: the chain corrects itself. That property does not carry over to
`extract`. A step's `before` snapshot is an ordinary single read, taken right after the previous
action, so the snapshot can be stale too. In the failing `extract.yaml` run, the baseline would have
read `1` while the screen showed `2`. The post-step read would have differed from the baseline, and
the barrier would have released early on the same stale `2` the defect already returns. A barrier
anchored on an untrustworthy baseline inherits the baseline's staleness. That is why units 1 and 2
anchor on the actuation instead.

**Require N consecutive unchanged reads instead of two.** Raising the stability threshold buys margin
without changing the question being asked. A long enough lag still defeats the threshold, and every
settled screen pays the extra reads. The `scroll` investigation rejected the same idea, for a second
reason specific to that path: re-scrolling to confirm doubles a step's travel.

**Wait out the lag in the driver's `query()` for every read.** Making every Android read pay the
budget would close all four instances in one place, and that is the reason to reject the idea.
`query()` is the dominant per-step cost on adb, which is why BE-0234 exists. A barrier belongs where
an ordering guarantee is actually needed: after an actuation, not on reads that follow no action.

**Leave `extract` as it is, and let authors assert instead of extract.** This asks scenario authors to
work around a tool defect. `extract` exists to carry a value the author cannot know in advance. The
scenario in Motivation is correct as written, so weakening the scenario would hide the defect rather
than fix it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — actuation-anchored barrier in `_settle_extract_read`.
- [x] Unit 2 — the same barrier in `AdbDriver._settle`, via arming the pan catch-up on the
      center-resolving `tap` / `long_press` / `double_tap`.
- [x] Unit 3 — read mark published by the resident Android reader (a `GET /source`
      `X-Bajutsu-Read-Mark` header plus a `GET /clock` endpoint), required by `AdbDriver`: the
      catch-up barrier and the `extract` poll release the instant a read postdates the pre-gesture
      device-clock mark.
- [x] Unit 4 — `GET /source?since=<mark>` blocks on the resident reader until an accessibility event
      postdates the requested mark (staleness), then a bounded settle closes tearing before it answers.
      The mark replaces the two-dump barrier's freshness role; the settle keeps its wholeness role.
- [x] Unit 5 — deterministic and conformance coverage for the barrier. Deterministic coverage landed
      with the host-side barrier; the conformance suite (BE-0114) now adds a marked-read contract check —
      a `ReadOrderProvider` backend confirms a read postdates a content-moving gesture, and a synchronous
      backend is exempt (skipped).
- [x] Unit 6 — read contract documented in `docs/architecture.md` and `docs/drivers.md`, both
      languages, with the read mark added to the already-documented budget-barrier contract.

Log:

- 2026-08-02 — Host-side barrier slice (PR #1442).
  - Unit 1: `_settle_extract_read` postdates a mutating step's actuation by `read_lag()`.
  - Unit 2: `tap`, `long_press`, and `double_tap` arm the existing pan catch-up, so a following
    `_settle` resolves against the published tree.
  - Deterministic coverage and the budget-barrier docs landed with the slice. Status → In progress.
  - The device-side read mark (Units 3–4, Kotlin) and the conformance check remain. Until the mark
    lands, the budget is a bounded wall-clock ceiling on the Android lane, as the design's Unit 1 note
    anticipates.
- 2026-08-02 — Read-mark slice (PR #1445): Unit 3.
  - The resident Android reader observes the accessibility event stream. The reader stamps every
    `GET /source` with an `X-Bajutsu-Read-Mark` header, the device-clock time of the newest event as of
    the served dump. The reader also adds a `GET /clock` endpoint.
  - `AdbDriver` takes a device-clock mark before each barrier-arming gesture and requires a read whose
    mark postdates the gesture's mark. The catch-up barrier closes the instant the device publishes an
    update, with no dwell. The `extract` poll (`ReadOrderProvider`) releases early on the same signal.
    The Unit 1 and 2 budgets become rarely reached ceilings.
  - The `uiautomator dump` fallback carries no mark and keeps the wall-clock budget.
  - Deterministic coverage for the mark path landed on both the driver and the extract poll. The CI
    Android lane validates the device-side change, because the lag does not reproduce on an
    Apple-silicon emulator.
- 2026-08-02 — Marked-read barrier slice (PR #1449): Unit 4.
  - `GET /source` now accepts a `?since=` device-clock mark. When the mark is present, the resident
    reader blocks on a condition wait until an accessibility event postdates the mark (staleness). A
    bounded settle then re-dumps until two hierarchies match before the reader answers (tearing).
  - The slice splits the retired `stableHierarchy`. The mark takes over deciding freshness. The
    two-dump barrier decided freshness only by proxy, so it could pass a settled-but-late tree, which is
    the read-lag defect. The settle keeps deciding wholeness, which `waitForIdle` alone cannot decide,
    because a dump caught between two node republishes tears.
  - The host stamps the pending gesture's actuation mark onto the request, so the driver's re-poll
    collapses into one blocking round trip.
  - The budget is a ceiling on the lag. The budget sits well inside the host's HTTP timeout and below
    the host's own read-lag budget, so the host re-poll stays the outer bound. On expiry, the reader
    returns the latest tree with its own mark, not an error.
  - Deterministic coverage landed for the `?since=` plumbing on both the resident client and the
    driver. The Kotlin compiles under the pinned Gradle (JBR). The CI Android lane validates the device
    behavior, where the lag reproduces (it does not on an Apple-silicon emulator).
  - The tearing settle answers a review question raised on the PR: the mark alone proves that catch-up
    started, and does not prove that the tree stopped republishing.
- 2026-08-02 — Conformance and docs slice (PR #1454): Units 5 and 6.
  - The driver conformance suite (BE-0114) gains a marked-read contract check. After a content-moving
    scroll on the scrollable screen, a `ReadOrderProvider` backend must report a read that postdates
    the gesture.
  - A backend that cannot order reads does not implement the protocol and is skipped. The synchronous
    backends and the fast gate's `FakeDriver` are such backends. `make check` stays green while the
    assertion runs on the adb on-device lane.
  - `docs/architecture.md` and `docs/drivers.md`, with their Japanese mirrors, replace the "read mark
    planned" note with the landed mark: the resident reader's `X-Bajutsu-Read-Mark` header and
    `GET /clock` endpoint, the device-clock mark taken before an actuation, the ordering requirement
    (`read_postdates_actuation()`), and the `GET /source?since=` blocking read. The docs also record
    that the wall-clock budget survives for the mark-less `uiautomator dump` fallback.
  - Status → Implemented.
- 2026-08-02 — Follow-up (PR #1455): the read mark no longer releases the `extract` poll early.
  - The Motivation's own failure reproduced on `smoke (adb)` after Unit 3 landed, with the same string
    `step 4 (assert_): expected equals='2' but actual='3'`. The mark answers a weaker question than the
    poll asks.
  - The mark certifies that *an* accessibility event postdates the gesture. `extract` needs the
    property it copies out to have been republished.
  - One gesture produces several events. Compose publishes the tapped button's own event before the
    `Text` mirroring the new count recomposes. A read can postdate the tap, still carry the previous
    value, and agree with the read after it. The mark and the two-agreeing-reads test then both pass on
    a stale pair.
  - The wall-clock budget separates the stale pair from a fresh one, so the `extract` poll keeps the
    budget as its only release.
  - Ordering stays the right question for the driver's own catch-up barrier, which waits on frames the
    mark does speak for. That use is unchanged.
  - Deterministic coverage replaces the Unit 3 early-release test with the regression: a backend whose
    mark fires while the value is still stale must bind the live value.
- 2026-08-06 — Follow-up: `respondAct`'s tearing settle (Unit 4) changed.
  - `respondAct` now compares the resolved identity's matched bounds across re-dumps.
  - `settledDump` still compares the whole dumped XML, but now for `respondSource` alone. That endpoint
    answers an arbitrary host-side selector, so it cannot narrow the comparison.
  - `respondAct` already knows which element it targets: the four identity fields (`want`) the host
    sent. A node unrelated to that target can keep changing elsewhere on screen. A status bar clock, a
    spinner, or a blinking cursor are common examples. Such a node no longer forces every gesture
    through the full `SETTLE_DUMPS` budget. The budget now releases once the target's own bounds stop
    moving.
  - The bound and the condition-driven settle stay the same. No fixed sleep enters either path.

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

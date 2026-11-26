**English** · [日本語](BE-0505-adb-read-lag-attempt-bound-ja.md)

# BE-0505 — Bound the adb read-lag barrier by completed device waits, so a loaded runner costs time and not a verdict

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0505](BE-0505-adb-read-lag-attempt-bound.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0505") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's Android backend drives an emulator over the Android Debug Bridge (adb). The backend reads
the screen from the accessibility tree the emulator publishes. Android applies a gesture first and
publishes the matching tree update after. A read taken inside that window describes the screen before
the gesture. [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) added the barrier
that waits such a late read out. The barrier works. This item changes how the driver bounds it.

The driver bounds the barrier in wall-clock seconds. Each attempt inside that bound costs one
resident-channel request. A loaded continuous integration (CI) runner makes such a request slow. An
eight-second bound covers more attempts on a quiet machine than on a loaded one. A loaded machine
gets three. The count of chances to observe a caught-up tree tracks machine speed. Load then converts into a
changed verdict in place of a longer run.

We propose two changes. The device reports on the wire whether its own settle closed. The
driver counts **completed device waits** alongside seconds. Each bound ends on the later of the two. A slower machine then
spends more wall-clock and still reaches the same answer.

## Motivation

The driver gives each of its two waits a ceiling in wall-clock seconds. Each attempt under that
ceiling costs one resident-channel request. A loaded machine thus gets fewer attempts than a quiet
one. The verdict can then depend on machine speed, which determinism-first rules out.

Two numbers show how machine load reaches the verdict. The `_READ_LAG_S` constant bounds the
barrier at eight seconds of wall-clock. The driver sends a `WAIT_BUDGET_MS` of 3,500 milliseconds
with each waiting request. The device spends at most that much on one request. A quiet emulator
answers well inside the cap, which leaves the barrier more polls. A loaded emulator spends the whole
cap, and the barrier gets three polls. The settle poll's `_SETTLE_DEADLINE_S` constant has the same
shape. It allows eight seconds for a poll needing two consecutive agreeing reads. A read costing
3,500 milliseconds reduces that poll to three pairs. The deadline starts after the first read, which
is what leaves three.

The project has widened the read-lag bound once. Commit `cf90a2763` raised `_READ_LAG_S` from
four seconds to eight against runner contention. A wider ceiling still counts seconds.

The barrier's expiry behavior compounds the coupling. On expiry the driver warns, drops the barrier,
and resolves from the current screen. The device side has the same shape. Its `settled` helper stops
after four reads, or when its own budget runs out. The helper then returns the last dump it holds.
Nothing in the response says the settle never closed. The server writes that sentence to its Android
log, which the driver never reads. A driver unable to separate a settled answer from a torn one
cannot keep waiting on purpose.

A later reader can check one observation. A run against a driver clock slowed by any factor reaches
the verdict an unslowed run reaches. The deterministic suite asserts that equivalence, which does not
hold today.

#### What this item does not explain

Issue [#2520](https://github.com/bajutsu-e2e/bajutsu/issues/2520) records two lanes going red:
`notification-banner (adb)` and `pool (adb)`. Each ended on three read-lag warnings and a selector
miss. This item first read that signature as a late tree. The device logs of one occurrence per lane
show a different cause.

- Android recreated the app's activity before the failing step. The log reports the change
  as a change of asset paths, mask `80000000`.
- The app returned to its first screen. The target of the step was thus absent from a current tree.
- The scroll recovery panned a short list that could not move. Such a pan publishes no accessibility
  event, which is the benign case the warning already names.
- Neither log holds the line for a spent dump cap.

This item thus claims no repair for those lanes. Pull request
[#2525](https://github.com/bajutsu-e2e/bajutsu/pull/2525) waits an install's
broadcasts out before the launch. The log order points to those broadcasts as the trigger of the
relaunch. That link is an inference from one occurrence per lane, not a reproduction.

## Detailed design

The design keeps every wait a condition wait. The device's wait for an accessibility event releases
the instant such an event arrives. The device's tearing settle releases the instant two reads agree.
What changes is the bound on the number of such waits the driver asks for.

Each bound takes one shape. It ends on the later of its current wall-clock ceiling and a floor of
completed device waits. A quiet machine thus keeps the span it has today. A loaded machine gains
attempts rather than losing them.

### Work breakdown (`MECE`)

- **Unit 1** — the resident server reports whether its settle closed.
- **Unit 2** — a tree the device could not settle stays away from the selector resolve.
- **Unit 3** — the read-lag barrier gains a floor of four completed device waits.
- **Unit 4** — the settle poll gains a floor of four polling reads.
- **Unit 5** — ~~a stall on a tree the device proved still moving~~ (withdrawn).
- **Unit 6** — `read_lag()` keeps its seconds contract for the `scroll` loop and the `extract` poll.
- **Unit 7** — the deterministic suite pins the slowed-clock equivalence.
- **Unit 8** — the read-lag contract and its mirror page both record the new bound.

#### Unit 1 names the answer the driver lacks

The server's `settled` helper has two expiry exits. One comes after the full dump cap with no two
consecutive reads agreeing. The other comes when the request's own wall-clock budget runs out. Either
exit returns a dump the settle never confirmed. The helper says so in a line in the Android log
alone.

Unit 1 turns that fact into a response header with two values: settled or unsettled. The driver acts
on that one distinction, so the header carries no more. The settle behind that header drops
SystemUI's decor nodes from its comparison. The body it returns still carries each window. Today's `settledDump` compares the whole
dumped hierarchy. That hierarchy carries the status bar's clock and icons. A minute tick could thus
exhaust the dump cap on a quiet app screen. The narrowing stays in the comparison for a reason. The
driver reads the heads-up banner off that decor before it strips the decor out. A device dropping it
from the wire would leave the notification-banner guard with nothing to read. The device narrows its
own comparison that way for `/act`. The `/act` reply carries the header too. The tree a confirmed
gesture brings back goes through the same helper under a tighter budget. `HierarchyRead` holds the
answer once for both paths. The header is additive. An older server sends none, and the driver reads a missing header
the way it reads today's answer. The surfaces are the resident server, its `adb_resident` client
package, and the `/act` reply.

#### Unit 2 keeps a torn tree away from the resolve

A tree the device could not settle may be part new and part old. Resolving a selector against such a
tree can miss an element that is present on screen. The case follows from the code; no observed run
has shown it yet. The driver re-reads instead, which is a condition wait on the device's own settle. That re-read is what spends a
loaded machine's extra wall-clock in place of its verdict.

The `/act` path needs the same refusal. The `_seed_from_act` helper adopts the tree a confirmed
gesture carries. The device settles that tree under the tighter `ACT_TREE_BUDGET_MS` budget. The
helper leaves `_settled_key` unset on purpose. An actuation's own resolve thus still confirms the
seed through the settle poll. A `wait` or an `assert` resolves through a bare `query()` instead. That is where a seed
the device could not settle reaches a selector unconfirmed. The helper's own docstring already names
the gap. The device returns a still-tearing dump with nothing on the wire to say so. Unit 1's header supplies that answer, and the
helper then refuses such a seed. It already refuses a degenerate tree the same way. The refusal costs
one read and nothing besides. The surfaces are the `hierarchy_read.py` and `adb_driver.py` modules.

#### Units 3 and 4 add a floor the driver clock cannot shrink

A completed device wait is one request the device answered after its own wait released or ran out. A
bound counting those waits measures the device's behavior rather than the driver's clock. A loaded
machine pays more wall-clock for the same count. It still lands on the same verdict.
Determinism-first asks for that property, and the current bound lacks it.

Both bounds keep their wall-clock ceiling and gain a floor of four completed waits. The later of the
two ends the wait. The ceiling still has work to do. `_SETTLE_DEADLINE_S` exists to span a real
animation whatever a read costs. A floor alone would let a fast channel's four reads span half a
second. The floor is what a loaded machine gains. Four is one more than the three eight seconds buys
at the 3,500-millisecond cap. A quiet machine's reads take 400 to 600 milliseconds. Four sits well
under what the same ceiling already fits, so no run loses attempts.

The worst case is worth stating against the lanes' own timeouts. Each recovery attempt pays both
floors. The recovery re-reads through a settle that waits the barrier out and then polls. One
expired barrier costs four waits of up to 3,500 milliseconds, so 14 seconds. The poll behind it costs
its first read plus four more, so 17.5 seconds. The recovery pays both three times, so about 95
seconds for a step whose recovery runs to its end. The same path costs about 75 seconds today.
`smoke (adb)` allows 40 minutes for a run that passes in 25 to 29, and `pool (adb)` allows 60
minutes. A minute and a half on a rare step thus stays well inside both.

Unit 6 keeps `read_lag()` seconds-valued for its two orchestrator consumers. Those consumers are the
`scroll` loop and the `extract` poll. Both loops still bound a read count by the driver's clock.
`read_lag()` is a cross-backend `Driver` surface. Re-shaping it would leak one backend's bounding
into the core. Those two loops thus keep the coupling. This item's equivalence claim covers the
driver's own barrier and settle alone.

The per-request socket timeouts in the `adb_resident/budgets.py` module stay as they stand. A lost
channel still has to look lost.

#### Unit 5 is withdrawn

This unit proposed a stall in place of the scroll recovery. The stall needed two conditions. The
barrier expired unclosed, and the device reported the dump cap. The unit asked for one measurement
first. That measurement was the exit the red lanes' logs show. Neither log shows the dump cap. No observed run
supports the new verdict, so this item drops it. The scroll recovery keeps today's behavior.

### Verification

- A fake channel reporting an unsettled tree gets a re-read before any selector resolve.
- A fake channel reporting a settled tree takes no more reads than today.
- A server sending no header keeps today's behavior.
- The same fake channel under a driver clock scaled by ten yields the unscaled run's verdict.
- A backend reporting no read lag keeps its single-read behavior, as BE-0339 Unit 1 requires.
- The resident budget test holds both ends to the new header and the unchanged socket timeouts.

All six checks run under `make check` on Linux without an emulator. The fast gate thus covers this
design's own claim about machine speed.

## Alternatives considered

#### Wait on the read mark's own progress

The comment on issue #2520 suggests waiting on the tree's progress in place of a fixed budget. Prime
directive 2 asks for that framing. The read mark cannot carry it. The mark names the newest
accessibility event the resident reader has seen. The barrier's anchor is a device-clock reading
taken before the gesture. Any event after that anchor postdates it and closes the barrier outright.
The mark thus never sits between "no progress" and "caught up". A binary signal affords no graded
progress to wait on. The device wait the mark drives is where the progress lives. Units 3 and 4 count
those waits for that reason.

#### Reduce the load the matrix puts on one runner

This would change nothing about one runner. The 13 adb jobs in
[`android-e2e.yml`](../../.github/workflows/android-e2e.yml) each declare the `ubuntu-latest` runner.
Each job gets its own virtual machine. Narrowing the matrix thus changes no single runner's load. The
emulator settings leave nothing meaningful on the table either. Every lane runs headless, with the
software renderer and no audio. Every lane turns off the boot animation and both cameras. Every lane
disables animations at the action. The `pool (adb)` lane also zeroes the three animation scales on
each of its two devices. The action reaches no emulator but its own, which is why that lane needs
the extra step. Every lane caches an Android Virtual Device (AVD) and saves no new snapshot. Optional
`dumpsys cpuinfo` polling stays off by default for the observer effect it would add. The `pool (adb)` lane is the heaviest one. Its one load
lever is the `--workers 2` parallelism the lane exists to exercise. That lane already raises its wait
floor to 30 seconds for the slower first render. A wait floor is a condition wait, and thus the right
instrument. Raising the core count on a single-emulator lane would add contention with the driver's
own Python. It would also invalidate an AVD cache key pinned to the hardware profile.

#### Retry the gesture when the tree never caught up

The evidence holds no case this would repair. In the observed runs a relaunch discarded the
screen an earlier gesture had reached. Repeating the last gesture would not restore that screen. A
retry would also inject an already-applied gesture a second time. That corrupts state on a toggle.
[`roadmaps/README.md`](../README.md) already places automatic retry in tension with
determinism-first. It confines retry to quarantine use if the project adopts it at all, because
retry hides flakiness.

#### Raise the read-lag ceiling again

Commit `cf90a2763` raised `_READ_LAG_S` from four seconds to eight for this same contention. Nobody can size a wall-clock ceiling against a machine whose speed varies. A
ceiling wide enough for the slowest machine charges that whole ceiling to a quiet gesture.

#### Fail the run whenever the barrier expires

[BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md) weighed this and
turned it down. Its reasoning holds. The benign case is routine, and the driver cannot separate it
from the tree alone. The lane would go red on correct scenarios. The logs behind issue #2520 show
that benign case: pans on a list that could not move.

#### Fold the corrupt system-image download into this item

Issue [#2519](https://github.com/bajutsu-e2e/bajutsu/issues/2519) shares the "a re-run passes" shape
and nothing else. There `sdkmanager` reports an unusable archive as a warning and keeps going. The
job ends later on a refused adb connection. The remedy lives in the workflow. The step should stop on
that warning while naming the package, and should retry the install once. That remedy touches no
driver, no barrier, and no determinism question. It stays its own issue for that reason. Folding it in
would couple a provisioning retry to a driver change and widen this item's lane.

#### What this item leaves uncovered

Issue #2520 records one occurrence where the heads-up dismissal went unrecorded. The notification
never appeared in that run. The occurrence carries no read-lag warning and no late tree. Nothing here
addresses it. The relaunch behind the other occurrences also stays outside this item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — the resident server reports whether its settle closed
- [ ] Unit 2 — a tree the device could not settle stays away from the selector resolve
- [ ] Unit 3 — the read-lag barrier gains a floor of four completed device waits
- [ ] Unit 4 — the settle poll gains a floor of four polling reads
- [x] Unit 5 — ~~a tree the device proved still moving raises a stall and skips the recovery~~
  - Withdrawn: the red lanes' device logs show no dump-cap exit.
- [ ] Unit 6 — `read_lag()` keeps its seconds contract for the `scroll` loop and the `extract` poll
- [ ] Unit 7 — the deterministic suite pins the slowed-clock equivalence
- [ ] Unit 8 — the read-lag contract and its mirror page both record the new bound

Log:

- 2026-11-26 — Read the device logs of both red lanes to size the withdrawn unit.
  Both show an activity relaunch and no dump-cap exit.
  Withdrew unit 5 and removed the claim of repairing those lanes.
  Unit 1 now reports whether the settle closed, since nothing else reads which exit ran.

## References

- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — the read-lag barrier and the
  read mark this item re-bounds.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident channel, and the rule that bounds a settle by elapsed time.
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md) — device-side
  actuation, and the publish confirmation letting a gesture skip the barrier.
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the `scroll` loop that
  consumes `read_lag()`.
- [#2520](https://github.com/bajutsu-e2e/bajutsu/issues/2520) — the two red lanes and their shared
  signature.
- [#2525](https://github.com/bajutsu-e2e/bajutsu/pull/2525) — the wait on an install's broadcasts.
- [#2519](https://github.com/bajutsu-e2e/bajutsu/issues/2519) — the corrupt system-image download,
  kept separate above.
- [`docs/drivers.md`](../../docs/drivers.md) — the read-lag contract a backend takes on.

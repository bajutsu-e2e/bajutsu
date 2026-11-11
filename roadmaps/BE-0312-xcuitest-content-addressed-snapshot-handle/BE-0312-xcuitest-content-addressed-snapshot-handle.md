**English** · [日本語](BE-0312-xcuitest-content-addressed-snapshot-handle-ja.md)

# BE-0312 — Derive XCUITest actuation handles from element identity so an unchanged screen keeps its handles valid

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0312](BE-0312-xcuitest-content-addressed-snapshot-handle.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0312") |
| Implementing PR | [#1297](https://github.com/bajutsu-e2e/bajutsu/pull/1297) |
| Topic | Platform support |
| Related | [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The XCUITest backend still fails a tap with `element vanished (stale handle)` on a screen whose
elements have not moved at all. The stale-handle re-resolution retry of
[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
does not close this failure class.

The cause is the handle scheme of the on-device runner, not the app or the scenario. The runner mints
a fresh handle for every element on every accessibility-tree snapshot. The runner also invalidates
every prior handle unconditionally. A handle encodes the *snapshot generation* in which the element
was seen, not the *identity* of the element itself.

One redundant `GET /elements` is therefore enough to advance the generation and strand a handle that the client is about to use, even though nothing on the screen changed between the resolve and the actuation. The transport-retry seam re-sends that read whenever a read looks like it blipped.

This item proposes to derive the handle deterministically from the stable identity of the element:
its identifier, label, and traits. A per-call counter no longer takes part. An element that is still
on screen keeps the same handle across snapshots. The handle stays valid no matter how many extra
snapshots the runner takes.

The behavioral change is confined to the `SnapshotStore` of the runner. The Python channel and the
HTTP router of the runner keep their logic, because a handle stays an opaque string to every
consumer. Only their doc comments realign (BE-0113).

## Motivation

The flake reproduces on the handle-based actuation path even after BE-0289 shipped. A `tap` by `id`
fails with `element vanished (stale handle)` immediately after a large screen transition, such as a
full-screen modal presenting. The target element is present the whole time. Four observations locate
the cause on the runner side and not in a screen change:

- The evidence snapshot captured when a `wait` for the element succeeded and the evidence snapshot
  captured when the following `tap` failed as `stale` agree on the target element's frame to the
  pixel (`[16.0, 784.0, 370.0, 40.0]` in both). The element did not move, resize, or leave the
  screen.
- Setting `dismissAlerts: false` does not stop the flake, so no artificial-intelligence path and no
  vision path is involved.
- A `tapPoint`, a raw coordinate tap that carries no handle, never reproduces the flake. The cause
  therefore sits on the handle and not on the tap itself.
- Other `tap` steps in the same scenario succeed when they do not follow a large screen transition.
  The flake tracks runner busyness, not the selector.

The handle scheme turns a redundant read into a stale handle. `refreshSnapshot` in
[`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift)
advances a monotonic `generation` counter on every call, clears `currentElements`, and mints
`h-<generation>-<index>` for each element. The very next snapshot invalidates a handle from one
snapshot, whether or not the screen changed.

The only trigger for a new snapshot is a `GET /elements`. For each handle-based step, the backend
issues one `GET /elements` (through `_resolve_handle`) followed by one `POST /tap` (through
`_actuate`). Under normal timing, the generation does not move between the resolve and the
actuation. A redundant `GET /elements` moves it.

The redundant read comes from the transport-retry seam. `_is_retry_eligible` in
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) returns true for every `GET`
(`return not delivered or method == "GET"`). A read is idempotent, and BE-0207 added the seam to
smooth a sub-second transport blip. When a `GET /elements` looks like it timed out on the client,
`_with_retry` re-sends it.

Right after a large screen transition, the main thread of the runner is busy re-snapshotting the
settling screen. A read is then slow enough to look blipped. The two effects compound into the stale
handle:

1. The client judges the first `GET /elements` a transport failure, and `_with_retry` re-sends it.
2. The client resolves the selector against the response of the re-sent read (generation N+1). The
   client then issues `POST /tap` with the handle that the runner minted in generation N+1.
3. The first read was in fact still running on the busy runner. The first read completes and runs
   `refreshSnapshot` *after* the re-send. The generation advances to N+2, and `currentElements` is
   replaced wholesale.
4. The generation-N+1 handle that the client sent is no longer a key in `currentElements`. The runner
   returns `stale`, and the backend raises the vanished-element error for a button that never left
   the screen.

The re-resolution retry of BE-0289 cannot recover this case, and the reason motivates a different
fix. The retry re-queries and re-actuates while the selector still resolves uniquely. That approach
assumes that a screen change caused the stale handle and that a fresh query will reflect the change.

Here the screen did not change. The runner is merely busy, and every re-query that the retry issues
is itself a `GET /elements`, exposed to the same re-send-and-interleave race. While the runner stays
busy, in the window right after a transition, all three bounded attempts of BE-0289 can stale for the
same non-reason. The step then fails.

The two items address different layers. BE-0289 tolerates a genuine snapshot-version race on the
client. This item removes the version from the handle, so an unchanged element never receives a new
handle in the first place.

The flake is expensive in the same three ways that BE-0289 documents:

- A required on-device gate that fails at random erodes trust in the gate. It invites either a
  reflexive re-run or a wasted investigation.
- Every re-run burns metered macOS-runner minutes on a job that builds the app and boots a
  Simulator.
- The `element vanished` message misleads. It reads as though the element is missing, when the
  element is present the whole time.

## Detailed design

The work splits into four units. Unit 1 changes how a handle is derived. Unit 2 preserves the
`stale`-versus-`notFound` distinction on which the retry of BE-0289 depends. Unit 3 proves the change
off-device. Unit 4 keeps the documentation in step. The units are ordered by dependency and can land
as separate pull requests.

**Unit 1 — Derive the handle from stable element identity, not from a generation counter.** In
`SnapshotStore`, replace the `h-<generation>-<index>` scheme with a handle derived deterministically
from the stable identity of the element: its `identifier`, `label`, and `traits`.

The handle keeps its `h-` prefix as an opaque-handle namespace. Only the part after the prefix
changes, from a generation and index to an identity encoding (for example `h-<identity-hash>`). The
prefix is deliberately preserved, so nothing downstream that keys off the prefix (a test, a log
filter) breaks.

The snapshot also carries three fields that the derivation deliberately excludes:

- The `backingElement` is excluded. Every `queryElements` call returns a fresh object reference for
  the same on-screen element. Including the reference would change the handle on every snapshot, the
  very defect that this item removes.
- The `value` and the `frame` are both excluded, because both change while a screen settles. The
  runner re-snapshots at exactly that time. A `frame` shifts by a sub-pixel as a transition animates.
  A `value` ticks as a loading label resolves, a countdown counts, or a spinner spins.

Including either field would give one unchanged element a new handle on each of two interleaved
reads. The race would reopen in the window that this item targets. Neither field buys actuation
precision either. A handle resolves to a `backingElement`, and the runner acts through that
reference, never through the stored `value` or `frame`.

The choice is not new to the package. `attributesMatch` in
[`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)
already defines element identity as `identifier` / `label` / `traits`. It excludes `value` and
`frame` for the same settling reason
([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)).
The handle reuses the existing identity notion of the package and invents no second one.

`refreshSnapshot` still clears and rebuilds `currentElements` on every call. The stored
`backingElement` that a handle resolves to is always the reference of the latest query. The only
change is that the *key* is now the identity handle. An element with unchanged identity maps to the
same handle string across snapshots.

A redundant `GET /elements` therefore re-issues the identical handle for an unchanged element. The
interleaving in *Motivation* can no longer strand the handle. Whichever snapshot the handle of the
client came from, the handle is still a live key as long as the element is on screen.

Two on-screen elements can share an identical identity, for example two buttons both labeled
`Delete` with no identifier. To keep their handles distinct, append an occurrence index to collisions
*within a single snapshot* in query order. The first occurrence keeps the bare identity handle, and
later occurrences take a suffixed variant.

**Unit 2 — Preserve the `stale`-versus-`notFound` distinction.** The retry of BE-0289 branches on the
reply of the runner. A `stale` reply triggers a re-resolution. A `not-found` reply fails immediately.
The two replies must stay distinguishable after the handle scheme changes.

Today the generation counter tells them apart. `lookup` parses the handle. A well-formed handle whose
generation is not current reads as `stale`, and an unparseable handle reads as `notFound`.

Identity-derived handles carry no generation to parse. `SnapshotStore` therefore records the set of
handles that it has ever issued, and `lookup` returns one of three results:

- `.found`, when the handle is a live key.
- `.stale`, when the handle was issued before but is not a live key. The element it named has left the
  screen or changed identity.
- `.notFound`, when the handle was never issued, such as a malformed or fabricated string.

The HTTP router of the runner keeps its existing three-way branch over the result. The driver-side
retry of BE-0289 keeps working unchanged. A real disappearance maps a once-issued handle to `.stale`
as before. The retry re-resolves, finds that the selector no longer resolves, and fails loudly.

**Unit 3 — Prove the change off-device.** Cover `SnapshotStore` with Swift unit tests in
[`BajutsuKit/Tests/BajutsuRunnerTests/SnapshotStoreTests.swift`](../../BajutsuKit/Tests/BajutsuRunnerTests/SnapshotStoreTests.swift).
The tests run in the off-device test suite of the runner, with no Simulator.

The central addition is the regression that this item exists to prevent. The test refreshes the same
unchanged snapshot two or more times, then resolves the handle of the *first* refresh to `.found`.
The old generation scheme fails this case, because it stales that handle on the second refresh. A
companion test pins the in-snapshot tiebreak: two identity-identical elements in one snapshot resolve
to distinct handles.

The existing tests keep their current form and keep passing under the new scheme. The suite depends
on them:

- A refresh with a *different* element still stales the handle of the first element (a genuine
  disappearance, `.stale`).
- A never-issued handle still reads `.notFound`.
- An issued handle still carries the `h-` prefix.
- The concurrency test still passes.

The identity derivation therefore neither breaks the `.stale` / `.notFound` contract that Unit 2
preserves nor reintroduces a data race.

**Unit 4 — Keep the documentation in step (BE-0113).** The `SnapshotStore` class comment states
today that "Each `refreshSnapshot` replaces the previous snapshot: all prior handles become stale".
This item changes exactly that behavior. Rewrite the comment to describe identity-derived handles and
the condition under which a handle is still stale: its element left the screen or changed identity.

The Python side of the same channel narrates the old model too, and it must move with the code.
Cross-file drift is what BE-0113 forbids:

- The module docstring of [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) calls
  the handle a "per-snapshot handle" that "can go stale when the screen re-snapshots between resolve
  and actuate".
- [`bajutsu/drivers/xcuitest_live.py`](../../bajutsu/drivers/xcuitest_live.py) twice calls the
  WebDriver element id the "per-snapshot handle" of the runner.

Realign all three to the identity-derived model, which is stable across a re-snapshot of an unchanged
screen. Update [`DESIGN.md`](../../DESIGN.md) and [`docs/architecture.md`](../../docs/architecture.md)
if either describes the handle staleness semantics of the runner, so the prose account matches the
behavior.

Two follow-ups are known, and the item deliberately leaves them out of scope:

- The set of ever-issued handles grows for the life of the runner process. A very long run
  accumulates handle strings monotonically. The growth is small (one short string per distinct
  element ever seen), and a run is process-scoped. A bound can be added later if a measurement shows
  that it matters, for example retaining the handles from the last few snapshots only.
- The occurrence-index tiebreak for identity-identical elements assumes that the query order of
  identical elements is stable across snapshots. A screen that reorders visually identical elements
  between snapshots could still reassign their handles. Such a screen is rare, and its elements are
  indistinguishable to a selector anyway. The item defers closing the gap until a real scenario needs
  it.

## Alternatives considered

**Make `GET /elements` non-retry-eligible so no redundant read is sent.** Removing the read from the
transport-retry seam of BE-0207 would delete the specific trigger in *Motivation*. But it would
reopen the transient-blip flake that BE-0207 shipped to close: a genuine sub-second transport blip on
a read would again fail the step outright.

The retry is also not the only conceivable source of an extra snapshot. Any future runner behavior
that re-snapshots would reintroduce the flake. Removing the retry treats one symptom and not the
cause. Identity-derived handles make a redundant snapshot harmless, wherever the snapshot comes from.

**Rely on BE-0289's re-resolution retry alone.** *Motivation* shows why the retry cannot recover this
case. The retry presumes a screen change that a fresh query will reflect. Here the screen is
unchanged, and every re-query is itself exposed to the same re-send race, so all bounded attempts can
stale for the same non-reason. The two items are complementary and not substitutes. BE-0289
tolerates a real race on the client, and this item removes the manufactured race at the source.

**Keep a bounded window of recent generations valid instead of the latest generation alone.**
Treating handles from the last few generations as live would paper over the interleaving without
changing the scheme. But the approach widens an arbitrary window and does not fix the mismatch. A
handle should be valid exactly while its element is on screen, which is a statement about identity
and not about how many snapshots have elapsed. Deriving the handle from identity states that
invariant directly. The derivation needs no tuning parameter that a slower runner could outrun.

**Include the element's `frame` or `value` in the handle key.** Keying on position or on the live
value as well would let two identity-identical elements take distinct handles without an occurrence
index. But it reopens the race that this item closes. A screen that settles after a transition
re-snapshots repeatedly. Either a `frame` that shifts or a `value` that ticks (a loading label, a
countdown, a spinner) between two interleaved reads gives one unchanged element two handles. The
redundant read then strands the handle again, in the window that the item targets.

The occurrence-index tiebreak already distinguishes identity-identical elements within a snapshot.
Neither field buys actuation precision, because the runner acts through the `backingElement` and not
through the stored `value` or `frame`. Both fields earn nothing that the design needs, at the cost
that the design exists to avoid. The `attributesMatch` of the package (BE-0287) already excludes both
for the same reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Identity-derived handle in `SnapshotStore` (`identifier` / `label` / `traits`;
      `backingElement`, `value`, and `frame` all excluded, matching `attributesMatch` / BE-0287),
      `currentElements` still rebuilt each refresh, with an in-snapshot occurrence-index tiebreak for
      identity-identical elements.
- [x] Unit 2 — `stale`-versus-`notFound` distinction preserved via an ever-issued-handle set, so the
      runner's three-way router branch and BE-0289's driver retry keep working.
- [x] Unit 3 — Off-device Swift unit tests: the regression case (unchanged re-refresh keeps the first
      handle `.found`) plus a companion for the in-snapshot tiebreak (identity-identical elements get
      distinct handles); the existing tests (`.stale` on a real change, `.notFound`, `h-` prefix,
      concurrency) kept and still passing.
- [x] Unit 4 — Documentation aligned (BE-0113): the `SnapshotStore` comment, the `xcuitest.py` and
      `xcuitest_live.py` "per-snapshot handle" docstrings, and `DESIGN.md` / `docs/architecture.md`
      where either describes handle staleness.

## References

- [BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md):
  the client-side stale-handle retry this item complements — BE-0289 tolerates a genuine snapshot
  race, whereas this item removes the manufactured race the retry cannot recover.
- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md):
  the transport-retry seam whose idempotent-`GET` re-send is the redundant read that strands a
  generation-based handle.
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md):
  the sibling on-device work on the same runner, and the `SnapshotStore` concurrency test this item
  must keep green.
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md):
  the determinism stance this item stays consistent with — remove the cause of a flake rather than
  absorb an outcome.
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md):
  the backend whose resolve-Python-side / actuate-by-handle addressing this item leaves intact while
  changing only how a handle is derived.
- [`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift):
  `refreshSnapshot` and `lookup` — the sole surface this item changes.
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift):
  the three-way branch over `lookup`'s result that this item keeps unchanged.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `_resolve_handle`, `_actuate`,
  and `_is_retry_eligible` — the resolve/actuate seam and the idempotent-read retry whose interaction
  produces the stale handle.

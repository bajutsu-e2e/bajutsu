**English** · [日本語](BE-0312-xcuitest-content-addressed-snapshot-handle-ja.md)

# BE-0312 — Derive XCUITest actuation handles from element identity so an unchanged screen keeps its handles valid

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0312](BE-0312-xcuitest-content-addressed-snapshot-handle.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0312") |
| Implementing PR | [#PENDING](https://github.com/bajutsu-e2e/bajutsu/pull/PENDING) |
| Topic | Platform support |
| Related | [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The XCUITest backend still fails a tap with `element vanished (stale handle)` on a screen whose
elements have not moved at all — a failure class the stale-handle re-resolution retry of
[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
does not close. The cause is the on-device runner's handle scheme, not the app or the scenario. The
runner mints a fresh handle for every element on every accessibility-tree snapshot and invalidates
every prior handle unconditionally, because a handle encodes the *snapshot generation* in which the
element was seen rather than the *identity* of the element itself. One redundant `GET /elements` —
which the transport-retry seam re-sends whenever a read looks like it blipped — is therefore enough
to advance the generation and strand a handle the client is about to use, even though nothing on the
screen changed between the resolve and the actuation. This item proposes to derive the handle
deterministically from the element's stable identity (its identifier, label, and traits)
instead of from a per-call counter, so an element that is still on screen keeps the same handle
across snapshots and the handle stays valid no matter how many extra snapshots the runner takes. The
behavioral change is confined to the runner's `SnapshotStore`; the Python channel and the runner's
HTTP router keep their logic (only their doc comments realign, BE-0113), because a handle stays an
opaque string to every consumer.

## Motivation

The flake reproduces on the handle-based actuation path even after BE-0289 shipped. A `tap` by
`id` fails with `element vanished (stale handle)` immediately after a large screen transition — a
full-screen modal presenting, for instance — while the target element is present the whole time. Four
observations locate the cause on the runner side rather than in a screen change:

- The evidence snapshot captured the moment a `wait` for the element succeeded, and the evidence
  snapshot captured when the following `tap` failed as `stale`, agree on the target element's frame
  to the pixel (`[16.0, 784.0, 370.0, 40.0]` in both). The element did not move, resize, or leave the
  screen.
- Setting `dismissAlerts: false` does not stop the flake, so no artificial-intelligence path and no
  vision path is involved.
- A `tapPoint` — a raw coordinate tap that carries no handle — never reproduces the flake, which
  places the cause on the handle, not on the tap itself.
- Other `tap` steps in the same scenario, the ones that do not follow a large screen transition,
  succeed. The flake tracks runner busyness, not the selector.

The handle scheme turns a redundant read into a stale handle. `refreshSnapshot` in
[`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift)
advances a monotonic `generation` counter on every call, clears `currentElements`, and mints
`h-<generation>-<index>` for each element, so a handle from one snapshot is invalidated by the very
next snapshot regardless of whether the screen changed. The only trigger for a new snapshot is a
`GET /elements`, and the backend issues one `GET /elements` (through `_resolve_handle`) followed by
one `POST /tap` (through `_actuate`) per handle-based step, so under normal timing the generation
does not move between the resolve and the actuation. A redundant `GET /elements` is what moves it.

The redundant read comes from the transport-retry seam. `_is_retry_eligible` in
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) returns true for every `GET`
(`return not delivered or method == "GET"`), because a read is idempotent and BE-0207 added the seam
precisely to smooth a sub-second transport blip. When a `GET /elements` looks like it timed out on
the client, `_with_retry` re-sends it. Right after a large screen transition the runner's main
thread is busy re-snapshotting the settling screen, which is exactly when a read is slow enough to
look blipped. The two effects compound into the stale handle:

1. The first `GET /elements` is judged a transport failure on the client and `_with_retry` re-sends
   it.
2. The client resolves the selector against the re-sent read's response (generation N+1) and issues
   `POST /tap` with the handle the runner minted in generation N+1.
3. The first read was in fact still running on the busy runner, and it completes and runs
   `refreshSnapshot` *after* the re-send — advancing the generation to N+2 and replacing
   `currentElements` wholesale.
4. The generation-N+1 handle the client sent is no longer a key in `currentElements`, so the runner
   returns `stale`, and the backend raises the vanished-element error — for a button that never left
   the screen.

BE-0289's re-resolution retry cannot recover this case, and the reason motivates a different fix.
That retry re-queries and re-actuates while the selector still resolves uniquely,
which assumes the stale handle was caused by a screen change that a fresh query will reflect. Here
the screen did not change; the runner is simply busy, and every re-query the retry issues is itself
a `GET /elements` exposed to the same re-send-and-interleave race. While the runner stays busy — the
window right after a transition — all three of BE-0289's bounded attempts can stale for the same
non-reason, and the step fails. The two items address different layers: BE-0289 tolerates a genuine
snapshot-version race on the client, whereas this item removes the version from the handle so an
unchanged element is never assigned a new handle in the first place.

The flake is expensive in the same three ways BE-0289 documents. A required on-device gate that
fails at random erodes trust in the gate and invites either a reflexive re-run or a wasted
investigation; every re-run burns metered macOS-runner minutes on a job that builds the app and
boots a Simulator; and the `element vanished` message actively misleads, because it reads as though
the element is missing when the element is present the whole time.

## Detailed design

The work splits into four units. Unit 1 changes how a handle is derived; Unit 2 preserves the
`stale`-versus-`notFound` distinction that BE-0289's retry depends on; Unit 3 proves the change
off-device; Unit 4 keeps the documentation in step. The units are ordered by dependency but can land
as separate pull requests.

**Unit 1 — Derive the handle from stable element identity, not from a generation counter.** In
`SnapshotStore`, replace the `h-<generation>-<index>` scheme with a handle derived deterministically
from the element's stable identity — its `identifier`, `label`, and `traits`. The handle keeps its
`h-` prefix as an opaque-handle namespace, so only the part after the prefix changes — from a
generation and index to an identity encoding (e.g. `h-<identity-hash>`); the prefix is deliberately
preserved so nothing downstream that keys off it (a test, a log filter) breaks. Three fields the
snapshot also carries are deliberately excluded from the derivation. The `backingElement` is excluded
because every `queryElements` call returns a fresh object reference for the same on-screen element, so
including the reference would make the handle change on every snapshot — the very defect this item
removes. The `value` and the `frame` are both excluded because both change while a screen settles,
which is exactly when the runner re-snapshots: a `frame` shifts by a sub-pixel as a transition
animates, and a `value` ticks as a loading label resolves, a countdown counts, or a spinner spins.
Including either would give one unchanged element a new handle on each of two interleaved reads and
reopen the race in the window this item targets, and neither buys actuation precision, because a
handle resolves to a `backingElement` and the runner acts through that reference, never through the
stored `value` or `frame`. The choice is not new to the package: `attributesMatch` in
[`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)
already defines element identity as `identifier` / `label` / `traits`, excluding `value` and `frame`
for the same settling reason ([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)),
so the handle reuses the package's existing identity notion rather than inventing a second
one. `refreshSnapshot` still clears and rebuilds `currentElements` on every call, so the stored
`backingElement` a handle resolves to is always the latest query's reference; the only change is that
the *key* is now the identity handle, so an element with unchanged identity maps to the same handle
string across snapshots. A redundant `GET /elements` therefore re-issues the identical handle for an
unchanged element, and the interleaving in *Motivation* can no longer strand it: whichever snapshot
the client's handle came from, the handle is still a live key so long as the element is on screen. Two
on-screen elements can share identical identity (two buttons both labeled `Delete` with no
identifier, say); to keep their handles distinct, append an occurrence index to collisions *within a
single snapshot* in query order, so the first occurrence keeps the bare identity handle and later
occurrences take a suffixed variant.

**Unit 2 — Preserve the `stale`-versus-`notFound` distinction.** BE-0289's retry branches on the
runner's reply: a `stale` reply triggers a re-resolution, whereas a `not-found` reply fails
immediately, so the two must stay distinguishable after the handle scheme changes. The generation
counter is what tells them apart today — `lookup` parses the handle, and a well-formed handle whose
generation is not current reads as `stale` while an unparseable handle reads as `notFound`.
Identity-derived handles carry no generation to parse, so `SnapshotStore` records the set of handles it has ever
issued and `lookup` returns `.found` when the handle is a live key, `.stale` when the handle was
issued before but is not a live key (the element it named has left the screen or changed identity),
and `.notFound` when the handle was never issued (a malformed or fabricated string). The runner's
HTTP router keeps its existing three-way branch over the result, and BE-0289's driver-side retry
keeps working unchanged: a real disappearance now maps a once-issued handle to `.stale` exactly as
before, so the retry re-resolves, finds the selector no longer resolves, and fails loudly.

**Unit 3 — Prove the change off-device.** Cover `SnapshotStore` with Swift unit tests in
[`BajutsuKit/Tests/BajutsuRunnerTests/SnapshotStoreTests.swift`](../../BajutsuKit/Tests/BajutsuRunnerTests/SnapshotStoreTests.swift),
which run in the runner's off-device test suite with no Simulator. The central addition is the
regression this item exists to prevent: refreshing the same unchanged snapshot two or more times,
then resolving the *first* refresh's handle to `.found` — the case the old generation scheme fails,
because the old scheme stales that handle on the second refresh. A companion test pins the in-snapshot
tiebreak, resolving two identity-identical elements in one snapshot to distinct handles. The existing
tests keep their current form and keep passing under the new scheme, and the suite depends on that: a
refresh with a *different* element still stales the first element's handle (a genuine disappearance,
`.stale`), a never-issued handle still reads `.notFound`, an issued handle still carries the `h-`
prefix, and the concurrency test still passes — so the identity derivation neither breaks the
`.stale` / `.notFound` contract Unit 2 preserves nor reintroduces a data race.

**Unit 4 — Keep the documentation in step (BE-0113).** The `SnapshotStore` class comment states
today that "Each `refreshSnapshot` replaces the previous snapshot: all prior handles become stale",
which is the exact behavior this item changes; rewrite the comment to describe identity-derived
handles and the condition under which a handle is still stale (its element left the screen or changed
identity). The Python side of the same channel narrates the old model too and must move with the code
(cross-file drift is what BE-0113 forbids): the [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)
module docstring calls the handle a "per-snapshot handle" that "can go stale when the screen
re-snapshots between resolve and actuate", and [`bajutsu/drivers/xcuitest_live.py`](../../bajutsu/drivers/xcuitest_live.py)
twice calls the WebDriver element id the runner's "per-snapshot handle"; realign all three to the
identity-derived model (stable across a re-snapshot of an unchanged screen). Update
[`DESIGN.md`](../../DESIGN.md) and [`docs/architecture.md`](../../docs/architecture.md) if either
describes the runner's handle staleness semantics, so the prose account matches the behavior.

Two follow-ups are known and deliberately left out of scope. The set of ever-issued handles grows
for the life of the runner process, so a very long run accumulates handle strings monotonically; the
growth is small (one short string per distinct element ever seen) and a run is process-scoped, but a
bound — retaining only the handles from the last few snapshots — can be added later if a measurement
shows it matters. The occurrence-index tiebreak for identity-identical elements assumes the query
order of identical elements is stable across snapshots; a screen that reorders visually identical
elements between snapshots could still reassign their handles, but such a screen is rare and its
elements are indistinguishable to a selector anyway, so closing that gap is deferred until a real
scenario needs it.

## Alternatives considered

**Make `GET /elements` non-retry-eligible so no redundant read is sent.** Removing the read from
BE-0207's transport-retry seam would delete the specific trigger in *Motivation*, but at the cost of
re-opening the transient-blip flake BE-0207 shipped to close: a genuine sub-second transport blip on
a read would once again fail the step outright. The retry is also not the only conceivable source of
an extra snapshot — any future runner behavior that re-snapshots would reintroduce the flake — so
removing the retry treats one symptom rather than the cause. Identity-derived handles make a
redundant snapshot harmless regardless of where the snapshot comes from.

**Rely on BE-0289's re-resolution retry alone.** *Motivation* shows why the retry cannot recover this
case: it presumes a screen change a fresh query will reflect, whereas here the screen is unchanged and
every re-query is itself exposed to the same re-send race, so all bounded attempts can stale for the
same non-reason. The two items are complementary, not substitutes — BE-0289 tolerates a real race on
the client, and this item removes the manufactured race at the source.

**Keep a bounded window of recent generations valid instead of just the latest.** Treating handles
from the last few generations as live would paper over the interleaving without changing the scheme,
but it only widens an arbitrary window rather than fixing the mismatch: a handle should be valid
exactly while its element is on screen, which is a statement about identity, not about how many
snapshots have elapsed. Deriving the handle from identity states that invariant directly, and it needs no tuning
parameter that a slower runner could outrun.

**Include the element's `frame` or `value` in the handle key.** Keying on position or on the live
value as well would let two identity-identical elements take distinct handles without an occurrence
index, but it reopens the exact race this item closes: a screen settling after a transition
re-snapshots repeatedly, and either a `frame` that shifts or a `value` that ticks (a loading label, a
countdown, a spinner) between two interleaved reads gives one unchanged element two handles, so the
redundant read strands the handle again in the very window the item targets. The occurrence-index
tiebreak already distinguishes identity-identical elements within a snapshot, and neither field buys
actuation precision because the runner acts through the `backingElement`, not the stored `value` or
`frame`; both earn nothing the design needs at the cost the design exists to avoid. The package's
`attributesMatch` (BE-0287) already excludes both for the same reason.

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

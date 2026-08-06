**English** · [日本語](BE-XXXX-adb-settle-proven-key-ja.md)

# BE-XXXX — Gate the adb settle poll's fast path on a key it actually proved stable

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-settle-proven-key.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1527](https://github.com/bajutsu-e2e/bajutsu/pull/1527) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

`AdbDriver._settle()` (`bajutsu/drivers/adb.py`) decides whether an Android screen has stopped
moving before an actuator resolves a coordinate from it. Its fast path trusted a bare match against
`_last_stable_key` — a cache that any `query()` call overwrites, from any caller — as proof of rest.
This item adds a second, narrower cache, `_settled_key`, that only holds a key `_settle` (or the
catch-up barrier's own dwell proof) has actually verified is stable, and gates the fast path on that
key instead. It also adds a `rawTree` capture kind so a future recurrence of this class of flake can
be diagnosed from the raw device dump directly, rather than by re-deriving it from `elements.json`
and screenshots across several runs.

## Motivation

Diffing a failing and a passing run of the `gestures` scenario's `long-press and double-tap targets
mirror their result` case turned up a genuine flake: `long_press` reported its gesture as delivered
(`accepted: true` from the resident actuation channel), yet the app's mirrored state never left
`idle`. The failing run's `log.longpress` frame was identical across four consecutive steps
(`wait`, `assert`, `long_press`, `double_tap`); the passing run's same frame moved between its
`assert` and its `long_press` — a further shift with no scenario-level gesture in between, meaning
the underlying scroll was still settling well after the `assert` step had already read it.

`_settle()`'s own comment describes the intended contract: "a stable screen still settles in a
single read (the first `query()` matches the cached key); only a genuinely-animating screen polls."
The cache it checked against, `_last_stable_key`, is written by `_record_tree`
(`bajutsu/drivers/coordinate_tree.py`) on *every* `query()` call, not only ones that survived
`_settle`'s own two-consecutive-equal-reads discipline — a `wait` step's internal poll (which stops
the moment its one awaited element matches, regardless of what the rest of the tree is doing) or a
bare `assert`'s single read write it exactly the same as `_settle`'s own poll does. So the fast path's
real test was never "has `_settle` proved this key stable" — it was "did the single most recent read
from anywhere happen to match this one" — and a torn or mid-fling read that coincidentally repeated
itself once could pass for genuine rest.

That the tree can genuinely misreport a rest state mid-transition is not a hypothesis: the passing
run's own `log.doubletap` frame was `[42, 2102, 996, 25]` — a wrong height and an order inverted
relative to a sibling element — for two consecutive reads before it corrected to `[42, 1686, 996,
63]`. `_advance_catchup`'s own docstring already names this shape: "Android republishes node bounds
one node at a time... a read taken mid-catch-up can be torn." The failing run's frozen frame across
four steps is consistent with the same class of read landing on a transient snapshot and never being
re-verified before the touch fired.

## Detailed design

### 1. A provenance-tracked settle cache

`AdbDriver` gains `self._settled_key: StableKey | None`, deliberately separate from
`_last_stable_key` (which other consumers — `_pan_baseline`, `_device_act`'s catch-up baseline,
`_advance_catchup`'s own change detection — still read as "the most recent observation," a
legitimate use this item leaves untouched). `_settle()` no longer captures `prev_key =
self._last_stable_key` before its own fresh read; it compares the fresh read's key against
`_settled_key` instead, and only takes the immediate-return fast path when that key is not `None` and
matches.

`_settled_key` is written in exactly two places, both of which are genuine two-observations-apart
proofs of rest rather than a bare single-read coincidence:

- Inside `_settle()`'s own poll, when two consecutive reads agree — the existing discipline,
  unchanged, now additionally recorded as trustworthy for a later call.
- Inside `_advance_catchup`'s projection-dwell branch (the `uiautomator dump` path with no device
  event mark), when the dwell requirement closes the barrier. The dwell itself already requires a
  changed projection to hold for `_CATCHUP_DWELL_S` before it closes, which is the same
  two-observations-apart shape `_settle`'s own poll relies on, just built from the reads a `wait` or
  `assert` already took rather than a poll of `_settle`'s own. This is what keeps the existing
  `test_reads_the_runner_already_takes_close_the_barrier_for_free` case free of extra polling: the
  runner's own intervening reads still earn the fast path, because they now leave behind a key marked
  as proven, not merely observed.

`_advance_catchup`'s **mark-postdate** branch (the resident channel) deliberately does *not* set
`_settled_key`. A device-clock mark postdating the actuation proves order — this read is not stale
relative to the gesture — never rest: a fling can keep publishing well past the first read that
postdates it. Crediting that close as "proven" would have reopened exactly the gap this item closes,
on the resident channel specifically, which is the channel the diagnosed flake ran on
(`"via": "identity"` in the failing run's own `manifest.json`).

`_settled_key` resets to `None` whenever the poll runs out its wall-clock budget without two reads
ever agreeing, so a later call cannot treat wherever it happened to land as proven either.

### 2. A `rawTree` capture kind

`_describe()` (`bajutsu/drivers/adb.py`) previously parsed the raw dump text and discarded it as a
local variable. `AdbDriver` now keeps the text behind its last read (`base.RawSource`, exposed
through the new `base.RawSourceProvider` protocol — the same narrow, `runtime_checkable`, opt-in
pattern as `ViewportProvider` / `ReadLagProvider` / `SettledReadProvider`), including the resident
channel's pre-`narrow_to_active_window` body when narrowing changed something. `write_raw_tree`
(`bajutsu/evidence/core.py`) writes `hierarchy.raw.xml` and, when present, `hierarchy.pre-narrow.xml`
under a step's directory; `capture()` gains a `rawTree` branch, and the scenario capture-token
grammar (`bajutsu/scenario/models/_base.py`) accepts it. It is never in `Defaults.capture` — a
scenario opts in with `capture: [rawTree, ...]`, since it adds a same-sized text artifact per
captured step. This does not fix the cache bug above; it exists so that if a mismatch between a
resolved coordinate and the real screen recurs, the raw device dump behind the mismatch is available
directly, rather than requiring the multi-run screenshot-and-`elements.json` forensics this
investigation needed.

### 3. Two smaller, related observations

Investigating the tree-generation pipeline for a processing bug (as opposed to a timing one) turned
up no scaling, clipping, or z-order logic to be wrong — `_bounds()` is a single regex extracting UI
Automator's `bounds="[l,t][r,b]"` attribute — but two related gaps were worth closing in the same
pass:

- `_bounds()` silently defaulted a malformed (present but unparseable) `bounds` attribute to
  `(0.0, 0.0, 0.0, 0.0)`, indistinguishable from a node that genuinely carries no such attribute (a
  fine, expected case). It now logs a warning in the malformed case only, leaving the genuinely
  absent case silent.
- `narrow_to_active_window` (`bajutsu/adb_resident.py`) filters SystemUI decor windows by package
  name; it has no notion of "the one active window" among several non-SystemUI windows, a gap its own
  docstring already named for the permission-dialog case. A characterization test now pins the
  current behavior (both windows survive narrowing) so a future change to it shows as a diff here
  rather than silently.

### Machine-checkable outcome

`tests/test_adb.py::test_settle_does_not_trust_a_coincidental_match_with_no_catchup_pending`
reproduces the cache-coincidence bug directly against a fake `run` sequence and fails against the
pre-fix code; `test_settle_fast_path_trusts_a_key_it_proved_itself`,
`test_settled_key_resets_when_the_poll_never_converges`,
`test_catchup_dwell_close_sets_the_settled_key`, and
`test_catchup_mark_postdate_close_does_not_set_the_settled_key` pin the rest of the new state
machine. The full existing `_settle` / catch-up suite (including
`test_reads_the_runner_already_takes_close_the_barrier_for_free`) passes unchanged, confirming the
existing free-settle optimization survives. `tests/test_evidence.py` and
`tests/test_adb_resident.py` cover `write_raw_tree`'s redaction and no-op behavior, the pre-narrow
body's presence/absence, and the multi-window characterization. `make check` is the judge; nothing
here touches a verdict path (prime directive 1).

## Alternatives considered

**Disable the fast path outright, always polling at least once.** Rejected: this is what an earlier
draft of this fix did, and it broke
`test_reads_the_runner_already_takes_close_the_barrier_for_free` — a deliberate, already-shipped
optimization where the runner's own intervening reads (a `wait`, an `assert`) satisfy the dwell
requirement and the next actuator should pay nothing extra. The provenance-tracked key preserves that
optimization for the case it was built for while closing the gap for the case it was not.

**Have the mark-postdate catch-up close also set `_settled_key`.** Rejected: a device-clock mark
postdating a gesture proves order, not rest, and the diagnosed flake ran on exactly this channel
(the resident server, `via: identity`). Trusting a single postdating read as "proven stable" would
have reopened the same gap this item closes, one call earlier.

**Reconstruct settledness from timing (a minimum wall-clock gap between the two agreeing reads)
instead of tracking a separate key.** Considered, but does not fix the diagnosed case with any more
confidence than the chosen design, adds a timestamp field to track, and does not compose as cleanly
with the catch-up barrier's own, already-validated dwell proof.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `_settled_key`, the rewritten `_settle()` fast path, and the two `_advance_catchup`
      write sites (dwell proves rest, mark-postdate does not).
- [x] Unit 2 — the `rawTree` capture kind: `base.RawSource` / `RawSourceProvider`, `AdbDriver`
      retaining the raw dump and the resident channel's pre-narrow body, `write_raw_tree`, the
      `capture()` branch, and the scenario capture-token grammar.
- [x] Unit 3 — `_bounds()` warns on a malformed (not merely absent) `bounds` attribute; a
      characterization test pins `narrow_to_active_window`'s current multi-window behavior.
- [x] Unit 4 — deterministic coverage for all three units, and `docs/evidence.md` /
      `docs/ja/evidence.md` updated for the new capture kind.

## References

- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident UI Automator server and the wall-clock-bounded `_settle` poll this item refines.
- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — the read-lag catch-up barrier
  whose dwell-close and mark-postdate-close this item distinguishes for the first time.
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md) — the
  device-side actuation channel (`_device_act`) the diagnosed flake's `long_press` went through.
- [BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) — the actuation record whose
  `manifest.json` data (frame, `via`, `accepted`) made this diagnosis possible without raised log
  levels.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence subsystem the `rawTree` kind joins.

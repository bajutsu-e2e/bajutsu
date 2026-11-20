**English** · [日本語](BE-0351-adb-settle-proven-key-ja.md)

# BE-0351 — Gate the adb settle poll's fast path on a key it actually proved stable

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0351](BE-0351-adb-settle-proven-key.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0351") |
| Implementing PR | [#1527](https://github.com/bajutsu-e2e/bajutsu/pull/1527) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

`AdbDriver._settle()` (`bajutsu/drivers/adb.py`) decides whether an Android screen has stopped
moving before an actuator resolves a coordinate from it. Its fast path trusted a bare match against
`_last_stable_key` as proof of rest. Any `query()` call, from any caller, overwrites that cache.

This item adds a second, narrower cache, `_settled_key`. It holds a key only after `_settle` (or the
catch-up barrier's own dwell proof) has verified that the key is stable. The fast path now gates on
`_settled_key`.

The item also adds a `rawTree` capture kind. A future recurrence of this class of flake can then be
diagnosed from the raw device dump directly. Without it, the diagnosis means re-deriving the dump
from `elements.json` and screenshots across several runs.

## Motivation

Diffing a failing and a passing run of the `gestures` scenario's `long-press and double-tap targets
mirror their result` case turned up a genuine flake. `long_press` reported its gesture as delivered
(`accepted: true` from the resident actuation channel), yet the app's mirrored state never left
`idle`.

- In the failing run, the `log.longpress` frame was identical across four consecutive steps (`wait`,
  `assert`, `long_press`, `double_tap`).
- In the passing run, the same frame moved between its `assert` and its `long_press`. That is a
  further shift with no scenario-level gesture in between, so the underlying scroll was still
  settling well after the `assert` step had read it.

The comment in `_settle()` describes the intended contract: "a stable screen still settles in a
single read (the first `query()` matches the cached key); only a genuinely-animating screen polls."
The cache it checked, `_last_stable_key`, is written by `_record_tree`
(`bajutsu/drivers/coordinate_tree.py`) on *every* `query()` call. It is written even by calls that
did not survive `_settle`'s two-consecutive-equal-reads discipline:

- A `wait` step's internal poll stops the moment its one awaited element matches, regardless of what
  the rest of the tree is doing.
- A bare `assert` takes a single read.

Both write the cache exactly as `_settle`'s own poll does. The fast path's real test was therefore
never "has `_settle` proved this key stable". It was "did the single most recent read from anywhere
happen to match this one". A torn or mid-fling read that coincidentally repeated itself once could
pass for genuine rest.

The tree can genuinely misreport a rest state mid-transition. In the passing run, the `log.doubletap`
frame was `[42, 2102, 996, 25]` for two consecutive reads. That frame has a wrong height and an order
inverted relative to a sibling element. It then corrected to `[42, 1686, 996, 63]`.
`_advance_catchup`'s docstring already names this shape: "Android republishes node bounds one node at
a time... a read taken mid-catch-up can be torn." The failing run's frozen frame across four steps
fits the same class of read. The read landed on a transient snapshot and was never re-verified before
the touch fired.

## Detailed design

### 1. A provenance-tracked settle cache

`AdbDriver` gains `self._settled_key: StableKey | None`. It stays deliberately separate from
`_last_stable_key`. Other consumers still read `_last_stable_key` as "the most recent observation",
a legitimate use that this item leaves untouched:

- `_pan_baseline`
- the catch-up baseline in `_device_act`
- the change detection in `_advance_catchup`

`_settle()` no longer captures `prev_key = self._last_stable_key` before its own fresh read. It
compares the fresh read's key against `_settled_key` instead. It takes the immediate-return fast path
only when `_settled_key` is not `None` and matches.

`_settled_key` is written in exactly two places. Both are genuine two-observations-apart proofs of
rest, not a bare single-read coincidence:

- Inside `_settle()`'s own poll, when two consecutive reads agree. This is the existing discipline,
  unchanged, now also recorded as trustworthy for a later call.
- Inside `_advance_catchup`'s projection-dwell branch (the `uiautomator dump` path with no device
  event mark), when the dwell requirement closes the barrier. The dwell requires a changed
  projection to hold for `_CATCHUP_DWELL_S` before it closes. That is the same
  two-observations-apart shape `_settle`'s own poll relies on, built from the reads a `wait` or
  `assert` already took instead of a poll of `_settle`'s own. This keeps the existing
  `test_reads_the_runner_already_takes_close_the_barrier_for_free` case free of extra polling. The
  runner's own intervening reads still earn the fast path, because they now leave behind a key marked
  as proven, not merely observed.

`_advance_catchup`'s **mark-postdate** branch (the resident channel) deliberately does *not* set
`_settled_key`. A device-clock mark that postdates the actuation proves order: the read is not stale
relative to the gesture. It never proves rest, because a fling can keep publishing well past the
first read that postdates it. Crediting that close as "proven" would reopen exactly the gap this item
closes. It would reopen it on the resident channel, which is the channel the diagnosed flake ran on
(`"via": "identity"` in the failing run's `manifest.json`).

`_settled_key` resets to `None` whenever the poll runs out its wall-clock budget without two reads
agreeing. A later call then cannot treat wherever the poll happened to land as proven either.

`_settled_key` is invalidated, not just written. Every actuation clears it, since a key proven stable
before the actuation describes a screen that the actuation may have just changed.

- `_act()` (every gesture), `_device_act()`'s success/uncertain branches, and `type_text()` each
  used to reset it inline. A self-review round found the last two gaps, and they were closed the same
  way as the two write sites above.
- They now share one method, `AdbDriver.invalidate_settled_cache()`. The method also resets
  `_tree_current` and `_read_ordered`, the two other per-actuation caches that this reset already
  covered. A future actuator therefore gains all three invalidations by construction, not by copying
  the pattern correctly.

A screen can also change outside the driver's own actuators. An app relaunch and a crawl reset both
replace the screen through `adb.Env` directly, never through `AdbDriver`. Nothing upstream would
otherwise know to distrust a key proved on the screen before the replacement. The new screen's
projection can coincide with the stale key. That is unremarkable, because many scenarios start and
end on the same home screen. `_settle`'s fast path would then trust a single read of a screen it
never proved at rest. This is the same class of bug the item exists to close, reached through a door
outside the driver's own actuators.

`base.SettledCacheInvalidator` closes that door. It is a `runtime_checkable` `Protocol` exposing
`invalidate_settled_cache()`, the same narrow opt-in shape as `RawSourceProvider` below. It lets
`AdbDriver` publish the method to a caller that has no other reason to import the concrete driver.
`AndroidEnvironment.relauncher()` and `crawl_reset()`
(`bajutsu/platform_lifecycle/environments/android.py`) each call it right after `adb.Env` replaces
the screen.

### 2. A `rawTree` capture kind

`_describe()` (`bajutsu/drivers/adb.py`) used to parse the raw dump text and discard it as a local
variable. `AdbDriver` now keeps the text behind its last read as `base.RawSource`. The text is exposed
through the new `base.RawSourceProvider` protocol, the same narrow, `runtime_checkable`, opt-in
pattern as `ViewportProvider`, `ReadLagProvider`, and `SettledReadProvider`. When narrowing changed
something, the kept text includes the resident channel's body from before
`narrow_to_active_window`.

`write_raw_tree` (`bajutsu/evidence/core.py`) writes `hierarchy.raw<suffix>` under a step's
directory. When the parsed-input body is present, it also writes `hierarchy.parsed-input<suffix>`.
`<suffix>` is `base.RawSource.suffix`, the backend's own dump format: `.xml` for adb, and `.json` for
XCUITest's undecoded `GET /elements` body.

`capture()` gains a `rawTree` branch, and the scenario capture-token grammar
(`bajutsu/scenario/models/_base.py`) accepts the token. `rawTree` is never in `Defaults.capture`. A
scenario opts in with `capture: [rawTree, ...]`, because it adds a same-sized text artifact per
captured step.

This does not fix the cache bug above. It exists for the next time a resolved coordinate mismatches
the real screen. The raw device dump behind that mismatch is then available directly. The
multi-run screenshot-and-`elements.json` forensics this investigation needed are no longer required.

Four smaller correctness properties keep the raw dump paired with the right `elements.json`, or
withhold it rather than mismatch it:

- **Capture order.** `capture()` sorts `rawTree` after any kind that can trigger its own read.
  `elements` is such a kind, because its writer queries the driver itself when no tree was already
  read. `sorted` is stable, so no other kind's relative order moves. `hierarchy.raw<suffix>` then
  always describes the same read `elements.json` came from, whatever order a scenario lists the
  tokens in.
- **Capture timing.** A step's pre-step baseline (`screenshot.before` + `elements`, written before
  the step acts) also writes `rawTree` when the step's own inline `capture` names it. Deferring it to
  the post-step capture would pair the dump with the wrong read. `write_raw_tree` persists the
  driver's *last* read, and that read is the pre-action one that the baseline's `elements` token
  already wrote, not this step's own action. Deferring would pair the dump with a post-action read.
  The sort above rules out this mismatch for capture *order* but not for capture *timing*.
- **`web` blocks.** Inside a `web` block, the post-step capture always targets the native driver,
  because a `WebContextDriver` cannot screenshot. `elements.json` there is written from the *web*
  driver's tree. A `rawTree` request is dropped there rather than serviced. The native driver's dump
  would describe an unrelated backend and an unrelated read, which is worse than no artifact.
- **Label redaction.** `write_raw_tree` refuses the artifact outright when `redactor.has_label_rules`
  is true (`redact.labels` configured). It logs why and writes neither file. `redact_elements` masks a
  labeled element's `value` structurally, using the parsed tree. The raw dump is free text with no
  such structure. `redact_text`'s key/literal-value masking alone would ship an unmasked superset of
  what `elements.json` just masked. Every other redaction rule applies to the dump as free text and
  leaves it enabled.

### 3. Two smaller, related observations

The investigation also examined the tree-generation pipeline for a processing bug, as opposed to a
timing one. It found no scaling, clipping, or z-order logic to be wrong. `_bounds()` is a single
regex that extracts UI Automator's `bounds="[l,t][r,b]"` attribute. Two related gaps were still worth
closing in the same pass:

- `_bounds()` silently defaulted a malformed (present but unparseable) `bounds` attribute to
  `(0.0, 0.0, 0.0, 0.0)`. That default is indistinguishable from a node that genuinely carries no such
  attribute, a fine and expected case. `_bounds()` now logs a warning in the malformed case and stays
  silent when the attribute is genuinely absent.
- `narrow_to_active_window` (`bajutsu/adb_resident.py`) filters SystemUI decor windows by package
  name. It has no notion of "the one active window" among several non-SystemUI windows, a gap its
  docstring already names for the permission-dialog case. A characterization test now pins the
  current behavior (both windows survive narrowing). A future change to it then shows as a diff
  there instead of passing silently.

### Machine-checkable outcome

`tests/test_adb.py` carries the state machine's tests:

- `test_settle_does_not_trust_a_coincidental_match_with_no_catchup_pending` reproduces the
  cache-coincidence bug directly against a fake `run` sequence. It fails against the pre-fix code.
- `test_settle_fast_path_trusts_a_key_it_proved_itself`,
  `test_settled_key_resets_when_the_poll_never_converges`,
  `test_catchup_dwell_close_sets_the_settled_key`, and
  `test_catchup_mark_postdate_close_does_not_set_the_settled_key` pin the rest of the new state
  machine.
- The full existing `_settle` / catch-up suite passes unchanged, including
  `test_reads_the_runner_already_takes_close_the_barrier_for_free`. That confirms the existing
  free-settle optimization survives.

The other test files pin the surrounding behavior:

- `tests/test_adb_lifecycle.py`'s `test_relauncher_invalidates_the_driver_settled_cache` and
  `test_crawl_reset_invalidates_the_driver_settled_cache` pin the two lifecycle call sites that reach
  `invalidate_settled_cache()` from outside the driver's own actuators.
- `tests/test_evidence.py` and `tests/test_adb_resident.py` cover these behaviors of
  `write_raw_tree`: redaction and no-op behavior, the presence or absence of the parsed-input body,
  the multi-window characterization, and the label-rule refusal. They also cover the same-read
  pairing that `capture()`'s stable sort guarantees regardless of token order.
- `tests/test_capture_firing.py`'s `test_inline_raw_tree_joins_the_pre_step_baseline` and
  `test_inline_raw_tree_is_dropped_inside_a_web_block` pin the run loop's two remaining pairing
  properties. An inline `rawTree` request is serviced pre-action, next to the same read `elements`
  took, not after the action with a mismatched read. It is dropped, not mismatched, when
  `active_driver` is a `web` block's driver rather than the native one `rawTree` reads from.

`make check` is the judge. Nothing here touches a verdict path (prime directive 1).

## Alternatives considered

**Disable the fast path outright, always polling at least once.** Rejected. An earlier draft of this
fix did so, and it broke `test_reads_the_runner_already_takes_close_the_barrier_for_free`. That test
guards a deliberate, already-shipped optimization: the runner's own intervening reads (a `wait`, an
`assert`) satisfy the dwell requirement, and the next actuator should pay nothing extra.

The provenance-tracked key preserves that optimization for the case it was built for, the
`uiautomator dump` fallback. There, the catch-up projection-dwell is a genuine two-observations-apart
proof that sets `_settled_key`. The key also closes the gap for the case the optimization was not
built for.

The key does **not** preserve the optimization on the resident channel. The mark-postdate close never
sets `_settled_key` (the next alternative explains why). `_settle()` therefore pays this rejected
alternative's exact cost there: one confirmatory read plus a `_SETTLE_POLL_S` sleep after every
actuation, even though the fast path itself still exists. The resident channel is the production
path, and the one the diagnosed flake ran on. This item accepts the cost rather than the risk.

**Have the mark-postdate catch-up close also set `_settled_key`.** Rejected. A device-clock mark that
postdates a gesture proves order, not rest, and the diagnosed flake ran on exactly this channel (the
resident server, `via: identity`). Trusting a single postdating read as "proven stable" would reopen
the gap this item closes, one call earlier. It would reopen the gap on the resident channel, in
exchange for the free-settle optimization that the alternative above describes losing there anyway.

**Reconstruct settledness from timing (a minimum wall-clock gap between the two agreeing reads)
instead of tracking a separate key.** Considered. It fixes the diagnosed case with no more confidence
than the chosen design. It also adds a timestamp field to track, and it composes less cleanly with the
catch-up barrier's own, already-validated dwell proof.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `_settled_key`, the rewritten `_settle()` fast path, the two `_advance_catchup` write
      sites (dwell proves rest, mark-postdate does not), and its invalidation seam:
      `AdbDriver.invalidate_settled_cache()` (consolidating `_act()`, `_device_act()`'s
      success/uncertain branches, and `type_text()`), `base.SettledCacheInvalidator`, and the two
      lifecycle call sites outside the driver's own actuators (`AndroidEnvironment.relauncher()`,
      `crawl_reset()`).
- [x] Unit 2 — the `rawTree` capture kind: `base.RawSource` / `RawSourceProvider`, `AdbDriver`
      retaining the raw dump and the resident channel's parsed-input body, `write_raw_tree`, the
      `capture()` branch, and the scenario capture-token grammar. Plus four correctness properties
      keeping the artifact paired with the right `elements.json` or withheld rather than mismatched:
      `capture()`'s same-read stable sort, the run loop capturing an inline request pre-action rather
      than post-action, dropping it inside a `web` block, and `Redactor.has_label_rules` refusing the
      artifact outright when `redact.labels` is configured.
- [x] Unit 3 — `_bounds()` warns on a malformed (not merely absent) `bounds` attribute; a
      characterization test pins `narrow_to_active_window`'s current multi-window behavior.
- [x] Unit 4 — deterministic coverage for all four units, and `docs/evidence.md` /
      `docs/ja/evidence.md` updated for the new capture kind, including the redaction-truncation
      caveat `redact.headers`/`redact.fields` carries for a single-line dump.

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

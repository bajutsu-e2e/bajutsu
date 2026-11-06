**English** · [日本語](BE-0299-settle-value-condition-wait-ja.md)

# BE-0299 — Close the read-race gap in mid-scenario reads and idb's settle timing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0299](BE-0299-settle-value-condition-wait.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0299") |
| Implementing PR | [#1274](https://github.com/bajutsu-e2e/bajutsu/pull/1274) |
| Topic | Driver & backend architecture |
| Related | [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

This item closes two related gaps in how Bajutsu's coordinate-based device backends observe the screen
right after a [step](../../docs/glossary.md#scenario-authoring) changes it. The backends are the iOS
Simulator over idb and Android over adb.

The first gap concerns reads. The `assert` and `extract` of a step each read the accessibility tree
once and never retry. An action may mirror its result into the tree a beat late, as with a
counter reflected into a label or the edited text of a field. The single read can then miss that
result outright.

The second gap concerns the idb [driver](../../docs/glossary.md#driver-backend-actuator-platform). Its
pre-action settle wait is bounded by a fixed number of polls, not by elapsed time. On a slow or loaded
machine, it grants far less margin than the matching Android wait.

Neither gap is new. [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
diagnosed and fixed the same class of race at one read site, the trailing `expect` block of a
scenario. It also converted the Android settle wait from a fixed poll count to a wall-clock deadline.
This item carries both fixes the rest of the way: to every mid-scenario read, and to the idb settle
wait.

## Motivation

Follow-up work on pull request #1221 (bundling the XCUITest Simulator runner) hit both gaps directly,
in CI runs unrelated to that change. Two symptoms appeared.

- A showcase scenario captured the value of a Log tab counter with an `extract` step right after the
  second of two taps. The step got the value from before that tap. The later `assert` step of the
  scenario then failed by comparing the captured value against the live, correct one.
- The iOS Simulator conformance suite of the same commit failed a different assertion on each of three
  consecutive reruns. The first was the text length of a field after a delete. The second was an
  element that the suite expected to be focusable. The third was the field again.

Those three symptoms look unrelated, but one underlying race caused them. They are not three separate
bugs. None of these lanes exercise the changed code. Only the slower, more loaded machine of
continuous integration (CI) reproduced the race, and a local run did not.

Bajutsu already has the right fix for this race, but the fix is not applied everywhere the race can
occur. `_evaluate_expect` (`bajutsu/orchestrator/loop.py:69`) evaluates the trailing `expect` block of
a scenario as a condition wait. It re-reads the tree and re-evaluates the assertions of the block
until they pass or a wall-clock deadline elapses. The docstring of that function, added by BE-0245,
gives the reason: "a value an action mirrors into the tree can land a beat after the action returns."

Every other non-retrying read that follows an action takes a single snapshot. A `wait` step is not
among them. Its own condition wait (`_wait`, `bajutsu/orchestrator/waits.py:137`) already polls
`query()` until satisfied, so it does not inherit this race. Two reads remain:

- A mid-scenario `assert` step reads the tree once (`bajutsu/orchestrator/loop.py:245`).
- An `extract` step reads it once more, through `_ScreenRead` (`bajutsu/orchestrator/loop.py:101`).
  By design, `_ScreenRead` reads at most once per step.

Whichever of these runs right after an action whose result is still propagating inherits the exact
race that `_evaluate_expect` was built to close, without the fix that closes it.

The idb settle gap concerns the polling strategy of `_settle`, not what the two backends share. The
docstring of `bajutsu/drivers/coordinate_tree.py` is explicit that `_settle` itself stays per-backend.
idb bounds its poll by a fixed read count, and adb by a wall-clock deadline. The docstring calls this
"a genuine strategy difference rather than shared tuning". So `IdbDriver` and `AdbDriver` each keep
their own `_settle` today, and this item does not propose hoisting the method into the shared base.

What the two backends do share is the projection that each `_settle` polls against: `_stable_key`
(`bajutsu/drivers/coordinate_tree.py:117`). It reduces the tree to identifier and frame only, and
excludes value, traits, and label by design. An ordinary data update on an otherwise static screen
is then not mistaken for the motion of a real gesture still landing.

BE-0245 converted the strategy of `AdbDriver._settle` from a fixed poll count to a poll bounded by an
eight-second wall-clock deadline (`bajutsu/drivers/adb.py:230`). The reason was specific. A fast read,
such as the resident channel that BE-0245 itself introduced, could otherwise collapse the settle
window and let a still-moving tree pass as settled.

`IdbDriver._settle` (`bajutsu/drivers/idb.py:335`–`336`) kept the strategy that BE-0245 moved away from
on the Android side. It still polls a fixed three times at a 50-millisecond interval. That is a
150-millisecond ceiling, however loaded the machine is, and about 53 times narrower than the
eight-second budget of Android.

A fixed poll count scales no better on idb than it did on adb before BE-0245. Nothing in the
documented reason for keeping `_settle` per-backend argues for leaving one backend on a strategy
already known to run out on a slow machine. Unit 4 below asks idb to adopt the wall-clock shape of
adb while keeping its own `_settle` method. That is the same per-backend split the docstring calls
for.

A settle projection that excludes value already cannot detect a still-propagating value change, which
is the gap addressed above. The second gap is narrower still. It concerns how much margin idb grants
even to the layout stability that its own projection is built to detect.

## Detailed design

Every unit below reuses the pattern that BE-0245 already established, rather than inventing a new one.
The pattern is a bounded poll on a wall-clock deadline. It honors the same wait-floor knob that every
condition wait in the runner already respects, and it never uses a fixed sleep.

### Work breakdown (MECE)

1. **Generalize the poll of `_evaluate_expect` into a shared, reusable retry.** Extract its
   read-evaluate-repeat-until-passed-or-deadline loop out of its current, `expect`-specific shape. Put
   it into a helper parameterized on which assertions to evaluate, so a second call site can reuse it
   without duplicating the loop.

2. **Route a mid-scenario `assert` step through that shared retry.** Replace the single
   `driver.query()` at `bajutsu/orchestrator/loop.py:245` with a call into the helper of unit 1. A
   step-level `assert` then gets the same condition wait that the trailing `expect` block already has,
   instead of judging a value against one snapshot.

   Keep building the same stripped `step_ctx` that this call site already builds before evaluating
   (`bajutsu/orchestrator/loop.py:246`–`249`, BE-0250 Unit 2). A step-level assert drops `visual` and
   `schema` from `ctx`, since no per-step screenshot is taken. `_evaluate_expect` takes `ctx` as a
   plain parameter and does not strip it itself. This unit passes the stripped `step_ctx`
   into the shared helper, not the unstripped `ctx`. Passing `ctx` would reintroduce stale
   `visual` and `responseSchema` input.

3. **Give the read of `extract` the same forgiveness, on its own terms, keyed to the properties it
   actually reads.** An `extract` step has no assertion to satisfy. It copies a value out of the tree
   into a variable, so the pass-or-fail loop of unit 1 does not fit it directly.

   A step's `extract` is not one property but a dict of named extracts (`_run_extract` takes
   `extracts: Mapping[str, Extract]`, `bajutsu/orchestrator/evidence_rules.py:72`). Each extract has
   its own selector and its own property. `el.get(ext.prop)` (`bajutsu/orchestrator/evidence_rules.py:83`)
   reads whatever property that one extract names, and `ext.prop` has three options: `value`, `label`,
   and `identifier`. So `ext.prop` can be `label` just as readily as `value`. The motivating case of
   this item, a counter reflected into a label, extracts `label`, not `value`. A step that names
   several extracts can also pull different properties off different elements.

   Add a parallel read that polls until a projection of the tree stops changing between two
   consecutive reads, or until the same wall-clock deadline elapses. The projection covers identifier,
   frame, and the union of every (selector, `ext.prop`) pair that the `extract` of the step names. It
   is a superset of the settle projection in `bajutsu/drivers/coordinate_tree.py`, keyed to every
   property that any extract of this step reads, not only one. Thread this read into the read of
   `_ScreenRead` (`bajutsu/orchestrator/loop.py:101`), so `extract` consumes the settled value rather
   than whichever value was still propagating when the single read fired.

   `.get()` caches its result on the first call. The choice of this poll must happen when
   `_ScreenRead` is constructed (`bajutsu/orchestrator/loop.py:591`), or on its first call. It keys
   off whether `interp_step.extract` is set. It cannot happen inside the call within the `extract`
   branch (`bajutsu/orchestrator/loop.py:613`). That call runs after a `screenChanged` capture has made
   its unconditional, non-polling call at line 592, which has already populated the cache on a step
   that carries both.

   The same reasoning extends to a non-mutating step (`assert_`, `wait`) that also carries `extract`.
   The read of such a step becomes the seed of `_ScreenRead` before `_ScreenRead` is ever
   constructed. That read is either the read of `assert_` at `bajutsu/orchestrator/loop.py:245` or the
   settled tree of `wait`. No decision made at construction time can retroactively poll a seed that
   already exists. For those step kinds, the property-aware poll belongs at that earlier
   read site, not inside `_ScreenRead`.

   This unit trades away part of why the read of `_ScreenRead` is deferred and taken at most
   once. On adb, a screen read (`uiautomator dump`) is the dominant per-step cost: about 2.4 seconds,
   against 0.1–0.3 seconds for the same read on idb. Polling it until stable can multiply that cost
   per `extract` step on a slow-to-settle screen, up to the same wall-clock deadline. The unit accepts
   that cost only where the `extract` of a step actually consumes the read. The laziness of
   `_ScreenRead` already means that a plain `assert` or `tap` step with no consumer never reads at all.
   The added polling lands merely on the steps that ask for it.

4. **Convert `IdbDriver._settle` to a wall-clock deadline.** Replace its fixed `_SETTLE_MAX_POLLS` /
   `_SETTLE_POLL_S` loop (`bajutsu/drivers/idb.py:393`–`400`) with the same deadline-bounded shape that
   `AdbDriver._settle` already uses (`bajutsu/drivers/adb.py:303`–`311`). The settle wait of idb then
   scales with how slow the machine running it actually is, instead of a fixed budget tuned for a fast
   one. This unit is independent of units 1–3. It fixes the pre-action wait of the driver, not the
   post-action read of the runner.

   Once this unit ships, the two `_settle` methods poll the same `_stable_key` projection on the same
   wall-clock-deadline shape. They differ merely in their deadline and poll-interval constants. That
   is exactly what the docstring of `coordinate_tree.py` calls "shared tuning". It is no longer the
   "genuine strategy difference" that the docstring currently gives as the reason `_settle` stays
   per-backend. This unit updates that docstring in the same change, so it stops asserting a
   difference that this conversion removes.

   Whether to go further and hoist `_settle` itself into `CoordinateTreeDriver` is left to the
   implementation. The `query()` of idb still recovers from an accessibility-bridge wedge
   (`_AX_RESET_RETRIES`) that the `query()` of adb does not. That interaction is worth weighing before
   merging the two methods into one.

5. **Verify against the exact failures this item traces to.** Add a regression test that reproduces
   the `extract`-after-tap race in `tests/test_driver_conformance.py`, the `FakeDriver`-based fast-gate
   suite. The test uses a driver double that mirrors a value into the tree one read late. A double
   fits that suite naturally, while `tests/test_driver_conformance_ondevice.py` drives merely the real
   iOS Simulator backends and never a double.

   Give the conversion of unit 4 the same deterministic proof that BE-0245 added for the analogous adb
   change (`tests/test_adb.py:963`–`1024`). The proof is a `FakeClock`-driven addition to
   `tests/test_idb.py`. It confirms that `IdbDriver._settle` polls past the old fixed-poll cap while the
   tree keeps moving, and gives up at its wall-clock deadline rather than at a fixed count. A fast,
   deterministic test then proves the conversion, not merely on-device reruns.

   Then run the on-device showcase `extract` scenario repeatedly in CI. Run the iOS conformance tests
   that flaked during the motivation of this item (`test_delete_text_reduces_the_field_length`,
   `test_tap_point_focuses_the_field_like_a_semantic_tap`) the same way. The runs confirm that the
   flakes stop reproducing there too. That result alone is not proof specific to units 1–3.

   Both named conformance tests drive the Driver directly: `driver.tap()`, `type_text`, `delete_text`,
   `tap_point`, and a bare `driver.query()` via `field_value` and `_field_center` in
   `tests/driver_conformance.py:59`–`67`. They never go through the `assert` and `extract` steps of
   `bajutsu/orchestrator/loop.py` that units 1–3 change. Unit 4 reaches only the leading
   `driver.tap({"id": FIELD_ID})` that each test performs first. The reason is that `_settle()` is
   invoked solely from `_center()`, the selector-based `tap`/`double_tap`/`long_press` path, and never
   from `type_text`, `delete_text`, `tap_point`, or a bare `query()`. The same holds for `AdbDriver`.

   Whether the widened margin alone is enough to stop these two tests flaking is exactly what the
   repeated CI runs test. The on-device showcase `extract` scenario is the one on-device signal that
   does exercise units 1–3, since running a scenario always goes through the orchestrator.

## Alternatives considered

- **Make the settle projection in `bajutsu/drivers/coordinate_tree.py` include value, traits, and
  label everywhere, instead of adding a separate, property-aware read for `extract`.** Rejected. That
  projection is shared by the pre-motion settle wait of every selector-based action, not merely by the
  post-step read that `extract` consumes. `_settle()` fires only from `_center()`, which backs
  `tap`/`double_tap`/`long_press`, and never from `type_text`, `delete_text`, `tap_point`, or a bare
  `query()`, as unit 5 traces. The docstring states why the projection excludes value, traits, and
  label today: an ordinary data update on an otherwise static screen must not trigger extra polls
  before a selector-based action. Widening the projection would reintroduce exactly that spurious-poll
  cost on every selector-based action, not only on the one step that needed the fix.

- **Duplicate a retry loop at each read call site instead of a shared helper.** Rejected. A
  hand-written poll at the call site of `assert` and another at that of `extract` would let the two
  drift out of sync the next time the shared logic needs a fix. That is the same reason the loop of
  `_evaluate_expect` is worth factoring out once rather than copied.

- **Raise the fixed poll count or interval of `IdbDriver` instead of converting to a wall-clock
  deadline.** Rejected. A larger fixed budget still runs out on a machine slower than whatever
  motivated the new constants. BE-0245 replaced exactly that failure mode on the Android side, with a
  deadline that scales with the machine actually running it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Generalize `_evaluate_expect`'s poll into a shared, reusable retry (`_poll_asserts`).
- [x] Route a mid-scenario `assert` step through that shared retry.
- [x] Give `extract`'s read the same forgiveness, on its own terms (a property-aware settle).
- [x] Convert `IdbDriver._settle` to a wall-clock deadline, matching `AdbDriver._settle`.
- [x] Verify against the exact failures this item traces to (extract-race regression test in
      `tests/orchestrator/test_extract_settle.py`, a `FakeClock`-driven `tests/test_idb.py`
      addition for unit 4; repeated on-device CI runs remain the on-device signal).

## References

[BE-0245 — Resident UI Automator server for adb reads](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py),
[`bajutsu/drivers/coordinate_tree.py`](../../bajutsu/drivers/coordinate_tree.py),
[`bajutsu/drivers/idb.py`](../../bajutsu/drivers/idb.py),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)

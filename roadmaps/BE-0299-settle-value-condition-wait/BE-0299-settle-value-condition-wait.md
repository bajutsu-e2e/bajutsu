**English** · [日本語](BE-0299-settle-value-condition-wait-ja.md)

# BE-0299 — Close the read-race gap in mid-scenario reads and idb's settle timing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0299](BE-0299-settle-value-condition-wait.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0299") |
| Implementing PR | [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN) |
| Topic | Driver & backend architecture |
| Related | [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

This item closes two related gaps in how Bajutsu's coordinate-based device backends — the iOS
Simulator over idb, Android over adb — observe the screen shortly after a
[step](../../docs/glossary.md#scenario-authoring) changes it. First, a step's `assert` and
`extract` each read the accessibility tree exactly once and never retry, so a result the action
mirrors into the tree a beat late — a counter reflected into a label, a field's edited text — can be
missed outright. Second, the idb [driver](../../docs/glossary.md#driver-backend-actuator-platform)'s
pre-action settle wait is bounded by a fixed number of polls rather than by elapsed time, so it grants
far less margin than the equivalent Android wait once the machine running it is slow or loaded.
Neither gap is new: [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
diagnosed and fixed the same class of race at one read site — a scenario's trailing `expect` block —
and converted the Android settle wait from a fixed poll count to a wall-clock deadline. This item
carries both fixes the rest of the way: to every mid-scenario read, and to the idb settle wait.

## Motivation

Follow-up work on pull request #1221 (bundling the XCUITest Simulator runner) hit both gaps directly,
in CI runs unrelated to that change. A showcase scenario's `extract` step captured a Log tab
counter's value right after the second of two taps and got the value from before that tap, not
after it; the scenario's later `assert` step then failed by comparing the captured value against the
live, correct one. The same commit's iOS Simulator conformance suite failed a different assertion on
three consecutive reruns of the same commit — a field's text length after a delete, then an element
the suite expected to be focusable, then the field again — three unrelated-looking symptoms of one
underlying race rather than three separate bugs. None of these lanes exercise the changed code; only
continuous integration (CI)'s slower, more loaded machine reproduced the race, while a local run did
not.

Bajutsu already has the right fix for this race; it is just not applied everywhere the race can
occur. `_evaluate_expect` (`bajutsu/orchestrator/loop.py:69`) evaluates a scenario's trailing
`expect` block as a condition wait: it re-reads the tree and re-evaluates the block's assertions
until they pass or a wall-clock deadline elapses, precisely because — in that function's own
docstring, added by BE-0245 — "a value an action mirrors into the tree can land a beat after the
action returns." Every other non-retrying read that follows an action takes exactly one snapshot
instead — a `wait` step is not among them, since its own condition wait (`_wait`,
`bajutsu/orchestrator/waits.py:137`) already polls `query()` until satisfied and so does not inherit
this race. A mid-scenario `assert` step reads the tree once (`bajutsu/orchestrator/loop.py:245`); an
`extract` step reads it once more, through `_ScreenRead` (`bajutsu/orchestrator/loop.py:101`), which
by design reads at most once per step. Whichever of these runs right after an action whose result is
still propagating inherits the exact race `_evaluate_expect` was built to close, without the fix
that closes it.

The idb settle gap is about the polling strategy `_settle` uses, not about what the two backends
share. `bajutsu/drivers/coordinate_tree.py`'s docstring is explicit that `_settle` itself stays
per-backend — idb bounds its poll by a fixed read count, adb by a wall-clock deadline, "a genuine
strategy difference rather than shared tuning" — so `IdbDriver` and `AdbDriver` each keep their own
`_settle` today, and this item does not propose hoisting the method itself into the shared base.
What the two backends do share is the projection each `_settle` polls against: `_stable_key`
(`bajutsu/drivers/coordinate_tree.py:117`) reduces the tree to identifier and frame only,
deliberately excluding value, traits, and label, so an ordinary data update on an otherwise static
screen is not mistaken for the motion of a real gesture still landing. BE-0245 converted
`AdbDriver._settle`'s own strategy from a fixed poll count to a poll bounded by an eight-second
wall-clock deadline (`bajutsu/drivers/adb.py:230`), specifically because a fast read — the resident
channel BE-0245 itself introduced — could otherwise collapse the settle window and let a
still-moving tree pass as settled. `IdbDriver._settle` (`bajutsu/drivers/idb.py:335`–`336`) kept the
strategy BE-0245 moved away from on the Android side: it still polls a fixed three times at a
50-millisecond interval, a 150-millisecond ceiling regardless of how loaded the
machine running it is — roughly 53 times narrower than the Android side's eight-second budget. A fixed
poll count scales no better on idb than it did on adb before BE-0245, and nothing in the documented
reason for keeping `_settle` per-backend argues for leaving one backend on a strategy already known
to run out on a slow machine; unit 4 below asks idb to adopt adb's wall-clock shape while keeping its
own `_settle` method, the same per-backend split the docstring calls for. A settle projection that
excludes value already cannot detect a still-propagating value change, which is exactly the gap the
paragraph above addresses; this second gap is narrower still, in how much margin idb grants even the
layout stability its own projection is built to detect.

## Detailed design

Every unit below reuses the pattern BE-0245 already established rather than inventing a new one: a
bounded poll on a wall-clock deadline, honoring the same wait-floor knob every condition wait in the
runner already respects, never a fixed sleep.

### Work breakdown (MECE)

1. **Generalize `_evaluate_expect`'s poll into a shared, reusable retry.** Extract its
   read-evaluate-repeat-until-passed-or-deadline loop out of its current, `expect`-specific shape
   into a helper parameterized on which assertions to evaluate, so a second call site can reuse it
   without duplicating the loop.

2. **Route a mid-scenario `assert` step through that shared retry.** Replace the single
   `driver.query()` at `bajutsu/orchestrator/loop.py:245` with a call into unit 1's helper, so a
   step-level `assert` gets the same condition wait the trailing `expect` block already has, instead
   of judging a value against one snapshot. Keep building the same stripped `step_ctx` this call site
   already builds before evaluating (`bajutsu/orchestrator/loop.py:246`–`249`, BE-0250 Unit 2): a
   step-level assert drops `visual`/`schema` from `ctx` since no per-step screenshot is taken, and
   `_evaluate_expect` takes `ctx` as a plain parameter rather than stripping it itself, so this unit
   passes the stripped `step_ctx` into the shared helper — not the unstripped `ctx` — to avoid
   silently reintroducing stale `visual`/`responseSchema` input.

3. **Give `extract`'s read the same forgiveness, on its own terms, keyed to the properties it
   actually reads.** An `extract` step has no assertion to satisfy — it copies a value out of the
   tree into a variable — so unit 1's pass-or-fail loop does not fit it directly. A step's `extract`
   is not one property but a dict of named extracts (`_run_extract` takes
   `extracts: Mapping[str, Extract]`, `bajutsu/orchestrator/evidence_rules.py:72`), each with its own
   selector and its own property: `el.get(ext.prop)` (`bajutsu/orchestrator/evidence_rules.py:83`)
   reads whatever property that one extract names (`ext.prop`'s three options are `value`, `label`,
   and `identifier`), so `ext.prop` can be `label` just as readily as `value` — the item's own
   motivating case, a counter reflected into a label, extracts
   `label`, not `value` — and a step naming several extracts can pull different properties off
   different elements. Add a parallel read that instead polls until a projection of the tree —
   identifier, frame, and the union of every (selector, `ext.prop`) pair the step's `extract` names,
   a superset of the settle projection in `bajutsu/drivers/coordinate_tree.py` keyed to every
   property any of this step's extracts reads, not only one — stops changing between two consecutive
   reads, or the same wall-clock deadline elapses, and thread
   it into `_ScreenRead`'s read (`bajutsu/orchestrator/loop.py:101`) so `extract` consumes the
   settled value rather than whichever one was still propagating when the single read fired. Because
   `.get()` caches its result on the first call, this poll has to be selected when `_ScreenRead` is
   constructed (`bajutsu/orchestrator/loop.py:591`) or on its first call, keyed off whether
   `interp_step.extract` is set — not inside the `extract` branch's own call
   (`bajutsu/orchestrator/loop.py:613`), which runs after a `screenChanged` capture's unconditional,
   non-polling call at line 592 has already populated the cache on a step that carries both. The
   same reasoning extends to a non-mutating step (`assert_`, `wait`) that also carries `extract`:
   its own read becomes `_ScreenRead`'s seed (from `assert_`'s read at `bajutsu/orchestrator/loop.py:245`,
   or `wait`'s settled tree) before `_ScreenRead` is ever constructed, so no decision made at
   construction time can retroactively poll a seed that already exists — for those step kinds the
   property-aware poll has to be applied at that earlier read site instead, not inside `_ScreenRead`.
   This trades away part of the reason `_ScreenRead`'s read is deferred and taken at most once in the
   first place: on adb a screen read (`uiautomator dump`) is the dominant per-step cost, roughly
   2.4 seconds against 0.1–0.3 seconds for the same read on idb, so polling it until stable can
   multiply that cost per `extract` step on a slow-to-settle screen, up to the same wall-clock
   deadline. This unit accepts that cost only where a step's `extract` actually consumes the read —
   `_ScreenRead`'s laziness already means a plain `assert`/`tap` step with no consumer never reads at
   all, so the added polling lands only on the steps that ask for it.

4. **Convert `IdbDriver._settle` to a wall-clock deadline.** Replace its fixed
   `_SETTLE_MAX_POLLS` / `_SETTLE_POLL_S` loop (`bajutsu/drivers/idb.py:393`–`400`) with the same
   deadline-bounded shape `AdbDriver._settle` already uses (`bajutsu/drivers/adb.py:303`–`311`), so
   idb's settle wait scales with how slow the machine running it actually is instead of a fixed
   budget tuned for a fast one. This unit is independent of units 1–3: it fixes the driver's
   pre-action wait, not the runner's post-action read. Once it ships, the two `_settle` methods poll
   the same `_stable_key` projection on the same wall-clock-deadline shape, differing only in their
   deadline and poll-interval constants — exactly what `coordinate_tree.py`'s docstring calls "shared
   tuning," not the "genuine strategy difference" it currently gives as the reason `_settle` stays
   per-backend. This unit updates that docstring in the same change so it stops asserting a
   difference this conversion removes; whether to go further and hoist `_settle` itself into
   `CoordinateTreeDriver` is left to the implementation, since idb's `query()` still recovers from an
   accessibility-bridge wedge (`_AX_RESET_RETRIES`) that adb's does not, and that interaction is
   worth weighing before merging the two methods into one.

5. **Verify against the exact failures this item traces to.** Add a regression test that reproduces
   the `extract`-after-tap race in `tests/test_driver_conformance.py`, the `FakeDriver`-based
   fast-gate suite, with a driver double that mirrors a value into the tree one read late; a double
   fits that suite naturally, while `tests/test_driver_conformance_ondevice.py` drives only the real
   iOS Simulator backends and never a double. Give unit 4's own conversion the same deterministic
   proof BE-0245 added for the analogous adb change (`tests/test_adb.py:963`–`1024`): a
   `FakeClock`-driven addition to `tests/test_idb.py` confirming `IdbDriver._settle` polls past the
   old fixed-poll cap while the tree keeps moving and gives up at its wall-clock deadline rather than
   at a fixed count, so the conversion is proven by a fast, deterministic test rather than only by
   on-device reruns. Then run the on-device showcase `extract` scenario and the iOS conformance
   suite that flaked during this item's own motivation (`test_delete_text_reduces_the_field_length`,
   `test_tap_point_focuses_the_field_like_a_semantic_tap`) repeatedly in CI to confirm the flakes
   stop reproducing there too — without treating that alone as proof specific to units 1–3. Both
   named conformance tests drive the Driver directly (`driver.tap()`, `type_text`, `delete_text`,
   `tap_point`, and a bare `driver.query()` via `field_value`/`_field_center` in
   `tests/driver_conformance.py:59`–`67`), never through `bajutsu/orchestrator/loop.py`'s
   `assert`/`extract` steps that units 1–3 change. Unit 4 reaches only the leading
   `driver.tap({"id": FIELD_ID})` each test performs first, since `_settle()` is invoked solely from
   `_center()` — the selector-based `tap`/`double_tap`/`long_press` path — never from `type_text`,
   `delete_text`, `tap_point`, or a bare `query()` (the same holds for `AdbDriver`). Whether that
   widened margin alone is enough to stop these two tests flaking is exactly what the repeated CI
   runs test; the on-device showcase `extract` scenario is the one on-device signal that does
   exercise units 1–3, since running a scenario always goes through the orchestrator.

## Alternatives considered

- **Make the settle projection in `bajutsu/drivers/coordinate_tree.py` include value, traits, and
  label everywhere, instead of adding a separate, property-aware read for `extract`.** Rejected:
  that projection is shared by every selector-based action's pre-motion settle wait (`_settle()`
  fires only from `_center()`, backing `tap`/`double_tap`/`long_press` — never `type_text`,
  `delete_text`, `tap_point`, or a bare `query()`, as unit 5 traces), not only the post-step read
  `extract` consumes, and its docstring states the reason it excludes value, traits, and label
  today — an ordinary data update on an otherwise static screen must not trigger extra polls before
  a selector-based action. Widening it would reintroduce exactly that spurious-poll cost on every
  selector-based action, not only the one step that needed the fix.

- **Duplicate a retry loop at each read call site instead of a shared helper.** Rejected: a
  hand-written poll at `assert`'s call site and another at `extract`'s would let the two drift out of
  sync the next time the shared logic needs a fix — the same reason `_evaluate_expect`'s loop is
  worth factoring out once rather than copied.

- **Raise `IdbDriver`'s fixed poll count or interval instead of converting to a wall-clock
  deadline.** Rejected: a larger fixed budget still runs out on a machine slower than whatever
  motivated the new constants, the exact failure mode BE-0245 replaced on the Android side with a
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

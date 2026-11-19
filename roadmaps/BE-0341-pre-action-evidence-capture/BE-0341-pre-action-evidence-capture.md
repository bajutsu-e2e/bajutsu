**English** · [日本語](BE-0341-pre-action-evidence-capture-ja.md)

# BE-0341 — Capture per-step report evidence before the step acts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0341](BE-0341-pre-action-evidence-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0341") |
| Implementing PR | [#1471](https://github.com/bajutsu-e2e/bajutsu/pull/1471) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Every step's report screenshot and element tree are captured after the step has acted, never before.
This item moves that always-on baseline capture ahead of the step's own action. A report then shows,
for each step, the screen the step acted on. It no longer shows the screen the action left behind.

## Motivation

A step's report entry exists to answer one question: what did this step do, and to what? Today the
entry answers a different question. `_handle_action` (`bajutsu/orchestrator/loop.py:864`) runs the
step's body first. `_run_step_body` taps, types, swipes, waits, or asserts (`loop.py:961`). Once
that call returns, the loop builds a `_ScreenRead` (`loop.py:1056`). It hands the resulting tree to
`self.cfg.sink.capture(...)` (`loop.py:1096`). That call writes `elements.json` and takes the
screenshot at that moment. The writers are `write_screenshot` and `write_elements` in
`bajutsu/evidence/core.py`.

The constant `_BASELINE_INSTANT = ("screenshot.after", "elements")` drives this behavior. It lives
in `bajutsu/orchestrator/evidence_rules.py:14` and is unconditional. Every step gets this pair,
regardless of [`capturePolicy`](../../docs/glossary.md#evidence-capturepolicy-trace-triage). The one
artifact that every step carries thus pictures where the tap landed. It does not picture the button
that the step tapped.

Two examples show the cost:

- Take a `tap: { id: settings.reindex }` step. Its screenshot shows the settings screen after the
  tap navigated away. It does not show the row that the step pressed.
- Take a failing `assert`. It may read a value that an earlier step's async update has not yet
  mirrored into the tree. Its artifact then shows a screen that has moved on from the screen the
  assertion read.

A reviewer may ask "why did this scenario tap the wrong row" or "what did the screen look like when
this assertion ran". To answer, the reviewer must read the *previous* step's artifact. The reviewer
then re-derives what was on screen a moment later. The evidence subsystem exists to remove that
reconstruction ([evidence](../../docs/evidence.md)).

The gap is not only the timing default. It is also a standing correctness bug in the modifier that
already claims to solve this problem.
[`docs/evidence.md`](../../docs/evidence.md#evidence-kinds-and-acquisition-timing) documents
`before` / `after` / `around` as real acquisition-timing modifiers. A scenario can already write
`capture: [screenshot.before]`. But `capture()` runs from one call site
(`bajutsu/evidence/core.py:145-180`). That site sits at the single post-step moment described above.

`write_screenshot` calls `driver.screenshot()` at that moment, whichever modifier named the file
(`core.py:170-178`: `name = f"{modifier or 'after'}.png"`). Today `screenshot.before` therefore
yields a `before.png` with the same post-action pixels as `after.png`. The modifier changes the
label and never the timing of the shutter. No code has yet taken a screenshot before a step's
action.

This item covers the artifact that every step already gets unconditionally, the baseline. It also
makes the existing `before` modifier honest wherever it appears. It does not touch how `extract` or
`assert` decide what value or state is *correct*. That machinery reads its own tree through
`_poll_asserts` / `_settle_extract_read`, independently of the report artifact
([BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md),
[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md)). A value must postdate the
action that produced it.

The report artifact answers a different question: what state was this step handed? The two questions
must not be conflated.

## Detailed design

The fix rests on one existing fact. `self.state.prev_after` is the previous step's cached post-step
tree (`loop.py:1102`, BE-0234 Unit 2). The loop maintains it unconditionally today, regardless of
`capturePolicy` or `wants_screen_changed`. This item adds one new, independent use of that tree. The
`screen_changed` logic, the `before`/`wants_screen_changed` gate (`loop.py:899-936`), and the
interrupt guard stay as they are.

The fix must *not* force a fresh device read at the loop level to obtain a pre-action tree.
`tests/orchestrator/test_read_count.py` pins the loop's own laziness. Its
`test_plain_tap_issues_no_runner_read` asserts zero loop-issued `query()` calls for a plain `tap`
with no consumer. The module's docstring exists to catch "a future change that reintroduces a
redundant read."

The existing post-step capture call already respects this. It hands the sink
`elements=screen.cached` (`loop.py:1094`). That value is `None` whenever nothing else has
materialized the tree. The sink then decides whether to query. A `FileSink` queries in real use. The
stub sink of the read-count tests does not, so the tests measure the loop's own behavior alone.

The new pre-step call follows the identical pattern. It passes whatever `prev_after` already holds.
That is real data on every step but the first (or the first after a tree-less one), and `None`
otherwise. The call never queries to fill that gap.

### Work breakdown (MECE)

1. **Add a pre-step capture call, and remove the baseline from the post-step one.** Right before
   `_run_step_body` runs, call the following unconditionally:

   `outcome.artifacts.extend(self.cfg.sink.capture(self.cfg.driver, step_id, ["screenshot.before",
   "elements"], elements=self.state.prev_after))`

   The call extends `outcome.artifacts` as the post-step call does (`loop.py:1095-1097`).
   `manifest.json` then records the new `before.png` / `elements.json` entries for Unit 5 to find.

   The call targets `self.cfg.driver`, not `active_driver`. The existing post-step call makes the
   same choice for the same reason (`loop.py:1090-1092`). Inside a `web` block, `active_driver` is a
   `WebContextDriver`, whose `screenshot()` unconditionally raises `UnsupportedAction`
   (`bajutsu/webview.py:193-194`). Capturing against it would fail every step of every scenario with
   a `web` block.

   Passing `self.state.prev_after` instead of a fresh tree keeps this call's cost identical to
   today's. `write_elements` queries the driver itself when `elements` is `None` (`core.py:69`).
   That mirrors the `screen.cached` argument of the post-step call. A sink that reads nothing pays
   nothing here, and the zero pinned by `tests/orchestrator/test_read_count.py` stays zero.

   The call writes `before.png` and `elements.json` from the pre-action state. These are the two
   artifacts that every step gets today, taken earlier. For `elements`, the tree is also the right
   one whenever one is in hand. The call removes `_BASELINE_INSTANT` from `_collect_captures`
   (`evidence_rules.py:157-168`). That function now returns what the scenario asked for:
   `step.capture` plus matching `capturePolicy` rules. No implicit baseline remains to dedupe
   against.

   **Accepted collateral of the deferred-query design.** The "cost identical to today's" claim holds
   for a sink that does not consume `elements`. It also holds for a consuming sink with no
   `screenChanged` policy. It fails for one narrow combination:

   - a consuming sink (`FileSink`),
   - a `screenChanged`/interrupt policy that needs a fresh `before`,
   - a *native* step, and
   - `prev_after` unset (the scenario's first step, or one right after a tree-less step).

   There, two queries fire against the identical pre-action state. One is the deferred query of
   `write_elements`, which fires because this call passes `elements=None`. The other is the `before`
   computation just below, which fires because `prev_after` is still unset. Today a single post-step
   read feeds both consumers, so this combination costs one query more.

   Closing this gap differs from the `web` block's first-step case described below. It would mean a
   query on every native step whenever a `screenChanged` policy is active, not on the scenario's
   first step alone. The `web` case gates its query on `isinstance(sink, NullSink)`, but the native
   case cannot. That gate would also trigger for `_KindsSink`, the stub in
   `tests/orchestrator/test_read_count.py`. The stub is a non-`NullSink` sink that never queries a
   driver itself, unlike a real consuming sink. A correctness fix would then turn into a test
   regression.

   This item leaves the gap as accepted, narrow collateral rather than reworking it under time
   pressure this late in review. A real fix must reorder the `before` computation ahead of the
   baseline capture for this one combination. A single fresh query then feeds both. That mirrors the
   seed-forward shape of the `web` case in the other direction. The fix is future work, and it does
   not block this item's defect, which is evidence captured pre- versus post-action.

   **Implementation note.** Inside a `web` block, `self.state.prev_after` is reset to `None` around
   the whole block (BE-0234 Unit 2). Left unchanged, that reset would force the fallback of
   `write_elements` to query `self.cfg.driver` (native), not the `WebContextDriver` that a nested
   step ran against.

   The pre-step call therefore resolves `elements` explicitly (`active_driver.query()`) whenever
   `prev_after` is unset and `active_driver is not self.cfg.driver`. It does so behind a best-effort
   `try`/`except (ConnectionError, base.UnsupportedAction, OSError)`. On a torn-down bridge, the
   call skips just this artifact rather than crashing the step before its own action runs.

   The same query also seeds `self.state.prev_after`, not only the local `pre_elements`. Without the
   seed, the `screenChanged`-policy `before` computed just below would see `prev_after` still unset
   on this first nested step. It would pay a second, duplicate query of the same web driver for the
   same pre-action moment.

   The `before` computation also tracks `before_is_fresh`. That flag records whether the tree in
   hand was just read this iteration, or is a snapshot carried over from the previous step's
   boundary. The interrupt guard right after the computation skips its own re-query only when
   `before_is_fresh` is set.

   A local `pre_query_was_fresh` flag feeds `before_is_fresh` when `before` is the tree that this
   call seeded. The flag is set when this call's own `active_driver.query()` succeeds. Otherwise the
   flag stays unset, as before this item. One such case is a step that reuses a current `prev_after`
   from an earlier step. Without the flag, the guard would treat a tree as stale when it need not
   re-read it. The guard would pay a redundant `query_dom()`.

2. **Close the scenario's final-step gap.** Chaining step *i*'s result forward as step *(i+1)*'s
   pre-step baseline (unit 1) leaves exactly one step uncovered: the scenario's last executed step,
   which has no following step to carry its result forward. Left alone, that step's own
   after-the-fact state would never become a default artifact. That would regress from today. Today
   every step, the last included, gets `after.png`.

   The loop tracks the identity of the last *leaf* step as it runs. It bundles that identity into
   one `LastLeafStep(outcome, step_id)` value (`loop.py`), not two parallel `Optional` fields. The
   two parts are then set together by construction. A consumer narrows both with one `is not None`
   check. `StepLoopState` holds a single `last_leaf: LastLeafStep | None` field.

   `_handle_action` constructs the value at the end, next to `self.state.prev_after =
   screen.cached`. Every actuating/`wait`/`assert`/`email` kind flows through `_handle_action`
   (`_run_one`, `loop.py:754-775`). The value is never set for the bookkeeping outcome of an
   `if`/`forEach`/`web` container. It is set for the leaf step that ran last.

   `_handle_action` has exactly one early `return` ahead of that end-of-function assignment. A
   `handleSystemAlert` step whose locale has no covered labels (`UncoveredSystemAlertLocale`) fails
   and returns immediately. That path sets `last_leaf` itself, right where it appends its own
   outcome. Otherwise a scenario that ends on this failure would keep a stale `last_leaf` from an
   earlier step (or none at all, for a single-step scenario).

   Unit 1's pre-step baseline capture runs ahead of this locale resolution too. The capture moved
   there on purpose. It depends on the screen the step is about to act on, not on the step's own
   resolved fields. The failure thus gets the same evidence contract as every other leaf step, not
   the final capture alone. Whatever the sink writes is what lands: a `NullSink` writes nothing, as
   for any other step.

   After the top-level `exec_steps` call returns in `_run_steps`, the loop extends
   `leaf.outcome.artifacts` with one more call, regardless of the outcome:
   `self.cfg.sink.capture(driver, leaf.step_id, ["screenshot.after"])`.

   The call adds a screenshot and never `elements`. `elements.json` has one fixed filename.
   Re-capturing it here would overwrite the pre-action tree with a post-action one. Meanwhile
   `_step_artifacts` in `reads.py` (unit 5) keeps resolving `screenshotUrl` to the *first*-recorded
   screenshot, `before.png`. The overwrite would decouple the screenshot and the tree, which the
   editor's element-picker relies on to describe the same moment. `elements.json` therefore stays
   the pre-action tree for every step, the last included. That keeps the pair consistent throughout
   the run.

   `after.png` is a raw artifact for anyone who reads the manifest directly. Both of today's
   viewers, the HTML report and the serve editor, show the first-recorded screenshot of a step. The
   file is thus not surfaced by default. Making a viewer prefer it for the last step is separate,
   future scope.

   This capture always targets `self.cfg.driver` (native), as unit 1's pre-step call does. A
   screenshot needs no tree at all. Unlike unit 1's call, this capture needs no web-driver re-query
   and no exception guard.

   The capture is gated on the leaf not having recorded an `after.png` yet. A `capturePolicy` rule
   may fire post-step on the same last leaf with `screenshot.after`, or with bare `screenshot`
   (which defaults to `after`). One example is the common safety net `{on: {result: error}, capture:
   [screenshot.after]}` on a failing final step. Such a rule has already written the file. Capturing
   again would overwrite the rule's own shot with a slightly later one (same fixed filename). It
   would also leave a duplicate `screenshot`/`after.png` entry in the leaf's artifacts, for the
   manifest reader named above.

3. **Drop redundant `.before` tokens from the post-step call.** A scenario's inline `capture` or a
   `capturePolicy` rule can still spell `screenshot.before` explicitly. Unit 1's pre-step call has
   already written that file. The post-step list (`loop.py:1093`, fed by `_collect_captures`)
   therefore filters any `screenshot.before` token out before it reaches `sink.capture(...)`. The
   post-step path never re-takes a pixel under that name. This closes the standing bug from
   Motivation for every call site, not only the baseline.

   `screenshot.after`, bare `screenshot` (defaults to `after`), and `screenshot.around` are
   untouched. They keep firing post-step, at the same point as today.

4. **`elements` stays post-step-capable for a rule that asks for it.** `elements` has no acquisition
   modifier (`docs/evidence.md:44-46`). It is one file, `elements.json`. A `capturePolicy` rule may
   also list `elements`, for example the `result: error` "capture the maximum" pattern from
   [evidence.md](../../docs/evidence.md#a-capturepolicy-rule-based). The rule's post-step write
   still fires and overwrites the pre-step one on disk.

   A rule's condition (`screenChanged` / `result: error`) is knowable after the step ran. What the
   rule wants to see is the post-action state. An error rule that shows the screen the step left
   broken is correct, and this item must not suppress it. This item changes the *default*, the
   artifact that every step gets when no rule matches. It never changes a rule's own explicit
   request.

   A scenario author needs to know the precedence. The pre-step baseline is the floor. A firing
   rule's own capture list is the ceiling. Unit 2's final-step capture has the same post-step shape,
   applied unconditionally to one step instead of gated on a rule.

   The same choice has an accepted collateral. `_step_artifacts` in `reads.py` (unit 5) still
   resolves `screenshotUrl` to `before.png`, the *first*-recorded screenshot. A firing rule's own
   `screenshot.after`/`.around` never touches that file. A firing rule that also lists `elements`
   thus leaves the editor's element-picker pairing a pre-action `before.png` with a post-action
   tree.

   Giving `elements` its own acquisition modifier (mirroring `screenshot`'s) would close this, as
   unit 2 does for the last step. A rule's post-action tree would land under a distinct name instead
   of overwriting the shared `elements.json`. But that is a DSL change: a new capture-token shape,
   `evidence/core.py`, docs, and tests. The scope is materially larger than this item's single
   fixed-shape default. It is left for a future item. This item guarantees the *default* pairing for
   a scenario with no firing rule, which unit 6 tests.

5. **Fix the one hardcoded consumer.** `_step_artifacts` in `bajutsu/serve/operations/reads.py`
   (`reads.py:411-456`) currently probes the literal paths `.../elements.json` and `.../after.png`
   via `_safe_exists`, rather than reading what the step recorded. Once the baseline screenshot is
   `before.png`, that hardcoded probe would report "no screenshot" for every ordinary step.

   The function reads the per-step `artifacts` list of the already-loaded `manifest.json` instead.
   Each entry is `{"name", "kind", "provider"}`, confirmed against the `asdict()` serialization of
   `bajutsu/report/manifest.py`. The function picks the first artifact of kind `"screenshot"` /
   `"elements"`. This mirrors the existing `by_kind.setdefault(a.kind, a)` pattern of
   `bajutsu/report/rows.py` (`report/rows.py:113-122`), which already reads generically by kind and
   needs no change itself.

   `_artifact_names` narrows each `kind`/`name` to `str` (not merely non-`None`) before indexing
   `by_kind`. A malformed or partially written manifest entry then degrades to "no link". No
   non-string value flows into the URL built from it.

   `_step_artifacts` restores the `or None` coercion that the removed `_find_sid` applied to `sid`.
   It validates the result with the file's own `_valid_step_id`, the gate that
   `resolve_scenario_pick` already applies to a `stepId` coming back *from* the client. A non-`str`
   or `..`/absolute-shaped `sid` bails to `[]`, the same way a missing one already does. Every
   `stepId` that this function returns derives from `sid`, so the check keeps a malformed or
   traversal-shaped step id out of all of them.

   The per-step lookup keys on the runtime step id, not on the outcome's `index`. The lookup parses
   the id from each outcome's own recorded artifact path (`name.rsplit("/", 1)[0]`). `index` counts
   every executed step, nested `if`/`forEach`/`web` steps included. The loop over `matched.steps`
   counts top-level YAML steps. The two counters diverge once the scenario nests anything before a
   given step. The runtime id of a named step depends on neither counter, so the lookup resolves to
   the right artifacts.

   Every level of this walk is `isinstance`-checked before use. The levels are the manifest,
   `scenarios`, each scenario's `steps`, each step's `artifacts`, and each artifact. The step id
   search skips any artifact that lacks a usable `name`. It does not stop at the first one. A usable
   name is a `str`, contains a slash, and is `_valid_step_id`-safe.

   The `_valid_step_id` check matters here, not just the slash check. A traversal-shaped name (for
   example `../../../run2/...`) must never become the key itself. `name.rsplit("/", 1)[0]` on such a
   name would produce a key that no real `step_id` ever matches. That would hide every other,
   legitimate artifact recorded for the same step. One malformed entry in a partially written
   manifest degrades to missing artifacts for that step. It causes no 500 and hides no
   otherwise-valid artifact.

6. **Cover the ordering, the final-step capture, the read-count invariant, and the non-regression in
   the deterministic suite.**

   - A `FakeDriver`-backed test in `tests/orchestrator/test_loop.py` records the call order of
     `screenshot()` / `query()` against the action call. It runs a mutating step (`tap`) and a
     non-mutating one (`assert_`/`wait`), and proves that the capture precedes the action in both.
   - A further case runs a multi-step scenario. It asserts that the last step's `outcome.artifacts`
     carries the pre-step (`before.png`) and the unit 2 final-step (`after.png`) entries. An earlier
     step carries the former alone. A variant that ends in an `if`/`forEach` proves that the final
     capture lands on the last *leaf* step, not on the container's outcome.
   - A content-level case proves that the last step's `elements.json` stays the pre-action tree. It
     matches `before.png`, and never the post-action tree that the final capture's
     `screenshot.after` alone shows. Unit 2's design note above depends on this pairing.
   - `tests/test_alerts.py` and `tests/orchestrator/test_waits.py` gain cases for the two retry
     paths of the `alert_guard` reactive guard: the end-of-step one-shot retry in `_handle_action`,
     and the mid-wait `_AlertGuardGate` in `waits.py`. The cases prove that neither path corrupts
     this item's evidence. The retried/dismissed step's pre-step baseline still shows the true
     pre-attempt state. The *following* step's own baseline reflects the post-recovery, settled
     state, never something stale from before the alert cleared.
   - `tests/orchestrator/test_read_count.py` gains a case. It proves that the pre-step call adds no
     loop-issued read when the sink does not consume `elements`. The case guards the invariant that
     `test_plain_tap_issues_no_runner_read` pins, so this item cannot regress it. A further case
     proves that a web block's pre-step baseline pays no bridge query under a `NullSink`.
   - A case in `tests/serve/test_editor_ops.py` proves that `_step_artifacts` resolves the
     manifest-recorded name, not a hardcoded one. That file already covers `_step_artifacts`. A
     further case proves that a named step after nested control flow resolves its own artifacts, not
     those of the nested step.
   - A regression test proves that `extract` / `assert` still read the settled post-action tree,
     unaffected by this item.

7. **Document the new default.** [`docs/evidence.md`](../../docs/evidence.md) and its Japanese
   mirror change the "Default modifiers … default to `after`" line (`docs/evidence.md:44-46`). The
   line now states the always-on baseline: `screenshot.before` + `elements`, taken before the step
   acts. An explicit rule or inline request keeps today's `after` default.

   [`DESIGN.md`](../../DESIGN.md) already lists `before.png` alongside `after.png` in its directory
   layout (§9). Its acquisition-timing table gains the same before/after distinction.
   [`docs/architecture.md`](../../docs/architecture.md) is checked for any description of this
   behavior, and updated if one exists (BE-0113).

### Machine-checkable outcome

The deterministic suite records the `screenshot`/`query` calls of a `FakeDriver` step in order. The
test asserts that the capture call precedes the action call, for a mutating and a non-mutating step
kind. It also asserts that the last step's `outcome.artifacts` carries a pre-step and a final
post-step entry. No AI is on this path. `make check` is the judge, exactly as it is for every other
`loop.py` test.

## Alternatives considered

**Rename nothing; just move the timing of the existing `after.png` write earlier.** This was the
first shape considered. It keeps the filename `after.png` and changes when the pixel is taken. It
avoids touching `reads.py` and every place that assumes `after.png` exists. The shape was rejected
because the artifact's own name would lie about what it holds. A maintainer who reads
`_BASELINE_INSTANT` or a report's `after.png` could not learn from the code that the file now holds
a before-action screenshot. `screenshot.before` / `before.png` already exist in the vocabulary of
the codebase (`docs/evidence.md`, `DESIGN.md`). Reusing them costs one fixed consumer (`reads.py`,
Unit 5). In exchange, the name means what it says.

**Make every rule-fired instant kind pre-step too, not just the baseline.** Motivation covers the
grounds for rejecting this in adjacent detail. The `screenChanged` and `result: error` triggers are
decidable after the step runs. They are meant to capture the post-action state: the error that the
step produced, or the change that the step caused. Moving those captures pre-step would fix no
defect. It would remove the ability to see what went wrong.

**Give `elements` its own before/after modifier, mirroring `screenshot`.** This would remove the
Unit 4 precedence rule. A rule that wants a post-action tree would write `elements.after.json`,
leaving the baseline's `elements.json` alone. The option was rejected for scope. It changes the
shape of an established, single-filename artifact across every consumer that reads `elements.json`:
`report/rows.py`, `reads.py`, `object_store.py`, the golden-comparison and visual-regression paths,
and their tests. The case it serves is a rule that requests `elements` on top of the baseline, which
the existing dedup of `_collect_captures` already treats as a single write today. A small,
well-scoped item keeps that shape unchanged and documents the one precedence rule instead.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — pre-step capture call added, reusing `self.state.prev_after`; baseline removed from
      `_collect_captures`.
- [x] Unit 2 — the scenario's last leaf step's outcome gains a final post-step capture, closing the
      final-step gap.
- [x] Unit 3 — redundant `.before` tokens filtered from the post-step call.
- [x] Unit 4 — post-step `elements`/`screenshot.after` precedence documented, unchanged in behavior.
- [x] Unit 5 — `reads.py` reads the manifest's recorded artifact names instead of hardcoded paths.
- [x] Unit 6 — deterministic coverage for capture ordering, the final-step capture, the read-count
      invariant, `reads.py` resolution, and the extract/assert non-regression.
- [x] Unit 7 — `docs/evidence.md` (+ ja), `DESIGN.md`, `docs/architecture.md` updated.

### Later revision

[#1633](https://github.com/bajutsu-e2e/bajutsu/pull/1633) revises two of the decisions recorded
above, so read them together.

The post-step capture now always records `elements`, and `_handle_action` shoots `screenshot.after`
itself, right after the step's action. Every step keeps the post-action tree, not only the steps
whose `capture` list asked for one. Unit 4's precedence rule no
longer turns on what a scenario requested, and "`elements` stays the pre-action tree" now holds for
one case: a step that fails before it acts.

`rawTree` moved to the post-step call for the same reason. A pre-action dump would no longer pair
with the tree beside it, and nothing can request one until a `rawTree.before` modifier exists.

Unit 2's end-of-run capture is gone with them. Every step that acts now shoots `after.png` itself,
right after its action, so the safety net only ever reached the step that returns before acting at
all — the `UncoveredSystemAlertLocale` failure — and giving *that* step a screenshot taken once the
run had finished left it pairing post-run pixels with a pre-action tree. It now keeps the matched
`before.png` + pre-action tree its baseline already gave it.

What this item established survives both revisions: every step captures `screenshot.before` +
`elements` before it acts.

## References

- [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) — the lazy, cached
  post-step read (`_ScreenRead`, `prev_after`) this item's pre-step capture reuses at zero extra
  cost on every step but the first.
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) ·
  [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — the settled post-action read
  `extract` / `assert` use, deliberately untouched by this item.
- [BE-0028](../BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) —
  the evidence-rule-firing guard this item's Topic sits alongside.
- [`docs/evidence.md`](../../docs/evidence.md) — the acquisition-timing modifiers (`before` /
  `after` / `around`) this item makes honest for `screenshot`.

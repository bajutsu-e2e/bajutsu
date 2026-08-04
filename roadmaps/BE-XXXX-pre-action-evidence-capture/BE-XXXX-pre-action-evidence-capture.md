**English** · [日本語](BE-XXXX-pre-action-evidence-capture-ja.md)

# BE-XXXX — Capture per-step report evidence before the step acts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-pre-action-evidence-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once this branch's PR is open (propose-and-build Phase C) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Every step's report screenshot and element tree are captured after the step has already acted,
never before. This item moves that always-on baseline capture ahead of the step's own action, so
the artifact a report shows for a step is the screen the step acted on, not the screen its action
left behind.

## Motivation

A step's report entry exists to answer one question: what did this step do, and to what. Today it
answers a different one. `_handle_action` (`bajutsu/orchestrator/loop.py:864`) runs the step's body
first — `_run_step_body` taps, types, swipes, waits, or asserts (`loop.py:961`) — and only once that
call returns does it build a `_ScreenRead` (`loop.py:1056`) and hand the resulting tree to
`self.cfg.sink.capture(...)` (`loop.py:1096`), which writes `elements.json` and takes the
screenshot (`bajutsu/evidence/core.py` `write_screenshot`, `write_elements`) at that moment. The
constant driving this, `_BASELINE_INSTANT = ("screenshot.after", "elements")`
(`bajutsu/orchestrator/evidence_rules.py:14`), is unconditional: every step gets this pair,
regardless of [`capturePolicy`](../../docs/glossary.md#evidence-capturepolicy-trace-triage). So the one
artifact every step is guaranteed to carry is a picture of where the tap landed, not the button it
tapped.

A `tap: { id: settings.reindex }` step's screenshot shows the settings screen after the tap
navigated away from it — not the row the step pressed. A failing `assert` on a value that a
previous step's async update hasn't yet mirrored into the tree shows a screen that already moved on
from the one the assertion actually read. Reviewing a report to answer "why did this scenario tap
the wrong row" or "what did the screen look like when this assertion ran" means reading the
*previous* step's artifact and mentally re-deriving what must have been on screen a moment later —
exactly the reconstruction the evidence subsystem exists to make unnecessary
([evidence](../../docs/evidence.md)).

The gap is not only the timing default; it is also a standing correctness bug in the modifier that
already claims to solve this. [`docs/evidence.md`](../../docs/evidence.md#evidence-kinds-and-acquisition-timing)
documents `before` / `after` / `around` as real acquisition-timing modifiers, and a scenario can
already write `capture: [screenshot.before]`. But `capture()` (`bajutsu/evidence/core.py:145-180`)
fires from one call site, at the single post-step moment described above; `write_screenshot` calls
`driver.screenshot()` right there regardless of which modifier named the file
(`core.py:170-178`: `name = f"{modifier or 'after'}.png"`). So `screenshot.before` today produces a
file named `before.png` holding the same post-action pixels as `after.png` would. The modifier
changes the label, never the shutter's timing. Nothing before this item has actually taken a
screenshot before a step's action.

This item is scoped to the artifact every step already gets unconditionally — the baseline — and to
making the existing `before` modifier honest wherever it appears. It does not touch how `extract` or
`assert` decide what value or state is *correct*: that machinery
([BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md),
[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md)) reads its own tree through
`_poll_asserts` / `_settle_extract_read`, independently of the report artifact, specifically because
a value must postdate the action that produced it. The report artifact answers a different
question — what state was this step handed — and the two must not be conflated.

## Detailed design

The fix rests on one existing fact: `self.state.prev_after` — the previous step's already-cached
post-step tree (`loop.py:1102`, BE-0234 Unit 2) — is maintained unconditionally today, regardless of
`capturePolicy` or `wants_screen_changed`. Nothing about `screen_changed`, the `before`/
`wants_screen_changed` gate (`loop.py:899-936`), or the interrupt guard needs to change: this item
adds one new, independent use of the tree `prev_after` already holds, and leaves that machinery
exactly as it is.

Just as important is what the fix must *not* do: force a fresh device read at the loop level merely
to have a pre-action tree in hand. `tests/orchestrator/test_read_count.py` pins the loop's own
laziness — `test_plain_tap_issues_no_runner_read` asserts zero loop-issued `query()` calls for a
plain `tap` with no consumer, and the module's docstring exists precisely to catch "a future change
that reintroduces a redundant read." The existing post-step capture call already respects this: it
hands the sink `elements=screen.cached` (`loop.py:1094`), which is `None` whenever nothing else
already materialized the tree, and leaves it to the sink whether to query — a `FileSink` queries in
real use; the read-count tests' stub sink deliberately does not, so as to measure only the loop's own
behavior. The new pre-step call must follow the identical pattern: pass whatever `prev_after` already
holds — real data on every step but the first (or the first after a tree-less one), `None`
otherwise — and never call `query()` itself to fill that gap.

### Work breakdown (MECE)

1. **Add a pre-step capture call, and remove the baseline from the post-step one.** Right before
   `_run_step_body` runs, unconditionally call
   `outcome.artifacts.extend(self.cfg.sink.capture(self.cfg.driver, step_id, ["screenshot.before", "elements"], elements=self.state.prev_after))`
   — extending `outcome.artifacts` the same way the existing post-step call already does
   (`loop.py:1095-1097`), so `manifest.json` records the new `before.png` / `elements.json` entries
   for Unit 5 to find. The call targets `self.cfg.driver`, not `active_driver` — the same choice the
   existing post-step call already makes, and for the same reason (`loop.py:1090-1092`): inside a
   `web` block `active_driver` is a `WebContextDriver`, whose `screenshot()` unconditionally raises
   `UnsupportedAction` (`bajutsu/webview.py:193-194`), so capturing against it would fail every step
   of every scenario with a `web` block. Passing `self.state.prev_after` rather than a freshly
   queried tree is what keeps this call's cost identical to today's: `write_elements` queries the
   driver itself only when `elements` is `None` (`core.py:69`), exactly mirroring the post-step
   call's own `screen.cached` argument — so a scenario with a sink that reads nothing pays nothing
   here either, and `tests/orchestrator/test_read_count.py`'s pinned zero stays zero. This writes
   `before.png` and `elements.json` from the pre-action state and a screenshot taken at that
   moment — the same two artifacts every step gets today, just earlier and (for `elements`) from the
   right tree whenever one is already in hand. `_BASELINE_INSTANT` is removed from
   `_collect_captures` (`evidence_rules.py:157-168`), which now returns only what the scenario
   actually asked for (`step.capture` plus matching `capturePolicy` rules) — no implicit baseline
   left to dedupe against.
   **Implementation note:** inside a `web` block, `self.state.prev_after` is reset to `None` around
   the whole block (BE-0234 Unit 2) and would otherwise force `write_elements`'s fallback to query
   `self.cfg.driver` (native) instead of the actual `WebContextDriver` a nested step ran against —
   so the pre-step call resolves `elements` explicitly (`active_driver.query()`) whenever `prev_after`
   is unset and `active_driver is not self.cfg.driver`, and does so behind a best-effort
   `try`/`except (ConnectionError, base.UnsupportedAction, OSError)` that skips just this artifact on
   a torn-down bridge rather than crashing the step before its own action even runs.

2. **Close the scenario's final-step gap.** Chaining step *i*'s result forward as step *(i+1)*'s
   pre-step baseline (unit 1) leaves exactly one step uncovered: the scenario's actual last
   executed step, which has no following step to carry its result forward. Left alone, that step's
   own after-the-fact state would never become a default artifact — a regression from today, where
   every step (including the last) unconditionally gets `after.png`. Track the last *leaf* step's
   identity as the loop runs, bundled into one `LastLeafStep(outcome, step_id, driver)` value
   (`loop.py`) rather than three parallel `Optional` fields, so the three are always set together by
   construction and a consumer narrows all three from one `is not None` check; `StepLoopState` holds
   a single `last_leaf: LastLeafStep | None` field. `_handle_action` constructs it at the end, next to
   `self.state.prev_after = screen.cached` — `_handle_action` is the single handler every
   actuating/`wait`/`assert`/`email` kind flows through (`_run_one`, `loop.py:754-775`), so this never
   fires for an `if`/`forEach`/`web` container's own bookkeeping outcome, only for the leaf step that
   actually ran last. After the top-level `exec_steps` call returns in `_run_steps`, regardless of its
   outcome, extend `leaf.outcome.artifacts` with one more call:
   `self.cfg.sink.capture(driver, leaf.step_id, ["screenshot.after", "elements"], elements=final_elements)`.
   `state.prev_after` is already that step's settled post-action tree at this point (maintained
   unconditionally), so this usually costs no extra query — the same reuse unit 1 relies on, spent
   once more at the very end of the run. **Implementation note:** the one exception is a scenario
   ending inside a `web` block, where `prev_after` was already reset to `None` on the way back out;
   the block re-queries `leaf.driver` directly in that case, behind the same best-effort
   `try`/`except` as unit 1's pre-step call — a web context torn down by the time this purely
   cosmetic, post-verdict capture runs must not crash an already-decided (possibly passing) run, so
   the capture is skipped rather than guessed at with the wrong driver's tree.

3. **Drop redundant `.before` tokens from the post-step call.** A scenario's inline `capture` or a
   `capturePolicy` rule can still spell `screenshot.before` explicitly; since Unit 1's pre-step call
   already wrote that file, the post-step list (`loop.py:1093`, fed by `_collect_captures`) filters
   any `screenshot.before` token out before it reaches `sink.capture(...)`, so the post-step path
   never re-takes a pixel under that name — closing the standing bug from Motivation for every call
   site, not only the baseline. `screenshot.after` / bare `screenshot` (defaults to `after`) /
   `screenshot.around` are untouched: they keep firing post-step, at the same point they do today.

4. **`elements` stays post-step-capable for a rule that asks for it.** `elements` has no acquisition
   modifier (`docs/evidence.md:44-46`) — one file, `elements.json`. When a `capturePolicy` rule
   (e.g. the `result: error` "capture the maximum" pattern from
   [evidence.md](../../docs/evidence.md#a-capturepolicy-rule-based)) also lists `elements`, its
   post-step write still fires and overwrites the pre-step one on disk: the rule's condition
   (`screenChanged` / `result: error`) is only knowable after the step ran, and what it wants to see
   genuinely is the post-action state — an error rule showing the screen the step left broken is the
   correct behavior, not a defect this item should suppress. This item changes only the *default* —
   the artifact every step gets with no matching rule — never a rule's own explicit request.
   Documented as the precedence a scenario author needs to know: the pre-step baseline is the
   floor, a firing rule's own capture list is the ceiling. Unit 2's final-step capture is exactly
   this same post-step shape, applied unconditionally to one step rather than gated on a rule.

5. **Fix the one hardcoded consumer.** `bajutsu/serve/operations/reads.py`'s `_step_artifacts`
   (`reads.py:411-456`) currently probes the literal paths `.../elements.json` and `.../after.png`
   via `_safe_exists`, rather than reading what the step actually recorded. Once the baseline
   screenshot is `before.png`, that hardcoded probe would report "no screenshot" for every ordinary
   step. Read the already-loaded `manifest.json`'s per-step `artifacts` list instead (each entry is
   `{"name", "kind", "provider"}` — confirmed against `bajutsu/report/manifest.py`'s `asdict()`
   serialization) and pick the first artifact of kind `"screenshot"` / `"elements"`, mirroring
   `bajutsu/report/rows.py`'s existing `by_kind.setdefault(a.kind, a)` pattern
   (`report/rows.py:113-122`) — which already reads generically by kind and needs no change itself.

6. **Cover the ordering, the final-step capture, the read-count invariant, and the non-regression in
   the deterministic suite.** A `FakeDriver`-backed test in `tests/orchestrator/test_loop.py` records
   the call order of `screenshot()` / `query()` against the action call, for a mutating step (`tap`)
   and a non-mutating one (`assert_`/`wait`), proving the capture precedes the action in both.
   A further case runs a multi-step scenario and asserts the last step's `outcome.artifacts` carries
   both the pre-step (`before.png`) and the unit 2 final-step (`after.png`) entries, while an earlier
   step carries only the former — and a variant ending in an `if`/`forEach` proves the final capture
   still lands on the last *leaf* step, not the container's own outcome.
   `tests/orchestrator/test_read_count.py` gains a case proving the new pre-step call costs no
   additional loop-issued read when the sink does not consume `elements` — guarding the exact
   invariant `test_plain_tap_issues_no_runner_read` already pins, so this item cannot silently
   regress it. A `tests/serve/test_editor_ops.py` case — where `_step_artifacts` is already
   covered — proves it resolves the manifest-recorded name rather than a hardcoded one. A regression
   test proves `extract` / `assert` still read the settled post-action tree, unaffected by this item.

7. **Document the new default.** [`docs/evidence.md`](../../docs/evidence.md) and its Japanese
   mirror: the "Default modifiers … default to `after`" line
   (`docs/evidence.md:44-46`) changes to state the always-on baseline is `screenshot.before` +
   `elements`, taken before the step acts, while an explicit rule/inline request keeps today's
   `after` default. [`DESIGN.md`](../../DESIGN.md) already lists `before.png` alongside `after.png`
   in its directory layout (§9); its acquisition-timing table gains the same before/after
   distinction. [`docs/architecture.md`](../../docs/architecture.md) is checked for any description
   of this behavior and updated if one exists (BE-0113).

### Machine-checkable outcome

The deterministic suite: a `FakeDriver` step records `screenshot`/`query` calls in order and the
test asserts the capture call precedes the action call, for both a mutating and a non-mutating step
kind, and that the scenario's last step's `outcome.artifacts` carries both a pre-step and a
final post-step entry. No AI on this path — `make check` is the judge, exactly as it is for every
other loop.py test.

## Alternatives considered

**Rename nothing; just move the timing of the existing `after.png` write earlier.** This was the
first shape considered: keep the filename `after.png`, only change when the pixel is taken. It
avoids touching `reads.py` and every place that assumes `after.png` exists. Rejected because it
leaves the artifact's own name lying about what it holds — a maintainer reading `_BASELINE_INSTANT`
or a report's `after.png` would have no way to learn, without reading this item, that it now holds a
before-action screenshot. `screenshot.before` / `before.png` already exist in the codebase's own
vocabulary (`docs/evidence.md`, `DESIGN.md`); reusing them costs one fixed consumer (`reads.py`,
Unit 5) in exchange for a name that means what it says.

**Make every rule-fired instant kind pre-step too, not just the baseline.** Considered and rejected
in Motivation-adjacent detail: `screenChanged` and `result: error` triggers are only decidable after
the step runs, and what they capture is specifically supposed to be the post-action state (the
error the step produced, the change the step caused). Moving those pre-step would not fix a defect;
it would remove the ability to see what actually went wrong.

**Give `elements` its own before/after modifier, mirroring `screenshot`.** This would remove the
Unit 4 precedence rule entirely — a rule wanting a post-action tree would write `elements.after.json`
without touching the baseline's `elements.json`. Rejected for scope: it changes an established,
single-filename artifact's shape across every consumer that reads `elements.json`
(`report/rows.py`, `reads.py`, `object_store.py`, the golden-comparison and visual-regression
paths, and their tests) for a case — a rule requesting `elements` on top of the baseline — that
`_collect_captures`'s existing dedup already treats as a single write today. A small, well-scoped
item keeps that shape unchanged and documents the one precedence rule instead.

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

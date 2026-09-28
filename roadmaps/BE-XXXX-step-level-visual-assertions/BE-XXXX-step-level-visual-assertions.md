**English** · [日本語](BE-XXXX-step-level-visual-assertions-ja.md)

# BE-XXXX — Step-level visual regression assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-step-level-visual-assertions.md) |
| Author | [@handle](https://github.com/handle) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
| Related | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md), [BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md), [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md), [BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context.md) |
<!-- /BE-METADATA -->

## Introduction

Extend the existing `visual` assertion (BE-0029) to a new evaluation site. Today it runs merely at
a scenario's trailing `expect:` block. This proposal adds a step's own `assert:` block as a second
site. The comparison stays a deterministic pixel diff against a stored baseline image. No AI enters
the `run`/CI gate.

## Motivation

Bajutsu already ships a deterministic `visual` assertion kind (BE-0029). It offers selectable
compare engines (BE-0165) and element scoping (BE-0171). Today, though, it runs at a single site: a
scenario's trailing `expect:` block. `_capture_visual_actual` takes one screenshot there, right
before the run evaluates that block.

A step's own `assert:` block accepts the same `Assertion` schema `expect` does. An author can write
`assert: [{ visual: { baseline: "modal.png" } }]` inside a step. The scenario then passes schema
validation. But the step-level `assert_` branch in `bajutsu/common/orchestrator/loop/_functions.py`
drops the visual context before every evaluation. `tests/orchestrator/test_loop.py` locks that drop
in as an intentional asymmetry (BE-0250 Unit 2). The comparison then always reports "no visual
context provided", whatever the screen actually shows.

That asymmetry has two costs for the author. First, a scenario cannot verify an intermediate screen.
A modal that must render right after it opens, before the next step dismisses it, is exactly this
case. The author's one path today is to split a scenario into two, purely to move the check onto the
final screen. Second, the gap stays invisible at authoring time. The schema accepts `visual` inside
`assert:`. The scenario docs describe `assert` as sharing `expect`'s domain-specific language (DSL),
and the `visual` section's own example already sits under `- assert:` — so nothing warns an author
that the check will not actually run there. An author discovers the gap at run time, from a message
that names no cause they can act on.

Once shipped, a step's `assert:` block can carry a `visual` entry. That entry runs a real pixel
comparison against a screenshot taken at that point in the scenario. It passes or fails on that
comparison, in place of always failing with "no visual context provided".

## Detailed design

This reuses the `VisualMatch` schema, the `compare_images` pixel-diff engine, and
`driver.screenshot()` on every backend, all built by BE-0029, BE-0165, and BE-0171. It needs no new
assertion kind. It needs no new `Driver` method.

- **Stop forcing `visual=None` in the step-level `assert_` branch.** Build a per-step
  `VisualContext` instead, via `dataclasses.replace(cfg.ctx.visual, prefix=…, screenshot_path=…)`.
  Do this whenever the step's `assert:` list carries a `visual` entry, and the routing runner's own
  context (`cfg.ctx` in `_StepRunner`) already carries one — the same `ctx.visual is not None` gate
  `_capture_visual_actual` applies at `expect`. Deriving from `cfg.ctx` rather than a fixed run-level
  context keeps a step naming a second target comparing against that target's own baselines
  (BE-0428). `_StepRunner._handle_action`, in `bajutsu/common/orchestrator/loop/_step_runner.py`, is
  the one place that already computes a step's evidence prefix (`step_id`); it must pass that prefix,
  and the step's `StepOutcome.index`, down to `_run_step_body` at each of its three call sites.
  `_poll_asserts` needs no change — the capture below happens before it runs. `SchemaContext` keeps
  being dropped at the same branch. BE-0250 Unit 2 dropped it there only to preserve pre-refactor
  behavior, not because a step-level assert lacks the network exchange `responseSchema` reads.
  Lifting that drop is a separate change; this proposal leaves it out of scope.
- **Capture exactly one screenshot per step `assert:` block that carries a `visual` entry.** Take it
  right before `_poll_asserts` runs, so every `visual` entry in that block compares against the same
  capture — exactly as the trailing `expect` block does today. `_evaluate_expect` already follows
  this pattern: it takes one screenshot via `_capture_visual_actual`, right before evaluating the
  trailing `expect:` block. `_poll_asserts` already treats `visual` as a `_READ_ONCE_KINDS` entry, so
  a poll tick re-reads only the UI tree; a step-level check needs no fresh screenshot per tick.
- **Scope the per-step screenshot's path to that step's own evidence prefix and outcome index.**
  `write_screenshot` already uses the evidence prefix for the step's `after.png`, but a step's name
  is not required to be unique, so a named step inside a `for_each` reuses the same prefix on every
  iteration. Key the capture one level deeper by `StepOutcome.index` — unique within the step's own
  `step_id` prefix, since one counter numbers every step of a phase, across targets and nested
  blocks, and that prefix already carries the phase label — so a step's visual capture never
  collides with the scenario's own `visual-actual.png`, nor with another execution of the same step;
  a retry of one execution (the TipKit retry, the alert-guard retry) reuses that execution's own
  index, so the result the step keeps points at its own final attempt's pixels.
- **Preserve the touch-marker suspension and notification-banner clearing that `expect`'s visual
  capture already applies.** Precede the capture with `_clear_notification_banner` directly, not the
  `_clear_notification_banner_before_visual_capture` wrapper, which drains into the expect-phase
  actuations list; a step already has its own end-of-body `drain_actuations`
  (`_StepRunner._handle_action`), which records the swipe on that step's own `StepOutcome.actuations`
  / `dropped_actuations` instead. Then route the capture itself through `_capture_visual_actual`,
  which suspends the in-app touch markers via `capability_suspended` whenever `_hides_touch_markers`
  says the scenario draws them. Both need inputs `_run_step_body` does not carry today: the run's
  collector `channel` and the `hide_markers` flag. Today both are locals of `run_scenario` alone.
  Both must reach `_LoopConfig`, and its per-target construction, so the step loop can read them. A
  control-channel failure during that suspension is left to fail the whole scenario, exactly as it
  does today when `_capture_visual_actual` raises one at `expect`; a step-level check gets no special
  handling that would turn it into a per-step failure instead. Finally, `_visual_asserting_scenarios`
  (`bajutsu/run/cli.py`), which `run --touch-markers` uses to
  decide which scenarios get the marker channel at all, reads only `expect` and each scenario's
  top-level `steps` today; it must walk the full step tree instead — every phase, plus nested `if`
  and `for_each` blocks the way `capability_preflight`'s own walk already does, and an `app:` block
  too, which that walk does not recurse into but which runs on the native driver like any other step.
  A step-level `visual` assertion nested inside a `web:` block stays out of scope: the driver a `web:`
  step runs against is a `WebContextDriver`, whose `screenshot` unconditionally raises
  `UnsupportedAction`, so such a check fails loudly with that error rather than silently.
- **Update the test that locks the old behavior.** `tests/orchestrator/test_loop.py` carries
  `test_step_level_assert_drops_visual_context`, added under BE-0250 Unit 2. Update it to assert
  the new behavior. A step-level `visual` assert now runs a real comparison. `responseSchema` still
  gets dropped there, unchanged.
- **Document the change**, and the one known gap it leaves. Update `docs/scenarios.md`'s `assert`
  section, its `visual` section, and its list of notification-banner capture sites, plus
  `docs/architecture.md`'s note on the `expect`-phase visual capture, and their `docs/ja/` mirrors.
  Call out plainly that `bajutsu approve` and the report's baseline/actual/diff strip do not yet
  read a step's own `assertion_results` (below), so a step-level check's first baseline still needs
  a manual copy.
- **Leave `bajutsu approve` and the report's visual strip untouched, and say so.** `approve`
  (`bajutsu/serve/cli/approve.py`) walks only a scenario's `expect_results`, and the report's
  baseline/actual/diff strip and Approve button (`_visual_row` in `bajutsu/common/report/rows.py`)
  render only for `expect` rows — neither sees a step's `assertion_results`. An author bootstraps a
  step-level check's first baseline by hand: even when no baseline exists yet, the failing result
  still records the capture's path under `assertion_results[].visual.actual` in the run's
  `manifest.json` — the element crop when the check sets `element:`, the whole capture otherwise, the
  same distinction `approve` itself already respects for `expect`. The author copies that exact file
  into the baselines directory under the name the `baseline:` field gives. Extending `approve` and
  the report to step-level results is real, independent work, left to a follow-up item rather than
  folded into this one.

## Alternatives considered

- **Reject `visual` inside `assert:` at schema or lint time, rather than supporting it.** This
  removes the silent gap, but leaves the underlying limitation standing. An author who genuinely
  needs a mid-scenario visual checkpoint would still split a scenario into two. Nothing else about
  that scenario calls for a split — the split exists purely to move a check onto the last screen.
- **Re-capture a screenshot on every `_poll_asserts` tick**, so a step-level `visual` check could
  retry against a settling screen, the way a `value` or `exists` check does. This would diverge from
  `expect`, which also captures once, and buys little on most lanes: the poll's own wait budget is the
  lane's wait floor (`BAJUTSU_MIN_WAIT_TIMEOUT`), zero unless a lane sets one. A step-level check on
  a screen still settling is handled the way an `expect` one already is — precede it with a `wait` —
  at the cost of one extra device round trip per tick on a lane that does set a floor.
- **Give a step-level visual check its own baseline-directory namespace, apart from `expect`'s.**
  A baseline is already keyed by the name an author gives it, via `baseline: <file>.png`. That key
  does not depend on whether the check runs from `expect` or `assert`. A step-level check needs no
  new namespace. It names its own baseline file, precisely as `expect` does today.
- **Extend `bajutsu approve` and the report's visual strip to step-level results in this same item.**
  Rejected for scope: doing so reaches into `bajutsu/serve/cli/approve.py`,
  `bajutsu/common/report/rows.py` and its template, the serve `/api/approve` endpoint
  (`approve_baseline` in `bajutsu/serve/operations/reads.py`) the report's own Approve button calls,
  and `bajutsu/templates/report.js`, on top of the orchestrator-loop change above. The manual-copy
  workaround in *Detailed design* keeps this item's own scope to the comparison itself, which is the
  part an author cannot work around any other way.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stop forcing `visual=None` in the step-level `assert_` branch. Thread the step's evidence
      prefix and `StepOutcome.index` from `_StepRunner._handle_action` into `_run_step_body`, and
      build a per-step `VisualContext` from the routing runner's own `cfg.ctx` when the step's
      `assert:` list carries a `visual` entry.
- [ ] Capture exactly one screenshot per step `assert:` block that carries a `visual` entry, right
      before `_poll_asserts` runs.
- [ ] Scope the per-step screenshot's path to that step's own evidence prefix and `StepOutcome.index`,
      so it never collides with the scenario's `visual-actual.png` or with another execution's
      capture.
- [ ] Precede the step-level capture with `_clear_notification_banner` (recording the swipe on the
      step's own `StepOutcome` via its existing `drain_actuations`) and thread `channel` and
      `hide_markers` into `_LoopConfig` so the capture can reuse `_capture_visual_actual`'s
      touch-marker suspension.
- [ ] Make `_visual_asserting_scenarios` (`bajutsu/run/cli.py`) walk the full step tree — every
      phase plus nested `if`, `for_each`, and `app` blocks — so `run --touch-markers` arms the marker
      channel for a nested step-level `visual` assertion too; leave a `visual` assertion nested in a
      `web:` block unsupported (it fails loudly on `WebContextDriver.screenshot`'s
      `UnsupportedAction`).
- [ ] Update `tests/orchestrator/test_loop.py`'s `test_step_level_assert_drops_visual_context` case
      for the new behavior. Add coverage for the single-shot capture, the index-scoped path, the
      touch-marker/banner reuse, and for `responseSchema` staying dropped.
- [ ] Document the change, and the manual-baseline-copy gap. Update `docs/scenarios.md`'s `assert`
      section, its `visual` section, and its banner capture-site list; update `docs/architecture.md`
      too; and update their `docs/ja/` mirrors.

## References

- [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) — the
  `visual` assertion kind this proposal extends to a new evaluation site.
- [BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md) — the selectable
  compare engines a step-level check reuses unchanged.
- [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md)
  — the element scoping and selector-based masking a step-level check reuses unchanged.
- [BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context.md)
  — Unit 2's log entry records the "step-level asserts drop `visual`/`responseSchema`" decision
  this proposal revisits, for `visual` alone.
- `bajutsu/common/orchestrator/loop/_functions.py` — `_run_step_body`'s `assert_` branch, which this
  proposal changes; `_poll_asserts` is reused unchanged.
- `bajutsu/common/orchestrator/loop/_step_runner.py` — `_StepRunner._handle_action`, the one place
  that already computes a step's evidence prefix and receives its outcome index.
- `bajutsu/common/orchestrator/loop/_loop_config.py` — `_LoopConfig`, which gains the `channel` and
  `hide_markers` fields the step-level capture needs.
- `bajutsu/run/cli.py` — `_visual_asserting_scenarios`, which this proposal extends to the full step
  tree.
- `docs/scenarios.md` — the `assert` (mid-step verification) and `visual` (visual regression)
  sections this proposal updates.

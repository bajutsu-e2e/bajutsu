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
| Related | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md), [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md), [BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context.md) |
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
validation. But the step-level `assert_` branch drops the visual context before evaluating, every
time — a line in `bajutsu/common/orchestrator/loop/_functions.py`. `tests/orchestrator/test_loop.py`
locks that drop in as an intentional asymmetry (BE-0250 Unit 2). The comparison then always reports
"no visual context provided", whatever the screen actually shows.

That asymmetry has two costs for the author. First, a scenario cannot verify an intermediate
screen — a modal that must render right after it opens, before the next step dismisses it. The
author's one path today is to split a scenario into two, purely to move the check
onto the final screen. Second, the gap stays invisible at authoring time. The schema accepts `visual` inside
`assert:`. The scenario docs describe `assert` as sharing `expect`'s domain-specific language
(DSL), and name no exception for `visual`. An author discovers the gap at run time, from a message
that names no cause they can act on.

Once shipped, a step's `assert:` block can carry a `visual` entry. That entry runs a real pixel
comparison against a screenshot taken at that point in the scenario. It passes or fails on that
comparison, in place of always failing with "no visual context provided".

## Detailed design

This reuses everything BE-0029, BE-0165, and BE-0171 already built. It reuses the `VisualMatch`
schema, the `compare_images` pixel-diff engine, `driver.screenshot()` on every backend, and the
baseline approval workflow (`bajutsu approve`). It needs no new assertion kind. It needs no new
`Driver` method.

- **Stop forcing `visual=None` in the step-level `assert_` branch.** Build a per-step
  `VisualContext` instead. Do this whenever the step's `assert:` list carries a `visual` entry, and
  the run's own context already carries baseline configuration. `_eval_context_for` already uses
  that same `ctx.visual is not None` gate — deciding whether a run configures visual assertions at
  all. That branch keeps dropping `responseSchema` too. It reads a captured network
  exchange, and a step-level assert has no matching capture for one. That is a separate constraint;
  this proposal leaves it alone.
- **Capture one screenshot per step-level visual check, and one alone.** Take it right before
  `_poll_asserts` runs. `_evaluate_expect` already follows the same pattern: it takes one screenshot
  via `_capture_visual_actual`, right before evaluating the trailing `expect:` block.
  `_poll_asserts` already treats `visual` as a `_READ_ONCE_KINDS` entry. A poll tick re-reads the UI
  tree alone. A step-level check needs no fresh screenshot per tick. A fresh screenshot per tick
  would add device round trips. It would also let timing vary between runs of what should stay a
  deterministic check.
- **Scope the per-step screenshot's path to that step's own evidence prefix.** `write_screenshot`
  already uses that prefix for the step's `after.png`. A step's visual capture then never collides
  with the scenario's own `visual-actual.png`. It never collides with another step's capture either
  — even when the same step runs more than once, as a retry or a `for_each` iteration does.
- **Preserve the touch-marker suspension and notification-banner clearing.** `expect`'s visual
  capture already applies both. `_hides_touch_markers` suspends the markers. The function
  `_clear_notification_banner_before_visual_capture` clears a lingering banner. A step-level capture
  reuses both, so neither pollutes it.
- **Update the test that locks the old behavior.** `tests/orchestrator/test_loop.py` carries
  `test_step_level_assert_drops_visual_context`, added under BE-0250 Unit 2. Update it to assert
  the new behavior. A step-level `visual` assert now runs a real comparison. `responseSchema` still
  gets dropped there, unchanged.
- **Document the change.** Update the `assert` sections of `docs/scenarios.md` and
  `docs/ja/scenarios.md`. An author reading either should no longer have to find the previous
  limitation by running into it.

## Alternatives considered

- **Reject `visual` inside `assert:` at schema or lint time, rather than supporting it.** This
  removes the silent gap, but leaves the underlying limitation standing. An author who genuinely
  needs a mid-scenario visual checkpoint would still split a scenario into two. Nothing else
  about that scenario calls for a split — the split exists purely to move a check onto the last
  screen.
- **Re-capture a screenshot on every `_poll_asserts` tick.** A step-level `visual` check would then
  retry against a settling screen, the way a `value` or `exists` check does. `visual` sits in
  `_READ_ONCE_KINDS` already. No tree re-read changes a pixel comparison's outcome. A repeated
  screenshot would add cost without adding coverage. It would also let timing vary between runs of
  a check that prime directive 2 requires to stay deterministic.
- **Give a step-level visual check its own baseline-directory namespace, apart from `expect`'s.**
  A baseline is already keyed by the name an author gives it, via `baseline: <file>.png`. That key
  does not depend on whether the check runs from `expect` or `assert`. A step-level check needs no
  new namespace. It names its own baseline file, precisely as `expect` does today.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stop forcing `visual=None` in the step-level `assert_` branch. Build a per-step
      `VisualContext` when the step's `assert:` list carries a `visual` entry and the run carries
      baseline configuration.
- [ ] Capture one screenshot per step-level visual check, and one alone, right before
      `_poll_asserts` runs. Reuse the touch-marker suspension and notification-banner clearing
      `expect` already applies.
- [ ] Scope the per-step screenshot's path to that step's own evidence prefix, so it never collides
      with the scenario's `visual-actual.png` or with another step's capture.
- [ ] Update `tests/orchestrator/test_loop.py`'s `test_step_level_assert_drops_visual_context` case
      for the new behavior. Add coverage for the single-shot capture and for `responseSchema`
      staying dropped.
- [ ] Document the change in the `assert` sections of `docs/scenarios.md` and `docs/ja/scenarios.md`.

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
- `bajutsu/common/orchestrator/loop/_functions.py` — `_run_step_body`'s `assert_` branch and
  `_poll_asserts`, the two functions this proposal changes.
- `docs/scenarios.md` — the `assert` (mid-step verification) and `visual` (visual regression)
  sections this proposal updates.

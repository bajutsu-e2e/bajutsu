**English** · [日本語](BE-XXXX-step-groups-report-folding-ja.md)

# BE-XXXX — Group steps into named sections, folded in report.html

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-step-groups-report-folding.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2059](https://github.com/bajutsu-e2e/bajutsu/pull/2059) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A new `group:` step lets an author name a run of consecutive steps under `steps:`. `group:` takes
`name` and a nested `steps:` list. It expands into that nested list before `run` starts. The
deterministic runner never sees `group` itself — the same treatment `use:` already gets
([BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md)). Each
expanded step keeps a record of the group it came from. `report.html` folds a group's steps into a
collapsed section under a heading. The heading names the group and counts its steps. A group whose
steps all pass stays collapsed by default. A group holding a failing step opens automatically. The
existing "expand all" / "collapse all" controls toggle a scenario's groups too.

## Motivation

`report.html`'s step table is flat. One row shows per step
(`steprow` / `steptable`, [report.html.j2](../../bajutsu/templates/report.html.j2)). Nothing
collapses a related run of steps. A scenario with a long step list produces a page a reviewer must
scroll through in full — even when a single phase of the run failed.

Three existing constructs come close to naming a run of steps. Each solves a different problem.

`from:` ([BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)) records the
natural-language phrase `record` normalized a step from. It already collapses a run of identical
consecutive values into one label. `from:` is provenance metadata, not an author-chosen section
name. It labels a run; it never folds one.

`use:` / `components:`
([BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md),
[BE-0422](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components.md)) name a
reusable step sequence. `expand_components`
([expand.py](../../bajutsu/common/scenario/expand.py)) fully flattens a `use:` call before `run`.
No trace of the call's boundary survives to report generation. `report.html` renders a called
component's steps as ordinary consecutive rows.

`if` / `forEach` / `web` / `app` are runtime container steps with their own nested `steps:`. Their
nested steps share their parent's numbering — a limitation `rows.py` documents in comments at two
call sites ([rows.py](../../bajutsu/common/report/rows.py)). None of the four renders folded
either.

Once `group:` ships, an author names a run of steps once. `report.html` then hides a passing group
behind a one-line summary. A failing group still opens on its own. A reviewer scanning a long run
can tell which named section failed without expanding every step in the scenario.

## Detailed design

### Schema: the `Group` model and `Step.group` field

Add `bajutsu/common/scenario/models/steps/group.py`. Shape it like `ForEach`. Neither field
accepts an empty value:

```python
class Group(_Model):
    name: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
```

Add a `group: Group | None = None` field to `Step`
([step.py:71](../../bajutsu/common/scenario/models/steps/step.py)). `_STEP_ACTIONS` derives from
`Step.model_fields` minus `_MODIFIERS`
([step.py:161](../../bajutsu/common/scenario/models/steps/step.py)). This field alone makes
`group` a recognized action. The existing `_one_action` validator then covers it too. A step cannot
combine `group` with `tap`, `use`, or any other action.

Add `group` to `_CONTROL_FLOW_ACTIONS`
([_base.py:33](../../bajutsu/common/scenario/models/_base.py)), alongside `if_` / `for_each` /
`web` / `app`. A `group` step produces no single outcome of its own. `capture` and `extract` make
no sense on it. The existing `_no_modifiers_on_control_flow` validator then rejects both. This needs no new
code.

`use` sits outside that list today. It drops `capture` / `extract` at expansion time, without
rejecting them. `group` does not copy that behavior. An author writing `group: { name: ...,
capture: [...] }` could reasonably expect `capture` to take effect. A silent drop invites that
mistake. Fixing the same gap in `use` falls outside this item. A separate issue tracks it
([#2057](https://github.com/bajutsu-e2e/bajutsu/issues/2057)).

Expansion (described below) also discards `group`'s `name`, `from`, and `target` fields — the
step-level `name`, not `Group.name`. This matches the treatment `use` gives `use.component` /
`use.with_`'s siblings ([expand.py:76](../../bajutsu/common/scenario/expand.py) onward).

### Carrying the group's identity past expansion

A flattened step must remember two facts, to reach `report.html`. One is which group it came
from. The other is which occurrence of that group. Add two fields to `Step`. An author is not meant
to write either by hand:

```python
report_group: str | None = Field(default=None, alias="_reportGroup")
report_group_id: int | None = Field(default=None, alias="_reportGroupId")
```

`report_group` is the display name. `report_group_id` is a sequence number, one per `group`
invocation. `_fold_groups` (below) finds a run of consecutive rows by matching `report_group_id`,
not the name. Two separate `group: { name: retry }` calls in one scenario could otherwise land next
to each other in the row list. They would then merge into a single, wrongly-counted fold.

Register both in `_MODIFIERS`
([_shared.py:8](../../bajutsu/common/scenario/models/steps/_shared.py)). Both are ordinary `Step`
fields. They survive the `model_dump` / `model_validate` round trip `_interp_steps` already
performs during `use` substitution ([expand.py](../../bajutsu/common/scenario/expand.py)). This
needs no extra plumbing.

An ordinary field is not enough on its own, though. `_Model`'s `extra="forbid"`
([_base.py:37](../../bajutsu/common/scenario/models/_base.py)) rejects an unknown key. But
`_reportGroup` and `_reportGroupId` are declared fields, not unknown ones. An author could write
either by hand, and fake a fold. Add a field validator on both, rejecting any value other than
`None`. `expand()` sets them through `model_copy(update=...)`. That path skips field validators, so
the internal write still succeeds. A value arriving through ordinary `model_validate` means an
author wrote it, and that path does not skip validators — the write fails there. Mark both
`Annotated[..., SkipJsonSchema()]` too. `bajutsu schema` then omits them from the authoring
surface.

A parallel array was the first design considered for this role, threaded alongside the existing
`step_lines` mechanism
([raw_source.py:36](../../bajutsu/common/scenario/raw_source.py)). We rejected it. `use`
substitution discards the calling step object entirely. It keeps `use.component` and `use.with_`
alone. A group name attached to a `use` step by array index would have nowhere to land, once that
step's substitution runs. Living on `Step` itself avoids that problem. Propagating both fields
through a `use` call inside a `group` then needs two extra arguments, threaded through the
expansion recursion below.

### Expansion: `group` and `use` share one recursion

Add two arguments to `expand()` inside `expand_components`
([expand.py:70](../../bajutsu/common/scenario/expand.py)). `group_ctx: str | None = None` names
the group enclosing the current recursive call. `group_id: int | None = None` names that call's
sequence number. The id comes from one counter, shared module-wide in `expand.py`
(`itertools.count()`) — not a counter local to one `expand_components` call.

A setup prelude expands through its own, separate `expand_components` call. This runs before
`apply_setups` splices the prelude's steps onto the scenario's own
([run/cli.py:292](../../bajutsu/run/cli.py)). A call-local counter would then hand out
`group_id=0` twice: once to the last group in the prelude, once to the first group in the
scenario's own `steps`. The two could merge into one fold, if they land adjacent. A module-wide
counter never repeats a value, so this cannot happen. Its exact numbers need not be stable across
runs; nothing outside one render depends on them.

At the top of the loop:

- `st.group is not None`: if `group_ctx` is already set, raise. This is a `group` reached from
  inside another `group`, even through an intervening `use`. Otherwise, draw a fresh id from the
  counter, and recurse as `expand(st.group.steps, stack, resolve, group_ctx=st.group.name,
  group_id=new_id)`. Append the result to `out` unchanged.
- `st.use is None` and `st.group is None` (an ordinary step): append it to `out`. Copy it with
  `report_group=group_ctx` and `report_group_id=group_id`, when `group_ctx` is not `None`; append
  it unchanged otherwise.
- `st.use is not None`: expand the substituted steps as `expand(substituted, [*stack, ref], nested,
  group_ctx=group_ctx, group_id=group_id)`, carrying both forward. A `use` call inside a `group`
  then tags every step the component expands to, with that group's name and id.

Four call sites start this recursion: `scenario.steps`, `scenario.before`, each `after` rule's
`steps`, and each `interrupts` entry's `steps`
([expand.py:104](../../bajutsu/common/scenario/expand.py) onward). All four start with
`group_ctx=None` and `group_id=None`. None needs any other change.

### Where nested `group` is caught

A `group` can nest in two ways. One is directly inside another `group.steps`. The other is inside
`if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps`. Both are visible by walking
the as-loaded `Scenario` tree, before expansion. Add a validator that recurses from
`scenario.steps` / `scenario.before` / each `rule.steps` / each `entry.steps`. It walks into every
one of those six locations, including `group.steps` itself. It rejects a `group` found there,
naming which of the two shapes matched.

That validator cannot see a third case. A `group` can sit inside a component a `use` call resolves,
itself called from inside a `group`. A `Scenario`, as loaded, holds no component bodies to walk.
The `group_ctx` check inside `expand()`, above, is what catches this one. It runs after `use`
resolution, where the static validator cannot reach.

`expand()` never recurses into `if.then` / `if.else_` / `for_each.steps` / `web.steps` /
`app.steps`. A `group` that reaches one of those through a component slips past the static
validator too, for the same reason. It is never expanded. It reaches `run`'s step loop as an
action-less step, and raises `AssertionError` there. This matches a constraint `use` already has in
the same position. This item stops there for now.

### Multi-target scenarios

`_targets.py:58` already reads `if step.use is not None and len(known) >= 2:`. It refuses `use` on
a scenario declaring two or more `targets`. The reason: expansion discards a `use` step's own
`target` field
([_targets.py:61](../../bajutsu/common/scenario/models/scenario/_targets.py) onward). Expansion
discards a `group` step's `target` field the same way. The same condition gains `or step.group is
not None`. Its message names whichever action triggered it, `use` or `group`. Reusing `use`'s fixed
wording would misdirect an author who wrote `group` instead.

### Excluding `group` from the run loop's own action list

`_RUNTIME_ACTIONS`
([_registry.py:19](../../bajutsu/common/orchestrator/actions/_registry.py)) is `STEP_ACTIONS`
minus `"use"`. It lists every action the run loop can dispatch. Adding `group` to `Step` puts it in
`STEP_ACTIONS` too. Left unexcluded, it would then land in `_RUNTIME_ACTIONS` as well. Change the
exclusion to `a not in ("use", "group")`. Without this change, a `group` left unexpanded — one
routed through a component into an `if`, say — would reach the run loop and find no registered
handler.

### Target config prelude and postlude steps

`_no_component_in_target_steps`
([target_config.py:166](../../bajutsu/common/config/schema/target_config.py)) already refuses
`use` in a target config's `before` / `after` / `interrupts`. The reason: those steps never pass
through scenario-file expansion. Add `group` to the same refusal, for the same reason.

### Reaching `report.html`

`html_report` / `write_report` receive already-expanded `Scenario` objects. `scenario_dict(s)`
([html.py:44](../../bajutsu/common/report/html.py)) dumps them by alias. `report_group` and
`report_group_id` then reach each step's definition dict — `rows.py`'s `plan[i]` — as
`_reportGroup` and `_reportGroupId`. This needs no new plumbing.

Add `group: str | None` and `group_id: int | None` parameters to `_step_detail` / `_step_run_row` /
`_step_skip_row` ([rows.py:30](../../bajutsu/common/report/rows.py) onward), alongside the existing
`from_` parameter. `_merged_rows` / `_phase_rows` / `_after_rows`
([rows.py:382](../../bajutsu/common/report/rows.py) onward) pass `step_def.get("_reportGroup")` and
`step_def.get("_reportGroupId")` straight through. `grouped_provenance`
([from_grouping.py](../../bajutsu/common/report/from_grouping.py)) nulls a repeated `from:` value
after its first row. This differs: every row in one group invocation keeps the same `group_id`
throughout. A later pass needs it on every row, to find where a run starts and ends.

The `steprow` macro can emit up to four `<tr>` elements per step: the body row, plus `alertrow` /
`actrow` / `genrow` when the step calls for them. A step row's own `expand` key is always `None`
([rows.py:201](../../bajutsu/common/report/rows.py),
[rows.py:276](../../bajutsu/common/report/rows.py)). So a step never emits the fifth kind,
`nxdetail` — only a network exchange row does
([rows.py:340](../../bajutsu/common/report/rows.py),
[rows.py:375](../../bajutsu/common/report/rows.py)). Exchange rows stay outside any group, by this
item's scope. The fold, then, has no `nxdetail` state to collide with. It can reuse the same bare
`hidden` attribute `nxdetail` already uses elsewhere. Folding must tag all four of a step's possible
rows with the same `group_id`, not the body row alone.

Add one pure function, `_fold_groups(rows: list[dict]) -> list[dict]`. Run it once, at the end of
each of the three row-building functions above. It inserts one heading row per run of consecutive
rows sharing a `group_id`. A network exchange row, or a not-run step, can interrupt that run. Per
this item's scope, an interrupted group renders as separate, independently headed folds, rather
than one. Each fragment marks its own member rows `hidden`, unless that fragment contains a failing
step. Input and output are plain dicts. `pytest` can test the function directly, without going
through Jinja.

Extend the `steprow` macro in `report.html.j2`
([report.html.j2:42](../../bajutsu/templates/report.html.j2)) to render the heading row, and add
`hidden` plus a `group_id` attribute to member rows. The toggle logic itself belongs in `report.js`,
not `report.html.j2`. `toggleAll` (the "expand all" / "collapse all" buttons) is defined at
[report.js:114](../../bajutsu/templates/report.js); `report.html.j2` carries no toggle logic of its
own beyond the `onclick` call. Add a function there, to flip `hidden` for a `group_id` on a heading
click. Extend `toggleAll` itself too, to open or close every group. Add heading-row styles to
`report.css`.

### Tools that read a scenario before expansion

`load_scenario_file` expands neither `use` nor `group`.
`bajutsu/serve/operations/audit.py:53` and `bajutsu/common/lint.py`'s `provenance_coverage` both
load scenarios through it, and each walks `steps` for its own counts and displays. Once `group`
exists, that walk stops at a `group` step, unless it also descends into `group.steps`. Left as is,
each tool would drop the steps a `group` wraps from whatever it counts or shows. Add that descent to
each tool's own step walk.

`bajutsu/analysis/trace.py:285` also calls `load_scenario_file`, but on `run_dir / "scenario.yaml"`
— the executed scenario `pipeline.py` writes from the already-expanded `scenarios`
([pipeline.py:1899](../../bajutsu/common/runner/pipeline.py) onward). No `group` step can survive
there, so `trace.py` needs no matching change. `use` needs no matching fix anywhere here either: its
steps stay invisible until expansion, regardless.

## Alternatives considered

- **Treat a `use:` / `components:` call as the fold unit.** `report.html` could fold under a called
  component's name, instead of introducing `group`. Rejected: extracting even a short, one-off run
  of steps into a named component forces the param and file-scoping machinery BE-0030 and BE-0422
  built for reuse across scenarios. That machinery is heavier than a long `report.html` calls for.
  Folding a `use:` call's own steps stays open for later work — `group` and `use` already compose,
  since a `group` may contain a `use`.
- **Extend `from:`'s grouping display into folding.** BE-0044's `grouped_provenance` already
  collapses a run of identical `from:` values into one label. Rejected: `from:` records the
  natural-language phrase `record` normalized a step from, not an author-chosen section name.
  Reusing it for both purposes would blur what a reader of `report.html` is looking at: `record`'s
  account of a step's origin, or the author's own organization.
- **Implement `group` as a runtime container step, like `if` / `forEach` / `web` / `app`.**
  Rejected: a runtime container's nested steps already share their parent's numbering. `rows.py`
  documents this as a known source of misattributed rows after such a step
  ([rows.py:442](../../bajutsu/common/report/rows.py) onward). `group` affects nothing at `run`
  time. Compiling it away, like `use`, avoids touching the orchestrator and avoids extending that
  known limitation.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `Group` model and `Step.group` field (`step.py`, new `group.py`), both fields
      `min_length=1`; confirm `_one_action` accepts a lone `group` step
- [x] Add `group` to `_CONTROL_FLOW_ACTIONS` and confirm the existing
      `_no_modifiers_on_control_flow` validator rejects `capture` / `extract` on it
- [x] Add the internal `report_group` / `report_group_id` fields, aliased `_reportGroup` /
      `_reportGroupId`, to `Step` and register both in `_MODIFIERS`; add a validator rejecting any
      author-supplied value, and mark both `SkipJsonSchema` so `bajutsu schema` omits them
- [x] Add a module-wide id counter to `expand.py`, shared across every `expand_components` call
      (including a setup prelude's own call); extend `expand()`'s recursion with `group_ctx` /
      `group_id`, expand `group`, propagate both through `use` substitution, and raise when a
      `group` reaches `expand()` with `group_ctx` already set
- [x] Add a load-time validator that walks the `Scenario` tree — `group.steps` / `if.then` /
      `if.else_` / `for_each.steps` / `web.steps` / `app.steps` — and rejects a `group` found
      nested, naming which shape matched
- [x] Extend `_targets.py`'s multi-target check so it also rejects `group` on a scenario declaring
      two or more `targets`, with a message naming the actual action
- [x] Exclude `group` from `_RUNTIME_ACTIONS` in `_registry.py`
- [x] Add `group` to `_no_component_in_target_steps`'s rejection in `target_config.py`
- [x] Thread `group` / `group_id` through `rows.py`'s row-building functions and add
      `_fold_groups`, tagging every companion row (`alertrow` / `actrow` / `genrow`) alongside the
      body row
- [x] Render the fold: heading row and `hidden` / `group_id` attributes in `report.html.j2`, toggle
      logic and `toggleAll` integration in `report.js`, heading-row styles in `report.css`
- [x] Teach `audit.py` to descend into `group.steps` when walking a pre-expansion scenario.
      `lint.py`'s `provenance_coverage` stays as is; see Log for why
- [x] Confirm `bajutsu lint` / `bajutsu schema` recognize the new `group` action
- [x] Update `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/reporting.md`, and their `docs/ja/`
      mirrors
- [x] Cover the above in the fast suite; `make check` green

Fixing `use`'s own silent drop of `capture` / `extract` / `name` / `from` / `target` stays outside
this item's scope; a separate GitHub issue tracks it
([#2057](https://github.com/bajutsu-e2e/bajutsu/issues/2057)).

**Log**

- `audit.py`'s selector and finding walkers now recurse into `step.group.steps`. The walkers are
  `_step_selectors`, `_nested_step_selectors`, and `_step_findings`. This matches their existing
  treatment of `if` and `forEach`. `lint.py`'s `provenance_coverage` keeps its current scope. It
  counts a scenario's top-level `steps` alone, and it already skips `if` and `forEach` too. Teaching
  it about `group` alone would patch one case of that limitation and leave `if` and `forEach`
  inconsistent; closing the limitation as a whole is outside this item's scope.
- The new `_no_author_report_group` validator added a branch in
  `bajutsu/common/scenario/models/steps/step.py` that no test exercised, which dropped the file
  below its per-file coverage floor (BE-0385). That branch is the validator's pass-through path: an
  explicit `_reportGroup: None` reaches it alone, because pydantic never validates an omitted
  field. A direct test now covers it. `coverage-floors.json` gained one new entry:
  `_group_nesting.py`, at its measured 100 percent.

## References

- [docs/specs/step-groups-report-folding.md](../../docs/specs/step-groups-report-folding.md) — the
  detailed technical design (Japanese) this item summarizes
- [BE-0044 — Scenario provenance](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) —
  the `from:` grouping this item's Alternatives section distinguishes itself from
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md),
  [BE-0422 — Inline, scenario-local components](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components.md)
  — the `use:` / `components:` machinery `group` does not reuse
- [docs/scenarios.md](../../docs/scenarios.md#step-grammar-steps),
  [docs/reporting.md](../../docs/reporting.md#reporthtml) — the docs this item updates

**English** · [日本語](BE-0446-multi-target-use-components-ja.md)

# BE-0446 — Let a multi-target scenario call use: components and group: sections

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0446](BE-0446-multi-target-use-components.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0446") |
| Implementing PR | [#2097](https://github.com/bajutsu-e2e/bajutsu/pull/2097) |
| Topic | Scenario authoring features |
| Related | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md), [BE-0436](../BE-0436-primary-target-default/BE-0436-primary-target-default.md), [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md), [BE-0438](../BE-0438-multi-target-interrupts/BE-0438-multi-target-interrupts.md), [BE-0439](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md) |
<!-- /BE-METADATA -->

## Introduction

A Bajutsu scenario can declare two or more
[targets](../../docs/glossary.md#target-app-device) and interleave its steps across them
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)).
Such a scenario cannot reuse steps today. The loader refuses every `use:` step, which calls a
reusable component, and every `group:` step, which names a folded section of the report. Both
refusals were a deliberate fail-closed choice: BE-0428 left open how a target should reach the steps
that an expansion produces.

This item answers that open question and lifts both refusals. A target reaches an expanded step in
one of two ways:

- **From the caller.** A `use:` or `group:` step may now carry `target`. Expansion stamps that
  target onto every step it produces that omits its own.
- **From the component.** A caller that omits `target` leaves each expanded step to name its own,
  directly or through a target group. A step that names none falls back to the scenario's
  `primaryTarget` when one exists.

```yaml
targets: [showcase-swiftui, web]
steps:
  # The caller fixes the target: every step of `web-login` runs against `web`.
  - use: { component: web-login, with: { email: a@b.com } }
    target: web
  # The component routes its own steps across both targets.
  - use: { component: like-and-verify }
```

Expansion stays a load-time macro. `bajutsu run` still sees the flat per-step `target:` form that
BE-0428 validates, so the runner, the report, and the Command Line Interface (CLI) need no change.

## Motivation

Once a scenario declares two or more targets, every step must name the target it runs against,
unless the scenario declares a `primaryTarget` for steps that omit one. A `use:` step meets neither
form of that rule. Expansion replaces the `use:` step wholesale with the component's
own steps, so expansion would drop any modifier on it without a word. The `Step` model thus refuses
every modifier on a `use:` step, `target` included
([`step.py`](../../bajutsu/common/scenario/models/steps/step.py)). A `group:` step accepts a
`target`, but expansion drops that value all the same. Faced with a required field that one step
cannot hold and another loses without notice, BE-0428 refused both steps outright
([`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)). The three later items
on the same surface left the refusal in place:

- **Primary target.** [BE-0436](../BE-0436-primary-target-default/BE-0436-primary-target-default.md)
  lets a step omit `target`, but its text states that `primaryTarget` does not settle which target a
  `use:` step's expanded steps should receive.
- **Target groups.** [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md)
  stamps one target onto a run of steps. A `use:` nested inside a group still hits the refusal once
  stamped.
- **Interrupts.** [BE-0438](../BE-0438-multi-target-interrupts/BE-0438-multi-target-interrupts.md)
  gives `interrupts` entries a target and keeps refusing a `use:` in their recovery steps.

The refusal falls hardest where reuse matters most. A cross-target flow typically repeats a sign-in,
a navigation, or a setup sequence per client, and those are the sequences authors extract into
components first. The showcase demo
[`cross_platform_app_web.yaml`](../../demos/showcase/scenarios/cross_platform_app_web.yaml) spells
out a five-step web sign-in inline, although the same sequence would read as one `use:` call in a
single-target scenario. An author who adds a second target to an existing scenario must also inline
every component the scenario calls. The copies then drift apart over time.

The refusal also buys less safety than it seems to. `expand_components` already re-runs the full
target check on its own output, since BE-0428 added that pass to catch a component whose steps omit
`target`. The load-time refusal thus guards a question, not a hazard: which target an expanded
step should receive. Once this item defines that answer, the existing post-expansion check enforces
the target rule on every expanded step, the same way it does for a hand-written one. One gap
still needs closing: expansion never reaches a `use:` step inside an `if`, `forEach`, `web:`, or `app:` body.
This item closes that gap as well, as *Expanding inside control-flow bodies* below describes.

We will know this item landed when three observable facts hold:

- The multi-target showcase scenario calls its web sign-in as a component through `use:` and
  `target: web`, instead of spelling out the steps inline.
- A two-target scenario that calls `use:` or `group:` with valid routing loads cleanly. The load no
  longer fails with `use: is not yet supported when the scenario declares 2 targets`. A missing or
  conflicting target still fails, but on its own terms rather than the construct itself.
- Neither the *Limits* section nor the `group:` paragraph of [`docs/scenarios.md`](../../docs/scenarios.md)
  lists `use:` or `group:` among the constructs a multi-target scenario refuses.

## Detailed design

### Two sources of a target for an expanded step

An expanded step receives its target from one of two sources, decided by whether the caller named
one.

| Caller (`use:` / `group:` step) | Expanded step omits `target` | Expanded step names `target` |
|---|---|---|
| Names `target: X` | Stamped with `X` | Accepted when it equals `X`; refused at load time otherwise |
| Omits `target` | Resolves as a hand-written step in the same position does: `primaryTarget` if declared, otherwise a load-time error; an interrupt recovery step instead stays on the runner whose guard fired | Kept as written, then checked against the declared targets |

The first row covers a single-target component: a sign-in, a navigation helper, or any sequence
that knows nothing about which device calls it. The second row covers a cross-target component,
whose own steps route across the scenario's targets. A component can do so with explicit `target`
fields, with target groups, or with a parameter such as `target: ${params.device}`. Parameter
substitution runs before any target check, so the parameterized form needs no new mechanism.

### `target` on a `use:` step

The `Step` model keeps refusing every other modifier on a `use:` step: `capture`, `extract`,
`name`, and `from`. Expansion would still discard each of them. `target` becomes the one exception,
because expansion now carries it forward instead of dropping it. The error message for the remaining
modifiers stays unchanged.

A target group ([BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md))
already stamps its target onto each direct child. A `use:` step inside a target group thus receives
the group's target as its caller target. The group's own rule still holds: its children, a `use:`
step included, omit `target` themselves.

### Conflict: a matching target is accepted, a different one is refused

A caller that names `X` may reach an expanded step that names a target of its own. When both name
the same target, the load accepts the step. A shared component file may name its target so the file
stands on its own, and a caller restating that same value leaves no doubt about the device.

When the two differ, loading fails. Picking either value would be a guess:

- **Caller wins.** The component's author routed the step to a device on purpose, and overriding
  that choice sends the step to a device its author never wrote it for.
- **Component wins.** The caller asked for every step on `X`, and letting one step escape breaks
  that request without notice.

The error names the component chain and the step, in this form:

```text
use: web-login > step 'submit': target 'showcase-swiftui' conflicts with the caller's target 'web'
```

Expansion already tracks the chain of component names it has entered, for cycle detection.
The message thus needs no new bookkeeping.

The same rule also settles a case the current expansion would get wrong once the refusal lifts. When a target group inside a component
holds a `use:` step, `expand_components` stamps the group's target after expanding the nested call.
A nested step that names a different target then keeps that target. Under this item, the target
group passes its target down to the nested `use:` as a caller target. A differing value then fails
at load time.

### Expanding inside control-flow bodies

`expand_components` walks the top of each step list today. It never descends into an `if` / `else`
branch, a `forEach` body, or a `web:` / `app:` block. A `use:` step in one of those bodies thus
stays unexpanded. Under two or more targets, the load-time refusal hides this gap inside an `if` / `forEach`
body. The refusal does not reach a `web:` / `app:` block, whose steps skip it. Wherever the
refusal does not apply, the scenario loads. The unexpanded `use:` step then reaches the run loop
as a step with no action. The run loop then aborts the whole run with an `AssertionError`, not one failed
scenario. [BE-0439](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md)
records the same gap for a `group:` routed into such a body through a component.

Lifting the multi-target refusal would expose that gap to every multi-target scenario with a
branch. This item thus makes `expand()` recurse into each body it can hold:

- **`if` / `else` and `forEach` bodies.** A `use:` step expands there as it does at the top level.
- **`web:` / `app:` blocks.** A `use:` step expands there too. The `use:` step and every step it
  produces must omit `target`, since the block already fixed the device.
- **A `group:` reached inside any of these bodies.** Expansion raises a load-time error. The
  nesting rule of BE-0439 already forbids a `group:` there, and this error extends that rule to a
  `group:` a component carries in.

The recursion also fixes each case where the same `use:` step aborts a run today:

- any body under zero or one target;
- a `web:` / `app:` block under two or more targets.

A scenario that already loads and runs keeps its runtime behavior. An unexpanded `use:` that the
run loop reaches always aborts today, so no passing run depends on one. Load time does change in
one case: a `use:` inside a branch the run never takes, such as an `if` whose condition is false
or a `forEach` over an empty list. Such a `use:` never reaches the run loop, so it never aborts
today. Expanding it at load time turns a broken reference there into a load-time failure for a
scenario that passes now. Examples are an unknown component, a missing or unknown param, and a
cycle. This item accepts that trade: an error at load time on a reference that was never valid
beats hiding it behind an untaken branch.

### How far a caller's target reaches

A caller's target reaches every step the expansion produces, at any depth. The stamp
descends into `if` / `else` branches and `forEach` bodies, and into a nested `use:`, which in turn
passes the target down again. The stamp stops at a `web:` or `app:` block. A step
nested in such a block must omit `target`, because it always runs against the device the enclosing
block resolved. The existing post-expansion check keeps enforcing that rule on expanded steps.

This depth differs from a target group, which stamps its direct children alone and leaves each
`if` / `forEach` body as a fresh scope. A target group's steps sit inline in the scenario, where the
author sees every branch and can name a different target per branch. A component's author writes it
once for any caller. Requiring a body inside the component to name a target would force the
component to know its device, which defeats the caller-side form entirely. A component that needs a
second target inside a branch is a cross-target component and belongs in the second row of the table.

### `group:` follows the same rule

A `group:` step already accepts `target`. It now means the same as on a `use:` step: expansion
stamps the value onto every step the group produces, with the same depth and the same conflict rule.
A `group:` step that omits `target` leaves each child to resolve as a hand-written step in the same
position would. This item leaves the report folding of
[BE-0439](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md) as it
stands, since that folding reads the tags expansion already writes.

### Where the rule is checked

`_check_target_requirements` runs at four points today:

1. The `Scenario` model validator, at load time and before expansion.
2. `expand_components`, after it splices component steps in.
3. `apply_setups`, after it prepends a shared setup prelude.
4. Config-level `before` / `after` hook folding.

At point 1, the check stops refusing a `use:` or `group:` step. Its own `target`, when present, must
still name a declared target. When absent, the step raises no error, and the step does not resolve
to the primary target either. The walk also stops descending into a `group:` step's own `steps`
at this point. Expansion has not yet stamped the caller's target onto those children, so checking
them here would reject a valid `group:` that names `target`. Point 2 checks them instead.
Resolution belongs to the steps the expansion produces, which the later passes see.

At point 2, `expand_components` stamps each expanded step before the existing check runs. The check
then applies the ordinary per-step rule to every step: required under two or more targets, resolved
through `primaryTarget` when omitted, and forbidden inside `web:` / `app:`. An interrupt recovery
step keeps its own rule, described in the next section.

Every consumer that runs a scenario loads it through
[`load_expanded.py`](../../bajutsu/common/scenario/load_expanded.py) or
[`run/cli.py`](../../bajutsu/run/cli.py), and both call `expand_components`. With the recursion
above, no `use:` step leaves that path unexpanded, so lifting the refusal adds no unexpanded route
to the runner.
Static readers such as `lint` and serve's audit parse the unexpanded file. They will now see a
`use:` or `group:` step carrying `target`, and their tests should cover that shape.

### Interrupt recovery steps and lifecycle phases

A `use:` or `group:` step may now appear in an `interrupts` entry's recovery `steps` under two or
more targets. Expansion stamps a named caller target as it does elsewhere. When the caller omits `target`, an expanded
recovery step that omits `target` keeps the existing recovery rule of
[BE-0438](../BE-0438-multi-target-interrupts/BE-0438-multi-target-interrupts.md). The step stays on
the runner whose interrupt guard fired, and it never resolves through `primaryTarget`.

The scenario's own `before` and `after` lifecycle steps follow the same rule as its main `steps`. A
target config's own `before`, `after`, and `interrupts` still refuse `use:` and `group:` entirely,
through `_no_component_in_target_steps`. That refusal has a separate cause (target configs never
pass through component expansion), and this item leaves it in place.

### Scenarios with zero or one declared target

Their target routing changes in one way alone: the new field. A `use:` step's `target` follows the
rule for any step. The author omits it when the scenario declares no targets, and either omits it
or names that one target when the scenario declares one. When the caller names that target,
stamping writes the same value onto each expanded step, which the existing checks accept. When the
caller omits it, expansion stamps nothing. These scenarios still gain the recursion described
under *Expanding inside control-flow bodies*, so a `use:` inside a body expands instead of aborting
the run.

### What stays unchanged

The runner, the report, the CLI, and codegen read a scenario after expansion. Each already handles
the flat per-step `target:` form, and none of them can tell a stamped step from a hand-written one.
The `Component` model gains no new field. A component that names no target stays usable from
single-target and multi-target scenarios alike.

### Docs

- [`docs/scenarios.md`](../../docs/scenarios.md) and its `docs/ja/` mirror: the step table's `use`
  row allows `target`; the *Limits* section and the `group:` paragraph drop their refusals; the
  reuse section gains a two-target example of each row of the table above.
- [`docs/architecture.md`](../../docs/architecture.md) and `DESIGN.md`, where either describes
  component expansion or the target rule.
- The showcase scenario `cross_platform_app_web.yaml` calls its web sign-in through a component.

### Work breakdown (MECE)

1. **Schema.** Exempt `target` from the `use:` modifier refusal in `Step`.
2. **Load-time check.** In `_check_step_target`, replace the `use:` / `group:` refusals with a
   membership check on an explicit `target`, and leave an omitted one unresolved.
3. **Recursion.** Make `expand()` descend into every control-flow body listed above. Raise a
   load-time error on a `group:` it finds there.
4. **Stamping.** In `expand_components`, carry a caller target through `use:`, `group:`, and
   target-group expansion; stamp it with the depth rule; raise the conflict error with the
   component chain.
5. **Tests.** Cover each of the following cases:
   - a `use:` inside each kind of body, in a single-target and a multi-target scenario;
   - a `group:` a component carries into a body;
   - both rows of the table, with a conflicting and an equal value;
   - the depth rule, including the stop at `web:` / `app:`;
   - nested components and a parameterized target;
   - recovery steps and the `primaryTarget` fallback;
   - a `group:` naming `target` under two targets with no `primaryTarget`;
   - a broken `use:` inside an untaken branch, which now fails at load time.
6. **Docs and demo.** The pages listed under *Docs*, in both languages, and the showcase scenario.

## Alternatives considered

- **Caller-side target only.** Make `target` required on a `use:` step under two or more targets,
  and forbid it inside components. The rule is simpler, but a component could never span two
  targets. A cross-target sequence, such as acting on the app and checking on the web, is the kind
  of flow multi-target scenarios exist to express.
- **Component-side target only.** Keep refusing `target` on a `use:` step, and require every
  component step to name its own target. Every component would then have to know its callers'
  target names, so a sign-in component could not serve both an iOS and a web target.
- **Let one side win silently on a conflict.** Rejected above: either choice routes a step to a
  device that someone did not ask for, and determinism favors failing over guessing.
- **Refuse a conflict even when the values match.** Match the stricter rule of target groups, whose
  children may never restate the group's target. Authors read and maintain a shared component file
  apart from its callers, unlike a target group's inline children. Naming its target there documents
  intent, and an equal value carries no ambiguity to refuse.
- **Keep refusing `use:` inside control-flow bodies.** Lift the refusal at the top level and
  directly under a target group alone. The item would stay smaller, but a scenario with a branch
  still could not reuse a sign-in. The single-target abort described above would also remain.
- **Stamp the top level alone, as target groups do.** Leave `if` / `forEach` bodies inside a
  component as fresh scopes. Rejected above: a single-target component with a branch would then
  have to name its own target, which ties it to one caller.
- **Declare the component's targets in the `Component` model.** Add a field listing the targets a
  component expects, mapped by the caller. The mapping adds a new concept for a need that parameter
  substitution (`target: ${params.device}`) already meets.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema: exempt `target` from the `use:` modifier refusal.
- [x] Load-time check: replace the `use:` / `group:` refusals with a membership check.
- [x] Recursion: expand `use:` inside control-flow bodies, and refuse a `group:` there.
- [x] Stamping: carry and stamp the caller target, and raise the conflict error.
- [x] Tests for every case listed in the work breakdown.
- [x] Docs (both languages) and the showcase demo.

Log:

- [#2097](https://github.com/bajutsu-e2e/bajutsu/pull/2097) — Implemented the whole item. `Step` now accepts `target` on a `use:` step. The load-time
  check replaces the `use:` / `group:` refusals with a membership check, and it walks a `group:`'s
  children with the group's target as their caller, so `bajutsu lint` still catches errors inside
  a group. `expand()` recurses into every `if` / `forEach` / `web:` / `app:` body and refuses a
  `group:` a component carries there. It stamps a caller target down to any depth, stopping at
  `web:` / `app:`, and refuses a conflicting target with the component chain in the message. Docs
  in both languages and the showcase `cross_platform_app_web.yaml` demo moved with it.

## References

- [BE-0428 — Multi-target scenario execution](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)
  — introduced `targets` / `target`, and the refusal this item lifts
- [BE-0436 — A primary target](../BE-0436-primary-target-default/BE-0436-primary-target-default.md)
  — the fallback an expanded step without a target resolves through
- [BE-0437 — Target groups](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md)
  — the stamping mechanism this item extends to `use:` and `group:`
- [BE-0438 — Multi-target interrupts](../BE-0438-multi-target-interrupts/BE-0438-multi-target-interrupts.md)
  — the recovery-step rule expanded recovery steps keep
- [BE-0439 — Group steps into named sections](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md)
  — the `group:` step this item also opens to multi-target scenarios
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md)
  and [BE-0422 — Inline scenario components](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components.md)
  — the `use:` / component machinery
- [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py),
  [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py),
  [`bajutsu/common/scenario/models/steps/step.py`](../../bajutsu/common/scenario/models/steps/step.py)

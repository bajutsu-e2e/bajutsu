**English** · [日本語](BE-XXXX-multi-target-step-groups-ja.md)

# BE-XXXX — Target groups: naming one target once for a run of steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-multi-target-step-groups.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)
lets one scenario declare two or more `targets`. Steps then interleave across them. Each step names
which target it runs against.

This item adds one construct on top of that: a **target group**. A target group is a step that
names a target once. It lists every nested action that runs against that target, so an author no
longer repeats `target: <name>` on each one.

```yaml
targets: [showcase-app, showcase-web]
steps:
  - target: showcase-app
    steps:
      - tap: { id: post.like }
      - wait: { for: { id: post.count }, timeout: 5 }

  - target: showcase-web
    steps:
      - assert:
          - value: { sel: { id: post.count }, equals: "1" }
```

A target group is pure authoring sugar. A new load-time pass expands it before validation. That
pass produces the flat, per-step `target: <name>` form BE-0428 already validates and runs.

`bajutsu run` never sees a target group. It sees the same shape it already handles today.

This item changes the scenario schema and its load-time expansion. Nothing else changes: not the
Command Line Interface (CLI), not the launch-and-teardown sequence, not the runner, not the report.
BE-0428 already built all four; this item reuses them unmodified.

## Motivation

Once a scenario declares two or more `targets`, every step must set its own `target`. This includes
an `if`, `forEach`, `web`, or `app` wrapper, not just a leaf action
([`docs/scenarios.md:1191-1193`](../../docs/scenarios.md)). `_check_target_requirements` enforces
the rule
([`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)).

BE-0428's own second worked example shows what that rule costs. It uses two real fixtures, not a
hypothetical. Ten consecutive steps repeat `target: showcase-swiftui` or `target: web`. One value
covers each contiguous run: four steps, then six steps
([BE-0428, lines 124–148](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)).
Two more repetitions follow in `expect` (lines 150–152). Twelve lines name a target, to express two
groups.

That repetition is not a correctness gap. BE-0428's validator already catches a wrong or missing
target at load time. The repetition causes friction, not a bug. A `target:` line reads the same on
every one of ten consecutive steps: it carries no new information, since a reader already saw the
same value one line above. The line exists because the rule requires it. It also crowds out the
step's own action in a diff, since every changed line in a target's run starts with the same key. A
reorder, an insertion, or a copy inside a same-target run risks a stray mismatched `target:`. The
validator catches that mismatch, but not before an author makes the mistake and has to notice and
fix it. A target group removes that whole class of edit. An author sets the target once, in one
place. The nested steps carry no target field of their own to drift out of sync.

Once this ships, an author states each target once per contiguous run of steps, rather than once
per step. BE-0428's own second example above would then state its two targets twice, not ten times.

## Detailed design

### Syntax: `steps` on `Step`

`Step` gains one new field: `steps: list[Step] | None = None`. It sits beside the existing
`target: str | None = None` field BE-0428 already added
([`bajutsu/common/scenario/models/steps/step.py:131`](../../bajutsu/common/scenario/models/steps/step.py)).
Setting `steps` makes a step a target group. A target group carries no action of its own — only a
nested list of ordinary steps.

`Step` already treats every field outside `_MODIFIERS` as one of its mutually exclusive actions.
`_exactly_one` enforces that rule
([`bajutsu/common/scenario/models/_base.py`](../../bajutsu/common/scenario/models/_base.py)).
`_STEP_ACTIONS` computes the action set from `Step.model_fields`
([`bajutsu/common/scenario/models/steps/step.py`](../../bajutsu/common/scenario/models/steps/step.py)).
`steps` joins that action set the same way `web` and `app` already do: it stays out of
`_MODIFIERS`
([`bajutsu/common/scenario/models/steps/_shared.py:8`](../../bajutsu/common/scenario/models/steps/_shared.py)).
A step cannot combine `steps` with `tap`, `wait`, or any other leaf action.

`target` stays a modifier. It keeps combining with `steps` the same way it already combines with
every leaf action.

### Requiring `target` on a group, and forbidding the rest

A leaf action's own `target` becomes required once the scenario declares two or more targets;
fewer than two, and the field stays optional. That is BE-0428's own rule
([`_check_target`](../../bajutsu/common/scenario/models/scenario/_targets.py)).

A target group requires `target` unconditionally, no matter how many targets the scenario declares.
Its purpose is naming a target once for its own nested steps. A target group with no target would
need an inheritance rule of its own, deciding what its children run against instead — reopening the
implicit-default design this item does not take (see *Alternatives considered*). `Step` enforces
this itself, in a new `model_validator`. The rule needs no access to the enclosing scenario: whether
a group needs a target never depends on how many the scenario declares, unlike the leaf-action rule
above.

The same validator refuses four more fields on a target group: `capture`, `extract`, `name`, and
`from_`. Each is a modifier read off the one step the runner executes — a screenshot capture, an
extracted variable, a report label, a provenance note. A target group never reaches the runner:
expansion, below, replaces it with its own nested steps before the scenario finishes loading.
Setting one of those four fields on it would vanish with no error and no trace. Refusing them at
load time turns that silent loss into a named, load-time error instead.

### Nesting: a group's children omit `target`; a group cannot sit inside `web:` or `app:`

Every step nested directly inside a target group's own `steps` list must omit `target`, whatever
kind of step it is — a leaf action, an `if`, a `forEach`, or a `web`/`app` block. The group already
fixed it. BE-0428 already uses this same shape for a step nested inside `web:` or `app:`, which must
also omit `target` because the enclosing block already resolved one
([`docs/scenarios.md:1222-1225`](../../docs/scenarios.md)). A target group extends that same
omit-inside rule to its own children, rather than inventing a second one. A child that sets its own
`target` anyway is a load-time error, not a silent override: an override would let one line deep
inside a group aim at a different target than every line around it, the exact confusion the group
exists to remove (see *Alternatives considered*). This rule also rejects a target group nested
directly inside another one, with no separate check: a nested group always sets its own `target`,
which is precisely what an immediate child may never do.

The omission a group requires of its children is shallow, not recursive. An `if` or `forEach`
nested directly inside a group inherits the group's target for its own condition, the same as a
leaf action would. The steps inside its own `then`, `else`, or body are a fresh scope: each still
requires its own explicit `target`, or a target group of its own, the same as it would outside any
group. A `web` or `app` block nested directly inside a group inherits the group's target the same
way, opening its bridge against the target the group already named. The steps nested inside that
block keep omitting `target` under the existing BE-0428 rule, unrelated to the group.

A target group is refused outright inside a `web:` or `app:` block's own nested steps. Inside one of
those, every step already runs against the one target the block itself resolved
([`docs/scenarios.md:1222-1225`](../../docs/scenarios.md)); a target group there would either repeat
that same target for no purpose or claim a different one no driver is open for. `if:` and `forEach:`
carry no such restriction. Nesting a target group inside their `then`, `else`, or body is ordinary,
since those are an ordinary step list like any other.

### Expansion: a load-time pass, not a runtime construct

A new pass, `_expand_target_groups`, handles this. It lives beside the existing
`_check_target_requirements`, in
[`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py).
It walks a scenario's `steps`, `before`, and every rule's `steps` in `after`. It recurses into an
`if`'s `then`/`else` and a `forEach`'s `steps`, the same way `_check_target_requirements`'s own
`walk_steps` helper already does. Reaching a target group, it replaces that one step with its own
nested steps and stamps the group's `target` onto each of them in place. Reaching a `web` or `app`
block, it recurses into that block's own nested steps, carrying the same `inside_web` flag
`_check_target_requirements` already threads through its own walk. Finding a target group there is
the one case this pass refuses, per the nesting rule above, rather than expands. After this pass
runs, no target group remains anywhere in the scenario: every step it produces is an ordinary,
already-stamped step, indistinguishable from one an author wrote by hand.

`_check_target_requirements` runs at four points today. Two later steps mutate an
already-validated `Scenario` in place, and pydantic does not re-run a `model_validator` against a
plain attribute write, so each of those two steps re-runs the check by hand:

1. `Scenario`'s own load-time `model_validator`
   ([`bajutsu/common/scenario/models/scenario/scenario.py:159`](../../bajutsu/common/scenario/models/scenario/scenario.py)).
2. `expand_components`, after it splices a `use:` component's own steps in
   ([`bajutsu/common/scenario/expand.py:118`](../../bajutsu/common/scenario/expand.py)).
3. `apply_setups`, after it prepends a shared setup prelude's steps
   ([`bajutsu/common/scenario/expand.py:236`](../../bajutsu/common/scenario/expand.py)).
4. Config-level `before`/`after` hook-folding, after a `model_copy` splices a target's own hooks in
   ([`bajutsu/common/runner/pipeline.py:1415`](../../bajutsu/common/runner/pipeline.py)).

`_expand_target_groups` runs once more, right before each of those four calls, rather than adding a
fifth checkpoint of its own. A component's or a setup prelude's own steps can carry a target group
the same way a scenario's own steps can, so the same points that already re-validate a
freshly-spliced step list are the points that need to expand one first. Once expansion runs ahead of
it, `_check_target_requirements` itself needs no change: every target group is already gone by the
time it walks the tree, so a step that came from a group and one an author wrote by hand enforce the
same rule, checked the same way, because they share the same shape by then.

This ordering also means every existing target rule keeps applying to a group's children, with no
new code. A `use:` step nested inside a group, once expanded and stamped, hits BE-0428's existing
refusal of `use:` under two or more declared targets the same way a hand-written one would
([`docs/scenarios.md`](../../docs/scenarios.md), the *Limits* section). A group used under a
scenario that declares zero or one target hits the existing checks too — "target is set but the
scenario declares no targets", or "target does not match the scenario's one declared target" — on
each of its stamped children, the same way a stray `target:` on a hand-written step fails today.

### What stays unchanged

The runner, the report, and the CLI need no change. `run_scenario`, `_run_one`, `StepOutcome`,
`RunResult`, and every CLI flag BE-0428 added or changed all read a scenario after expansion already
ran. None of them can tell a step came from a target group rather than from an author's own flat
list, because the scenario is already a flat list by the time any of them see it. `record`,
`crawl`, and `codegen` stay untouched for the same reason in reverse: nothing in this item changes
what they generate, so they keep emitting the flat, per-step `target:` form they already do.

### Docs

[`docs/scenarios.md`](../../docs/scenarios.md)'s `targets` / `target` section gains the
target-group syntax, its nesting and omission rules, and a worked example. BE-0428's own second
example fits best, rewritten as two groups instead of ten repeated lines, to show the saving
directly. Its `docs/ja/` mirror gets the same addition.

## Alternatives considered

- **An implicit "same target as the previous step unless stated" default**, with no new syntax at
  all: a step that omits `target` reuses whichever target the step above it named. Rejected: a
  reader checking one step's target would have to scan backward through the file to find the last
  explicit one, and an insertion or a reorder could change which target a later step inherits, with
  no marker at that step showing inheritance rather than declaration. BE-0428 rejected a related
  shape, `switchTarget`, for a similar reason: it reads as an imperative routing action a reader has
  to track across the whole step list, rather than a property visible on the step it governs
  (BE-0428, *Alternatives considered*). This item's own load-time-rejected form of the same
  objection explains why a group's children may not override its target either (see *Nesting*,
  above): a step's effective target must stay visible on the step itself.
- **A dedicated wrapper keyword**, such as `group:` or `section:`, holding `target` and `steps` as
  its own fields, mirroring how `web:` holds `within` and `steps`. Rejected: it adds a second piece
  of vocabulary for an idea `target` and `steps` already express directly on `Step`, with no new
  expressive power. Setting `target` alongside a nested `steps` list already reads as "this target,
  these steps", with no wrapper name needed.
- **A runtime-native construct**, keeping the group as a real node the runner and report see
  through execution, the way `web` and `app` do. Rejected as disproportionate to the problem:
  BE-0428 needed a runtime construct because `web`/`app` open a real driver context nothing else can
  substitute for. A target group changes nothing about execution; it merely spells the same
  per-step `target` more than once, so a load-time expansion delivers the same authoring benefit
  without touching the runner, the report, or the CLI at all.
- **Allowing a child to override a group's target.** A middle ground between full inheritance and
  full omission, letting one step inside a same-target run diverge when it genuinely needs to.
  Rejected: it reintroduces the exact ambiguity a group exists to remove, since a reader could no
  longer tell a step's target from the group's own header line. A stray override left over from an
  earlier edit would fail the same way today's ordinary repeated-`target:` mistake does — at the
  exact point in a scenario meant to no longer allow that mistake.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: `Step.steps`, the target-required-on-a-group validator, and the
      capture/extract/name/from_ refusal on a group.
- [ ] Expansion: `_expand_target_groups`, called right before each of the four existing
      `_check_target_requirements` call sites; the `web:`/`app:` nesting refusal; the
      child-target-override refusal.
- [ ] Docs: `docs/scenarios.md` and its `docs/ja/` mirror.
- [ ] Tests: schema validator (group requires target; forbidden modifiers; child omission; nested
      group rejection; `web:`/`app:` nesting rejection), expansion correctness (a group-authored
      scenario produces the same stamped step list a hand-flattened version would), and the
      re-expansion surviving `expand_components`, `apply_setups`, and hook-folding the same way
      `_check_target_requirements` already does.

## References

- [`docs/scenarios.md`](../../docs/scenarios.md) — the scenario file format this item extends.
- [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)
  — BE-0428's target-routing rule. This item reuses it unmodified, once expansion runs first.
- [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py) — the existing
  compile-time expansion module (`use:` components, setups). This item's own expansion pass joins it
  in spirit, though its code lives beside `_check_target_requirements` instead (see *Expansion*,
  above).
- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) —
  the multi-target execution this item's syntax sugar sits on top of, unmodified.

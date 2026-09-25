**English** · [日本語](BE-XXXX-primary-target-default-ja.md)

# BE-XXXX — A primary target: let a multi-target scenario's steps omit `target`

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-primary-target-default.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) |
<!-- /BE-METADATA -->

## Introduction

A [multi-target scenario](../../docs/scenarios.md#targets--target-multi-target-scenarios-be-0428)
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
gains an optional top-level field, `primaryTarget`, naming one entry of its own `targets` list. Once
a scenario sets it, a step, an `if` / `forEach` / `web` / `app` wrapper, or a top-level `expect`
entry may omit `target` again, the same way it always could on a single-target scenario — and an
omitted one now runs against the named primary target instead of raising a load error:

```yaml
- name: liking a post on the app shows up on the web
  targets: [showcase-app, showcase-web]
  primaryTarget: showcase-app
  steps:
    - tap: { id: post.like }              # target omitted: runs on showcase-app
      extract:
        postId: { sel: { id: post.id } }
    - target: showcase-web
      wait: { for: { id: "post.${vars.postId}.likeCount" }, timeout: 10 }
  expect:
    - target: showcase-web
      value: { sel: { id: "post.${vars.postId}.likeCount" }, equals: "1" }
```

`primaryTarget` is optional at every declared-target count, and a scenario that never sets it keeps
today's rule unchanged: every step and every top-level `expect` entry still names its own `target`
explicitly once `targets` holds two or more entries. Nothing about `targets` itself changes, and a
scenario declaring zero or one target is untouched either way.

## Motivation

BE-0428's own validator, `_check_target_requirements`
([`bajutsu/common/scenario/models/scenario/_targets.py:80-143`](../../bajutsu/common/scenario/models/scenario/_targets.py)),
requires `target` on every step and every `expect` entry once a scenario names two or more targets.
That rule is deliberate. BE-0428's own *Alternatives considered* rules out any implicit source for a
step's destination, one a reader would otherwise have to memorize. The rule still fits a scenario
that keeps switching targets throughout. It fits less well the shape a cross-platform check usually
takes instead: one target does almost all the acting, and the scenario detours to a second target
once, to verify or to feed one step's result.

BE-0428's own worked example shows that second shape
([`docs/scenarios.md:1193-1206`](../../docs/scenarios.md)). One target is named on every step but
one. A reader has to reread `target: showcase-app` six times to learn one fact true of the whole
scenario: this test's business is on `showcase-app`. The scenario steps aside to `showcase-web`
once. The repeated field states that fact once and restates it five more times for nothing.
Worse, the one line that actually matters — the step naming the *other* target — no longer stands out
against six identical neighbors making the same statement.

`primaryTarget` answers a narrower question than BE-0428's own validator does. It does not say
*which* target a step runs against. It says *which one goes unstated*. Once a scenario declares that
answer, a step states its own target only where the target is not that answer — the same economy a
single-target scenario already gets by never stating `target` at all. A reader can then read a
cross-target scenario's shape at a glance: every step carrying an explicit `target` is the one that
leaves the primary. Nothing else in the scenario needs a second look.

## Detailed design

### Declaring `primaryTarget`

`Scenario` gains a new field, `primary_target: str | None = Field(default=None,
alias="primaryTarget")`, beside its existing `targets: list[str]`
([`bajutsu/common/scenario/models/scenario/scenario.py:59`](../../bajutsu/common/scenario/models/scenario/scenario.py)).
A new check, `_check_primary_target`, enforces it against `scenario.targets`. It lives in
[`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py), called once from
`_check_target_requirements` before that function walks any step. Leaving `primaryTarget` unset is
always legal. Setting it while `targets` is empty is a load error: `primaryTarget is set but the
scenario declares no targets`, mirroring `_check_target`'s own message for that same shape on a
step's own `target`. Setting it to a name outside `targets` is a load error too, naming the declared
list. A `targets` list of one target accepts a matching `primaryTarget` as well, rather than
rejecting it as redundant — the single-target case already lets a step name that one target
explicitly instead of omitting it ([`docs/scenarios.md:1214-1215`](../../docs/scenarios.md)), and
`primaryTarget` restates that one name the same harmless way.

### What "omit" resolves to

`_check_target_requirements` calls one function, `_check_target`, once per step and once per
`expect` entry ([`_targets.py:26-46`](../../bajutsu/common/scenario/models/scenario/_targets.py)).
That function gains a `default: str | None` parameter, and a return value each caller now keeps.
Today the caller throws that return value away: a passing check already returns the field's own
value, unchanged. This item's version returns `default` in one new case that would otherwise still
fail — two or more declared targets, `target` omitted, and a `default` on hand. Every other branch
returns the field's own value as it does today. So does every scenario that leaves `primaryTarget`
unset: `default` stays `None` there, the new case never triggers, and the load error `target is
required` still raises.

`_check_target_requirements` passes `scenario.primary_target` as `default` into every one of its own
calls to `_check_target`. That covers a step's own `target`
([`_targets.py:68`](../../bajutsu/common/scenario/models/scenario/_targets.py), inside
`_check_step_target`) and a top-level `expect` entry's `target`
([`_targets.py:142`](../../bajutsu/common/scenario/models/scenario/_targets.py)) alike. Each caller
writes the return value straight back onto the field it just checked: `step.target = ...`,
`a.target = ...`. Neither `Step` nor `Assertion` carries a `frozen` config
([`bajutsu/common/scenario/models/_base.py:35`](../../bajutsu/common/scenario/models/_base.py)), so
this plain attribute write uses the same technique `expand_components` already uses to replace a
scenario's own `steps` in place.

The write-back matters beyond keeping `Scenario` tidy. Leaving an omitted `target` as `None`, and
letting the runner fall back to whatever driver it already has, would break two pieces of code that
already read `step.target` / `a.target` directly — both written for a world where the field is never
`None` once a scenario declares two or more targets.

First, `_steps_for_target`
([`bajutsu/common/runner/pipeline.py:1263-1275`](../../bajutsu/common/runner/pipeline.py)) narrows a
scenario to one target's own steps, for the BE-0082 capability preflight. It keeps a step whose
`target` is `None` in *every* target's own narrowed copy. That is correct today: `None` occurs only
on a scenario with at most one declared target, where every step runs against that one target
regardless. Under this item, an unresolved `None` on a two-target scenario would mean something
else — "runs on the primary" — and `_steps_for_target` has no way to tell the two meanings apart. It
would keep that step in *both* targets' preflight groups, checking it against a backend it never
actually runs against.

Second, `_route`
([`bajutsu/common/orchestrator/loop/_step_runner.py:78-114`](../../bajutsu/common/orchestrator/loop/_step_runner.py))
updates `self.state.last_target` when the step it routes carries a non-empty `target`. So
does its reset of the `prev_after` / `prev_after_screenshot` pair BE-0428 keys off a device switch
([`_step_runner.py:87`](../../bajutsu/common/orchestrator/loop/_step_runner.py)). A step landing back
on the primary after a detour needs that same reset — the `app, web, app` shape BE-0428's own code
comment names
([`_step_runner.py:98-103`](../../bajutsu/common/orchestrator/loop/_step_runner.py)). An explicit
`target: <primary>` already triggers it. A step left at `None` would skip the reset, and hand that
third step a screenshot and an accessibility tree the second step's own device produced.

Writing the resolved name back at load time avoids both mistakes at once, and needs no change to
either piece of runner code. Once every step's `target` is a concrete declared name, `_route`'s
existing `self.by_target.get(step.target)` lookup
([`_step_runner.py:88`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) dispatches a
primary-bound step the same way it already dispatches an explicitly-named one. `_steps_for_target`
groups it into the right target's check without ever seeing a `None`. The resolved name reaches the
report the same way
an author-written one already does: `StepOutcome(index=idx, action=kind, target=self.target)`
([`_step_runner.py:137-150`](../../bajutsu/common/orchestrator/loop/_step_runner.py)), and
`AssertionResult` via `replace(r, target=name)` inside `_evaluate_expect`
([`_functions.py:196-227`](../../bajutsu/common/orchestrator/loop/_functions.py)).

The resolution is flat, not inherited from an enclosing `if` / `forEach`. A step nested inside either
one, that omits `target`, resolves to `scenario.primary_target` directly — never to the wrapper's
own. One rule applies at every nesting depth. A reader never has to trace an enclosing wrapper's own
`target` to know what a nested step's omission means.

### What stays required

A step nested inside a `web` or `app` block keeps its existing, opposite rule. It must still omit
`target` outright. Setting one there still fails at load time, because the step always runs against
the device the enclosing block already resolved
([`_targets.py:49-57`](../../bajutsu/common/scenario/models/scenario/_targets.py)). `primaryTarget`
changes nothing about that block's own `target` field, which resolves the same way any other step's
does: explicit, or defaulted to the primary once a scenario declares one.

A `use:` step and a non-empty `interrupts` list stay rejected outright once a scenario declares two
or more targets, the same way BE-0428 left them.
[`_targets.py:58-67, 128-137`](../../bajutsu/common/scenario/models/scenario/_targets.py) gives the
reason for each: expansion discards a `use:` step's own `target`, and an `interrupts` entry's
`condition` has no enclosing step to fix a target for it in the first place. Both are open questions
BE-0428 deferred, not guessed at, and `primaryTarget` answers neither one. A future item is still
free to decide how a component's own steps should resolve `target`, or which target an interrupt's
condition should poll.

### Work breakdown (MECE)

1. **Schema.** `Scenario.primary_target: str | None`; `_check_primary_target`, called once per
   scenario before the step walk; `_check_target`'s new `default` parameter and return value;
   `_check_step_target` and the `expect` loop writing the resolved value back onto `step.target` /
   `a.target`.
2. **Docs.** `docs/dsl-grammar.md` (the `primaryTarget` field and the updated `target`-requirement
   rule) and `docs/scenarios.md` (the `targets` / `target` section, with a worked example), and their
   `docs/ja/` mirrors.
3. **Tests.** Schema: `primaryTarget` membership and the empty-`targets` rejection; a step, a
   nested `if` / `forEach` step, a `web` / `app` wrapper, and a top-level `expect` entry each
   resolving to the primary when omitted, across zero/one/two-or-more `targets`; a step nested inside
   `web` / `app` still rejecting an explicit `target`; a `use:` step and a non-empty `interrupts`
   still rejecting outright. Runner: a step landing back on the primary after a detour through another
   target resets `prev_after` / `prev_after_screenshot` the same way an explicit `target: <primary>`
   already does; the capability preflight groups a primary-resolved step into only its own target's
   check, never the other's.

## Alternatives considered

| Alternative | Summary | Why not chosen |
|---|---|---|
| Positional primary | Treat `targets`' own first entry as the primary, adding no new field. | BE-0428's own *Alternatives considered* already rejected this shape for `Step.target` itself: reordering `targets` would silently change which target every omitted step runs against, the exact implicit rule a reader would have to memorize that BE-0428 exists to avoid. |
| Mapping-form `targets` | Extend `targets` to accept `{name, primary}` entries (`targets: [{name: showcase-app, primary: true}, {name: showcase-web}]`), instead of a separate field. | `targets` stays a plain `list[str]` everywhere else in the schema — `tags`, `capturePolicy`'s tokens, and every other scenario-level list share that shape. A mapping form doubles the YAML Ain't Markup Language (YAML) for the common case (every entry, not only the primary) and buys nothing a separate `primaryTarget: <name>` does not already say more plainly. |
| `primaryTarget` required once `targets` holds two or more | Force every multi-target scenario to declare a primary, even one that names `target` on every step and never relies on the omission. | Every multi-target scenario already committed under BE-0428 explicitly names `target` on every step; requiring `primaryTarget` regardless would force an edit to each one for a field it would never use. Optional costs nothing for a scenario indifferent to it — the load-time rule for an unset `primaryTarget` is unchanged from today's. |
| Omission only on leaf action steps | Let a plain action step (`tap`, `type`, `wait`, and so on) omit `target`, but keep requiring it explicitly on `expect` entries and on `if` / `forEach` / `web` / `app` wrappers, since those decide or verify which target a check runs against. | The rule this item adds is already one sentence: omitted `target` means `primaryTarget`, everywhere `target` is legal. Splitting it into "omittable here, required there" adds a second rule the reader has to keep straight, for a distinction — leaf step versus wrapper or `expect` — that carries no reason to resolve differently once a scenario has already opted in by declaring a primary. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: `Scenario.primary_target`, `_check_primary_target`, and `_check_target`'s `default`
      parameter threaded through `_check_step_target` and the `expect` loop.
- [ ] Docs: `docs/dsl-grammar.md`, `docs/scenarios.md`, and their `docs/ja/` mirrors.
- [ ] Tests: schema resolution across every step shape and declared-target count; the runner's
      `prev_after` reset on a return to the primary; the capability preflight's per-target grouping.

## References

- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) —
  the `targets` / `target` mechanism this item extends, and the source of the `target`-required rule
  this item narrows.
- [`docs/scenarios.md#targets--target-multi-target-scenarios-be-0428`](../../docs/scenarios.md#targets--target-multi-target-scenarios-be-0428) —
  the current, authoritative reference for `targets` / `target` this item's own doc work extends.
- [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py) —
  the validator this item's schema unit changes.
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) —
  `_route`, whose existing dispatch and `last_target` bookkeeping this item relies on unchanged.

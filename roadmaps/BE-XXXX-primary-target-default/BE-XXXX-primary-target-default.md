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
gains an optional top-level field, `primaryTarget`, naming the first entry of its own `targets`
list — the same one the runner already treats as primary for leasing and crash recovery (see
*Declaring `primaryTarget`* below). Once a scenario sets it, a step, an `if` / `forEach` / `web` /
`app` wrapper, or a top-level `expect` entry may omit `target` again, the same way it always could on
a single-target scenario — and an omitted one now runs against the primary instead of raising a load
error:

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

Leaving `primaryTarget` unset keeps today's rule unchanged, at every declared-target count: every
step and every top-level `expect` entry still names its own `target` explicitly once `targets` holds
two or more entries, and `targets` of zero or one entries behaves exactly as it does today either
way. Setting `primaryTarget` changes that only for a scenario declaring two or more targets. Setting
it on a scenario declaring none is itself a load error, and setting it on a scenario declaring exactly
one is legal only when it names that one target — both spelled out in *Declaring `primaryTarget`*
below.

## Motivation

BE-0428's own validator, `_check_target_requirements`
([`bajutsu/common/scenario/models/scenario/_targets.py:80-143`](../../bajutsu/common/scenario/models/scenario/_targets.py)),
requires `target` on every step and every `expect` entry once a scenario names two or more targets.
That rule is deliberate. BE-0428's own *Alternatives considered* rules out any implicit source for a
step's destination, one a reader would otherwise have to memorize. The rule fits a scenario that keeps
switching targets throughout. It fits less well a scenario built around one target's own flow, checked
once against a second target — the shape BE-0428's own worked example already shows: one `tap`
against `showcase-app`, then a `wait` and a `value` assertion against `showcase-web` to confirm it
landed
([`docs/scenarios.md:1193-1206`](../../docs/scenarios.md)). Naming `target: showcase-app` on that one
step carries real information there: a reader has no other way to know which target the step acts on.

The same statement carries no new information on the fourth or the tenth step of a longer flow that
never leaves `showcase-app`. Each repetition after the first states a fact the scenario's own shape
already settled. Only the one step that finally turns to `showcase-web` says something the reader did
not already know, and it says that no more clearly surrounded by ten identical neighbors than by one.

`primaryTarget` answers a narrower question than BE-0428's own validator does. It does not say
*which* target a step runs against. It says *which one goes unstated*. Once a scenario declares that
answer, a step states its own target only where the target is not that answer — the same economy a
single-target scenario already gets by never stating `target` at all. A reader can then read a
cross-target scenario's shape at a glance: a step carrying an explicit `target` other than the primary
is the one that acts on a different platform. Nothing else needs a second look — including a step that
restates the primary's own name explicitly, which stays legal but adds nothing either way.

## Detailed design

### Declaring `primaryTarget`

`Scenario` gains a new field, `primary_target: str | None = Field(default=None,
alias="primaryTarget")`, beside its existing `targets: list[str]`
([`bajutsu/common/scenario/models/scenario/scenario.py:59`](../../bajutsu/common/scenario/models/scenario/scenario.py)).
A new check, `_check_primary_target`, enforces it against `scenario.targets`. It lives in
[`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py), called once from
`_check_target_requirements` before that function walks any step.

Leaving `primaryTarget` unset is always legal, and changes nothing. Setting it while `targets` is
empty is a load error: `primaryTarget is set but the scenario declares no targets`, mirroring
`_check_target`'s own message for that same shape on a step's own `target`. Setting it to any other
value is legal only when that value is `targets`' own first entry, `targets[0]` — rejected otherwise
with a load error naming both the value given and the one required. This is a real restriction, not a
stand-in for "any declared target": `_lease_set` and `_target_runtimes`
([`bajutsu/common/runner/pipeline.py:930-943, 1000-1007`](../../bajutsu/common/runner/pipeline.py))
already single out `targets[0]` as *the* primary throughout the runner today — its lease is the one
the crash-recovery loop judges, and `_runtime_for`
([`bajutsu/common/runner/pipeline.py:1011-1060`](../../bajutsu/common/runner/pipeline.py)) reuses its
already-resolved evidence context verbatim, rather than rebuilding it through the target-config-first
path every other declared target goes through. Letting `primaryTarget` name a different entry would
let the scenario file's own idea of "primary" and the runner's own diverge silently; requiring the match keeps
the two one fact, and the claim later in this section — that no runner code needs to change — depends
on it. A `targets` list of exactly one target accepts a matching `primaryTarget` too, rather than
rejecting it as redundant, for the same reason as always: `targets[0]` is also that one entry.

### What "omit" resolves to

`_check_target_requirements` calls one function, `_check_target`, once per step and once per `expect`
entry ([`_targets.py:26-46`](../../bajutsu/common/scenario/models/scenario/_targets.py)). That
function is annotated `-> None` today, and every branch either raises or falls through without an
explicit `return` — Python returns `None` from an unmodified fall-through exactly as it would from a
literal `return None`. This item changes every one of those falling-through branches to `return
target` explicitly, and adds one new branch: `return default` when two or more targets are declared,
`target` is `None`, and a `default` is on hand. Changing every branch, not only the new one, matters
more than it looks: a version that added `return default` only to the new case, and left the rest
falling through unchanged, would still type-check and pass a test that supplies an explicit `target`
— right up until that same explicit choice reached the caller as `None` regardless, since an
unmodified branch returns `None` exactly as readily as the one meant to. `_check_target_requirements`
passes `scenario.primary_target` as `default` into every call; a scenario that leaves `primaryTarget`
unset passes `None`, so the new branch's condition never holds and every other branch's now-explicit
return leaves the field exactly where it already stood.

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

Writing the resolved value back this way is only safe because every `Step` and `Assertion` instance a
`Scenario` holds is that scenario's own — with one existing exception this item must also close.
`apply_setups` ([`bajutsu/common/scenario/expand.py:208-236`](../../bajutsu/common/scenario/expand.py))
resolves a shared setup reference once and caches the resulting `Step` list, then splices the *same*
object instances into every scenario that names that reference:
`scenario.steps = [*cache[ref], *scenario.steps]`. Two scenarios sharing a setup today only read those
shared steps, so sharing the objects costs nothing; this item's write-back would not stay read-only.
The first scenario `_check_target_requirements` validates would stamp a shared step's `target` with
its own `primary_target`. A second scenario sharing that same setup — with no `primaryTarget` of its
own, or a different one — would then see that step's `target` as if its own author had written it
explicitly: either raising `target is set but the scenario declares no targets` on a scenario
declaring none, or silently misrouting the step to the first scenario's primary. `apply_setups` closes
this gap by cloning each cached step before splicing it into a given scenario —
`st.model_copy(deep=True)` for every step in `cache[ref]` — so a write-back this item introduces lands
on that scenario's own copy, never on one another scenario already validated.

The write-back matters beyond that. Leaving an omitted `target` as `None`, and letting the runner fall
back to whatever driver it already has, would break two pieces of code that already read
`step.target` / `a.target` directly — both written for a world where the field is never `None` once a
scenario declares two or more targets.

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

Writing the resolved name back at load time avoids both mistakes at once, and — because `primaryTarget`
is pinned to `targets[0]`, per *Declaring `primaryTarget`* above — needs no change to either piece of
runner code. Once every step's `target` is a concrete declared name, `_route`'s existing
`self.by_target.get(step.target)` lookup
([`_step_runner.py:88`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) dispatches a
primary-bound step the same way it already dispatches an explicitly-named one. `_steps_for_target`
groups it into the right target's check without ever seeing a `None`. The resolved name reaches the
report the same way an author-written one already does:
`StepOutcome(index=idx, action=kind, target=self.target)`
([`_step_runner.py:137-150`](../../bajutsu/common/orchestrator/loop/_step_runner.py)), and
`AssertionResult` via `replace(r, target=name)` inside `_evaluate_expect`
([`_functions.py:196-227`](../../bajutsu/common/orchestrator/loop/_functions.py)).

One pre-existing gap in `_steps_for_target` is worth naming rather than leaving for a reader to find
later. Its own docstring already limits the narrowing to top-level entries: "a nested `if` / `forEach`
body keeps whatever it holds, since a nested step carries its own `target` and the preflight walks
into it anyway" ([`pipeline.py:1266-1269`](../../bajutsu/common/runner/pipeline.py)). BE-0428 already
lets a nested step name a target different from its enclosing wrapper's own, so a nested step whose
target differs from the wrapper that carried it into the preflight's filtered list is checked against
the wrong backend's capabilities today — independently of this item, reachable already by writing that
mismatch out by hand. This item does not create the gap. It does make the same construct a little
easier to reach without meaning to, since a nested step that merely omits `target` now resolves to the
primary silently, rather than forcing its author to notice and name a target themselves. Closing
`_steps_for_target`'s own top-level-only narrowing is not this item's to fix — the gap predates it, and
BE-0428 never closed it either — but the work breakdown below adds a test that reaches this exact
shape, so the gap stays visible rather than silently passing, and names it as a known limitation for
whichever item takes on `_steps_for_target`'s own recursion next.

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

1. **Schema.** `Scenario.primary_target: str | None`; `_check_primary_target`, requiring it unset or
   equal to `targets[0]`, called once per scenario before the step walk; `_check_target`'s new
   `default` parameter, with every branch — not only the new one — returning the field's resolved
   value explicitly; `_check_step_target` and the `expect` loop writing that value back onto
   `step.target` / `a.target`; `apply_setups` cloning each cached step
   (`st.model_copy(deep=True)`) before splicing it into a scenario, so this item's write-back never
   lands on a step another scenario already validated.
2. **Docs.** `docs/dsl-grammar.md` (the `primaryTarget` field, its `targets[0]` constraint, and the
   updated `target`-requirement rule) and `docs/scenarios.md` (the `targets` / `target` section, with
   a worked example), and their `docs/ja/` mirrors.
3. **Tests.** Schema: `primaryTarget` accepted only unset or equal to `targets[0]`, and the
   empty-`targets` rejection; a step, a nested `if` / `forEach` step, a `web` / `app` wrapper, and a
   top-level `expect` entry each resolving to the primary when omitted, under two or more declared
   `targets`; separately, a step under zero or one declared `targets` behaving exactly as it does
   today whether or not `primaryTarget` is set; a step nested inside `web` / `app` still rejecting an
   explicit `target`; a `use:` step and a non-empty `interrupts` still rejecting outright; two
   scenarios sharing one `setup` reference with different `primaryTarget` values each resolving their
   own omitted steps correctly, proving the cloned-step fix. Runner: a step landing back on the
   primary after a detour resets `prev_after` / `prev_after_screenshot` the same way an explicit
   `target: <primary>` already does; the capability preflight groups a primary-resolved top-level step
   into only its own target's check; and, naming the known limitation above rather than passing over
   it, a primary-resolved step nested inside a non-primary-routed wrapper is checked against the
   wrapper's own backend, recorded as an existing gap for a later item.

## Alternatives considered

| Alternative | Summary | Why not chosen |
|---|---|---|
| Positional primary | Treat `targets`' own first entry as the primary, adding no new field at all. | BE-0428's own *Alternatives considered* already rejected this shape for `Step.target` itself, for the same reason it applies here: reordering `targets` would silently change which target every omitted step runs against, with no field anywhere recording that the order carries meaning. This item's own `primaryTarget` still resolves to `targets[0]` (see *Declaring `primaryTarget`*), but as a name the author states and the loader checks against the list — a `targets` edit that moves the intended primary out of first place is a load error naming the mismatch, not a silent change in behavior. |
| Thread `primaryTarget` through the runner, allowing any declared target | Instead of constraining `primaryTarget` to `targets[0]`, extend `_lease_set`, `_target_runtimes`, and `_runtime_for`'s evidence-context resolution (`pipeline.py:930-1060`) to read `scenario.primary_target` wherever they hard-code `targets[0]` today. | The more flexible option — an author could pick any declared target as primary regardless of declared order. Rejected for this item: it turns a schema-only change into a runner change touching leasing, crash-recovery lease judging, and evidence-context resolution, for a benefit — choosing a primary independent of declaration order — an author already gets for free by ordering `targets` itself. Left as a follow-up if a real scenario ever needs primary and first-declared to differ. |
| Mapping-form `targets` | Extend `targets` to accept `{name, primary}` entries (`targets: [{name: showcase-app, primary: true}, {name: showcase-web}]`), instead of a separate field. | `targets` stays a plain `list[str]` everywhere else in the schema — `tags`, `capturePolicy`'s tokens, and every other scenario-level list share that shape. A mapping form doubles the YAML Ain't Markup Language (YAML) for the common case (every entry, not only the primary) and buys nothing a separate `primaryTarget: <name>` does not already say more plainly. |
| `primaryTarget` required once `targets` holds two or more | Force every multi-target scenario to declare a primary, even one that names `target` on every step and never relies on the omission. | Every multi-target scenario already committed under BE-0428 explicitly names `target` on every step; requiring `primaryTarget` regardless would force an edit to each one for a field it would never use. Optional costs nothing for a scenario indifferent to it — the load-time rule for an unset `primaryTarget` is unchanged from today's. |
| Omission only on leaf action steps | Let a plain action step (`tap`, `type`, `wait`, and so on) omit `target`, but keep requiring it explicitly on `expect` entries and on `if` / `forEach` / `web` / `app` wrappers, since those decide or verify which target a check runs against. | The rule this item adds is already one sentence: omitted `target` means `primaryTarget`, everywhere `target` is legal. Splitting it into "omittable here, required there" adds a second rule the reader has to keep straight, for a distinction — leaf step versus wrapper or `expect` — that carries no reason to resolve differently once a scenario has already opted in by declaring a primary. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: `Scenario.primary_target`, `_check_primary_target` (unset or `targets[0]` only),
      `_check_target`'s `default` parameter with every branch returning explicitly, threaded through
      `_check_step_target` and the `expect` loop, and `apply_setups` cloning its cached steps.
- [ ] Docs: `docs/dsl-grammar.md`, `docs/scenarios.md`, and their `docs/ja/` mirrors.
- [ ] Tests: schema resolution across every step shape and declared-target count; the shared-setup
      cloning fix; the runner's `prev_after` reset on a return to the primary; the capability
      preflight's per-target grouping, including the known nested-wrapper limitation.

## References

- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) —
  the `targets` / `target` mechanism this item extends, and the source of the `target`-required rule
  this item narrows.
- [`docs/scenarios.md#targets--target-multi-target-scenarios-be-0428`](../../docs/scenarios.md#targets--target-multi-target-scenarios-be-0428) —
  the current, authoritative reference for `targets` / `target` this item's own doc work extends.
- [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py) —
  the validator this item's schema unit changes.
- [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py) — `apply_setups`,
  whose shared-step cache this item's write-back requires cloning.
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) — `_lease_set` and
  `_target_runtimes`, the source of the `targets[0]`-is-primary rule this item's own `primaryTarget`
  is pinned to rather than diverging from.
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) —
  `_route`, whose existing dispatch and `last_target` bookkeeping this item relies on unchanged.

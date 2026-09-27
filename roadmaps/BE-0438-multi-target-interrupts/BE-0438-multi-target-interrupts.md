**English** · [日本語](BE-0438-multi-target-interrupts-ja.md)

# BE-0438 — Give `interrupts` entries a `target`, defaulting to the primary target

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0438](BE-0438-multi-target-interrupts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0438") |
| Topic | Scenario authoring features |
| Related | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario's [`interrupts`](../../docs/scenarios.md#interrupts-handling-unpredictable-interstitial-screens)
entries gain a `target: str | None = None` field.
[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)
already gave this field to a `Step` and to a top-level `expect` entry. It names which of the
scenario's declared [targets](../../docs/glossary.md#target-app-device) that entry concerns. An
`interrupts` entry's `target` differs from those two fields in one way. It stays optional even
once the scenario declares two or more targets. An entry that omits `target` watches the scenario's
primary target. The primary target is the target `--target` names. For a scenario declaring its own
`targets`, the first one declared plays that role instead. An entry that sets `target` watches that named target
instead. A `Step` inside that entry's own `steps` inherits the entry's target when it omits its
own. Those `steps` are the recovery steps that clear the interrupt. A `Step` inside them does not
inherit the scenario's primary target.

This change also removes a restriction BE-0428 left in place on purpose. Today, a scenario
declaring two or more targets refuses any non-empty `interrupts` list outright. That refusal fires
at load time, regardless of what the list's entries contain.

## Motivation

BE-0428 let one scenario interleave `Step`s across the backends Bajutsu supports. Those are iOS
Simulator, web, and Android. BE-0428 added `target` to `Step`, and to a top-level `expect` entry,
to do this. It left `interrupts` out on purpose. Nothing named which target's element tree an entry's
`condition` should poll. A validator named `_check_target_requirements` enforces this today
([`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)). It refuses a
scenario's `interrupts` the moment `len(scenario.targets) >= 2`. It never looks at a single entry
first.

That refusal costs every scenario mixing two targets a mechanism a single-target scenario keeps.
`interrupts` handles a screen with no fixed point in the step sequence. An OS permission prompt and
a Cookie-consent banner are two examples. An author working around the block today has two options.
One hand-writes an `if` branch ahead of every action that might trigger such a screen. The other
drops the second target and loses `interrupts` altogether. Whether that screen appears has nothing
to do with which target the scenario routes a given step to.

Falling back to the primary target when an entry omits `target` is not new. This codebase already
applies the same pattern one field over. `expect`'s own `Assertion.target` resolves an omitted
value to the primary target, at evaluation time. The call is
`groups.setdefault(a.target or primary_target, []).append(i)`
([`_functions.py:198`](../../bajutsu/common/orchestrator/loop/_functions.py)). This item extends
that same fallback to `interrupts`. A scenario declaring two or more targets keeps using
`interrupts` the same way. An author needs a specific reason to route one entry elsewhere.

## Detailed design

### Unit 1 — `Interrupt.target`

[`Interrupt`](../../bajutsu/common/scenario/models/steps/interrupt.py) gains
`target: str | None = None`. Its docstring documents that an omitted value resolves to the primary
target. `Step.target` ([`step.py:131`](../../bajutsu/common/scenario/models/steps/step.py)) stays
untouched. BE-0428 already requires a `Step`'s own `target` once the scenario declares two or more
targets. That rule keeps holding, unchanged, alongside this new, always-optional field.

### Unit 2 — Validation

[`_check_target`](../../bajutsu/common/scenario/models/scenario/_targets.py) gains a
`required: bool = True` parameter. With `required=False`, the function skips a branch. That branch
otherwise raises once `len(known) >= 2` and `target is None`. The function still rejects a named
`target` absent from `known`. [`_check_target_requirements`](../../bajutsu/common/scenario/models/scenario/_targets.py)
drops the outright rejection at lines 128–137. It validates each `interrupts` entry instead. The
call is `_check_target(entry.target, known=known, context="interrupts entry", required=False)`. An
entry's `condition` keeps going through
[`_reject_assertion_target`](../../bajutsu/common/scenario/models/scenario/_targets.py) unchanged.
`target` lives on `Interrupt` itself, never on `condition`. That split mirrors what `Step`'s own
`assert:` list and `if.condition` already enforce.

`_check_step_target` and the `walk_steps` helper that calls it gain a third mode. Two modes exist
today. A top-level step requires `target` once the scenario declares two or more targets. A step
nested inside `web:`/`app:` forbids `target` outright. The new mode covers a `Step` inside an
`Interrupt`'s own `steps`. That step always may omit `target`, and a `target` it does set must
still name a declared target. The recovery steps never require `target`. An omitted one falls back
to the enclosing entry's target, not the scenario's primary one.

### Unit 3 — Config-level `interrupts` reject `target`

[`TargetConfig.interrupts`](../../bajutsu/common/config/schema/target_config.py) already lives
inside one `targets.<name>` block. Letting an entry there name a different target would contradict
the block it lives in. A new validator rejects a `target` set on a `targets.<name>.interrupts`
entry. It sits next to
[`_no_component_in_target_steps`](../../bajutsu/common/config/schema/target_config.py), and it
runs at load time.

The same validator also rejects `target` on any `Step` inside that entry's own `steps`.
`TargetConfig` never passes through `_check_target_requirements`, the scenario-side validator.
Nothing checks a config-level `Step.target` today because of that gap. Left unchecked,
`targets.web.interrupts[0].steps[0].target: ios` would load cleanly. `_StepRunner._route`
([`_step_runner.py:87`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) would then
dispatch that step to the `ios` runner at runtime. That is the same hole this unit exists to close,
one level down. The walk reuses a shape `_no_component_in_target_steps` already builds. That shape
is `[s for entry in self.interrupts for s in entry.steps]`.

### Unit 4 — Runtime composition

[`_runtime_for`](../../bajutsu/common/runner/pipeline.py) copies every `scenario.interrupts`
entry into each target's `TargetRuntime.interrupts`. It does this today, unconditionally.
[`_run_on_lease`](../../bajutsu/common/runner/pipeline.py) does the same for the primary's own
`_LoopConfig.interrupts`. Both switch to filtering. The filter is
`(entry.target or primary_target) == <that target's name>`. Both apply it once `_routed` returns a
non-empty list.

`primary_target` reuses a value `_run_on_lease` already computes. That happens at line 1175. The
value is `next(iter(self._routed(s)), "")`. Neither call site derives its own, separate value.

That condition matters. `_routed` returns an empty list in two cases. One: the scenario declares no
`targets`. Two: it declares one target, but the runner's own config carries no `targets.<name>`
map. Both are the ordinary single-target path most scenarios take today. On that path,
`primary_target` is `""`. An entry may still name a real target, though. A scenario declaring
`targets: [ios]` may write an `interrupts` entry with `target: ios`. `_check_target`'s `n == 1`
branch accepts that. Filtering there would compare `("ios" or "") == ""`. That comparison never
holds, so the entry would drop out. Every such scenario's `interrupts` would stop firing, with no
warning. Skipping the filter whenever `_routed` is empty avoids that regression. Validation already
guarantees this for every entry on that path. Each one either omits `target` or names the one
target that exists. Nothing needs filtering out there.

Config-level `interrupts` (`run_defaults.interrupts`) stay unfiltered either way. Each already
belongs to the one target whose config declared it.

`_config_for` needs no change
([`_functions.py:1180-1203`](../../bajutsu/common/orchestrator/loop/_functions.py)). Neither does
the `by_target` construction it feeds. Both already pass `TargetRuntime.interrupts`
straight through to each target's own `_LoopConfig`. A filtered list there is all `_InterruptGuard`
([`_interrupt_guard.py`](../../bajutsu/common/orchestrator/loop/_interrupt_guard.py)) needs. This
guard is already built once per `_StepRunner`
([`_step_runner.py:598-605`](../../bajutsu/common/orchestrator/loop/_step_runner.py)).

`Interrupt.steps`' own routing needs no change either. `_StepRunner._route`
([`_step_runner.py:87`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) redirects a step
to another runner. It does this when the step itself sets `target`, never otherwise. An omitted
`target` leaves the step on
`self` — the runner whose `_InterruptGuard` had fired. `_run_recovery`
([`_step_runner.py:116`](../../bajutsu/common/orchestrator/loop/_step_runner.py)) runs
`entry.steps` on that same `self`. This unit places each entry into the runner that owns it. That
runner already is the entry's own effective target. An omitted `Step.target` inside `entry.steps`
lands in the right place, with no extra mechanism. An earlier draft of this item computed an
effective target instead. It swapped that value into the recovery run explicitly. *Alternatives
considered* below records why review dropped that approach.

### Unit 5 — Test coverage

A new regression test confirms `interrupts` still fires on the ordinary single-target path. It
covers two scenarios. One declares no `targets`. The other declares one target, with no
`targets.<name>` map configured. Both must behave the same way before and after this change.

A new integration test declares two targets in one scenario. One target shows an
interrupt-triggering screen; the other does not. The test confirms the interrupt fires against the
target its `target` field (or the fallback) names. The same target clears it. The other target's
`_InterruptGuard` never sees it. A third case in the same test declares a config-level
`targets.<name>.interrupts` entry. It confirms that entry's recovery steps run against that
config's own target. They never run against the scenario's primary target.

### Unit 6 — Documentation

[`docs/scenarios.md`](../../docs/scenarios.md) carries two sections that need updating, for
different reasons. The
[`interrupts`](../../docs/scenarios.md#interrupts-handling-unpredictable-interstitial-screens)
section gains the new `target` field and its primary-target fallback. It says nothing about the
restriction in place today. That restriction lives in the **Limits** section instead, around lines
1247–1256. That section says the loader refuses a non-empty `interrupts` outright. The new text
replaces that sentence and the one after it. The rest of *Limits* stays unchanged. That covers the
`use:` restriction and the `Assertion.target` rules on `condition` — neither one changes.
[`docs/ja/scenarios.md`](../../docs/ja/scenarios.md) carries the matching Japanese update.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Require `target` on an `interrupts` entry once the scenario declares two or more targets, matching `Step` and `expect` | A `Step`'s own required `target` tells a reader which of several interleaved targets that one action concerns. Most `interrupts` entries in a multi-target scenario still concern the primary target, so requiring every entry to say so would add a repeated line with no routing decision behind it. |
| Let one `interrupts` entry name several targets at once (`targets: list[str]`) | Each target's own `_InterruptGuard` already polls its own element tree independently, so one entry naming several targets would still expand into one check per target internally. The single-value `target` keeps the field symmetric with `Step.target`; an author needing the same condition and recovery steps on two targets writes two entries. |
| Let a `Step` inside `Interrupt.steps` default to the scenario's primary target when it omits its own | The target where an interrupt fires and the target its recovery steps act on are the same target in the ordinary case. Defaulting to the primary target would force `target` onto nearly every recovery step of an entry that watches a non-primary target. |
| Compute `Interrupt.steps`' effective target (`entry.target or primary_target`) explicitly and swap it into the recovery run | `_StepRunner._route` (`_step_runner.py:87`) already redirects only a step that sets `target`. An omitted one stays on `self`, the runner whose `_InterruptGuard` fired. `_run_recovery` (`_step_runner.py:116`) already runs there. Once Unit 4 places each entry into the runner that owns it, this mechanism needs no help. Injecting the scenario's `primary_target` explicitly instead misroutes a config-level entry (`target` always `None`, per Unit 3) to the scenario's primary target rather than to the config's own runner. |
| Allow `target` on a `targets.<name>.interrupts` entry, matching the scenario-level field | An entry under `targets.<name>.interrupts` already belongs to the one target that config block configures. A `target` field there could only repeat that same name or contradict it, and neither says anything a reader cannot already read off the block itself. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `Interrupt.target` (`bajutsu/common/scenario/models/steps/interrupt.py`).
- [ ] Unit 2 — `_check_target`'s `required` parameter, the removed blanket rejection, and the new
      `Interrupt.steps` validation mode (`_targets.py`).
- [ ] Unit 3 — reject `target` on a config-level `interrupts` entry and on any `Step` inside its
      `steps` (`target_config.py`).
- [ ] Unit 4 — filter `interrupts` per target in `_runtime_for` and `_run_on_lease`, conditioned on
      `_routed` being non-empty (`pipeline.py`).
- [ ] Unit 5 — test coverage: single-target regression, cross-target isolation, config-level route.
- [ ] Unit 6 — `docs/scenarios.md` / `docs/ja/scenarios.md`.

## References

- [Spec — マルチターゲットシナリオにおける interrupts の target 対応](../../docs/specs/multi-target-interrupts.md) —
  the design write-up this item formalizes.
- [BE-0428 — Multi-target scenario execution](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) —
  added `Step.target` and `Assertion.target`. It left `interrupts` out on purpose.
- [BE-0314 — Scenario interrupt handlers](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) —
  `interrupts` itself, before target routing existed.

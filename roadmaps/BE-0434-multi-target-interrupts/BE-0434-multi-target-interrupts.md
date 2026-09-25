**English** · [日本語](BE-0434-multi-target-interrupts-ja.md)

# BE-0434 — Give `interrupts` entries a `target`, defaulting to the primary target

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0434](BE-0434-multi-target-interrupts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0434") |
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
`interrupts` exists for a screen with no fixed point in the step sequence. An OS permission prompt
and a Cookie-consent banner are two examples. A scenario author working around the block today
hand-writes an `if` branch. It goes ahead of every action that might trigger such a screen. The
alternative drops the second target and loses `interrupts` altogether. Neither choice follows from
the screen's appearance, which has nothing to do with target routing.

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

### Unit 4 — Runtime composition

[`_runtime_for`](../../bajutsu/common/runner/pipeline.py) copies every `scenario.interrupts` entry
into each target's `TargetRuntime.interrupts`. It copies unconditionally, today.
[`_run_on_lease`](../../bajutsu/common/runner/pipeline.py) does the same for the
primary's own `_LoopConfig.interrupts`. Both switch to filtering by
`(entry.target or primary_target) == <that target's name>`. Both reuse the `primary_target` value
`_target_runtimes` already resolves as `routed[0]`. Config-level `interrupts`
(`run_defaults.interrupts`) stay unfiltered. Each already belongs to the one target whose config
declared it.

### Unit 5 — Effective target for recovery steps

A `Step` inside `Interrupt.steps` that omits `target` runs against the entry's effective target.
That target is `entry.target or primary_target`, not the scenario's primary target. Both
`run_scenario` and the `_step_runner.py` interrupt-recovery path pass a `primary_target` value in.
That value feeds the recovery run. Each swaps in the entry's effective target for that value while
running the entry's own `steps`. Each restores the scenario's primary target afterward.

### Unit 6 — Cross-target firing coverage

A new integration test declares two targets in one scenario. One target shows an
interrupt-triggering screen; the other does not. The test confirms the interrupt fires against the target its
`target` field (or the fallback) names. The same target clears it. The other target's
`_InterruptGuard` never sees it.

### Unit 7 — Documentation

[`docs/scenarios.md`](../../docs/scenarios.md) carries two sections that need updating. One is
"interrupts". The other is "What a multi-target run does with the rest of a target's config". Both
say a scenario refuses `interrupts` outright once it declares two or more targets. Both instead
describe the new `target` field and its primary-target fallback.
[`docs/ja/scenarios.md`](../../docs/ja/scenarios.md) carries the matching Japanese update.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Require `target` on an `interrupts` entry once the scenario declares two or more targets, matching `Step` and `expect` | A `Step`'s own required `target` tells a reader which of several interleaved targets that one action concerns. Most `interrupts` entries in a multi-target scenario still concern the primary target, so requiring every entry to say so would add a repeated line with no routing decision behind it. |
| Let one `interrupts` entry name several targets at once (`targets: list[str]`) | Each target's own `_InterruptGuard` already polls its own element tree independently, so one entry naming several targets would still expand into one check per target internally. The single-value `target` keeps the field symmetric with `Step.target`; an author needing the same condition and recovery steps on two targets writes two entries. |
| Let a `Step` inside `Interrupt.steps` default to the scenario's primary target, the way a top-level step's own omitted `target` behaves | The target where an interrupt fires and the target its recovery steps act on are the same target in the ordinary case. Defaulting to the primary target would force `target` onto nearly every recovery step of an entry that watches a non-primary target. |
| Allow `target` on a `targets.<name>.interrupts` entry, matching the scenario-level field | An entry under `targets.<name>.interrupts` already belongs to the one target that config block configures. A `target` field there could only repeat that same name or contradict it, and neither says anything a reader cannot already read off the block itself. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `Interrupt.target` (`bajutsu/common/scenario/models/steps/interrupt.py`).
- [ ] Unit 2 — `_check_target`'s `required` parameter, the removed blanket rejection, and the new
      `Interrupt.steps` validation mode (`_targets.py`).
- [ ] Unit 3 — reject `target` on a config-level `interrupts` entry (`target_config.py`).
- [ ] Unit 4 — filter `interrupts` per target in `_runtime_for` and `_run_on_lease` (`pipeline.py`).
- [ ] Unit 5 — resolve `Interrupt.steps`' effective target for recovery steps.
- [ ] Unit 6 — cross-target interrupt-firing integration test.
- [ ] Unit 7 — `docs/scenarios.md` / `docs/ja/scenarios.md`.

## References

- [Spec — マルチターゲットシナリオにおける interrupts の target 対応](../../docs/specs/multi-target-interrupts.md) —
  the design write-up this item formalizes.
- [BE-0428 — Multi-target scenario execution](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) —
  added `Step.target` and `Assertion.target`. It left `interrupts` out on purpose.
- [BE-0314 — Scenario interrupt handlers](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) —
  `interrupts` itself, before target routing existed.

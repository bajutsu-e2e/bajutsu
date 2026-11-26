**English** · [日本語](BE-XXXX-declarative-hook-conditions-ja.md)

# BE-XXXX — Gate `hooks` on targets, platforms, routes, and environment variables

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-declarative-hook-conditions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md), [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md), [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md), [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md), [BE-0497](../BE-0497-tag-conditional-hooks/BE-0497-tag-conditional-hooks.md), [BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks.md) |
<!-- /BE-METADATA -->

## Introduction

This item widens the `when` block that decides whether a `hook` runs. Today the block reads scenario
tags alone.
It gains `targets`, `platforms`, `routes`, and `env`. A *route* is the kind of destination
a target runs on, such as the iOS Simulator or a real device.

The widened block also applies to a second kind of `hook`. Today `when` guards external commands
alone. After this item it also guards the `setup` and `cleanup` steps that wrap a scenario. Every
field is declarative. The block holds no expression and runs no probe command.

## Motivation

A *command `hook`* is an external command that Bajutsu runs around a run or a scenario. A target
declares such entries under `targets.<name>.hooks`. A scenario declares its own under `hooks`.
BE-0497 gave each entry an optional `when: { tags, exclude }` block. Tags describe what a scenario
is about. They do not describe where the scenario runs.

Preparation often depends on where a scenario runs:

- One scenario runs under `--target ios` and under `--target android`. Each run needs its own
  preparation.
- A shared `defaults.hooks` block should serve some targets and skip the others.
- A real device needs a step that a simulator does not need.
- A mock server starts on a developer's machine, while continuous integration (CI) provides its own.

None of these cases has a good answer today. A command can read `BAJUTSU_TARGET` and branch inside
its script. The report then shows the entry as run even when the script did nothing. A scenario's
own entry receives its first declared target alone in that variable. The `setup` and `cleanup` steps
have no condition at all. Their `if` step checks the screen, so it cannot read a target name.

After this item ships, a reader can check three observable differences. One scenario file carries the
preparation of both platforms. The report lists each skipped entry and block with its condition. A
`hook` script no longer needs a branch on the target name or on a CI variable.

## Detailed design

### The fields of `when`

| Field | Type | The condition holds when |
|---|---|---|
| `tags` | list of strings | the scenario carries at least one of the tags (BE-0497, unchanged) |
| `exclude` | list of strings | the scenario carries none of the tags (BE-0497, unchanged) |
| `targets` | list of target names | a judged target is listed |
| `platforms` | list of `ios`, `android`, `web`, `fake` | a judged target has a listed platform |
| `routes` | list of route names | a judged target runs on a listed route |
| `env` | map from name to string or null | every listed variable matches |

Bajutsu already has route names (BE-0467). They decide which settings a destination can apply.

The iOS names are `ios-simulator`, `ios-device`, and `ios-live`. The other names are `android`
and `web`. The `ios-live`
route is a live WebDriver endpoint. The `fake` backend has no route, so a `routes` condition never
holds there.

```yaml
defaults:
  hooks:
    beforeRun:
      - cmd: "./scripts/start-mock.sh"
        when: { env: { CI: null } }          # only where CI is unset
    beforeScenario:
      - cmd: "./scripts/trust-certificate.sh"
        when: { platforms: [ios], routes: [ios-device] }
```

### How the fields combine

- Values inside one list combine with "or".
- Different fields combine with "and".
- `targets`, `platforms`, and `routes` describe one and the same judged target.
- `exclude` still wins over `tags`.
- A `when` block with no constraint fails to load, as today.

A name in `targets` that the config does not declare fails to load, because such a name is a typo.

### Which target a condition judges

The place of a condition decides which target `targets`, `platforms`, and `routes` read:

| Where the condition sits | Judged target |
|---|---|
| a block that carries `target` | that target |
| an entry, block, or cleanup rule under `targets.<name>.hooks` | `<name>`, the owner |
| an entry, block, or cleanup rule of a scenario | any target the scenario drives |

An entry merged from `defaults` or a template belongs to the target that received it. Suppose
`defaults.hooks` holds an entry with `when: { targets: [ios] }`. The copy that `android` received
never runs, even in a scenario that drives both targets.

A condition written directly under `targets.<name>.hooks` can be unsatisfiable from the start. Its `targets`
or `platforms` may leave out the judged target. Its `routes` may name no route of that target's
platform. Such a condition fails to load, as a `when` with no constraint does. The check reads each
entry as written, before `defaults` or a template merges in. A merged copy that never runs stays
valid.

A scenario drives the targets it declares. A scenario that declares none drives the run's own target.
In a scenario, `{ targets: [app], routes: [ios-device] }` asks whether `app` runs on a real device.

### Environment variables

`env` reads the environment of the `bajutsu run` process. Under `serve`, that process runs on the
worker, so the worker's environment decides. The entry's own `env` field plays no part.

A string value asks for an exact, case-sensitive match. A null value asks for an unset variable. A
variable set to the empty string counts as set.

A value that the config parser reads as a boolean or a number
fails to load, so authors quote it. Reports print the condition as written. They never print the
actual value of a variable.

### Run-level lists

A `beforeRun` or `afterRun` entry has no single scenario. BE-0497 checks its condition against a set
of scenarios. The set holds the selected scenarios that drive the entry's target. This item keeps the
set for `tags` and `exclude` alone.

The other fields need no scenario. `targets`, `platforms`, and `routes` read the owner target, and
`env` reads the process. A condition made of these fields can hold even under `record`, `crawl`,
and `repl`. Their scenario set is empty. Each command learns the owner's route before its first
`hook`.
A condition with `tags` still does not hold for an empty set. The load check on a run-level entry
narrows. Today it refuses any `when` without `tags`. After this item it refuses an `exclude` without
`tags` alone.

The run decides the conditions of `beforeRun` and `afterRun` once, before the first `beforeRun`
entry. Each `afterRun` entry uses the decision taken then.

### Conditional setup and cleanup steps

A `setup` list accepts a *conditional block* beside ordinary steps. The block holds `when` and
`steps`. A `cleanup` rule accepts `when` beside `on`. Both forms work in a scenario and in
`targets.<name>.hooks`.

```yaml
setup:
  - when: { platforms: [ios] }
    steps:
      - tap: { id: onboarding.skip }
  - tap: { id: tab.home }
cleanup:
  - on: always
    when: { platforms: [android] }
    steps:
      - tap: { id: debug.reset }
```

The block resembles the existing *target group* (BE-0437). A group names one `target` for its
steps.
A block may also carry `target`. Without it, each step follows the usual target rules. A block needs
at least one step. A block cannot sit inside another block, inside `steps`, or inside an `if` step.

Bajutsu evaluates every block before the first step of the scenario. The condition reads the same
facts as a command `hook`, never the screen. To branch on the screen, authors keep using an `if` step.

Load-time checks apply to every block, even a block whose condition does not hold. These checks
cover components, `target` names, and the other rules for steps. This item adds no check that links a
variable saved inside a block to a later step that reads it.

### Reading the output of a conditional entry

A scenario reads a command's output as `${hooks.<name>.<key>}`. Under BE-0497, a scenario cannot read
an entry whose condition the scenario does not meet. This item keeps that rule in two stages:

| Fields in the condition | Which scenarios Bajutsu checks | When |
|---|---|---|
| `tags`, `exclude`, `targets`, `platforms` | every expanded scenario | at load, as today |
| `env`, `routes` | the selected scenarios | after Bajutsu knows each route, before the first `hook` |

A condition that names `env` or `routes` waits for the second stage as a whole. The first stage
checks conditions that name neither field.

A mismatch exits 2 in both stages. The second stage skips deselected scenarios. The files alone do not decide their result.
The `env` field depends on the machine, and a route depends on flags such as `--backend` and `--udid`. A token inside a skipped block needs no value, so
Bajutsu does not check it.

### Preflight, routing, code generation, and evidence

- **Preflight.** The capability check reads the steps that remain after evaluation. A missing capability inside a
  skipped block never stops a run.
- **Routing under `serve`.** `serve` picks a worker by the capabilities a job needs. It drops a
  block's needs when `tags`, `exclude`, `targets`, or `platforms` rule the block out. It also
  drops them when `routes` lists the route of no judged target. A worker does not change a route.
  `serve` already derives each route from the config and the request to route the job (BE-0467). An `env` block keeps its needs, since
  `serve` does not know a worker's environment.
- **Code generation.** `bajutsu codegen` does not accept a conditional block, nor a `cleanup` rule with `when`.
  The `if` step has the same limit. A block gated on `env` has no run
  that could decide it.
  Deciding the static fields would need the run's target and platform rules inside `codegen`. A
  later item can add that.
- **Command policy.** The worker still checks every declared command (BE-0492). A condition that does not hold
  changes nothing here.
- **Evidence.** A skipped command shows as `skipped (when)` with its condition, as today. A skipped
  block shows the same label in the report.

### Prime directives

A condition compares strings from the files, the config, and the process. No Large
Language Model (LLM) takes part. Identical files, flags, environments, and destinations always select
the same `hooks`. The fields name no particular app, so per-app choices stay in config and scenarios.

## Alternatives considered

| Alternative | Why this item does not adopt it |
|---|---|
| A probe command whose exit code decides | The result stays unknown until the run. The report could state no reason beyond a number. A later item can add the field if declarative fields prove too narrow. |
| Judge every condition against any target the scenario drives | A shared `defaults` entry would run once per driven target, including targets the author meant to skip. The cost of this choice: a target's own entry cannot ask which other targets the scenario drives. |
| An expression such as `target == 'ios' and env.CI` | BE-0034 and BE-0497 turned down an expression language. It needs a parser and a documented grammar. |
| A separate `excludeEnv` field | A null value covers the common case of an unset variable. No known case needs "any value but this one". |
| A `virtual` or `physical` field instead of `routes` | Bajutsu would need a new rule to classify Android and WebDriver destinations. The route names exist already. |
| A `when` modifier on every step | The change would reach the scenario body, far past `hooks`. |
| New condition kinds inside the `if` step | `if` reads the screen at run time. Static facts in the same field would mix two meanings. |
| Evaluate static fields in `codegen` | `codegen` would need the run's target and platform rules. Refusing the block is the smaller start. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: the four new fields of `when`, with their load errors.
- [ ] Schema: the narrowed load check on run-level entries.
- [ ] Schema: the load error for a target's own condition that can never hold.
- [ ] Evaluation: one predicate over a scenario, its driven targets, their routes, and the environment.
- [ ] Command `hooks`: apply the predicate to scenario-level and run-level lists.
- [ ] Setup and cleanup: the conditional block and `when` on a cleanup rule.
- [ ] Block evaluation before the first step.
- [ ] Output tokens: the two-stage check.
- [ ] Preflight, `serve` routing, and `codegen` behavior for conditional blocks.
- [ ] Evidence: skipped blocks in the report.
- [ ] User docs: `docs/configuration.md`, `docs/scenarios.md`, and `docs/codegen.md`.
- [ ] Design docs: `DESIGN.md` and `docs/architecture.md`.
- [ ] The translated mirror of each document.

## References

- [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md): scenario tags.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): `setup` and `cleanup`.
- [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups.md): the target group.
- [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md): environment routes.
- [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md): command `hooks` of a target.
- [BE-0497](../BE-0497-tag-conditional-hooks/BE-0497-tag-conditional-hooks.md): the `when` block.
- [BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks.md): command `hooks` of a scenario.

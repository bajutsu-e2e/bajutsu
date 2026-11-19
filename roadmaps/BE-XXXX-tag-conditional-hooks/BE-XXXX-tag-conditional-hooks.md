**English** · [日本語](BE-XXXX-tag-conditional-hooks-ja.md)

# BE-XXXX — Gate a command hook on scenario tags

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-tag-conditional-hooks.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md), [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md), [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds an optional `when` block to each command hook entry. The block names scenario tags.
The runner runs the entry when the scenario's tags match the block, and skips the entry otherwise.
The match follows the rule that the `--tag` and `--exclude` flags already apply to selection. A
scenario matches when it carries any listed tag, and an excluded tag always wins.

The block applies to both places a hook entry can live:

- the four lists under `targets.<name>.hooks` that
  [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md) adds: `beforeRun`,
  `afterRun`, `beforeScenario`, and `afterScenario`;
- the two lists of a scenario's own `hooks` block, `beforeScenario` and `afterScenario`. The sibling
  proposal *Declare external commands in a scenario to run before and after that scenario* adds
  these two lists.

A scenario-level entry checks the scenario about to run. A run-level entry checks the run's whole
set of scenarios and runs when any one of them matches. A scenario that reads an entry's output must
itself match that entry, and the loader rejects one that does not.

## Motivation

A target's command hooks prepare data for its scenarios, and BE-0491 runs every entry for every
scenario. Preparation in a real suite often concerns a subset. Scenarios about payments need a
sandbox payment account. Scenarios that write to a database need a reset that takes seconds.
Scenarios about offline behavior need a mock server that no other scenario uses.

An unconditional entry spends that cost where no scenario needs it. Take a database reset of five
seconds, run before each of 200 scenarios. When only 20 of those scenarios touch the database, the run spends 15 of its
nearly 17 minutes of resets on scenarios that never needed one. A `beforeRun` entry that starts a server runs even in a `--tag smoke` stage of CI that
selects no scenario using that server.

Three workarounds exist, and each one moves the decision out of sight:

- **A script that branches on `BAJUTSU_SCENARIO`.** The script compares the scenario name against a
  list that it keeps itself. Renaming a scenario breaks the match without any error. The report
  shows the entry as run even when the script did nothing.
- **A second target.** The second target differs from the first by one hook entry. The suite then
  splits into two `bajutsu run` invocations that CI must keep in step.
- **A copy of the entry in each scenario.** The sibling scenario-hook proposal lets one scenario
  declare its own entry. Twenty payment scenarios then carry twenty copies. A run-level entry has no
  scenario to live in at all.

Scenarios already carry the label that would decide these cases.
[BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md) gave every scenario a
`tags` list, and authors already tag scenarios `payments` or `db` to select them. A hook condition
that reads the same tags needs no second set of names. A reader also sees the condition next to the
command that it guards.

Once this item ships, a reader can check two observable differences. A target declares a payment
seed once, with `when: { tags: [payments] }`. A `--tag smoke` run's report then lists that entry as
skipped by its condition. No in-repository script decides whether to do its work by inspecting
`BAJUTSU_SCENARIO`.

## Detailed design

### The `when` block

Every hook entry, at both levels and in every list, accepts one optional field.

| Field | Type | Meaning |
|---|---|---|
| `when.tags` | list of strings | The entry matches a scenario that carries at least one of these tags. Absent or empty, it places no constraint. |
| `when.exclude` | list of strings | The entry does not match a scenario that carries any of these tags. `exclude` wins over `tags`. |

An entry without `when` runs as BE-0491 specifies. A `when` block that leaves both lists empty fails
to load. A condition that constrains nothing points to a mistake rather than an intent. A tag in
either list is a plain string. The match compares whole strings and is case-sensitive. It has no
glob, no regular expression, and no expression syntax.

```yaml
targets:
  web:
    hooks:
      beforeRun:
        - name: mock
          cmd: "./scripts/start-mock.sh"
          when: { tags: [offline] }            # starts when the run selected an offline scenario
      afterRun:
        - cmd: "./scripts/stop-mock.sh"
          when: { tags: [offline] }
      beforeScenario:
        - name: account
          cmd: "./scripts/create-payment-account.sh"
          when: { tags: [payments], exclude: [readonly] }
      afterScenario:
        - on: always
          cmd: "./scripts/delete-payment-account.sh"
          when: { tags: [payments], exclude: [readonly] }
```

The rule is the one `select_scenarios` in `bajutsu/common/scenario/select.py` applies to `--tag`
and `--exclude`. The implementation factors that rule into one predicate over a scenario's tags,
and both selection and hook conditions call it. A single predicate keeps the two meanings from
drifting apart.

### Which tags a condition reads

A condition reads the scenario's `tags` after component and data expansion. Selection by `--tag`
reads the same expanded tags. Data expansion substitutes `${row.*}` throughout a scenario,
`tags` included, so a data-driven scenario can tag each row differently:

```yaml
name: checkout as ${row.plan}
tags: [checkout, "${row.plan}"]
data:
  - { plan: free }
  - { plan: premium }
hooks:
  beforeScenario:
    - name: subscription
      cmd: "./scripts/grant-premium.sh"
      when: { tags: [premium] }
```

That substitution gives a scenario's own hooks a use for `when`. Every row shares the scenario's
`hooks` block, and the condition picks the rows a command serves. A scenario without data rows has
fixed tags, so a `when` on its own entries always evaluates the same way. The loader accepts such
an entry like any other.

### Scenario-level lists

A `beforeScenario` or `afterScenario` entry, at either level, evaluates `when` against its own
scenario: the one about to run, or the one that has ended. An entry runs when every gate that applies to it allows it:

1. `preconditions.inheritSetup` keeps the target's scenario lists, as BE-0491 specifies.
2. `when` matches the scenario's tags.
3. For `afterScenario`, `on` matches the verdict.

The `when` gate and the `on` gate are independent and combine with "and". `when` looks at what the
scenario is, and `on` looks at how the scenario ended.

A `beforeScenario` entry and the `afterScenario` entry that tears down its data see the same tags,
so a pair with the same `when` runs together or not at all. Bajutsu does not link the two entries.
The documentation tells authors to copy the condition to the teardown. A teardown with a broader
condition still runs. It then finds no `BAJUTSU_HOOK__<name>__<key>` variable for a skipped entry,
because a skipped entry writes no outputs.

### Run-level lists

A `beforeRun` or `afterRun` entry has no single scenario to check. It evaluates `when` against the
run's scenario set. The set holds the scenarios that remain after expansion, `--tag`, and
`--exclude`. The set matches the list that the run hands to its scenario loop. The entry matches when at least one scenario
in that set matches.

The run computes the set once, before the first `beforeRun` entry, and keeps the result for the
whole run. An `afterRun` entry then decides on the same set as its `beforeRun` counterpart. A
cancellation does not change the set. The scope of the set follows the scope of the run-level
lists:

| How the run starts | The scenario set |
|---|---|
| `bajutsu run`, with or without `--workers` | every scenario the run selected, across all workers |
| a `serve` job | the job's selection, since the worker runs the job as one `bajutsu run` |
| `audit` | the scenarios it replays |
| `record`, `crawl`, `repl` | empty, since these commands run no scenario file |

An empty set matches no condition. A conditional `beforeRun` entry never runs under `record`,
`crawl`, or `repl`. A server that those commands also need stays unconditional.

### Outputs of a conditional entry

BE-0491 checks each `${hooks.<name>.<key>}` token when it loads a scenario. This item adds one rule
to that check: a scenario that reads an entry's output must match the entry's `when`. Otherwise the
load fails, and the error names the entry, its condition, and the scenario's tags.

The rule holds for both levels. A scenario-level entry runs for the scenarios that match it and
for no others. A run-level entry runs whenever its set holds a matching scenario. A scenario that
reads the token is itself in that set. A token that passes the check thus always names an entry
that ran. A missing value surfaces at load time rather than in the middle of a run.

The check runs on every scenario after expansion, one row at a time, and ignores `--tag` and
`--exclude`. A mismatch is a defect in the files whichever subset a run selects. A smoke stage in CI
that skips the scenario still reports the defect.

### Evidence and the command policy

A skipped entry appears in its hook block in the report, labeled `skipped (when)` with its
condition. The entry runs no command, so it writes no log file and no output file.

The worker's command policy from
[BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md)
checks every declared entry before device work, including an entry whose condition does not match.
A worker's acceptance of a config then does not depend on the tags that a job selects. `serve`
keeps routing jobs by the commands that the config declares. The `commandExecutions` record of BE-0492 gains a fourth
`decision` value, `skipped`, for an entry whose condition did not match.

### Prime directives

- **AI never judges.** A condition is a set operation over strings that the scenario file states. No
  large language model (LLM) call enters the run or the gate.
- **Determinism first.** The same files and the same `--tag` and `--exclude` flags always run the
  same entries. The run fixes its scenario set before the first hook, so a run-level decision never
  depends on which scenario finished first.
- **App-agnostic.** The condition reads scenario metadata alone. It adds no per-app logic to the
  runner.

### Dependencies

This item lands after BE-0491, which introduces the hook entries and the token check. The
scenario-level part lands after the sibling scenario-hook proposal. The target-level part does not
wait for the sibling proposal.

## Alternatives considered

| Alternative | Why this item does not adopt it |
|---|---|
| A boolean expression, such as `when: "smoke and not wip"` | BE-0034 rejected an expression language for selection, and the same reasons hold here: a parser, a grammar to document, and a second meaning of tags beside `--tag`. If selection ever gains expressions, hook conditions adopt the same grammar. |
| Three lists, `anyTags`, `allTags`, and `noneTags` | The lists can express "and", but the meaning departs from `--tag` and `--exclude`. An author would learn two rules for one label. |
| A single `tag` string | Several tags or an exclusion would force a copy of the entry for each case. |
| A run-level condition that reads the `--tag` flag values | A run without `--tag` gives the condition nothing to read, and the flag values ignore `--exclude`. The selected scenario set answers the question the author asks. |
| No `when` on `beforeRun` and `afterRun` | Starting a server only when a selected scenario needs it is a common case, and that case would stay with the workarounds. |
| A skipped entry's token fails the step at run time | The mistake would surface in the middle of a run, after device work. Tags are static after expansion, so the loader can catch the mistake. |
| A skipped entry's token resolves to an empty string | A missing value would pass silently into the app and fail far from its cause. |
| An `afterScenario` entry that follows its `beforeScenario` entry automatically | Bajutsu would need a new way to pair entries, which BE-0491's flat lists do not have. Copying one `when` is simpler, and the two entries already see the same tags. |
| A `BAJUTSU_SCENARIO_TAGS` variable that scripts branch on | The decision would hide inside a script, the report could not show a skip, and a run-level script would still lack the run's scenario set. A later item can add the variable for scripts that need it, without changing this design. |
| Exempt a non-matching entry from the command policy | A worker's acceptance of a config would then depend on the tags of each job. `serve` could not route a job by the config alone. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Shared predicate: factor the tag rule out of `select_scenarios` into one function that selection and hook conditions call.
- [ ] Schema: the `when` block on every hook entry at both levels, with the load error for an empty block.
- [ ] Scenario-level evaluation: gate `beforeScenario` and `afterScenario` entries at both levels on `when`, combined with `inheritSetup` and `on`.
- [ ] Run-level evaluation: fix the run's scenario set before `beforeRun`, gate `beforeRun` and `afterRun` on it, and treat `record`, `crawl`, and `repl` as an empty set.
- [ ] Token check: reject a scenario that reads the output of an entry whose `when` it does not match, per expanded scenario.
- [ ] Evidence: the `skipped (when)` label in the report and the `skipped` decision in `commandExecutions`.
- [ ] Docs: `docs/configuration.md`, `docs/scenarios.md`, `docs/run-loop.md`, `DESIGN.md`, and `docs/architecture.md`, each with its Japanese mirror.

## References

- [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md): scenario tags and the
  `--tag` / `--exclude` selection rule this item reuses.
- [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md): the target-level
  hook lists, the output tokens, and their load-time check.
- [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md):
  the worker's command policy and the `commandExecutions` record.
- The sibling proposal *Declare external commands in a scenario to run before and after that
  scenario*: the scenario-level hook lists.
- [`bajutsu/common/scenario/select.py`](../../bajutsu/common/scenario/select.py): `select_scenarios`,
  today's tag rule.
- [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py): data expansion,
  which substitutes `${row.*}` in `tags`.

**English** · [日本語](BE-0501-scenario-command-hooks-ja.md)

# BE-0501 — Declare external commands in a scenario to run before and after that scenario

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0501](BE-0501-scenario-command-hooks.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0501") |
| Topic | Scenario authoring features |
| Related | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md), [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md), [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a top-level `hooks` block to a scenario file. The block holds two lists of external
commands, `beforeScenario` and `afterScenario`, and the commands run around that one scenario.

[BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md) already runs commands
around every scenario of a target, from `targets.<name>.hooks`. A scenario that needs its own
preparation, such as seeding one particular account, has no place for it there. This item gives the
scenario that place, with the same entry fields, the same output file, and the same failure rules as
BE-0491.

The scenario-level lists run inside the target-level lists. Before the scenario, the target's
entries run first. After the scenario, the scenario's entries run first.

## Motivation

BE-0491 settles how a target prepares data for all of its scenarios. It leaves one case open: data
that only one scenario needs. Three workarounds exist today, and none of them fits.

- **A second target.** The target carries the scenario's preparation, so a one-off seed turns into
  a second target that differs from the first by one hook entry.
- **A `beforeScenario` entry that branches on `BAJUTSU_SCENARIO`.** The script inspects which
  scenario is about to run and does that scenario's work. The knowledge of what a scenario needs
  then lives in a shell script, away from the scenario that depends on it.
- **An `http` step in `setup`.** This works for a single request. It stops working once the
  preparation needs a file, a second request, or a branch on a response.

All three separate a scenario from the preparation it depends on. A reviewer who opens the scenario
file cannot see what the scenario needs before it starts, and a scenario copied to another project
arrives without it.

Once this item ships, a reader can check two observable differences. A scenario that needs unique
data declares it in its own `hooks.beforeScenario` and reads the result through
`${hooks.<name>.<key>}`. The target that scenario runs against carries no entry that exists merely
for that one scenario.

### Why this does not reopen BE-0036

[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md) rejected a `shell` / `exec` step
because a scenario that can run any command stops being portable and reviewable. A command in
`hooks` has the same cost, and this item accepts it for one reason. Preparation that belongs to one
scenario is part of what the scenario means, so a reader gains more from seeing it in the file than
from finding it in a separate script.

The commands differ from a step in four ways, which keep the cost bounded:

- **Not a step.** The commands sit in `hooks`, apart from `steps`, `setup`, and `cleanup`. A step
  list still describes only what the user does and sees.
- **Not part of the verdict.** A command never asserts anything about the app. It fails a scenario
  only when its precondition or its teardown did not hold.
- **Visible in the report.** Each hook phase appears as its own block, with the command's log.
- **Governed by the worker.** The worker's command policy decides whether the commands run at all
  (see *Whether a command may run*).

## Detailed design

### The `hooks` block of a scenario

A scenario gains one optional top-level field.

```yaml
name: checkout with an expired card
target: web
hooks:
  beforeScenario:
    - name: card
      cmd: "./scripts/create-expired-card.sh"   # writes cardId=... to $BAJUTSU_OUTPUT
      timeout: 30
  afterScenario:
    - on: always
      cmd: "./scripts/delete-card.sh"           # reads $BAJUTSU_HOOK__card__cardId
steps:
  - type: { text: "${hooks.card.cardId}", into: { id: checkout.card } }
```

The block accepts only `beforeScenario` and `afterScenario`. A scenario is not a run, so it has no
`beforeRun` or `afterRun`. A scenario that declares either one fails to load, and the error says
that run-level commands belong to `targets.<name>.hooks`. A scenario that writes `setup`, `cleanup`,
or `interrupts` under `hooks` fails to load too. At the scenario level those three stay top-level
fields, as [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)
named them, and the error names the top-level field to use.

An entry has the fields that BE-0491 defines for its scenario-level lists.

| Field | Meaning |
|---|---|
| `cmd` | The command, split like a shell would and run without a shell. |
| `use` | The name of a command the worker lists in `namedCommands`, in place of `cmd`. An entry sets exactly one of the two. |
| `name` | An identifier that follows BE-0491's pattern. Required on an entry whose outputs are read. |
| `cwd`, `env` | The working directory and extra environment. `cwd` resolves against the scenario file's directory. |
| `timeout` | Seconds the command may take. Default 300. |
| `on` | `afterScenario` only: `always`, `success`, or `failure`. Default `always`. |
| `mask` | When true, the entry's output values are masked in evidence. Default false. |

### Order against the target's hooks

One scenario runs in this order:

1. The target's `beforeScenario` entries, in declaration order.
2. The scenario's `beforeScenario` entries, in declaration order.
3. The device lease and the app launch.
4. The scenario's `setup` steps, its `steps`, its `expect`, and its `cleanup` rules. BE-0392
   introduced the two phases as `before` and `after`, and BE-0452 renamed them.
5. The scenario's `afterScenario` rules whose `on` matches the verdict, in declaration order.
6. The target's `afterScenario` rules whose `on` matches the verdict, in declaration order.

The two levels nest like function calls. What the scenario prepares sits inside what the target
prepares, so the scenario's teardown runs first and never finds the target's data already gone.
When a scenario drives several targets, BE-0491's multi-target order applies to the target-level
part of this sequence, and the scenario's own entries sit at the center.

The scenario's `setup` steps and `cleanup` rules keep their place inside the app session. The commands in
`hooks` run outside it: before the launch, and after the verdict exists. The two mechanisms do not
overlap, because a command can seed data before the app starts, which a step cannot.

A scenario that sets `preconditions.inheritSetup: false` skips the target's `beforeScenario` and
`afterScenario` lists, as BE-0491 specifies. It does not skip its own `hooks`. The flag opts a
scenario out of what the target shares, and the scenario's own entries are not shared.

### Names, outputs, and tokens

The value-passing rules of BE-0491 apply unchanged. A command writes `key=value` lines to
`$BAJUTSU_OUTPUT`, and later commands see them as `BAJUTSU_HOOK__<name>__<key>`. The scenario reads
them as `${hooks.<name>.<key>}`.

A `name` must be unique among the entries one scenario can see: its own entries and the entries of
the targets it drives. Two different scenarios may reuse a name, because each scenario's outputs
stay inside that scenario. The loader checks each token statically. A token that names no visible
`beforeRun` or `beforeScenario` entry is a load error. A target's entries are not visible to a
scenario that sets `preconditions.inheritSetup: false`. A token naming one therefore stays the load
error BE-0491 defines, and the name of one never collides with the scenario's own.

### Failures and the verdict

The failure table of BE-0491 holds, with one addition.

| Failure in | Effect |
|---|---|
| A scenario `beforeScenario` entry | The scenario fails with `beforeScenario: <name>: …` and skips its launch and steps. Every `afterScenario` rule that matches still runs, at both levels. |
| A scenario `afterScenario` entry | The failure becomes the verdict on a passing scenario, and is appended behind an existing failure. Later rules still run. |

A target-level `beforeScenario` failure skips the scenario's own `beforeScenario` entries, and it
skips the scenario's own `afterScenario` rules with them. BE-0491 treats the two lists as a pair in
the same way under `inheritSetup: false`. A teardown then never acts on outputs that were never
written.

### Whether a command may run

A scenario's commands follow the same worker policy as the target's commands. The scenario file and
the config that holds the target arrive from the same config source: an uploaded bundle, a Git
checkout, or a local path. [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md)
decides per config source, so one decision covers both.

Under a `named` mode, a `cmd` entry in a scenario is refused like a `cmd` entry in a target, and a
`use` entry runs. Such a worker therefore accepts a scenario that names an installed script and
rejects a scenario that spells out a command line. This item adds no policy axis of its own.

The item does add one input to BE-0492's routing. `serve` derives `command-execution:<source>`,
`named-execution:<source>`, and `named-command:<name>` from a job's scenario files as well as from
its config. A job whose only commands sit in a scenario therefore still reaches a worker that
accepts its source and holds the named scripts.

Under the `sandbox` mode, the scenario's entries run in the container of the scenario's first
declared target, which BE-0491's `hooks.sandbox` block defines.

### Where the commands run, and evidence

Placement follows BE-0491. The commands run in the `bajutsu run` process on the machine that runs
it, which for a Web UI job is the worker that leased the job. `run` and `audit` run the scenario
lists. `record`, `crawl`, and `repl` run no scenario file, so they run no scenario-level list. The
target's `beforeRun` and `afterRun` still run for them, as BE-0491 specifies.

A scenario's command output goes to `hooks/<list>/_scenario/<scenario-index>/<name-or-index>.log`
under the run directory. In BE-0491's paths that position holds a target's name. The loader
therefore refuses a target named `_scenario`, so the logs of the two levels never share a path. Masking and the raw-area boundary are the ones BE-0491 specifies. The report shows the
scenario's hook phases as their own blocks, labeled with the level they came from.

### Prime directives

- **AI never judges.** A hook is a command and an exit code. No large language model call enters
  the run or the gate.
- **Determinism first.** Every command has a bounded `timeout`, and the scenario waits on a
  condition, never on a fixed pause.
- **App-agnostic.** The scenario names no platform-specific mechanism. The commands are opaque to
  the runner, and the worker's policy decides whether they run.

### Dependency on BE-0491

This item builds on the hook runner, the output file, the log copy, and the report block that
BE-0491 introduces. It lands after BE-0491 and adds the scenario-side schema, the load-time token
check, and the two-level ordering.

## Alternatives considered

- **A scenario names a target hook, and the command lives in config.** Rejected: the scenario file
  then cannot be read alone, since the command's definition sits in another file. The decision
  here was to accept that cost for the sake of locality. The tradeoff reverses if a worker runs
  mostly in `named` mode, where `use` already supplies this indirection.
- **A `cmd` entry in the scenario's `steps`.** Rejected for the reason BE-0036 gives. It also runs
  after the app launch, so it cannot seed data the app reads at startup.
- **Extend the scenario's `setup` / `cleanup` to accept commands.** Rejected: those phases run inside the
  app session, after the launch. A seed command must run before the launch.
- **A scenario's lists replace the target's lists.** Rejected: a scenario that adds one entry would
  silently drop every shared one, and forgetting to repeat a shared seed fails far from the cause.
- **Name the lists `beforeRun` and `afterRun`.** Rejected: BE-0491 uses those names for commands
  that run once per run. Reusing them for a per-scenario meaning would give one word two scopes.
- **A path to a script file in place of `cmd`.** Rejected: running a named file is still running an
  arbitrary program, so the policy question stays the same, and `cmd` already covers it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: the scenario's top-level `hooks` block, its entry model, and the load error for `beforeRun` / `afterRun`.
- [ ] Token check: resolve `${hooks.<name>.<key>}` against the entries one scenario can see, including name uniqueness.
- [ ] Ordering: nest the scenario's entries inside the target's, including `inheritSetup: false`, multi-target runs, and failure paths.
- [ ] Policy: route the scenario's entries through BE-0492's modes, its `manifest.json` record, and its routing tokens, including `use` and the sandbox container.
- [ ] Evidence: the scenario-level log paths and the labeled report blocks.
- [ ] Tests, plus the docs and `DESIGN.md` updates in both languages.

## References

- [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md): utility steps, and the rejected
  `shell` / `exec` step.
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md):
  today's policy for commands that uploaded configs declare.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the
  scenario's setup and teardown phases, now named `setup` and `cleanup`.
- [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md): the target-level
  command hooks that this item extends.
- [BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy.md):
  the worker policy that decides whether config-declared commands run.

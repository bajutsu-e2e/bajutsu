**English** · [日本語](BE-XXXX-target-command-hooks-ja.md)

# BE-XXXX — Run external commands around a run and each scenario from target config

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-target-command-hooks.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md), [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md), [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a target's config run external commands around a run and around each scenario.
Four new lists under `targets.<name>.hooks` hold those commands: `beforeRun`, `afterRun`,
`beforeScenario`, and `afterScenario`. A command can seed test data through a web application
programming interface (API), place a file, or start the app's own web server. A command can also
hand values back to the scenario through an output file, the way a GitHub Actions step writes
`$GITHUB_OUTPUT`.

Every entry is a command that runs to completion. A `beforeRun` script that starts a server and
waits until the server answers therefore does what `services.server` does today, so this item
removes `services.server` and its readiness fields. It also removes `services.mockServer`, a
schema-only block that no run has ever started. After this item, `services` holds `mailbox`
alone: the external inbox a run polls but never starts.

## Motivation

Test data that a scenario depends on has to come from somewhere. A scenario can create the data
itself with the `http` step inside its `setup` phase. That route fits a single request to a seed
endpoint. Once the preparation grows into several requests, a file to write, or logic that
branches on a response, the route fits poorly. The scenario file then carries a script written in
step syntax, and none of that script is the behavior under test.

Bajutsu deliberately has no general escape hatch inside a scenario. The utility-steps item
([BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md)) rejected a `shell` / `exec` step. A
scenario that can run any command stops being portable, and a reviewer can no longer read what it
does. That decision keeps the scenario a description of user-visible behavior, and this item keeps
it.

What remains today is wrapping `bajutsu run` in a CI script or a `Makefile` target. The wrapper
works but has three gaps:

- **No values reach the scenario.** An exported variable in the wrapper does not become a scenario
  token. The one route is declaring the variable under `evidence.secrets`, which masks it in every
  report as if it were a credential.
- **No per-scenario scope.** The wrapper runs once around the whole run. Data that each scenario
  needs fresh cannot be prepared that way without one `bajutsu run` per scenario file.
- **No path through `serve`.** A run that the Web UI starts never passes through a CI wrapper, so
  its preparation step does not run at all.

Bajutsu already runs one config-declared command around a run: `services.server`, added by
[BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md) to bring up the web
target's host. That block starts a process, polls a readiness URL, and stops the process at the
end. A script can do all three itself, with whatever readiness check its server needs. Keeping
`services.server` beside the new hooks would leave two ways to start a server, with two field sets
for a reader to learn.

Once this item ships, a reader can check two observable differences. A scenario that needs seeded
data reads the seeded identifier through a `${hooks.<name>.<key>}` token, and its own `setup`
holds no `http` step that exists merely to prepare data. `demos/serve-ui/dogfood.config.yaml`
starts its inner server from a `hooks.beforeRun` script, and `services.server` no longer loads.

## Detailed design

### The four hook lists

Each list lives under `targets.<name>.hooks`, beside the existing `setup`, `cleanup`, and
`interrupts`. The existing `setup` and `cleanup` keep holding scenario steps. The new lists hold
external commands.

| List | Runs | Scope of its outputs |
|---|---|---|
| `beforeRun` | once, before the first scenario | the whole run |
| `afterRun` | once, after the last scenario, on every path out of the run | — |
| `beforeScenario` | before each scenario, ahead of the app launch | that scenario |
| `afterScenario` | after each scenario, once its verdict exists | — |

```yaml
targets:
  web:
    platform: web
    subject: { url: "http://127.0.0.1:8787/index.html" }
    hooks:
      beforeRun:
        - name: server
          cmd: "./scripts/start-server.sh"    # starts the server, waits until it answers, exits
          timeout: 60
        - name: catalog
          cmd: "./scripts/seed-catalog.sh"
      beforeScenario:
        - name: user
          cmd: "./scripts/create-user.sh"     # writes userId=... to $BAJUTSU_OUTPUT
      afterScenario:
        - on: always
          cmd: "./scripts/delete-user.sh"     # reads $BAJUTSU_HOOK__user__userId
      afterRun:
        - on: always
          cmd: "./scripts/stop-server.sh"
```

A scenario then reads the seeded value without any preparation step of its own:

```yaml
- name: sign in as a seeded user
  steps:
    - type: { text: "${hooks.user.userId}", into: { id: login.username } }
```

### Fields of one entry

| Field | Lists | Meaning |
|---|---|---|
| `cmd` | all | The command. Bajutsu splits it like a shell would, then runs it without a shell. Pipes or redirects need `sh -c` or a script. |
| `name` | all | An identifier, `[A-Za-z][A-Za-z0-9_]*` without `__`, unique across the four lists of every target the run drives. Required on an entry whose outputs are read. |
| `cwd`, `env` | all | The working directory and extra environment, as `services.server` has them today. |
| `timeout` | all | Seconds the command may take. Default 300. |
| `on` | `afterRun`, `afterScenario` | `always`, `success`, or `failure`. Default `always`. The same words as a scenario's `cleanup` rules. |
| `mask` | all | When true, the entry's output values are masked in evidence, like `${secrets.*}`. Default false. |

### Lifecycle and order

Each entry runs to completion before the next entry starts. A non-zero exit or an expired
`timeout` fails the entry. On a timeout, Bajutsu stops the command's whole process group.

A script may leave a process running after the script itself exits, such as a server started in
the background. Bajutsu neither tracks nor stops that process. The script that starts the server
owns two duties:

- **Readiness.** The script exits once the server accepts requests. Each script picks the check
  its server needs: an HTTP probe, a port check, or a line in a log.
- **Teardown.** An `afterRun` or `afterScenario` entry stops the server.

Bajutsu writes each command's output to a log file rather than a pipe. A server that inherits the
script's output then cannot keep Bajutsu waiting after the script exits.

One scenario runs in this order:

1. The `beforeScenario` entries, in declaration order.
2. The device lease and the app launch, unchanged.
3. The `setup` steps, then `steps` and `expect`, then the `cleanup` rules, all unchanged.
4. The `afterScenario` rules whose `on` matches the verdict, in declaration order.

The `beforeScenario` entries run ahead of the launch so the app reads seeded data at startup. A
scenario that sets `preconditions.inheritSetup: false` skips the target's `beforeScenario` along
with its `setup`. Its `afterScenario` still runs, as the target's `cleanup` does today.

The run itself wraps every scenario the same way. `beforeRun` runs before the first scenario, and
`afterRun` runs once every scenario is done. `afterRun` and `afterScenario` also run on a cancelled
run (`SIGTERM`, or the Web UI's Cancel button). They share the bounded slice of the cancellation
grace window that `cleanup` uses.

### Failures and the verdict

A hook never judges the app. A hook failure fails a run or a scenario because the precondition or
teardown did not hold, not because a command decided what passes.

| Failure in | Effect |
|---|---|
| `beforeRun` | No scenario runs. The `afterRun` rules for `always` and `failure` run. The run exits 2, as a `services.server` start-up failure does today. |
| `beforeScenario` | The scenario fails with `beforeScenario: <name>: …` and skips its launch and steps. Its `afterScenario` rules still run. |
| `afterScenario` | The rule a scenario `cleanup` follows: the failure becomes the verdict on a passing scenario, and is appended behind an existing failure. |
| `afterRun` | Every scenario keeps its verdict. The report records the failure, and the run exits 2. |

A later `afterScenario` or `afterRun` rule still runs after an earlier rule fails. Skipping the rest
of a teardown is the outcome teardown exists to avoid.

### Passing values: the output file and the environment

Each entry runs with an environment variable `BAJUTSU_OUTPUT` that names a fresh, empty file. The
command writes one `key=value` line per value. A key follows the same pattern as `name`. A
malformed line fails the entry, so a typo never turns into a silently missing value.

A scenario reads a value as `${hooks.<name>.<key>}`. The run loop substitutes the token at action
time, as it does for `${secrets.*}`. The scenario loader checks each token statically. A token
naming no `beforeRun` or `beforeScenario` entry is a load error. A key the command never wrote fails the step and names
both the hook and the key. A scenario sees the outputs of `beforeRun` and of its own
`beforeScenario` entries.

Every later command in scope sees earlier outputs as environment variables named
`BAJUTSU_HOOK__<name>__<key>`. The double underscore keeps the mapping unambiguous, which is why
`name` and the key forbid `__`. Each command also receives context variables:

| Variable | Set for |
|---|---|
| `BAJUTSU_RUN_ID`, `BAJUTSU_RUN_DIR`, `BAJUTSU_TARGET` | every entry |
| `BAJUTSU_SCENARIO`, `BAJUTSU_SCENARIO_FILE`, `BAJUTSU_WORKER` | `beforeScenario` and `afterScenario` |
| `BAJUTSU_VERDICT` (`success` or `failure`) | `afterRun` and `afterScenario` |

`BAJUTSU_WORKER` is the worker index under `run --workers`. Scenario hooks run in the worker that
runs their scenario, so parallel scenarios run their hooks in parallel. A script that touches shared
state, or that starts a server per scenario, can pick its data or its port by that index.

### Scenarios that drive several targets

A scenario can drive more than one target. A run then executes the hooks of every target it
drives. Within each list, the targets run in the order the scenario declares them. The `after`
lists run in the reverse order, so the last target prepared is the first one torn down. A hook
`name` is unique across every target the run drives. Two targets that declare the same `name` fail
at load time, so a `${hooks.<name>.<key>}` token never resolves ambiguously.

### Evidence

A command's standard output and standard error go to the run directory, under
`hooks/<list>/<target>/<name-or-index>.log` for the run-level lists, and under
`hooks/<list>/<target>/<scenario-index>/<name-or-index>.log` for the scenario-level lists. Today `services.server` discards both. The report shows each
hook phase as its own block, apart from the scenario's numbered steps, as it already does for
`setup` and `cleanup`. Masking applies to the logs: each command writes to a raw file outside the run
directory, and Bajutsu copies it into the run directory with `${secrets.*}` values and the values
of an entry with `mask: true` replaced. It copies when the entry exits, and again at the end of the
run for output that a background process wrote later.

### Where the commands run

The commands run inside the `bajutsu run` process, on the machine that runs `bajutsu run`:

| How the run starts | Where the hooks run |
|---|---|
| `bajutsu run` from a terminal or CI | that machine |
| the `serve` Web UI | the `bajutsu worker` that leased the job, since `serve` runs no job itself ([BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md)) |
| a device-cloud batch, such as AWS Device Farm | the device-cloud host, where the batch runs `bajutsu run` |

A seed API that a hook calls, or a file a hook places, must therefore be reachable from that
machine. Setup that belongs to one deployment rather than to the app keeps its existing homes: the
Device Farm `pre_test_commands` of BE-0432, and the batch lifecycle hook of BE-0435.

Each Bajutsu command runs the lists that fit it:

| Command | `beforeRun` / `afterRun` | `beforeScenario` / `afterScenario` |
|---|---|---|
| `run` (and the jobs `serve` hands to workers) | yes | yes |
| `audit` | yes | yes, since it replays scenarios through `run_all` |
| `record`, `crawl`, `repl` | yes, replacing their `services.server` bring-up | no; these commands run no scenario file |

### Whether a command may run

A hook runs whatever its config says. Whether a given machine runs a given config's commands at
all is a separate decision, owned by the machine that runs them. A sibling proposal, *Let the worker
decide whether config-declared commands run*, defines that decision: a policy in the worker's
capability file, chosen per config source. That proposal replaces the `serve --upload-exec` option
of [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md),
and this item's hook lists are the commands it governs.

This item lands after the sibling proposal. Removing `services.server` first would leave uploaded
web bundles without a governed way to start their server.

Under the sibling proposal's `sandbox` mode, the hook entries of one target run in one container
that lives for the whole run. The target declares the container in a `hooks.sandbox` block:

```yaml
hooks:
  sandbox:
    dockerImage: "node:20-slim"   # or dockerfile: a path inside the bundle
    ports: [8787]                 # published to the worker's loopback interface
```

- **One container per run.** Bajutsu starts the container before `beforeRun` with an idle command of
  its own in place of the image's default, and runs each entry inside it with `docker exec`. A server that a `beforeRun` script starts therefore keeps running until the run ends.
  Bajutsu removes the container after `afterRun`.
- **Ports.** Each port in `ports` is published to the same port on the worker's loopback interface.
  The browser or the app on the worker then reaches the server at `127.0.0.1:<port>`.
- **Files.** The bundle is mounted read-only from the bundle directory the worker prepared, never
  from an entry's `cwd`. The run directory's `hooks/` area is mounted writable. Bajutsu rewrites
  `BAJUTSU_OUTPUT` and `BAJUTSU_RUN_DIR` to their paths inside the container.

`hooks.sandbox` replaces the `dockerImage`, `dockerfile`, and `port` fields that `services.server`
carries today. A target whose hooks meet the `sandbox` mode without a `hooks.sandbox` block fails
before any device work, and the message names the block.

### Removing `services.server` and `services.mockServer`

Both blocks leave the schema. A config that still declares either fails to load, and the error
names the replacement: a `beforeRun` script that starts the server and waits until it answers. The
loader reads no old name, which is the rule
[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) applied when it
renamed the target's keys. `services.mailbox` stays as it is.

The removal migrates every in-repo user:

- `demos/serve-ui/dogfood.config.yaml` moves its inner server to a pair of scripts. A `beforeRun`
  script starts the server and waits until it answers, and an `afterRun` script stops it.
- `bajutsu/common/runner/launch_server.py` goes away. The five callers of
  `_start_launch_server_or_exit` call the run-level hook phase instead.
- `docs/configuration.md`, `docs/scenarios.md`, `docs/run-loop.md`, `docs/cli.md`,
  `docs/self-hosting.md`, `docs/overview.md`, `docs/developer-guide.md`, `DESIGN.md`, and
  `docs/architecture.md` change with the behavior, each with its Japanese mirror.

### Prime directives

- **AI never judges.** A hook is a command and an exit code. No large language model (LLM) call
  enters the run or the gate.
- **Determinism first.** Every command has a bounded `timeout`. The readiness wait moves into the
  script, where its author picks a condition check. Bajutsu cannot see inside the script, the same
  as with today's `services.server` command.
- **App-agnostic.** The hooks live in `targets.<name>`, so per-app preparation stays in config. The
  scenario domain-specific language (DSL) gains one token namespace and no new step.

## Alternatives considered

- **A `shell` step inside the scenario.** Rejected for the reason BE-0036 gives: a scenario that
  runs any command stops being portable and reviewable. This item keeps commands in the target's
  config, where an operator owns them.
- **Keep `services.server` beside the new hooks.** Rejected: two ways to start a server would need
  two field sets and two sets of docs. A `beforeRun` script expresses everything `services.server`
  does.
- **Keep a readiness URL on a hook entry.** Rejected: a URL probe is one readiness check among
  several, and a script can run any of them. A hook-level probe would also bring back the reuse
  rule and its edge cases under parallel workers. The script owns readiness, and `timeout` bounds
  it.
- **Keep `services.mockServer` for a later mock-server feature.** Rejected: the block has never
  started a process. A `beforeRun` script can start a mock server like any other server.
- **Fold `subject.build` into `beforeRun` as well.** Deferred: `build` runs merely when the app
  binary is missing, and `serve` relies on that conditional behavior for prebuilt bundles. Moving
  it would change build semantics, a decision of its own.
- **Pass values through environment variables declared in `evidence.secrets`.** Rejected as the
  main route: every value would be masked as a credential, and a value created inside the run could
  not reach the scenario at all. The output file covers both cases, and `mask: true` keeps masking
  for a value that is a secret.
- **Merge hook outputs into `${vars.*}`.** Rejected: a hook key could shadow a variable a step
  captures, and the scenario could no longer tell where a value came from. A separate namespace
  makes the source visible in the token.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: the four hook lists and their entry model; remove `services.server` and `services.mockServer` with a load error naming the replacement.
- [ ] Hook runner: entries, timeouts, the output file, the environment, logs, and masking.
- [ ] Run-level phase: wire `beforeRun` / `afterRun` into `run`, `audit`, `record`, `crawl`, and `repl`, replacing the `services.server` bring-up.
- [ ] Scenario-level phase: wire `beforeScenario` / `afterScenario` into the scenario lifecycle, including `inheritSetup`, workers, multi-target order, and cancellation.
- [ ] The `${hooks.<name>.<key>}` token: static load-time check and action-time substitution.
- [ ] Sandbox: the `hooks.sandbox` block and the per-run container, its ports, mounts, and paths.
- [ ] Report: hook phases as their own blocks, with links to the logs.
- [ ] Migrate `demos/serve-ui/dogfood.config.yaml`, and update the docs, `DESIGN.md`, and `docs/architecture.md` in both languages.

## References

- [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md): utility steps, and the rejected
  `shell` / `exec` step.
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md): `launchServer`, later
  `services.server`, which this item replaces.
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md):
  today's policy for commands that uploaded configs declare.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the
  scenario's own setup and teardown phases, whose failure rules this item reuses.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md): `serve` queues every
  job, and workers run them.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the target
  config groups, and the rule that the loader reads no old name.
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py): today's
  server bring-up, probe, and teardown.
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py): `_start_launch_server_or_exit`, the
  bring-up the command callers share.
- [`demos/serve-ui/dogfood.config.yaml`](../../demos/serve-ui/dogfood.config.yaml): the one in-repo
  config that declares `services.server`.

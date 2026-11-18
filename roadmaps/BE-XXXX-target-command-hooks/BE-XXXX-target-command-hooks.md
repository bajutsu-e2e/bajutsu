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
| Related | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md), [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md), [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md), [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a target's config run external commands around a run and around each scenario.
Four new lists under `targets.<name>.hooks` hold those commands: `beforeRun`, `afterRun`,
`beforeScenario`, and `afterScenario`. A command can seed test data through a web application programming interface (API), place a file,
or start a long-running process such as the app's own web server. A command can also hand values
back to the scenario through an output file, the way a GitHub Actions step writes `$GITHUB_OUTPUT`.

The same mechanism replaces two service blocks. A `beforeRun` entry with `background: true` and a
`readyUrl` does what `services.server` does today, so this item removes `services.server`. It also
removes `services.mockServer`, a schema-only block that no run has ever started. After this item,
`services` holds `mailbox` alone: the external inbox a run polls but never starts.

## Motivation

Test data that a scenario depends on has to come from somewhere. A scenario can create the data
itself with the `http` step inside its `setup` phase. That route fits a single request to a seed
endpoint. It fits poorly once the preparation grows into several requests, a file to write, or
logic that branches on a response. The scenario file then carries a script written in step syntax,
and none of that script is the behavior under test.

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
target's host. That block is a special case of the hook this item needs. Its shape is a command,
plus a readiness probe and a teardown. Keeping both would leave two ways to start a process, with
two sets of fields and two code paths under the upload policy of
[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md).

Once this item ships, a reader can check one observable difference. A scenario that needs seeded
data reads the seeded identifier through a `${hooks.<name>.<key>}` token, and its own `setup`
holds no `http` step that exists merely to prepare data. `demos/serve-ui/dogfood.config.yaml`
starts its inner server through `hooks.beforeRun`, and `services.server` no longer loads.

## Detailed design

### The four hook lists

Each list lives under `targets.<name>.hooks`, beside the existing `setup`, `cleanup`, and
`interrupts`. The existing `setup` and `cleanup` keep holding scenario steps. The new lists hold
external commands.

| List | Runs | Scope of its outputs and background processes |
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
          cmd: "python -m http.server 8787"
          background: true
          readyUrl: "http://127.0.0.1:8787/"
          readyTimeout: 30
        - name: catalog
          cmd: "./scripts/seed-catalog.sh"
          timeout: 120
      beforeScenario:
        - name: user
          cmd: "./scripts/create-user.sh"     # writes userId=... to $BAJUTSU_OUTPUT
      afterScenario:
        - on: always
          cmd: "./scripts/delete-user.sh"     # reads $BAJUTSU_HOOK__user__userId
      afterRun:
        - on: always
          cmd: "./scripts/reset-catalog.sh"
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
| `timeout` | all | Seconds a foreground command may take. Default 300. Ignored for a background entry. |
| `background`, `readyUrl`, `readyTimeout` | `beforeRun`, `beforeScenario` | Start a long-running process and wait on its readiness uniform resource locator (URL). Both readiness fields are required with `background: true`. |
| `on` | `afterRun`, `afterScenario` | `always`, `success`, or `failure`. Default `always`. The same words as a scenario's `cleanup` rules. |
| `mask` | all | When true, the entry's output values are masked in evidence, like `${secrets.*}`. Default false. |
| `dockerImage`, `dockerfile`, `port` | all | The sandbox runtime under BE-0090's `sandbox` mode, carried over from `services.server`. |

The `readyUrl` of a background entry has no default. Today's `services.server` falls back to the
target's `subject.url`. A hook list holds several commands, and only one of them could own that
fallback, so the readiness URL is always explicit.

### Lifecycle and order

A foreground entry runs to completion before the next entry starts. A non-zero exit or an expired
`timeout` fails the entry. A background entry starts its process in its own process group, then
waits until `readyUrl` answers with a status below 400. That wait is a condition wait, polled the
way `services.server` polls today. When `readyUrl` already answers before the process starts,
Bajutsu reuses the server that answered and never stops it. This keeps BE-0059's reuse rule.

The reuse rule holds for `beforeRun` alone. Under `run --workers`, two workers could otherwise share
one `beforeScenario` server, and the worker that finishes first would stop the server mid-scenario
for the other. Two rules prevent that sharing:

- **Per-worker values.** In a `beforeScenario` entry, Bajutsu replaces the text `${BAJUTSU_WORKER}`
  in `cmd`, `readyUrl`, and each `env` value with the worker index. An entry such as
  `cmd: "./mock --port 90${BAJUTSU_WORKER}"` with `readyUrl: "http://127.0.0.1:90${BAJUTSU_WORKER}/"`
  then starts one server per worker.
- **No reuse per scenario.** A background `beforeScenario` entry whose `readyUrl` already answers
  before its process starts fails the scenario. Such an answer means another worker or a leftover
  process owns the port, and a shared server would break the isolation between scenarios.

A background entry also writes its resolved `readyUrl` as the output key `url`. A scenario reaches
a per-worker server through `${hooks.<name>.url}`, without computing the port itself.

One scenario then runs in this order:

1. The `beforeScenario` entries, in declaration order.
2. The device lease and the app launch, unchanged.
3. The `setup` steps, then `steps` and `expect`, then the `cleanup` rules, all unchanged.
4. The `afterScenario` rules whose `on` matches the verdict, in declaration order.
5. The scenario's background processes stop, in reverse start order.

The `beforeScenario` entries run ahead of the launch so the app reads seeded data at startup. A
scenario that sets `preconditions.inheritSetup: false` skips the target's `beforeScenario` along
with its `setup`. Its `afterScenario` still runs, as the target's `cleanup` does today.

The run itself wraps every scenario the same way. `beforeRun` runs before the first scenario.
`afterRun` runs once every scenario is done, and its run-level background processes stop last.
`afterRun` and `afterScenario` also run on a cancelled run (`SIGTERM`, or the Web UI's Cancel
button). They share the bounded slice of the cancellation grace window that `cleanup` uses.

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
naming no declared `name` is a load error. A key the command never wrote fails the step and names
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
state can partition by that index.

### Scenarios that drive several targets

A scenario can drive more than one target. A run then executes the hooks of every target it
drives. Within each list, the targets run in the order the scenario declares them. The `after`
lists run in the reverse order, so the last target prepared is the first one torn down. A hook
`name` is unique across every target the run drives. Two targets that declare the same `name` fail
at load time, so a `${hooks.<name>.<key>}` token never resolves ambiguously.

### Evidence

A command's standard output and standard error go to the run directory, under
`hooks/<list>/<name-or-index>.log`. Today `services.server` discards both. The report shows each
hook phase as its own block, apart from the scenario's numbered steps, as it already does for
`setup` and `cleanup`. Masking applies to the logs: `${secrets.*}` values and the values of an
entry with `mask: true` are replaced before the logs are written.

### Which commands run the hooks

| Command | `beforeRun` / `afterRun` | `beforeScenario` / `afterScenario` |
|---|---|---|
| `run` (and `serve`, which spawns it) | yes | yes |
| `audit` | yes | yes, since it replays scenarios through `run_all` |
| `record`, `crawl`, `repl` | yes, replacing their `services.server` bring-up | no; these commands run no scenario file |

### The upload policy

BE-0090 governs every command an uploaded bundle's config declares, through `serve
--upload-exec=<deny|reuse|sandbox>`. The four hook lists join that governed surface in place of
`services.server` and `services.mockServer`:

- **`sandbox`:** every entry runs in a throwaway container, built from the entry's `dockerImage` or
  `dockerfile`. The bundle is mounted read-only from the trusted bundle root that `serve` controls, never from
  the entry's `cwd`, as BE-0090's sandbox does today. The output file is mounted writable.
  An entry declaring neither runtime fails, never falling back to the bare host.
- **`reuse`:** no entry runs. A background entry whose `readyUrl` already answers counts as
  satisfied. Any other entry fails the run.
- **`deny`:** no entry runs, and a run that declares any entry fails.

Under `sandbox`, every container of one run joins a Docker network created for that run. A
sandboxed entry reaches a background entry under that entry's `name` as the host name, such as
`http://server:8787/`. Bajutsu also rewrites the context variables to the paths inside the
container. `BAJUTSU_OUTPUT` and `BAJUTSU_RUN_DIR` then name the mounted output file and run
directory, never the host paths, which mean nothing inside the container.

A local or Git-sourced config stays ungoverned, as today.

### Removing `services.server` and `services.mockServer`

Both blocks leave the schema. A config that still declares either fails to load, and the error
names the replacement: a `beforeRun` entry with `background: true`. The loader reads no old name,
which is the rule BE-0452 applied when it renamed the target's keys. `services.mailbox` is untouched.

The removal migrates every in-repo user:

- `demos/serve-ui/dogfood.config.yaml` moves its inner server to `hooks.beforeRun`, adding the
  explicit `readyUrl` the server previously took from `subject.url`.
- `bajutsu/common/runner/launch_server.py` and `sandbox.py` become the background-entry path of the
  hook runner. The five callers of `_start_launch_server_or_exit` call the run-level hook phase.
- `docs/configuration.md`, `docs/scenarios.md`, `docs/run-loop.md`, `docs/cli.md`,
  `docs/self-hosting.md`, `docs/overview.md`, `docs/developer-guide.md`, `DESIGN.md`, and
  `docs/architecture.md` change with the behavior, each with its Japanese mirror.

### Prime directives

- **AI never judges.** A hook is a command and an exit code. No large language model (LLM) call enters the run or the
  gate.
- **Determinism first.** Readiness is a condition wait. Every foreground command has a bounded
  `timeout`. A hook script can still sleep internally. Bajutsu cannot see inside the script, the
  same as with today's `services.server` command.
- **App-agnostic.** The hooks live in `targets.<name>`, so per-app preparation stays in config. The
  scenario domain-specific language (DSL) gains one token namespace and no new step.

## Alternatives considered

- **A `shell` step inside the scenario.** Rejected for the reason BE-0036 gives: a scenario that
  runs any command stops being portable and reviewable. This item keeps commands in the target's
  config, where an operator owns them and the upload policy governs them.
- **Keep `services.server` beside the new hooks.** Rejected: two ways to start a process would need
  two field sets, two sets of docs, and two branches under BE-0090. A background `beforeRun` entry
  expresses everything `services.server` does.
- **Keep `services.mockServer` for a later mock-server feature.** Rejected: the block has never
  started a process. A mock server is a background command with a readiness URL. A dedicated block
  can return with its own item if a real need for stub management appears.
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
- **Default a background entry's `readyUrl` to `subject.url`.** Rejected: one list can hold several
  background entries, and an implicit default for one of them would be a rule a reader has to know.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: the four hook lists and their entry model; remove `services.server` and `services.mockServer` with a load error naming the replacement.
- [ ] Hook runner: foreground and background entries, the output file, the environment, logs, and masking.
- [ ] Run-level phase: wire `beforeRun` / `afterRun` into `run`, `audit`, `record`, `crawl`, and `repl`, replacing the `services.server` bring-up.
- [ ] Scenario-level phase: wire `beforeScenario` / `afterScenario` into the scenario lifecycle, including `inheritSetup`, workers, and cancellation.
- [ ] The `${hooks.<name>.<key>}` token: static load-time check and action-time substitution.
- [ ] Upload policy: extend BE-0090's `deny` / `reuse` / `sandbox` to every hook entry.
- [ ] Report: hook phases as their own blocks, with links to the logs.
- [ ] Migrate `demos/serve-ui/dogfood.config.yaml`, and update the docs, `DESIGN.md`, and `docs/architecture.md` in both languages.

## References

- [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md): utility steps, and the rejected
  `shell` / `exec` step.
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md): `launchServer`, later
  `services.server`, which this item replaces.
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md):
  the upload policy for config-declared commands.
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks.md): the
  scenario's own setup and teardown phases, whose failure rules this item reuses.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the target
  config groups, and the rule that the loader reads no old name.
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py): today's
  server bring-up, probe, and teardown.
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py): `_start_launch_server_or_exit`, the
  bring-up the command callers share.
- [`demos/serve-ui/dogfood.config.yaml`](../../demos/serve-ui/dogfood.config.yaml): the one in-repo
  config that declares `services.server`.

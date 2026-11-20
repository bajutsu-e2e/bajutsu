**English** · [日本語](BE-0492-worker-command-execution-policy-ja.md)

# BE-0492 — Let the worker decide whether config-declared commands run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0492](BE-0492-worker-command-execution-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0492") |
| Implementing PR | [#2494](https://github.com/bajutsu-e2e/bajutsu/pull/2494) (units 1–8) |
| Topic | Security hardening |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md), [BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) |
<!-- /BE-METADATA -->

## Introduction

This item moves the decision about whether a config's commands run from the server to the worker.
A config can declare commands that Bajutsu runs on the machine that executes a run: today
`services.server.cmd`, `subject.build`, and `driver.runner.build`. Two `serve` options decide today
whether such a command runs. `--upload-exec` governs an uploaded bundle's server command, and
`--allow-remote-build` lets a Git config bound through the Web UI build. This item replaces both
options with a `commandExecution` block in the worker capability file, `worker.yaml`. The block sets one mode
per trust level of a config: an uploaded bundle, a Git config bound through the Web UI, or a config
the operator chose.

For the least-trusted sources, a worker can go further and run only *named commands*: scripts the
worker's operator installed on the worker and listed in `worker.yaml`. A config then picks a command
by name instead of spelling out a command line, so the code that runs belongs to the worker, not to
the config.

The worker advertises the sources it accepts, and `serve` routes a job that declares commands to a
worker that accepts the job's source. A run also records every command it executed, with the
decision that let it run, so an operator can find a command that should not have run.

## Motivation

[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
put the command policy on `serve` when `serve` still executed jobs itself. Since
[BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md), `serve` queues every job
and a `bajutsu worker` executes it. The policy and the risk now sit on different machines:

- **The worker carries the risk.** A command from a config runs on the worker's host, with the
  worker's files, network, and credentials. A worker that holds signing keys warrants a stricter
  policy than a disposable Linux container. One server-wide value cannot express that difference.
- **The server cannot see the worker's host.** The `sandbox` mode needs Docker on the machine that
  runs the container. `serve` sets the mode without knowing whether a given worker has Docker.
- **A worker's operator cannot refuse.** A worker leases whatever jobs it can run, and it runs
  their commands under the server's policy. The person who operates the machine has no setting to
  say no.

The two options also split one decision by command rather than by trust. A Git config
([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) that a Web UI user binds
after startup is less trusted than one the operator typed, because a cross-site request could have
bound it ([BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md)). `serve` withholds that config's builds unless the operator
passes `--allow-remote-build`, yet it runs the same config's server command on the bare host. An
operator who wants to know what such a config may run has to read two options and two documents.

Finally, only an uploaded bundle's server command leaves a record today: BE-0090 stamps its decision
into the manifest's `uploadExec` provenance, without a hash of what ran. A command from a Git or
local config leaves none. An unintended command, such as a changed script in a repository, thus
leaves no trace in the run's evidence.

Once this item ships, a reader can check two observable differences. A worker whose `worker.yaml`
sets `uploaded: deny` never leases a job whose uploaded config declares a command. Every run's
`manifest.json` lists each command the run executed or refused, with its source, its mode, and a
hash of what ran.

## Detailed design

### The `commandExecution` block in `worker.yaml`

The worker capability file of
[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) gains one optional block:

```yaml
version: 1
environment: local
drivers: [playwright]
commandExecution:
  uploaded: sandbox
  remote: sandbox
  operator: allow
```

Each key names a config source, and each value is a mode:

| Mode | What happens to a command from that source |
|---|---|
| `allow` | The command runs on the worker's host. |
| `sandbox` | The command runs in a throwaway Docker container on the worker's host, as BE-0090's `sandbox` mode does today. |
| `named` | Only a named command that the worker lists in `namedCommands` runs, on the worker's host. A command line the config spells out never runs, and both builds are skipped. |
| `deny` | No command from that source runs. The worker takes no job whose config from that source declares a command that *Routing* below adds a requirement for, and it skips both builds at run time. |

An omitted block or key takes the defaults `uploaded: sandbox`, `remote: sandbox`, and
`operator: allow`. These keep today's behavior with one tightening: a `remote` config's server
command now runs in a container instead of on the bare host, matching how today's rules already
treat that config's builds. A worker that sets a key to `sandbox` explicitly checks for Docker at startup and
exits with a message naming the key when Docker is missing. A worker whose `sandbox` mode comes from
the default advertises `command-execution:<source>` for that source only when Docker is present. Either way,
a worker never accepts a job it would then fail. No default uses `named`, since it asks the operator
to install scripts first. An operator who runs untrusted configs on a worker that holds secrets sets
`uploaded: named` and `remote: named` there.

### Named commands

A `namedCommands` block in `worker.yaml` lists the commands the worker's operator vouches for:

```yaml
commandExecution:
  uploaded: named
  remote: named
  operator: allow
namedCommands:
  seed-users:
    path: /opt/bajutsu/hooks/seed-users.sh   # a file on the worker, outside any bundle or checkout
    env: [SEED_COUNT]                        # the variables a config may set for this command
```

A config refers to a named command by its name, through the `use` field of a hook entry that the
sibling hook proposal defines. The rules keep the config from steering what runs:

- **The code belongs to the worker.** `path` is an absolute path on the worker. Bajutsu refuses a
  path inside the config's directory, so a bundle cannot replace the file.
- **No command line.** The config cannot add arguments. Bajutsu runs `path` directly, without a
  shell.
- **Listed variables alone.** The config may set only the variables in `env`. Bajutsu adds its own
  context variables, such as `BAJUTSU_OUTPUT`, and passes nothing else from the config.
- **No loader variables.** `worker.yaml` fails validation when `env` lists a variable that changes
  how a process loads code: `PATH`, `LD_*`, `DYLD_*`, `NODE_OPTIONS`, `PYTHONPATH`, and the like.

A named command runs on the worker's host under every mode except `deny`, since its code is the
operator's own. The output file, the `${hooks.<name>.<key>}` tokens, and the logs work as for any
other hook entry.

### The commands the block governs

The block governs every command a config declares for Bajutsu to run:

| Command | `allow` | `sandbox` | `named` | `deny` |
|---|---|---|---|---|
| `services.server.cmd` | runs on the host | runs in a container, as today | never runs | never runs |
| `subject.build` | runs on the host when the binary is missing | never runs | never runs | never runs |
| `driver.runner.build` | runs on the host when the runner is missing | never runs | never runs | never runs |
| a hook entry with `use` | runs on the host | runs on the host | runs on the host | never runs |

Neither build runs under `sandbox`: an iOS build needs the macOS toolchain, which no Linux container
provides. That matches the rule
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) already applies to
uploads, whose bundle ships a prebuilt binary. A run whose binary is missing then fails and names
the mode that kept the build from running.

A sibling proposal, *Run external commands around a run and each scenario from target config*, adds
four hook lists under `targets.<name>.hooks` and removes `services.server`. Once that proposal lands,
its hook entries with `cmd` take the place of `services.server.cmd` in the table above. Until then,
no config can name a command, so the `use` row applies once the sibling proposal lands.

### The config source

`serve` labels each job with the source of the config the job runs:

| Source | The config came from |
|---|---|
| `uploaded` | a bundle a browser user uploaded (BE-0073) |
| `remote` | a Git repository and ref that a Web UI user bound after `serve` started (BE-0063, BE-0121) |
| `operator` | the operator's own choice: the config `serve` started with, local or Git, or the config a terminal or CI run names |

The label travels in the job's intent, in place of today's `upload_exec` field. The config itself
has no way to set its own label. A `bajutsu run` started from a terminal or CI labels its config
`operator`, since the person who started the run chose the config. A worker passes the
job's label to the command it spawns (`run`, `record`, or `crawl`) through an option that takes the
place of `--upload-exec`, and that command then uses the label instead of deriving one. The worker
also passes its `worker.yaml` to each of the three, so the check inside the run reads the same
`commandExecution` block the worker advertised.

### Routing

The worker advertises one capability token per source it accepts: `command-execution:uploaded`,
`command-execution:remote`, or `command-execution:operator`, for each source whose mode is not `deny`. `serve` adds
`command-execution:<source>` to the required set of a job whose config declares at least one governed
command. The routing test of [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)
is an all-of subset check, so a job that declares commands reaches only a worker that accepts its
source.

Named commands add two more tokens. A worker advertises `named-execution:<source>` for each source whose mode
is not `deny`, and `named-command:<name>` for each entry of `namedCommands`. A job whose config uses
a named command requires `named-execution:<source>` and `named-command:<name>` for each name it uses. A job
that uses only named commands therefore reaches a `named` worker that has those scripts installed.

`command-execution:`, `named-execution:`, and `named-command:` join the reserved routing prefixes, so a worker's hand-written token override
cannot claim a source its `commandExecution` block denies. A job that no connected worker matches keeps waiting, with the existing notice that no
worker is available.

Workers are upgraded before `serve`. A worker that predates this item advertises no `command-execution:`
token, so it never leases a job that declares a governed command. Such a job waits until an upgraded
worker connects. Routing the job to an old worker instead would be unsafe: the new `serve` no longer
sends `--upload-exec`, so the old worker would run an uploaded command on its bare host.

`subject.build` and `driver.runner.build` add no requirement, because `serve` cannot know whether
a worker already holds the built binary or runner. The worker decides the build at run time, under the table above.

### The check inside the run

`bajutsu run` checks the policy again before any device work, against the `worker.yaml` it reads.
A command the policy refuses fails the run with exit 2, naming the command, its source, and the
mode. The policy refuses a command line the config spells out (`services.server.cmd`, or a hook
entry with `cmd`) under `named` or `deny`, and a hook entry with `use` under `deny`. A build that the
mode does not run is skipped instead, as the table above says, so a run whose binary already exists
still runs, and a run whose binary is missing fails by naming the mode. That second check covers a run started without `serve`, and a worker whose
advertisement no longer matches its file. The run writes `manifest.json` with the refused entry
before it exits.

A run started from a terminal or CI reads the `worker.yaml` that its `--worker-config` option names,
or the shipped default when the option is absent, as `run` does today
([BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md)). It never searches the disk
for a worker's file. The shipped default sets `operator: allow`, so ordinary local runs keep working.
An operator who wants a stricter policy for CLI runs on a machine passes that machine's file through
`--worker-config`.

### The command record

Every run adds a `commandExecutions` list to its `manifest.json`. Each entry records one governed command
the run met:

| Field | Meaning |
|---|---|
| `field` | where the config declared the command, such as `services.server.cmd` |
| `cmd` | the command line as declared, with `${secrets.*}` values masked |
| `source`, `mode` | the config source and the mode that applied |
| `decision` | `ran`, `sandboxed`, or `refused` |
| `exitCode` | the command's exit code, when it ran |
| `sha256` | the hash of the command line, and of the executable file when that file lies inside the config's directory or is a named command's `path` |

The report shows the list beside the run's other provenance. A script that changed between two runs
then shows a different hash, even when its command line stayed the same. The list replaces the
`uploadExec` provenance entry.

### Removing `--upload-exec` and `--allow-remote-build`

`serve --upload-exec`, `$BAJUTSU_UPLOAD_EXEC`, `serve --allow-remote-build`, and
`$BAJUTSU_ALLOW_REMOTE_BUILD` go away. `serve` refuses to start when it gets any of them, and the
message names the `commandExecution` block of `worker.yaml`. An operator who passed `--allow-remote-build`
sets `remote: allow` on the workers instead. The launchd plist that
`serve --emit-launchagent` prints stops passing `--upload-exec`. The `--upload-exec` option of `run`, `record`, and
`crawl`, which `serve` passed to the run today, goes away with it.

The `reuse` mode does not carry over. Under `reuse`, an uploaded `services.server.cmd` never ran,
and the run used a server that already answered the readiness URL. The sibling hook proposal
removes that readiness URL, so a mode built around it has nothing left to check. An operator who
used `reuse` picks `sandbox` or `deny`. Note that `deny` now fails a run whose config declares a
server command, where today it lets the run proceed against a server that already answers the
readiness URL.

### Migration

- `bajutsu/common/capability/` reads, validates, and advertises the `commandExecution` block.
- `bajutsu/serve/server/worker_job.py` and `bajutsu/serve/commands.py` carry the source label in
  place of `upload_exec`.
- `bajutsu/common/runner/launch_server.py` and `sandbox.py` take the mode from the run's policy
  check in place of the `--upload-exec` option.
- `bajutsu/serve/operations/dispatch.py` drops its `_build_trusted` decision, which the worker's
  policy now makes. `Job.withhold_runner_build` goes away with the environment variable that
  carries it to the run.
- `docs/self-hosting.md` (including its "Remote-config command execution (BE-0121)" section),
  `docs/cli.md`, `docs/drivers.md`, `DESIGN.md`, and `docs/architecture.md` change with the
  behavior, each with its Japanese mirror.

### Prime directives

- **AI never judges.** The policy is a table lookup on the worker. No large language model (LLM)
  call enters routing, the run, or the gate.
- **Determinism first.** The same file, source, and command always give the same decision.
- **App-agnostic.** The policy lives in `worker.yaml`, which belongs to the machine's operator, and
  the app's config stays unchanged.

## Alternatives considered

- **Keep the policy on `serve`.** Rejected: `serve` runs no job, cannot see a worker's host, and
  leaves a worker's operator no way to refuse.
- **Keep both, with the stricter value winning.** Rejected: a server-wide ceiling would put the
  policy in two places, and an operator reading one of them could not tell the effective mode.
- **An allowlist of command paths.** Rejected: a path does not vouch for the file's contents. A
  bundle can ship a harmful script under an allowed name, and an allowed interpreter such as `node`
  or `sh` runs whatever its arguments point at. Named commands keep the allowlist idea but list
  files the worker owns, which closes both gaps.
- **Named commands as the only way to run a command.** Rejected: a config the operator chose is
  trusted today, and requiring every script to be installed on every worker would slow ordinary
  development. The `named` mode applies to the sources an operator picks.
- **An allowlist of approved hashes.** Deferred: it would stop a changed script from running, but
  it needs a flow for someone to review and approve each new hash. The command record above
  provides the hashes such a flow would start from.
- **Keep the `reuse` mode.** Rejected: the sibling hook proposal removes the readiness URL that
  `reuse` depends on.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `worker.yaml`: the `commandExecution` block, its defaults, validation, and the Docker check at startup.
- [x] Source label: carry `uploaded` / `remote` / `operator` in the job intent in place of `upload_exec`, pass it to `run`, `record`, and `crawl` through the option that carries the label, together with the worker's `worker.yaml`, and label CLI runs.
- [x] Named commands: the `named` mode, the `namedCommands` block and its validation, and running a named command.
  - The `named` mode, the block, its validation, and the resolution a runner calls before it runs one (`resolve_named_command`) shipped here. No config can name a command until [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md) adds the `use` field of a hook entry, so its hook runner is what executes the resolved script.
- [x] Routing: advertise `command-execution:<source>`, `named-execution:<source>`, and `named-command:<name>` tokens and add the requirements to jobs that declare commands.
  - The worker advertises all three token kinds, and a job whose config declares `services.server.cmd` requires `command-execution:<source>`. The `named-execution:` / `named-command:` requirements on a job wait for BE-0491's `use` field, since no config names a command before it. A worker advertises `command-execution:<source>` only for a source whose mode runs a spelled-out command line (`allow`, or `sandbox` with Docker present), not for `named`, so it never leases a job it would refuse.
- [x] Run-time check: refuse a command the mode does not run (a spelled-out command line under `named` or `deny`, a `use` entry under `deny`), and skip builds the mode does not run, before device work.
- [x] Command record: the `commandExecutions` list in `manifest.json` and its place in the report.
- [x] Remove `serve --upload-exec`, `$BAJUTSU_UPLOAD_EXEC`, `serve --allow-remote-build`, `$BAJUTSU_ALLOW_REMOTE_BUILD`, and the `--upload-exec` option of `run`, `record`, and `crawl`.
- [x] Update the docs, `DESIGN.md`, and `docs/architecture.md` in both languages.

Log:

- 2026-11-20 — All eight units landed together ([#2494](https://github.com/bajutsu-e2e/bajutsu/pull/2494)).
  - `worker.yaml` gains `commandExecution` and `namedCommands`; the worker advertises the sources it accepts, and a job whose config declares `services.server.cmd` routes only to a worker that runs commands from its source.
  - `serve` labels each job `uploaded`, `remote`, or `operator`; the worker stamps the label and its own `worker.yaml` onto the `run`, `record`, or `crawl` it spawns, and decides its own pre-run build.
  - A run checks the policy before any device work, refuses a spelled-out server command under `named` or `deny` even when a server already answers, and records every governed command in `manifest.json` as `commandExecutions`, shown in the report.
  - `serve --upload-exec`, `--allow-remote-build`, their environment variables, the `--upload-exec` option of `run` / `record` / `crawl`, and `BAJUTSU_WITHHOLD_RUNNER_BUILD` are removed; `serve` refuses the removed options by name.
  - An untrusted source's build travels as `governed_build`, and the legacy `withhold_runner_build` flag still travels, so a worker that predates this item never runs it.

## References

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md): loading a config from a Git
  repository.
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md): uploaded bundles,
  whose `build` never runs.
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md):
  the `serve --upload-exec` policy this item replaces.
- [BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md): the trust split for Git configs bound through the Web UI,
  and `--allow-remote-build`, which this item replaces.
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md): routing jobs
  by capability tokens.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md): `serve` queues every
  job, and workers run them.
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the worker capability file.
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py) and
  [`sandbox.py`](../../bajutsu/common/runner/sandbox.py): today's governed server start.
- [`bajutsu/serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py): where the
  worker builds the run command from a job's intent.

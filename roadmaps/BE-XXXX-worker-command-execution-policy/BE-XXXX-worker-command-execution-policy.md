**English** · [日本語](BE-XXXX-worker-command-execution-policy-ja.md)

# BE-XXXX — Let the worker decide whether config-declared commands run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-worker-command-execution-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Security hardening |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) |
<!-- /BE-METADATA -->

## Introduction

This item moves the decision about whether a config's commands run from the server to the worker.
A config can declare commands that Bajutsu runs on the machine that executes a run: today
`services.server.cmd` and `subject.build`. Today the `serve` option `--upload-exec` decides, for
uploaded bundles alone, whether such a command runs. This item replaces that option with a
`commands` block in the worker capability file, `worker.yaml`. The block sets one mode per config
source: an uploaded bundle, a Git repository, or a config on the local file system.

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

The policy also leaves one source ungoverned. BE-0090 treats every config that does not come from an
upload as trusted, and a config loaded from a Git repository
([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) falls under that rule. A
worker that runs jobs from many repositories therefore runs any command any of those configs
declares.

Finally, nothing records which commands a run executed. An unintended command, such as a changed
script in a repository, leaves no trace beside the run's evidence.

Once this item ships, a reader can check two observable differences. A worker whose `worker.yaml`
sets `uploaded: deny` never leases a job whose uploaded config declares a command. Every run's
`manifest.json` lists each command the run executed or refused, with its source, its mode, and a
hash of what ran.

## Detailed design

### The `commands` block in `worker.yaml`

The worker capability file of
[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) gains one optional block:

```yaml
version: 1
environment: local
drivers: [playwright]
commands:
  uploaded: sandbox
  git: allow
  local: allow
```

Each key names a config source, and each value is a mode:

| Mode | What happens to a command from that source |
|---|---|
| `allow` | The command runs on the worker's host. |
| `sandbox` | The command runs in a throwaway Docker container on the worker's host, as BE-0090's `sandbox` mode does today. |
| `deny` | No command from that source runs. The worker takes no job whose config from that source declares one. |

An omitted block or key keeps today's behavior: `uploaded: sandbox`, `git: allow`, and
`local: allow`. A worker that sets any key to `sandbox` checks for Docker at startup and exits with
a message naming the key when Docker is missing. A worker that cannot honor its own file thus never
starts, so it never accepts a job it would then fail.

### The commands the block governs

The block governs every command a config declares for Bajutsu to run:

| Command | `allow` | `sandbox` | `deny` |
|---|---|---|---|
| `services.server.cmd` | runs on the host | runs in a container, as today | never runs |
| `subject.build` | runs on the host when the binary is missing | never runs | never runs |

`subject.build` does not run under `sandbox`: an iOS build needs the macOS toolchain, which no Linux
container provides. That matches the rule
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) already applies to
uploads, whose bundle ships a prebuilt binary. A run whose binary is missing then fails and names
the mode that kept the build from running.

A sibling proposal, *Run external commands around a run and each scenario from target config*, adds
four hook lists under `targets.<name>.hooks` and removes `services.server`. Once that proposal lands,
its hook entries take the place of `services.server.cmd` in the table above.

### The config source

`serve` labels each job with the source of the config the job runs:

| Source | The config came from |
|---|---|
| `uploaded` | a bundle a browser user uploaded (BE-0073) |
| `git` | a Git repository and ref (BE-0063) |
| `local` | a file on the file system of the machine that started the run |

The label travels in the job's intent, in place of today's `upload_exec` field. The config itself
has no way to set its own label. A `bajutsu run` started from a terminal or CI labels its config
`git` when it loads the config through the Git source, and `local` otherwise.

### Routing

The worker advertises one capability token per source it accepts: `commands:uploaded`,
`commands:git`, or `commands:local`, for each source whose mode is not `deny`. `serve` adds
`commands:<source>` to the required set of a job whose config declares at least one governed
command. The routing test of [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)
is an all-of subset check, so a job that declares commands reaches only a worker that accepts its
source. A job that no connected worker matches keeps waiting, with the existing notice that no
worker is available.

`subject.build` adds no requirement, because `serve` cannot know whether a worker already holds the
built binary. The worker decides the build at run time, under the table above.

### The check inside the run

`bajutsu run` checks the policy again before any device work, against the `worker.yaml` it reads.
A governed command whose source maps to `deny` fails the run with exit 2, naming the command, its
source, and the mode. That second check covers a run started without `serve`, and a worker whose
advertisement no longer matches its file.

### The command record

Every run adds a `commands` list to its `manifest.json`. Each entry records one governed command
the run met:

| Field | Meaning |
|---|---|
| `field` | where the config declared the command, such as `services.server.cmd` |
| `cmd` | the command line as declared, with `${secrets.*}` values masked |
| `source`, `mode` | the config source and the mode that applied |
| `decision` | `ran`, `sandboxed`, or `refused` |
| `exitCode` | the command's exit code, when it ran |
| `sha256` | the hash of the command line, and of the executable file when that file lies inside the config's directory |

The report shows the list beside the run's other provenance. A script that changed between two runs
then shows a different hash, even when its command line stayed the same.

### Removing `--upload-exec`

`serve --upload-exec` and `$BAJUTSU_UPLOAD_EXEC` go away. `serve` refuses to start when it gets
either, and the message names the `commands` block of `worker.yaml`. The launchd plist that
`serve --emit-launchagent` prints stops passing the option. The `--upload-exec` option of `run`, `record`, and
`crawl`, which `serve` passed to the run today, goes away with it.

The `reuse` mode does not carry over. Under `reuse`, an uploaded `services.server.cmd` never ran,
and the run used a server that already answered the readiness URL. The sibling hook proposal
removes that readiness URL, so a mode built around it has nothing left to check. An operator who
used `reuse` picks `sandbox` or `deny`.

### Migration

- `bajutsu/common/capability/` reads, validates, and advertises the `commands` block.
- `bajutsu/serve/server/worker_job.py` and `bajutsu/serve/commands.py` carry the source label in
  place of `upload_exec`.
- `bajutsu/common/runner/launch_server.py` and `sandbox.py` take the mode from the run's policy
  check in place of the `--upload-exec` option.
- `docs/self-hosting.md`, `docs/cli.md`, `docs/drivers.md`, `DESIGN.md`, and
  `docs/architecture.md` change with the behavior, each with its Japanese mirror.

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
  bundle can ship a harmful script under an allowed name.
- **An allowlist of approved hashes.** Deferred: it would stop a changed script from running, but
  it needs a flow for someone to review and approve each new hash. The command record above
  provides the hashes such a flow would start from.
- **Keep the `reuse` mode.** Rejected: the readiness URL it depends on leaves Bajutsu with the
  sibling hook proposal.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `worker.yaml`: the `commands` block, its defaults, validation, and the Docker check at startup.
- [ ] Source label: carry `uploaded` / `git` / `local` in the job intent in place of `upload_exec`, and label CLI runs.
- [ ] Routing: advertise `commands:<source>` tokens and add the requirement to jobs that declare commands.
- [ ] Run-time check: refuse a governed command whose source maps to `deny`, before device work.
- [ ] Command record: the `commands` list in `manifest.json` and its place in the report.
- [ ] Remove `serve --upload-exec`, `$BAJUTSU_UPLOAD_EXEC`, and the `--upload-exec` option of `run`, `record`, and `crawl`.
- [ ] Update the docs, `DESIGN.md`, and `docs/architecture.md` in both languages.

## References

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md): loading a config from a Git
  repository.
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md): uploaded bundles,
  whose `build` never runs.
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md):
  the `serve --upload-exec` policy this item replaces.
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md): routing jobs
  by capability tokens.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md): `serve` queues every
  job, and workers run them.
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the worker capability file.
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py) and
  [`sandbox.py`](../../bajutsu/common/runner/sandbox.py): today's governed server start.
- [`bajutsu/serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py): where the
  worker builds the run command from a job's intent.

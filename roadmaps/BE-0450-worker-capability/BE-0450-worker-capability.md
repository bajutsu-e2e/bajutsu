**English** · [日本語](BE-0450-worker-capability-ja.md)

# BE-0450 — Declare what one worker can run: host support and device limits

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0450](BE-0450-worker-capability.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0450") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

A **worker capability** states what one worker can run. A worker is the machine or process that runs scenarios: a developer's machine, a server worker, or a worker that submits to a device cloud. The worker capability lives in a small YAML file, `worker.yaml`. It names the worker's environment, how many jobs the worker may have in flight, how many targets one job may hold, and which drivers the worker offers. Before any device work begins, Bajutsu compares a scenario's needs against the file, and a scenario that cannot run on this worker fails immediately, with a reason that names the unmet line. The worker capability extends the vocabulary that the server already uses to route jobs to workers ([BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)).

Two questions decide runnability today, and neither has a declaration. The first is whether the worker's host can drive the platform at all: an iOS scenario needs macOS, and a Linux worker cannot run it. The second is how many targets a job may drive at once. A target is one entry under `targets` in `bajutsu.config.yaml` ([glossary](../../docs/glossary.md#target-app-device)). While a scenario runs, each of its targets holds one device: a Simulator, an emulator, a physical phone, or a browser context. An AWS Device Farm run reserves one physical phone, so a scenario that drives two targets cannot run there. The host is a fact about the driver and the machine, so this item takes it from the driver and the machine. The target limit is a fact about the worker's environment, so the operator states it in `worker.yaml`.

## Motivation

Bajutsu already checks a scenario against what a *driver* can do. The preflight capability check ([BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)) gates each construct on the driver's capability tokens. The device-control tokens ([BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md)) split the gate per operation. That machinery answers "can this driver perform this step?" It cannot answer "can this worker run this driver at all, and with how many targets?" Those two facts belong to neither the step nor the driver's tokens.

The missing facts surface as vague failures. On a host without `xcodebuild`, the iOS actuator is simply unavailable, and `select_actuator` raises a generic `no available actuator` ([`bajutsu/common/backends.py:121`](../../bajutsu/common/backends.py)). The message does not say that iOS needs macOS, so an operator who sends an iOS job to a Linux worker must work that out. The server worker pool ([BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)) routes on platform tokens such as `platform:ios`, but nothing ties that token to the host operating system.

The target count has the same gap. A multi-target scenario ([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)) holds one target per entry of its `targets` list for its whole length, and the local runner refuses a device pool that is too small. Serve's Device Farm dispatch ([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md)) builds one request per scenario, and each request carries a single `target`. Nothing declares that one Device Farm run reserves exactly one device, so dispatch has no reason to refuse a scenario that names two targets, although such a scenario can never run there. The job reserves a paid device first and fails later.

The observable difference is threefold:

- An iOS scenario run on a Linux host fails before any work with a reason naming the unmet requirement (`xcuitest runs only on macOS`), in place of `no available actuator`.
- A two-target scenario leased by a Device Farm worker fails before a device is reserved, with a reason naming the one-target limit.
- The same scenario suite still runs unchanged on a Mac.

## Detailed design

### What the file describes

A **worker** is the machine that runs scenarios, and a **target** names the application a scenario drives. The two have different authors and different lifetimes: an application team writes the target in `bajutsu.config.yaml`, and an operator knows what a machine can host. The worker capability therefore lives in its own file, `worker.yaml`, and never in `bajutsu.config.yaml`. The project configuration also reaches a server worker only after a job is leased, so it cannot state what the worker advertises to be leased.

One `worker.yaml` describes one worker. The worker has one host and one environment, so neither needs a list. The file has five top-level keys.

| Key | Type | Meaning |
|---|---|---|
| `version` | integer | The schema version of the file. Today the loader accepts `1` and rejects any other value. |
| `environment` | string | Where this worker runs jobs: `local` (a developer's machine, or a worker that drives devices on its own host) or the name of a device-cloud provider, such as `devicefarm`. Omitted means `local`. |
| `maxJobConcurrency` | positive integer, optional | How many jobs this worker may have in flight at once. Omitted means one. The worker sends no lease request while it is full. |
| `maxTargetsPerJob` | positive integer, optional | How many targets one scenario may declare, and so how many devices it holds at once, counted across every driver. A target on a Simulator, an emulator, a physical phone, or a browser counts as one. Omitted means unbounded. |
| `drivers` | list of driver names | The drivers this worker offers. A driver that the list omits is unavailable on this worker. |

`maxTargetsPerJob` counts targets of every driver together, per scenario. `--workers` lanes that run separate scenarios side by side are not added together. With `2`, a job may operate a Simulator and a browser at the same time. With `1`, a job may operate one of them and no more.

The file is loaded into frozen values and validated against a schema. Loading fails on an unknown driver name, an unknown environment, or a non-positive number. An environment other than `local` must name a batch-provider kind that Bajutsu can register ([`serve/batch_provider`](../../bajutsu/serve/batch_provider/_functions.py); today `devicefarm`). Whether that provider actually registers, for example with `DEVICEFARM_PROJECT_ARN` set, is checked when the worker starts, as the Device Farm dispatch item describes. Adding a cloud means adding its provider kind, so the file names an environment and does not define one. A `local` worker accepts `maxJobConcurrency` of one only. Running several jobs at once on a local worker needs a device allocated to each job, which is separate work; the loader rejects a larger value and says so. The `--workers` lanes of `bajutsu run` are unaffected.

### What comes from the driver and the machine

The worker capability file carries no host list. The host is the operating system the worker process runs on, which the worker reads from the machine and never from the file. Whether a driver can run on that host is a fixed fact about the driver, so each driver declares it beside its `CAPABILITIES` (`xcuitest` needs macOS, and the other three run anywhere Bajutsu runs). On a worker whose environment is `local`, a driver that the file lists but the host cannot run is unavailable, with a reason such as `xcuitest runs only on macOS; this worker runs on linux`. On a worker whose environment is a device cloud, the driver runs on the cloud's host, so the host rule does not apply.

The default file, shipped with the package, describes a local worker that offers every driver without a target limit. On a Linux machine, `xcuitest` is then listed but unavailable, for the host reason.

```yaml
# bajutsu/common/capability/worker.yaml (the default, shipped with the package)
version: 1
environment: local
drivers: [xcuitest, adb, playwright, fake]
```

### Replacing the file

An operator replaces the default for one process by passing another file with `--worker-config` at startup. The file is used in place of the default, not merged with it, and the loader validates it against the schema. Three files follow: a Mac, a Linux machine that serves Android emulators and browsers, and a worker that submits to Device Farm.

```yaml
# mac-ci.worker.yaml
version: 1
environment: local
maxTargetsPerJob: 2                # a job may use a Simulator and a browser together
drivers: [xcuitest, playwright]
```

```yaml
# linux-android.worker.yaml
version: 1
environment: local
maxTargetsPerJob: 1                # a job uses an emulator or a browser, not both
drivers: [adb, playwright]
```

```yaml
# devicefarm.worker.yaml
version: 1
environment: devicefarm
maxJobConcurrency: 2               # at most two Device Farm jobs in flight
maxTargetsPerJob: 1                # a Device Farm run holds one phone
drivers: [adb, xcuitest]
```

The third file omits `playwright`, so a browser scenario is unavailable on that worker.

The file declares no routing tokens. The runtime and device-class tokens that the worker advertises (`ios18`, `ipad`) come from what the machine actually has, as they do today through the Simulator inventory ([`serve/capabilities.py`](../../bajutsu/serve/capabilities.py)). A declaration cannot promise that a lease will succeed, and a hand-written inventory would be a second source that drifts from the real one. The target limit differs: no probe answers it before a run, so it is declared.

`bajutsu worker` (including `bajutsu worker --once`) and `bajutsu run` accept `--worker-config <path>`, since a developer's own machine is a worker too. `bajutsu run` is a thin wrapper around the one-shot worker, as the item that stops the server from executing jobs describes, and it rejects a file whose `environment` is not `local`, because `run` drives devices on its own host and never submits to a cloud. Without `--worker-config`, the default file applies. There is no environment variable and no implicit lookup in the working directory, so the file in force is always the one named on the command line. The server does not read the file, because the server is not a worker.

### Abolishing `--capabilities` and `requires`

Two settings let an operator put a free-form token into routing today. A server worker accepts `--capabilities` or `$BAJUTSU_WORKER_CAPABILITIES`, for example `ios18,ipad`, and adds the tokens verbatim to what it advertises ([`serve/cli/worker.py`](../../bajutsu/serve/cli/worker.py)). A target, or the `defaults` block, lists `requires` tokens that become part of a job's required set ([`serve/helpers.py`](../../bajutsu/serve/helpers.py)). The two are the two ends of one mechanism, and this item abolishes both.

The worker capability covers the advertising side of what they were for. The runtime and device-class tokens come from the Simulator inventory without any flag. The environment and the target limit come from `worker.yaml`. What `--capabilities` could do beyond that is advertise a token the machine does not have, and that is a defect to remove: a worker that claims `ios18` without the runtime attracts jobs it cannot run, which is the misrouting BE-0166 exists to prevent. With the flag gone, a `requires` token that no inventory can produce could never be advertised, so the job would wait forever. The setting goes with the flag.

A job's required set therefore becomes `platform:<p>`, plus `host:<os>` for a driver that runs on one host operating system only. Requiring an iOS runtime or a device class was possible through `requires`, and it needs a new source once `requires` is gone. Deriving the requirement from the target's `device` field is a separate item, and until then a job cannot require an iOS runtime or a device class.

The worker advertises the union of these sources: the `platform:*` tokens from `--platform`, the tokens derived from its Simulator inventory, `host:<os>` for the operating system it runs on, and `environment:<name>` when its environment is not `local`. A worker whose environment is not `local` drives no device on its own host, so it advertises no `platform:*` token, ignores the default of `--platform`, and rejects an explicit `--platform` with a message that names the environment. For a `local` worker, the worker checks `--platform` against the file and the host at startup and exits with a message naming the driver when the file omits it or the host cannot run it, in place of advertising a platform it cannot serve. This turns a Linux machine that starts with the default `--platform ios` from an idle poller into a startup error, which is the right failure.

`--capabilities`, `$BAJUTSU_WORKER_CAPABILITIES`, and `requires` are deprecated in one release. `--capabilities` and the variable are removed in the next; `requires` is removed only once the item that derives the requirement from the target's `device` has landed, because removing it earlier would leave an `ios18` or `ipad` job with no way to reach a compatible worker. While deprecated they are still honored, and a one-time notice appears (`warn_once`, [`deprecations.py`](../../bajutsu/common/deprecations.py)). The notice for the flag and the variable says that the tokens now come from the inventory and the file. The notice for `requires` says that requiring an iOS runtime or a device class is not supported until the requirement is derived from the target's `device`. Once removed, `requires` fails with the same reason, through a removal error that names no replacement key, because `requires` has none. While they remain honored, a token with the reserved prefix `environment:` in any of the three is rejected at startup or config load, with a message that names the reservation, since only a registered provider may advertise that prefix. `--platform` stays, since it selects among drivers and is not a capability token.

### Deriving a scenario's needs

A scenario's requirement is computed, never authored. It is a pure function of the scenario and the effective target configuration:

- The drivers it needs: the actuator resolved for each declared target, or for the single resolved target, after the host and `drivers` filter below.
- The target count: the number of declared targets, or one. BE-0428 counts per pool, but this count is the sum over every pool, so one Simulator target and one browser target count two.

No new scenario field is added. Prime directive 3 holds: per-application differences stay in `targets.<name>`, and the worker facts stay in `worker.yaml`.

### The check

A new pure function, `worker_capability_unsupported()`, sits beside `unsupported()` in [`capability_preflight.py`](../../bajutsu/common/capability/capability_preflight.py). It takes the scenario, the effective target configuration, the worker's capability, and the host. `unsupported()` keeps its signature, because it receives only capability tokens, and its other callers (the two paths of the runner, actuator escalation, and `doctor`) have no worker file to pass. The new function runs once per scenario, before the per-target token checks, because a driver that cannot start makes step-level checks moot. For each driver the scenario needs, it evaluates rules 1 and 2. Once per scenario, it evaluates rule 3:

1. On a `local` worker, the host can run the driver.
2. The driver appears in the worker's `drivers`.
3. The scenario's target count is at most `maxTargetsPerJob`.

A violation is reported per scenario as a preflight reason string that begins with the fixed prefix `worker-capability-unsupported:`, so a reader and a test can tell it from a capability reason without a schema change. The message names the driver and the unmet requirement. Only the offending scenarios fail; the rest run, as in BE-0082. The check is a pure function with no device access and no clock, so it stays on the deterministic path (prime directive 1) and needs no Simulator to test.

The check lands at two points, both on the worker, since the worker alone holds the file.

- **`bajutsu run`.** Rules 1 and 2 run inside `_select_actuator_or_exit` ([`cli/_shared.py`](../../bajutsu/cli/_shared.py)), before `select_actuator` is called. It drops each requested actuator whose driver the host cannot run or the worker's `drivers` omits, returns the backend list without them so the per-scenario selection in `runner/pool.py` cannot pick one of them again, and exits with code 2 on the named reason only when no candidate remains, so a fallback list such as `[ios, web]` still resolves to `playwright` on Linux. Because `record`, `crawl`, `audit`, and `repl` share this helper, they gain the same message. Without this placement, a Linux host exits with code 2 on the generic `no available actuator` before the runner's preflight ever starts. Before `_resolve_target_effs`, `run` evaluates `worker_capability_unsupported()` over the loaded scenarios, reports each scenario it fails with the prefixed reason, and removes those scenarios from the list passed on, so no device is acquired for a target that only a failed scenario declares, and `_pool_demand` never counts them. The check reads each scenario's own declared targets from the configuration, without acquiring a device, so a later scenario in an inferred-target batch whose targets are disjoint from the first file's is judged on its own targets. `_select_actuator_or_exit` exits with code 2 only when no scenario remains runnable. `_acquire_targets` is split so that every declared target's actuator is selected and checked before the first `acquire_device` call.
- **A leased job, or `bajutsu worker --once`.** For a job that carries a cloud request (one scenario per job), the worker runs the function before it submits anything and fails the job through the existing result route with the named reason. For a local job, the check runs per scenario inside the `run` path above, so only the offending scenarios fail. The worker passes its `--worker-config` path, resolved to an absolute path, to the internal run entry point it spawns, so the check in that process reads the same file.

The server keeps to routing. A driver that runs on a single host operating system adds the matching `host:<os>` token to the job's required set, and the routing test is an all-of subset check ([`serve/capabilities.py`](../../bajutsu/serve/capabilities.py)), so a driver that runs on several hosts adds no `host:` requirement. A worker that advertises an `environment:*` token serves only jobs that require it; the Device Farm dispatch item adds that rule to `can_serve`, and it also builds the cloud job's required set. A job that carries a cloud request (`Job.batch`) is the exception: it requires exactly `environment:<name>`, with no `platform:*` and no `host:*` token, since the device and its host belong to the provider. A job without a cloud request, even from a target that sets `cloudBatch`, keeps its local requirement. A job that no connected worker matches keeps waiting as in BE-0166, because the worker fleet changes over time and a refusal against the current fleet would reject valid jobs during start-up.

### Boundaries

The item is a decision about *runnability*, not about capability of a step, so the existing token gates stay as they are. It adds no skipped status: an unrunnable scenario is a failure, since a silent skip would let a misconfigured worker report green. It does not probe Device Farm quota; the target limit is a fact the operator states, and quota stays the concern of the job-concurrency budget of the Device Farm dispatch item. The server does not check a job against the workers' limits at dispatch. The worker fails such a job at lease, and a later item can have workers advertise their limits so that the server refuses earlier. Deriving an iOS runtime or device-class requirement from the target's `device`, now that `requires` is gone, is likewise a later item. The server adds `host:<os>` to a job whose driver runs on one host only, so workers should be upgraded before the server.

### Prime-directive compliance

- **AI never judges.** The check is a deterministic function of the scenario, the worker file, and the host. No model call is involved.
- **Determinism first.** The gate fails fast and names the unmet requirement, replacing a late or vague failure.
- **App-agnostic.** The worker facts are data in one YAML file, and the tool, runner, and scenarios stay unchanged across targets.

### Work breakdown (MECE)

1. **Worker file and loader.** Add the default `worker.yaml`, the schema and loader that yield the worker capability, and `--worker-config` on `worker` and `run`.
2. **Driver host requirement.** Declare each driver's host requirement beside its `CAPABILITIES`, and read the host from the machine.
3. **Requirement derivation.** Compute a scenario's drivers and target count.
4. **The check.** Add `worker_capability_unsupported()`, fix the message prefix, and wire it into `bajutsu run` (before the device lease, with `_acquire_targets` split so every target is checked first), the shared actuator selection that drops actuators the host or the file rules out, and the worker's lease path and `--once`, forwarding the file path to the internal run entry point.
5. **Advertising and routing.** Advertise `host:<os>`, enforce the `environment:` reservation in the deprecated token inputs, apply the `--platform` rules of a `local` worker, build the required set of local jobs, and deprecate `--capabilities`, `$BAJUTSU_WORKER_CAPABILITIES`, and `requires`.
6. **Documentation.** Update `docs/drivers.md`, `docs/scenarios.md`, `docs/architecture.md`, `docs/configuration.md`, `docs/self-hosting.md`, and `docs/cli.md` with their Japanese mirrors, and `deploy/self-host/README.md`, which tells operators to pin runtimes with `requires`.

## Alternatives considered

| Option | Summary | Why not |
|---|---|---|
| A table of every driver in every environment | One shared file lists each driver with its hosts and each environment it can run in. | A worker has one host and one environment, so the table asks the operator to read rows that never apply. The host is a fact about the driver and the machine, and the environment is a fact about the worker. |
| The target limit per driver | Each driver carries its own limit under `drivers`. | A job's targets span drivers, so "a Simulator and a browser together" would need a rule for which driver's limit applies. One top-level number says it directly. |
| Keep `--capabilities` beside the file | The flag stays as an override. | It lets an operator advertise tokens the machine lacks, so a worker attracts jobs it cannot run, and the inventory already supplies the tokens. |
| Keep `requires` and let `worker.yaml` declare labels | The file gains free-form labels that `requires` names. | It returns to hand-written tokens, which the inventory-derived design was chosen to avoid. |
| Declare the inventory in the file | Typed fields such as `iosRuntimes`, `apiLevels`, and `browsers` under each driver. | A declaration cannot promise a lease will succeed, and it is a second source that drifts from the inventory the worker can probe. Only facts that no probe answers belong in the file. |
| Merge the operator's file into the default | The operator's file overrides individual entries, narrowing only. | A merge needs rules for omission and widening, and a reader must combine two files to know what is in force. Whole-file replacement leaves one file to read. |
| Put it in `bajutsu.config.yaml` | Add a `workerCapabilities` key to the project configuration. | The file describes targets and is authored by an application team, and a server worker receives it only after leasing a job, so it cannot decide what the worker advertises. |
| More driver capability tokens only | Add tokens such as `hostOS:darwin` and `singleDevice` to `capabilities()`. | The same `xcuitest` class runs on a Mac and on Device Farm with different limits, so a static token set cannot express both. |
| Runtime probing only | Detect OS and target count and reject at run time. | Device Farm's one-target limit cannot be probed, and the requirement to *declare* would go unmet. |
| Skip instead of fail | Report an unrunnable scenario as skipped. | A misconfigured worker would skip everything and still pass CI, which conflicts with determinism first. |

## Progress

> Keep this section current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one entry per unit of work), and the log records what changed and when, oldest
> first, each with a link to its PR.

- [ ] Worker file and loader
- [ ] Driver host requirement
- [ ] Requirement derivation
- [ ] The check
- [ ] Advertising and routing
- [ ] Documentation and Japanese mirrors

## References

- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md): the preflight this item extends.
- [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md): the per-operation capability tokens.
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md): worker capability routing.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md): Device Farm dispatch and the device budget.
- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md): multi-target scenarios and the device pool rule.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): device-cloud providers.

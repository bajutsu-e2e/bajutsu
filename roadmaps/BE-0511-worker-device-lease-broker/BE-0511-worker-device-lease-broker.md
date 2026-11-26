**English** · [日本語](BE-0511-worker-device-lease-broker-ja.md)

# BE-0511 — Lease a device from a lease broker for each job a worker runs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0511](BE-0511-worker-device-lease-broker.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0511") |
| Implementing PR | [#2565](https://github.com/bajutsu-e2e/bajutsu/pull/2565) |
| Topic | Device-cloud execution |
| Related | [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md) |
<!-- /BE-METADATA -->

## Introduction

A worker takes the devices a job needs from a **lease broker**. A lease broker is a program that lends out simulators, emulators, and phones. It keeps two borrowers off the same device. The worker borrows after it claims a job. It hands the devices to the run and returns them when the run ends.

A new device provider, `leased`, does the borrowing. It plugs into the device-provider seam that already reserves a device for a run. The operator declares the broker in `worker.yaml`. No scenario or target changes.

When the broker cannot supply every device the job needs, the worker keeps none of them. It returns the job to the queue. While the job's decline window is open, the job waits in the queue. Its verdict never records that wait.

## Motivation

A worker runs on a machine whose devices other people and agents share. Today the worker has no way to ask for one. An operator who wants a run on a shared device borrows it by hand.

The operator starts a lease at the broker and copies the device address into `--udid`. The operator also exports the environment the broker returned. One example is the port of the Android Debug Bridge (adb) server that owns the emulator. The operator then keeps the lease process alive for the whole run and stops it afterward. A job with more than one target or lane repeats these steps for every device.

A worker cannot do any of this. A worker on a shared machine can drive the devices that nobody else touches, and no others.

That limit has two costs. A worker that drives a device it does not own can collide with another borrower. The collision looks like a scenario error that has nothing to do with the app. A machine that dedicates devices to workers leaves them idle between jobs. No other borrower can use them.

The change works when a worker on a shared machine runs a queued job on devices it borrowed. The operator types no `--udid` flag and exports no broker environment.

A job that needs three devices holds three device leases while it runs.

When the job ends, the broker lists no lease held by that worker.

When the broker cannot supply every device, the job stays queued and the worker polls again. While the job's decline window is open, its result never turns red for want of a device. The window is one hour by default.

A job that needs both an iOS and an Android device runs on a `leased` worker. A local worker with both devices runs it too.

A worker that stops mid-run leaves no run driving a device that the broker has lent again.

The word *lease* already has a second meaning in the serve control plane. A worker *leases a job* from the server. The server reclaims the job when the worker stops sending heartbeats. This item says *device lease* for the broker's loan and *job lease* for the server's.

## Detailed design

### Where the borrowing happens

The seam already exists. A `DeviceProvider` ([BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)) has one method, `acquire(eff, requested_udid)`. It returns a `DeviceLease`. The lease holds the udid spec the run drives. It also holds a `release` callable that the run calls in a `finally`.

Providers register under a `kind` in `bajutsu/common/runner/device_provider/_functions.py`. Three kinds exist today: `local`, `appium`, and `devicefarm-host`.

This item adds a fourth kind, `leased`. A `_LeasedProvider` runs the broker's lease command and reads the device from its answer. It returns a `DeviceLease` whose `release` ends the lease. The `bajutsu run` command already takes a device through `acquire_device` and releases it in a `finally`.

The run pipeline changes in one place. `_acquire_targets` applies each lease's `env` to the process environment. It does so before it resolves the target's devices.

Two other callers take a device and drop its lease.

One is `_device_udid_or_exit`. It serves `record`, `crawl`, and `repl`. It also serves `doctor` and `audit`. The other is the Model Context Protocol (MCP) tool that calls `acquire_device` through `_probe_device`. Under a `leased` environment each would leave the lease command running. Both release the lease when the command ends. The MCP server is long-lived, so its tool releases the lease when the tool call returns. Both also apply the lease's `env` to the process environment before they use the address.

Two existing checks compare the worker's environment to `local` directly. They are `_register_environment_or_exit` and `environment_refusal`. Setting `is_local` does not reach them. Two others read `is_local` but must treat `leased` apart. The capability tokens take the platforms from the file. `_device_reader` skips `leased`. Each gets a row below.

| Layer | Change |
|---|---|
| `bajutsu/common/runner/device_provider/` | New `_leased_provider.py`, registered as `leased` in `_ensure_builtins` |
| Same package | New helper that splits a platform-tagged `--udid` list. `_LeasedProvider` and `_reject_unshareable_groups` both call it |
| `DeviceLease` (`device_lease.py`) | New field `env` for the variables the run's device tools must see |
| `bajutsu/common/config/schema/device_provider.py` | `DeviceProvider` gains fields for the lease command, the acquire timeout, and the release timeout |
| `bajutsu/common/capability/worker_capability.py` | New environment `leased` with a `lease` block and a `platforms` list |
| Same file | `device_provider_for` returns the `leased` provider with those fields for a platform listed in `platforms`, and None (local) for any other, web included |
| Same file | `is_local` is true for `leased`, so the one-job cap of a local worker applies |
| `bajutsu/serve/cli/worker.py` | Capability tokens, route tokens, and `--machine-id` treat `leased` as `local` |
| `bajutsu/serve/cli/worker.py` (`_register_environment_or_exit`) | `leased` registers no batch provider, like `local` |
| `bajutsu/serve/server/one_shot.py` (`environment_refusal`) | `leased` takes a plain run spec and refuses a batch, like `local` |
| `bajutsu/serve/cli/worker.py` | `_device_reader` skips `leased`, whose real devices belong to the broker |
| `bajutsu/serve/cli/worker.py` (`_run_with_heartbeat`) | After `claim_check` passes and the bundle fetch finishes, the worker borrows the job's devices on the heart-beaten run thread. It returns before the run starts when `lost` is set. A total borrow budget below the job-lease timeout bounds the borrow. The worker releases the devices in a `finally` |
| `bajutsu/serve/server/worker_job.py` | On a `leased` worker, drops a spec-supplied `--udid` and boots no spec `udids` |
| New module that `bajutsu/serve/cli/worker.py` and `bajutsu/run/cli.py` both import | `_pool_demand` moves there, so the worker and the run count devices with one function |
| `bajutsu/run/cli.py` (`_acquire_targets`) | Applies each lease's `env` to the process environment before it resolves the target's devices |
| `bajutsu/run/cli.py` (`_reject_unshareable_groups`) | Reads the entries of the target's platform from a tagged `--udid` |
| `bajutsu/cli/_shared.py` (`_device_udid_or_exit`) and `bajutsu/mcp/tools.py` (`_probe_device`) | Apply `env`, and release the lease when the command ends or the tool call returns |
| Serve routes and database | New `POST /api/worker/decline`. Each job stores a decline count, its first decline time, its last reason, a `not_before` time, and the environment of the worker that declined. The lease request names the worker's environment |
| `bajutsu/serve/server/db/sql_repository.py` (`_lease_candidates`) | The lease scan skips a queued job until its `not_before` has passed, only for a worker of the environment that declined it |
| Server settings | New `BAJUTSU_DECLINE_WINDOW_SECONDS`, one hour by default |
| `bajutsu/serve/jobs.py` (`_run_job`) | A `run` job spawned on the piped path gets the same parent pipe that `_run_inherited` already gives a one-shot caller |

### What the operator declares

The `worker.yaml` file ([BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md)) describes the machine. The broker belongs to the machine. An `appium` environment already names a grid there. A `leased` environment names a broker the same way.

```yaml
environment: leased
platforms: [ios, android]
lease:
  command: ["broker-cli", "lease", "--platform", "{platform}", "--device", "{device}"]
  acquireTimeout: 60s
  releaseTimeout: 10s
```

The `command` key is an argument list, not a shell string, so no value reaches a shell. The worker replaces `{platform}` with the platform of the device it borrows (`ios` or `android`).

It replaces `{device}` with the device model the job's `runsOn` names. The worker refuses a model that begins with `-` before it builds the argv. The strings `runsOn.ios.model` and `runsOn.android.avd` are free-form and travel with the job. A flag-shaped value would reach the operator's command as an option, not a device name. When the job names no device, the worker drops the `{device}` element and the element before it.

The `acquireTimeout` key bounds how long the worker waits for an answer. The `releaseTimeout` key bounds the wait for the command to exit after the termination signal. The `platforms` key lists the platforms the broker serves. The schema accepts `ios` and `android` there.

The schema refuses an empty `command` and an unknown placeholder when `worker.yaml` loads. It also refuses `{device}` inside a longer element. It refuses `{device}` as the first element or after another placeholder. `{device}` must be a whole element that follows its flag.

It also refuses a `platforms` key under any other environment. A typo then stops the worker at startup and not on the first job.

### The contract with the broker

The repository names no broker. The contract is the behavior of the lease command. Any broker that behaves this way works.

1. The command **stays running** while the worker holds the device. Ending the process ends the lease. Bajutsu releases the device by sending the process a termination signal. It waits up to `releaseTimeout` for the process to exit. It then force-ends a command that is still alive. One wedged command cannot hold the job's `finally` open.
2. Within `acquireTimeout`, the command prints **one line** to standard output. The line holds one data object. The worker reads that line and discards any later standard output. A broker that logs past its answer then never blocks on a full pipe:

   ```json
   {"device": {"address": "emulator-5588"}, "environment": {"ANDROID_ADB_SERVER_PORT": "5038"}}
   ```

   The value `device.address` becomes the udid spec. Every `environment` entry becomes an entry of `DeviceLease.env`.
3. A command that exits before it prints that line has **declined**. A nonzero exit status with a message on standard error is a decline with a reason. The worker logs the reason.

   A command that prints no line within `acquireTimeout` also counts as a decline. The worker ends that command first.
4. The command ends the lease when its standard input reaches end of file.

   The worker keeps that input open. The operating system closes it when the worker exits for any reason.

The provider never inspects the device. It reads two fields. A missing `device.address` raises a `ValueError` that names the field. An address the run cannot drive must stop at resolution and not at the first tap.

Point 4 ends a lease when its worker stops. It does not stop a run that still drives the device. The section on the run's lifetime closes that gap.

### Devices for one job

One job can need more than one device. The `bajutsu run` command calls `acquire_device` once for each target.

Targets of one platform share one device pool. A scenario holds one device for each such target.

The worker borrows for each platform pool that the job's targets use. A web target drives a browser context and needs no device, so the worker never borrows for it.

The demand has two parts. The **mandatory demand** is the largest per-scenario target count of each pool. One scenario cannot run with fewer devices.

The **extra lanes** come from `--workers`, which asks for parallel lanes. Each lane needs its own devices. The run treats `--workers` as a throughput knob. `_resolve_multi_target_workers` caps it to the lanes the devices allow. It never refuses a run that has enough devices to finish.

A device group counts once. The worker counts the mandatory demand through `_pool_demand`, the function the run uses. It moves to a module that both import. The function takes the actuator of each target, not a `_TargetSetup`. The worker can then call it before any device exists. The run computes the same demand before it starts, so it never finds its pool short.

Each borrow asks the broker for one device. The worker borrows the mandatory demand first, then the extra lanes. The placeholder `{platform}` is the pool's platform. The placeholder `{device}` is the model that the pool's `runsOn` names. The iOS value is `runsOn.ios.model`. The Android value is `runsOn.android.avd`.

A list of models multiplies runs ([BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md)). Each run takes devices of its own model from the pool. The worker borrows the pool's count once for each listed model, with each model in turn. A `family` list picks a family at run time and names no model. The worker drops the `{device}` pair for it.

The worker passes the borrowed addresses as one `--udid` list. The run already reads `--udid` as a comma list. Each entry carries the platform of its pool as a prefix, `ios=` or `android=`:

```
--udid ios=AAAA-1111,ios=BBBB-2222,android=emulator-5588
```

Every address a broker returns passes through this prefix. An address with a colon, such as `192.0.2.7:5555`, stays whole. The prefix ends at the first `=`.

The `leased` provider's already-leased path reads the target's platform. It keeps the entries tagged with that platform and the entries with no tag. It strips the prefix and joins the rest with commas. A target then sees the addresses of its own platform and no others. Two targets of one platform see the same list. `_reject_incompatible_actuator_sharing` then finds one device spec and passes.

An entry with an unknown prefix is a `ValueError`. A target whose platform has no entry is a `ValueError` too. Each message names the platform. An entry with no prefix serves every platform, which keeps a hand-written `--udid` working.

`_reject_unshareable_groups` runs before the worker's devices are in use. It passes the raw `--udid` to `capabilities_for_run`. It reads the entries of the target's platform through the same helper.

One limit stays. The `leased` provider alone reads the prefix. A target with another provider sees the raw list. On a `leased` worker that is a web target alone. A job that needs any other platform absent from `platforms` never reaches the worker. A web target's lanes come from `--workers` and ignore the list (`_resolve_lanes`).

The `environment` maps of all the leases merge into one map for the child. Two leases that return different values for one key cannot share a process environment. One example is a different adb server port for each emulator. The worker then releases every lease it holds. It fails the job at once with an infrastructure reason that names the key. It does not decline, because a retry gets the same conflict.

Borrowing the mandatory demand is all-or-nothing. When any of those borrows declines, the worker releases the devices it already holds. It then declines the job. A job holds its whole mandatory demand, or nothing.

The worker takes the extra lanes opportunistically. It borrows one more lane at a time, up to the `--workers` multiplier. Each lane is all-or-nothing across the pools. The first lane the broker will not lend ends the extras, and the worker keeps what it holds. A shortfall never declines the job.

The worker passes `--workers` through unchanged. The run caps its lanes to the devices it receives, as it does for a local worker with fewer devices.

The borrow runs on the thread that `_run_with_heartbeat` heart-beats. It starts once `claim_check` passes and the bundle fetch finishes. An earlier borrow would hold devices during the download.

A borrow before the Heartbeat starts would let the server reclaim the job mid-borrow. Two workers would then run it at once, a fault the bundle fetch already hit.

The worker checks `lost` between borrows. When the flag is up, it releases what it holds and returns before the run starts, as the fetch does.

The worker also bounds the total borrow time. A job of three devices could otherwise wait three times `acquireTimeout`. That passes the job-lease timeout (`DEFAULT_LEASE_TIMEOUT_SECONDS`, 120 seconds by default).

The worker keeps a borrow budget of 90 seconds for the whole job. Each borrow waits the lesser of `acquireTimeout` and the budget left. A spent budget declines the job when the mandatory demand is unmet. Otherwise it ends the extras.

### How the run uses the device

A worker runs each job in a child process, and the child inherits the worker's environment. The worker passes the merged `environment` map to the child, together with `--udid <addresses>`. It also forwards its own `--worker-config`, as `_with_worker_config` does today.

A job's spec can already name a device. A run request's `udid` puts `--udid` into the argv. It puts the same devices into the spec's `udids`. `_boot_devices` boots those before the child starts. On a `leased` worker those devices are not borrowed. The worker drops a spec-supplied `--udid` from the argv, as `_with_worker_config` drops `--worker-config`. It boots no `udids`. The borrowed addresses reach the child, and nothing else does.

The `leased` provider treats a non-empty `requested_udid` other than `booted` as already leased. It returns the entries of the target's platform as a plain lease with a release that does nothing.

The child resolves the `leased` provider and finds the addresses. It borrows nothing. The worker keeps every device lease.

A direct `bajutsu run` without `--udid` borrows for itself. It asks the broker for one device per target. It serves a scenario with at most one target per platform. Two targets on one platform resolve different devices. `_reject_incompatible_actuator_sharing` then stops the run with exit 2.

A direct run with parallel lanes borrows by hand and passes the addresses with `--udid`. A run with two targets on one platform does the same.

The broker's adb server port reaches `adb` through the environment. That is the channel `adb` already reads. The adb layer needs no new setting.

### How long the run lives

The worker already sends heartbeats for the job lease while the child runs. The device leases live as long as the job lease. When the run's child process exits abnormally, control returns to the worker. The worker then releases the devices in the same `finally` that uploads the run's evidence.

When the worker stops, point 4 of the contract ends every lease. That alone is not enough. The worker spawns the child apart from itself (`start_new_session=True` in `_run_job`). It survives a `SIGKILL` of the worker. The broker would then lend a device that the orphan still drives.

The child watches its parent. A one-shot caller already gets this. `_run_inherited` holds the write end of a pipe while the child runs. The child reads the other end, named by `PARENT_FD_ENV`, in `_watch_parent` (`bajutsu/run/internal.py`). End of file means the caller has exited. The child then sends itself `SIGTERM`.

A worker takes the piped path in `_run_job`, which creates no pipe today. This item gives a `run` job on that path the same pipe. The worker holds the write end and closes it when the child exits. The child needs no change.

The stop is the cancel that `SIGTERM` already means ([BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)). The run unwinds at a safe boundary and runs its normal teardown. It writes its manifest and evidence as it does for a cancel. The scenario it reached ends with `failure="cancelled"`. The stop gives no step a pass or a fail. The stopped worker uploads nothing. The server reclaims the job when its job lease expires and queues it again.

One limit stays. The teardown runs up to its own deadline. The broker ends the lease when the worker stops. For that long, the run can still drive a device that the broker may lend. The window has a bound. It does not go away.

### When the broker has no device

The worker claims a job from the server before it knows whether the broker has the devices. The worker must claim first, because the server decides which worker takes which job. A worker that holds a claim and lacks a device must give the claim back.

Today the server reclaims a job when a worker's heartbeats stop. That happens after the job-lease timeout, which is 120 seconds by default. Waiting that long wastes the time of every job behind it. It also consumes one of the job's three lease attempts, so a third refusal fails the job.

This item adds `POST /api/worker/decline`. The request names the job, the job lease the worker holds, and a reason. The server moves the job back to the queue as if nobody had claimed it. The server checks the lease owner, as it does for `/api/worker/result`. One worker cannot decline another's job.

A declined job does not consume a retry or a lease attempt, because the scenario never ran. The server records the number of declines, the time of the first, and the latest reason. It stops returning the job to the queue once the decline window has passed.

The window is a server setting. Its name, `BAJUTSU_DECLINE_WINDOW_SECONDS`, follows the style of the lease timeout setting. The default is one hour.

A busy broker never turns a job red inside the window. Past the window, the job fails with an infrastructure reason that names the broker's last message. The bound turns "no device ever frees up" into a visible error. It also keeps a misconfigured broker from holding a job in the queue forever.

A declined job keeps its old `created_at`. The lease scan, `_lease_candidates`, orders jobs oldest first. Left alone, every `leased` worker would claim the declined job at each poll. Every younger job would wait behind it.

The decline route sets a `not_before` time on the job. It also records the environment of the declining worker, which the request names.

The lease scan skips a queued job until its `not_before` has passed. It skips the job for a worker of the recorded environment alone. The scan reads that environment from the lease request, as it reads the advertised tokens today. A free `local` worker still takes the job at once.

The delay grows with each decline. It starts at 2 seconds, the default poll interval, and doubles each time. It stops growing at 60 seconds.

Younger jobs run while the declined job waits. A worker with no device does not claim and decline in a tight loop. A job declined for the whole window costs about 65 claims. A flat 2-second retry would cost about 1,800. Each claim runs the lease command once.

### Advertising

A `leased` worker routes like a local worker. It advertises the platform tokens named by `platforms` in `worker.yaml`. A plain job with no `environment` reaches it.

Its `maxJobConcurrency` stays at 1, as for a local worker. It takes `--machine-id` as a local worker does. The device provider is the one difference.

An `appium` worker is different. It advertises no platform token. It takes the jobs that name `environment: appium`, because the grid owns the device and its machine.

A `leased` worker runs the driver on its own machine against ordinary simulators and emulators. A job cannot tell those devices from local ones. The job needs no `environment` to find the worker.

The worker takes its platforms from the file and not from `--platform`. A `leased` worker refuses `--platform`, as an `appium` worker does. The worker cannot ask the broker what it holds before a job arrives. A job that needs a platform the file did not list never reaches the worker.

### Boundaries

- **The verdict.** The device is an input to a run and never a judgment of it. A decline, a broker error, and a wait for a device produce no pass or fail for any step. A run stopped because its worker stopped ends as a cancel. The stop gives no step a verdict. Prime directive 1 holds.
- **Determinism.** Waiting for a device is a queue wait, not a pause inside a scenario. The worker polls the server on its existing interval. No scenario step sleeps for a device. Prime directive 2 holds.
- **App-agnostic.** The broker appears in `worker.yaml`, which describes the machine. Scenarios, targets, drivers, and the runner stay unchanged. Prime directive 3 holds.
- **No broker in the repository.** The repository ships no broker. No file under `bajutsu/` or `docs/` names one. The contract above is the whole interface.

### Work breakdown (MECE)

1. **Lease environment.** Add the `leased` environment, the `lease` block, and the `platforms` list.

   Add the lease command, `acquireTimeout`, and `releaseTimeout` to the `DeviceProvider` schema. Update `device_provider_for`. Make `is_local` true for `leased`.

   Make the capability tokens, route tokens, and `_device_reader` follow the routing above. Apply the `--machine-id` check and the `--platform` refusal.

   Make `_register_environment_or_exit` and `environment_refusal` accept `leased` as they accept `local`.
2. **Provider and lease environment.** Add the `env` field to `DeviceLease`. Add the `_LeasedProvider` and its already-leased path.

   Add the helper that splits a platform-tagged `--udid` list. Call it from the provider and from `_reject_unshareable_groups`.

   Make `_acquire_targets`, `_device_udid_or_exit`, and `_probe_device` apply the lease's `env`. Make the last two release the lease when the command ends or the tool call returns. Test against a stub lease command.
3. **Decline route.** Add the route and five stored values per job.

   They are the decline count, the first decline time, and the last reason. The other two are `not_before` and the declining worker's environment.

   Add the window setting.

   Make the lease request name the worker's environment. Make `_lease_candidates` skip a job before its `not_before` for a worker of the recorded environment. Add the infrastructure error past the window.
4. **Worker integration.** Count the devices through the same function the run uses.

   Extract `_pool_demand` to a module both import, not a second copy.

   Borrow the mandatory demand all-or-nothing for each platform pool. Then borrow the extra lanes of `--workers` opportunistically, and pass `--workers` through.

   Run the borrow on the heart-beaten thread. Check `lost` between borrows. Keep the total under the borrow budget.

   Merge the environments, and join the tagged addresses into `--udid`. Release them in the `finally`. Drop a spec-supplied `--udid` and its `udids`.

   Decline when a mandatory borrow declines. Fail the job at once when two environments conflict.
5. **Orphaned run stop.** Give a `run` job on the piped path of `_run_job` the parent pipe of `_run_inherited`.

   Test that the child cancels itself when the write end closes. Test that the run still writes its manifest.
6. **Documentation and ja mirrors.** Update the worker setup and broker contract pages in `docs/`. Update `docs/architecture.md` and the mirrors under `docs/ja/`.

## Alternatives considered

| Alternative | Why it was not adopted |
|---|---|
| Borrow once when the worker starts and keep the device | The device sits idle between jobs and blocks other borrowers. That defeats a shared pool. It also ties a device to a worker that may wait hours for a job. A mode that keeps the device for good can come later. |
| Mark the job red when the broker has no device | A busy machine would turn queue pressure into red jobs. They look like scenario errors. A person would have to tell the two apart. The app is not at fault. |
| Stop declining after a fixed number of declines | A count has no link to time. A broker that is busy for an afternoon would turn jobs red, while the same count at a slow poll would wait for days. A wall-time window states the real tolerance. |
| Let the job lease lapse instead of adding a decline route | It needs no new route. The job waits out the lease timeout (120 seconds by default) on every refusal. Each lapse also consumes one of the job's three lease attempts. |
| Keep the declined job's place at the head with no delay | Every `leased` worker claims it again at each poll. Younger jobs wait behind a job that no worker can serve yet. |
| Delay the job only for the worker that declined it | Every other `leased` worker still claims it at each poll. The job stays at the head of the queue for them. A per-job `not_before` needs no per-worker state. |
| Apply `not_before` to the whole fleet, whatever the worker | A plain job reaches `local` and `leased` workers alike. One busy broker would then stall a job that a free local worker could start, for up to 60 seconds per decline. The scan stays unbounded so that a fixed window never hides a servable younger job (BE-0166). Recording the declining environment hides the job only from workers that would decline. |
| Decline a job that needs two platforms | A local worker with both runs the job, so the refusal is an artifact of the broker. Tagged entries in `--udid` remove it. |
| Decline when two leases return different values for one environment key | A retry gets the same conflict, so the job would wait out the whole window for nothing. A job that fails at once with the key named is the faster answer. |
| Rely on contract point 4 alone when a worker stops | The worker spawns the run's child apart from itself, so the child survives the worker. The broker would lend a device that the orphan still drives. The parent pipe stops the child. |
| Stop the orphan with a separate watcher process beside the worker | It adds a process that can stop on its own. The pipe closes when the worker stops, for any reason, with no extra process. |
| Talk to the broker over a web interface | It needs a client, credentials, and a schema per broker. It also names a product in the code. A command contract works with any broker that can print one line and stay alive. It keeps credentials in the broker's own command. A second provider kind can add a broker interface later. |
| Borrow inside the child run, in the run's own provider call | The worker would still hold a claim for a job it cannot start. It would have no way to return the claim. The worker needs the decision, so the borrowing happens where the worker can act on a decline. |
| Borrow each mandatory device as its own decline-able step | A job could then hold some of its devices while it waits for the rest. Two such jobs could each hold half of what both need. All-or-nothing borrowing of the mandatory demand rules out that deadlock. The extra lanes are not part of it, because a job runs without them. |
| Fold the `--workers` multiplier into the all-or-nothing demand | It turns a throughput knob into a hard requirement. A `--workers 8` job on a busy broker would decline for the whole window and then fail, where a worker holding two devices would run it on two lanes. The run already caps `--workers` to its devices and never refuses (`_resolve_multi_target_workers`). |
| Extend the `local` provider with an optional lease command | One kind would carry two behaviors. Then `local` would no longer mean a device attached to this machine. A separate kind keeps the registry's rule of one kind and one behavior. |

## Progress

> Keep this section current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one entry per unit of work), and the log records what changed and when, oldest
> first, each with a link to its PR.

- [x] Lease environment in `worker.yaml`
- [x] Provider and lease environment
- [x] Decline route
- [x] Worker integration
- [x] Orphaned run stop
- [x] Documentation and ja mirrors

Log:

- [#2565](https://github.com/bajutsu-e2e/bajutsu/pull/2565): all six units shipped. The table lists where the implementation differs from the
  design.

| Point | What shipped, and why |
|---|---|
| Device count | The worker runs `python -m bajutsu.run.demand` in the job's directory, so relative paths resolve as they do for the run |
| Mixed models | A family or OS condition beside a named model adds one device of the broker's choosing |
| Fake driver | A `leased` run under it borrows nothing |
| `record`, `crawl`, `--once` | Each borrows for itself and is not declined |
| Early lease exit | The job log reports it, and the run goes on |

## References

- `bajutsu/common/runner/device_provider/`: the provider seam and its three built-in kinds.
- `bajutsu/common/capability/worker_capability.py`: `device_provider_for` and the `worker.yaml` schema.
- `bajutsu/common/config/schema/device_provider.py`: the `DeviceProvider` schema the lease command joins.
- `bajutsu/serve/cli/worker.py`: the job lease, its Heartbeat, and the capability tokens.
- `bajutsu/serve/server/one_shot.py`: `environment_refusal`.
- `bajutsu/serve/server/db/sql_repository.py`: `_lease_candidates`, the lease scan.
- `bajutsu/serve/jobs.py`: `_run_job` and `_run_inherited`, which spawn the child and hold its parent pipe.
- `bajutsu/run/internal.py`: `_watch_parent`, the child's end of that pipe.
- `bajutsu/serve/server/worker_job.py`: the function that forwards the worker's file to the child.
- `bajutsu/run/cli.py`: `_acquire_targets`, the lane resolution, and the pool demand.
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md): the device-provider abstraction.
- [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md): the graceful cancel that `SIGTERM` starts.
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) and [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the `worker.yaml` file and the environments it declares.
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md): how a `runsOn` list multiplies runs or picks a family.
- [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md): the job lease a worker holds for a group of jobs.

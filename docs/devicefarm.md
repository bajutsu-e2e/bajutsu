# Running on AWS Device Farm

[AWS Device Farm](https://docs.aws.amazon.com/devicefarm/latest/developerguide/welcome.html) is a
device cloud where you run tests against real and virtual devices Amazon hosts. Bajutsu runs there
through Device Farm's **custom test environment**, driving its Android (adb) backend against the
reserved device — with no change to the deterministic core.

## The batch model, and why this is a submitter, not a driver

Device Farm is a **batch** service, not a live-device provider. It does not lend you a device to
drive over the network; it runs *your commands on its host*, and that host already has `adb`
connected to the reserved device. So the deliverable is not a runtime driver (there is no device to
acquire) — it is a **CI-side submitter**: package Bajutsu plus your scenarios, upload them with a
test-spec that runs `bajutsu run --backend adb`, let Device Farm execute it on the host, and collect
the artifacts.

The submitter core lives entirely outside the deterministic core, at
[`bajutsu/common/cloud/devicefarm.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/cloud/devicefarm.py)
(`render_test_spec` / `build_package` / `submit_and_collect` and the `DeviceFarmClient` / `Transfer`
seams, BE-0336), reusable by any caller; the CLI at
[`scripts/devicefarm_submit.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/devicefarm_submit.py)
is now a thin wrapper holding only argument parsing and the real boto3 / urllib adapters.
Nothing in the upload / poll / download machinery touches the `run`/CI verdict path: Bajutsu runs
inside Device Farm exactly as it does locally — the same deterministic core, the same pass/fail from
machine-checkable assertions — so the verdict the submitter reports comes from **Bajutsu's own
`manifest.json`**, never from Device Farm's own PASSED/FAILED classification.

## Batch-topology caveats

These are properties of Device Farm itself; the submitter documents and handles them so a future
Device Farm change does not silently break the flow.

- **Raw adb is a by-product, not a guarantee.** Device Farm's first-class custom-environment path is
  Appium. `adb` against the reserved device is available because it is part of the host toolchain,
  not a contract Amazon promises — Bajutsu's Android backend needs only "a host with `adb` connected
  to a device", which the custom environment provides, but treat it as a by-product that a future
  platform change could remove.
- **150-minute hard cap.** A single custom-environment execution is capped at 150 minutes. The
  submitter polls no longer than that and then fails loudly rather than blocking a CI job forever.
- **APK only.** Device Farm's Android app upload accepts an `.apk`; it does not accept an `.aab`.
  Build a debug APK for the run.
- **Per-Appium-command timeout.** Device Farm's per-command Appium timeout does not apply to the
  raw-adb path Bajutsu uses; the 150-minute execution cap is the effective bound.

iOS on Device Farm runs on real devices, which adds the two constraints in
[iOS: re-signing and real-device capabilities](#ios-re-signing-and-real-device-capabilities) below.

## iOS: re-signing and real-device capabilities

This shift to a real device changes two things Bajutsu accounts for up front (BE-0238). Both are
properties of a physical device rather than of Device Farm's classification, so they hold for any
real iOS device the XCUITest backend drives (`runsOn.kind: device`) — Device Farm or a
locally attached device.

- **Re-signing strips entitlements.** Device Farm re-signs the uploaded `.ipa` with its own
  provisioning profile so it installs on the reserved device, and the re-sign drops the entitlements
  the new profile does not carry — commonly Push (`aps-environment`) and App Groups
  (`com.apple.security.application-groups`). An app feature that depends on a dropped entitlement
  (remote-push registration, an App-Group shared container) does not work under the re-signed build,
  so a scenario that asserts on such a feature should expect the re-signed behavior, not the App
  Store one.
- **simctl device control and permissions do not apply.** Bajutsu's iOS device control
  (`setLocation`, the clipboard steps, `push`, `clearKeychain`, `background` / `foreground`, and the
  status-bar overrides) and its permission grants are all backed by `simctl`, which reaches only the
  Simulator — never a physical device. On a real device the XCUITest backend therefore advertises
  neither, and a scenario that uses one is **skipped by the preflight** (BE-0082) before any device
  work, with a clear reason, rather than failing late with a `simctl` error mid-run. The on-device
  capabilities the XCTest runner drives itself — query, elements, screenshots, taps, and two-finger
  gestures — are unaffected.
- **Two routes join the host and the app.** A physical device has its own loopback. Bajutsu bridges the runner and the in-app responders over usbmuxd. The app
  reports network exchanges to a host address that each run offers it, or to the one that
  `BAJUTSU_HOST_ADDRESS` names. Run `bajutsu doctor --udid "$DEVICEFARM_DEVICE_UDID" --environment-only` on the Device Farm host
  before `bajutsu run`. It fails when usbmuxd does not list the reserved device. It also lists the
  host addresses that the app would receive. A fresh install also lacks the iOS Local Network permission that the second route needs. Nobody
  has verified either route on Device Farm yet
  ([iOS on a real device](ios-device-cloud.md#reaching-a-real-devices-channels)).

## The test spec

Device Farm drives the run from a
[custom-environment test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
— a YAML file with `install` / `pre_test` / `test` / `post_test` phases of shell commands. The
submitter renders one that:

1. **install** — bootstraps Python 3.13 with uv and installs Bajutsu from the uploaded test package
   into a venv on it. Device Farm's host tops out at Python 3.12 (`devicefarm-cli use python` only
   offers the runtimes Amazon preinstalls) while Bajutsu requires 3.13, so the phase installs uv with
   the host's base pip, has uv fetch a standalone 3.13, and installs into that venv. This is a
   temporary workaround, removed once Device Farm ships 3.13 (see `_python_bootstrap_commands` in the
   submitter). The adb backend is pure subprocess, so the base install (no extras) is enough.
2. **pre_test** — runs `adb devices` to prove the reserved device is visible (the serial-resolution
   check). A caller may append its own device-side setup here through `render_test_spec`'s
   `pre_test_commands` parameter — commands spliced in verbatim, in order, after the probe, for
   whatever one deployment's backend needs before the run (a network relay, a VPN client). Paired
   with `build_package`'s `extra_texts` (which ships the setup script into the package), it keeps
   that per-deployment setup entirely outside `bajutsu/`. It is a Python-API-only hook: no `serve`
   endpoint, config field, or batch-request field wires to it, so no client-supplied value reaches a
   shell on the host holding the run's AWS role.
3. **test** — one `bajutsu run --backend adb --udid booted` per scenario, so a scenario that fails
   still leaves a manifest for the others.
4. **post_test** — copies the whole `runs/` tree into `$DEVICEFARM_LOG_DIR` so the artifacts come
   back.

A checked-in reference spec for two showcase scenarios is at
[`demos/showcase/devicefarm/testspec.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/demos/showcase/devicefarm/testspec.yml).

## Using the submitter

Install the optional extra:

```bash
uv sync --extra aws        # pulls in boto3
```

Build the package and spec locally without any AWS credentials (useful to inspect what will be
uploaded):

```bash
uv run python scripts/devicefarm_submit.py \
  --scenario scenarios/firstlook.yaml --scenario scenarios/controls.yaml \
  --target showcase-compose --config showcase.devicefarm.config.yaml \
  --app-apk app-debug.apk \
  --package .=. \
  --package demos/showcase/devicefarm/showcase.devicefarm.config.yaml=showcase.devicefarm.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package-only
```

The run uses `showcase.devicefarm.config.yaml`, a Device-Farm variant of the `showcase-compose`
target that carries **no `app.path`**: Device Farm installs the uploaded APK on the reserved device
itself, so the adb backend launches the pre-installed app rather than `adb install`ing a local file
(a None `app_path` is the runner's "run against the already-installed app" path). This is a
per-environment difference, so it lives in config, not in the tool.

Each `--package SRC=ARCNAME` adds a file or directory to the test package under `ARCNAME` (the
arcname `.` packs a directory at the package root); the scenario / config paths you pass are the
paths *inside* the package. Packaging Bajutsu with `--package .=.` puts its `pyproject.toml` and
`tests/` directory at the root, and the submitter synthesizes an empty root `requirements.txt`, so
the upload satisfies Device Farm's APPIUM_PYTHON_TEST_PACKAGE validation while the test spec still
does the real install. Drop `--package-only` and add
`--project-arn` / `--device-pool-arn` (with AWS credentials configured in the environment) to submit
the run, poll it to completion, download the artifacts, and print Bajutsu's verdict. The process
exit code is `0` only when every scenario passed.

For the iOS showcase, pass `--platform ios` to select the XCUITest backend and the iOS app upload
type. Build the device-signed `.ipa` and runner first (BE-0288). The runner build reads your
[signing file](ios-device-cloud.md#the-signing-file) (BE-0456):

```bash
make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<your-10-char-team-id>
make -C demos/showcase runner-build-device
```

Then run the submitter (dry-run with `--package-only`; drop it and add `--project-arn` /
`--device-pool-arn` to submit):

```bash
uv run python scripts/devicefarm_submit.py \
  --platform ios \
  --scenario scenarios/firstlook.yaml \
  --target showcase-swiftui \
  --config showcase.devicefarm.ios.config.yaml \
  --app demos/showcase/ios/swiftui/build/export-device/BajutsuShowcaseSwiftUI.ipa \
  --package .=. \
  --package demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml=showcase.devicefarm.ios.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package BajutsuKit/Runner/build/dd-device/Build/Products=. \
  --package-only
```

`showcase.devicefarm.ios.config.yaml` sets `runsOn.kind: device` (the XCUITest backend
drives a physical device instead of a Simulator) and carries no `app.path` (Device Farm installs the
uploaded `.ipa` on the reserved device itself). The `--package
BajutsuKit/Runner/build/dd-device/Build/Products=.` line places the device-signed `.xctestrun` and
its test bundles at the package root, where the config's `driver.runner.testRunner: BajutsuRunner.xctestrun`
resolves them. The re-signing and simctl caveats described [above](#ios-re-signing-and-real-device-capabilities)
apply to this run.

## The GitHub Actions workflow

[`.github/workflows/devicefarm.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/.github/workflows/devicefarm.yml)
wraps the submitter as a **manual, opt-in** workflow. It is `workflow_dispatch` only — never on push
or pull request, so it is not on the merge path and is not a required check. It mints a short-lived
AWS credential by exchanging the workflow's GitHub OIDC token for the `AWS_DEVICEFARM_ROLE_ARN` role
(no static key), scoped to a `devicefarm` Environment, and reads the project and device-pool ARNs
from the `DEVICEFARM_PROJECT_ARN` / `DEVICEFARM_DEVICE_POOL_ARN` repository variables. With any of
the three unset, the job is a green no-op (a `::notice::`, never red), so it stays dormant until an
operator wires up an account.

## Dispatching from serve

The submitter and the workflow above are the headless path: one run packages the whole suite and
executes its scenarios back to back on a single reserved device. `bajutsu serve` (BE-0336) adds a
second path on the surface operators already use to dispatch runs and watch history — and it changes
the shape. Instead of one serialized run, serve **fans the suite out one Device Farm run per
scenario**, so scenarios run in parallel. The Device Farm workers' job budget, not a single device,
bounds how many run at once.

The target names no batch provider. Where a run happens is a choice per run, not a property of the
target. The fan-out request names the provider instead, as its `environment`
([BE-0452](../roadmaps/BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)). The
target needs nothing beyond an `app.path`, the binary Device Farm installs on the reserved device:

```yaml
targets:
  showcase-compose:
    platform: android
    app:
      id: com.example.showcase
      path: app-debug.apk         # the APK Device Farm installs on the reserved device
    paths:
      scenarios: demos/showcase/scenarios
```

The server does not submit these runs itself (BE-0448). Each fan-out job waits in the queue for a
Device Farm worker. Such a worker's capability file, `worker.yaml`, sets `environment: devicefarm`.
That worker packages the run and submits it. It then polls the run and posts the verdict back. The
AWS role thus lives in the worker's environment, never in the server's. The server and the worker
may share one machine as two processes. Start the worker with the file and no `--platform`:

```yaml
# devicefarm.worker.yaml
version: 1
environment: devicefarm
maxJobConcurrency: 4    # hold at most four Device Farm runs at once
drivers: [adb, xcuitest]
```

```bash
DEVICEFARM_PROJECT_ARN=arn:aws:devicefarm:us-west-2:123456789012:project:abc \
  bajutsu worker --worker-config devicefarm.worker.yaml --server-url http://localhost:8765
```

The worker reaches AWS through its own environment, not the config file. No ARN or credential lands
in a checked-in target. **`DEVICEFARM_PROJECT_ARN`** names the project. boto3 finds the AWS
credentials itself: an instance role, the `AWS_*` variables, or a named profile.
**`DEVICEFARM_REGION`** overrides the default `us-west-2` for an account pinned elsewhere. At
startup, the worker exits with the reason unless all three conditions below hold:

- The environment names `DEVICEFARM_PROJECT_ARN`, and the provider and its hooks load.
- The worker runs from a Bajutsu source checkout. Each run's package carries the checkout's
  `pyproject.toml`, `tests/`, and `bajutsu/`, which an installed Bajutsu lacks.
- The command line names no `--platform`. The phone belongs to the Device Farm host, so the worker
  drives no device of its own.

Once registered, the worker prints `provider registered: devicefarm`. It advertises the routing
token `environment:devicefarm`, beside the `host:<os>` token every worker advertises for its machine.
A fan-out job requires `environment:devicefarm` and no other token. A worker
advertising the token leases no other job. Local jobs and Device Farm jobs never cross. Serving
both on one machine takes two workers. This registration alone may advertise the `environment:`
prefix. `--capabilities` and `$BAJUTSU_WORKER_CAPABILITIES` refuse it at startup. A fan-out job
that no live worker can serve stays queued as unroutable, like any other job. A server with no
queue refuses `POST /api/run-set`. Unlike the headless submitter, the worker reserves one device per run through a
device-selection filter. It needs no device-pool ARN.

> **Config and scenario placement.** The worker composes a separate package for each job. The
> package holds the source checkout's three parts, plus the job's bundle directory under `bundle/`.
> The bundle directory is the config's own directory, an uploaded bundle, or the config and scenario
> the scenario store ships with the job. The request names the config and the scenario relative to
> that directory. A scenario outside the config's directory makes `POST /api/run-set` return a 400
> error. The target's `app.path` travels as written, and the worker resolves a relative one against
> the same directory. A missing binary fails the job before the worker uploads anything.
>
> **Application delivery.** A job whose config and scenario travel with it still needs the binary.
> When no uploaded bundle carries the binary, the server stores the file that `app.path` names in its
> object store during the fan-out. The lease then carries a signed download link for that file. The
> worker downloads the binary to the same `app.path` inside the job's directory. It then checks the
> file's digest against the one the job names. Such an `app.path` must name a file inside the config's directory, and the server
> needs an object store. Otherwise `POST /api/run-set` returns a 400 error.

### The job budget

The worker owns the budget. The top-level `maxJobConcurrency` of its `worker.yaml` caps the jobs it
holds in flight. A job counts from its lease until the worker posts its result. While every slot
holds a job, the worker sends no lease request. Each running job keeps renewing its lease, which
keeps the worker registered as live. The queued Device Farm jobs thus never show as unroutable
during a backlog. A job that has posted its result and still uploads its evidence no longer counts.
The worker may then lease again.

Each job in flight holds one Device Farm device. With one worker, `maxJobConcurrency` is the exact
device cap. With two or more workers, the devices in use are the sum of their jobs in flight. The
operator keeps the sum of their `maxJobConcurrency` values within the account's quota. An omitted
`maxJobConcurrency` means one job at a time, a safe default for a Device Farm quota. `N` single-job
workers remain a valid alternative to one worker with `maxJobConcurrency: N`. They cost `N` copies
of the AWS environment, the source checkout, and the process.

A worker whose `maxJobConcurrency` is above one runs each job in a **slot**. A slot is a thread that
owns one leased job and its lease renewals. A slot also owns a workspace under `.slots/<n>/`, the
package it uploads, and the job's log. Slots share nothing that changes. A workspace returns to the
pool once its job has uploaded its evidence. The next job thus never reuses a tree that an earlier
job still uploads from. Size the disk for one application binary per slot, plus the run trees still
uploading.

Before it uploads anything, the worker checks each leased scenario against its `worker.yaml`. The
check is the one `bajutsu run --worker-config` makes
([Drivers](drivers.md#worker-capability-check-be-0450)). The worker judges every target on the
driver that Device Farm runs. That driver is `adb` for an Android request and `xcuitest`
for an iOS one. A scenario fails when `drivers` lacks that driver. It also fails when it declares
more targets than `maxTargetsPerJob` allows. A scenario file the worker cannot read fails too. The
job then fails with a `worker-capability-unsupported:` reason. The worker posts that reason through
the ordinary result route. Device Farm receives nothing. A job that resumes a stored run skips the
check, since an earlier attempt already submitted that run.

The server holds no budget of its own. It used to read a target's `cloudBatchBudget` and a request's
`deviceBudget`; BE-0452 removed both. A request that still names
`deviceBudget` gets a 400 error that points at `maxJobConcurrency`. The server's global, per-user,
and per-org caps count queued jobs too. Those caps may still cut a large fan-out short.

### Fanning a target out

A `POST /api/run-set` request fans a target out. Its body names the `target` and the batch provider
as `environment` (today `devicefarm`). An optional `scenarios` subset narrows the run:

```json
{ "target": "showcase-compose", "environment": "devicefarm", "scenarios": ["firstlook.yaml", "controls.yaml"] }
```

Omitting `scenarios` runs every scenario in the target's directory. Every requested name resolves to
its scenario **before** any dispatch, so one unknown name fails the whole request closed rather than
fanning out the good ones and erroring on the bad one. serve queues one job per scenario. The Device
Farm workers lease the jobs as their budgets allow. serve refuses the request outright when it
cannot honor it at all:

- a missing `environment`, or one that names no batch provider. `appium` is one of these: the plain
  run request reaches a grid ([self-hosting](self-hosting.md));
- a web target, which has no device to reserve;
- a target with no `app.path`;
- a config or scenario outside the config's directory, which the worker packages.

### Restart and lease loss

A run is **durable across a restart** of the server or the worker. Device Farm names each scheduled
run by an Amazon Resource Name (ARN). The worker sends that ARN to the server over HTTP as soon as
Device Farm accepts the run. The server stores the ARN for the worker that holds the job's lease. It
refuses every other worker. A repeat of the ARN it already holds succeeds from any worker. The
worker retries a transport error or a `5xx` for up to six minutes. A worker that leases the job later receives the stored ARN with its lease. That worker resumes
polling the same run and uploads and schedules nothing again. The job thus holds one device for the
whole 150-minute poll. No worker needs a database connection for it.

Each run carries its job id as its name on Device Farm, so an operator can find the run of a job.
A worker may stop after Device Farm schedules a run but before the server stores the run's ARN.
That run then has no owner. The job submits again, and the first run keeps its device until Device
Farm ends it.

A worker gives a run up in five ways:

| Event | What happens to the run | What the worker posts |
|---|---|---|
| The server relays the job's cancel to the worker, or a stop signal reaches a worker whose `maxJobConcurrency` is one | The worker stops the run through Device Farm's `StopRun` call | The job, as cancelled |
| A stop signal reaches a worker whose `maxJobConcurrency` is above one | The worker leaves a run whose ARN the server stored, and the next worker to lease the job resumes it. It stops a run whose ARN the server does not hold, since nobody could resume that run | Nothing; each lease lapses, and the next worker posts the result |
| The lease is lost after the server stored the ARN | The run continues, and the next worker to lease the job resumes it | Nothing; the next worker posts the result |
| The server answers `409` to the ARN because the lease is gone | No worker could resume the run, so the worker stops it | A result the server ignores, since the lease moved on |
| The server refuses the ARN with another `4xx` (a server or configuration error), or six minutes of retries run out | The server holds no ARN to resume from, so the worker stops the run and logs the error | The job's error, so the job ends without a verdict |

A cancel that arrives while the worker is still uploading schedules no run at all. A slot worker
treats a stop signal as a shutdown, not as a cancel. The signal ends the lease requests, and every
lease in flight lapses. A restart or another worker then picks the jobs up. The worker waits for its
slots to leave their runs and for uploads already under way. Then it exits. A second SIGINT exits
without waiting.
`bajutsu worker --once <spec.json>` with the same `worker.yaml` submits one batch spec with no
server. Its `batch.app_path` is a path on that machine, and it stores no ARN. A SIGINT, SIGTERM, or SIGHUP
stops its scheduled run the same way a cancel does.

None of this touches the verdict. Each Device Farm run reports pass or fail from Bajutsu's own
`manifest.json`, exactly as a local run does. The worker lands the downloaded run and uploads it.
serve's report viewer and history then render it like any other run. A submission that fails before
any verdict posts its reason as the job's error. The submission and polling
machinery stays off the `run`/CI verdict path, with no large language model (LLM) call on it (prime
directive 1).

## Batch lifecycle hooks (BE-0435)

`render_test_spec`'s `pre_test_commands` hook (BE-0432) handles **device-host setup**: commands that run on the Device Farm host, during the run, as shell. Some per-run setup cannot run there. It must happen in the worker that submits the run, before packaging — for example, minting a short-lived credential that the app must receive through its launch environment.

`BatchLifecycleHook` covers that gap. A hook runs in the worker whose environment is `devicefarm`, around one `DeviceFarmBatchProvider.submit` call. The server runs none:

- **`before_submit(ctx)`** — called before the package is built. May populate `ctx.launch_env` with key/value pairs that are merged into the packaged config's `targets.<target>.app.launch.env` before upload, so the app receives them as launch environment variables.
- **`after_run(ctx, verdict)`** — called in a `finally` after the verdict is collected (or with `verdict=None` when the run fails before a verdict is available), in reverse hook order so teardown mirrors setup. A hook whose `after_run` raises does not stop the remaining hooks. Once `submit` collects a verdict, it logs every teardown error and raises none. A failed credential release thus never discards a finished run. Without a verdict, `submit` raises one teardown error and logs the others. The raised one comes from the last-registered hook that raised.

`BatchContext` carries the run's `request`, `work_dir`, `job_id` (the stable per-job identifier, useful for looking up per-job state across a restart), and the mutable `launch_env` dict.

Since the worker dispatch (BE-0448), `work_dir` is the job's bundle directory. The request names
`request.config` and `request.scenario` by their paths inside it. `request.app_path` keeps the
target's `app.path` as written. A hook that reads the binary resolves a relative value against
`ctx.work_dir`, not its own working directory. A worker that runs from a source checkout puts the
bundle under `bundle/` inside the package. A `pre_test_commands` command that names a file beside
the config needs the `bundle/` prefix on the Device Farm machine.

### Wiring hooks

Hooks are wired at **process start only**, through the `BAJUTSU_BATCH_HOOKS` environment variable — a comma-separated list of `module:factory` paths. Bajutsu imports each module and calls the named factory (no arguments) to produce one hook instance. A worker whose `maxJobConcurrency` is above one shares that instance across its slots. Its hooks may then run at the same time, so a hook must be thread-safe and keep per-run state keyed by `ctx.job_id`:

```bash
BAJUTSU_BATCH_HOOKS=myapp.hooks:make_proxy_hook,myapp.hooks:make_audit_hook \
  bajutsu worker --worker-config devicefarm.worker.yaml
```

A deployment ships its hook module in the worker's image and names it in that variable. A hook that fails to load stops the worker at startup. No `serve` endpoint, config field, or batch-request field may select or configure a hook — hook identity comes only from the deploy-time environment (the same trust boundary as `pre_test_commands`).

```python
# myapp/hooks.py
from bajutsu.serve.batch_provider import BatchContext, BatchLifecycleHook

class ProxyHook(BatchLifecycleHook):
    def before_submit(self, ctx: BatchContext) -> None:
        psk = _mint_session_key(ctx.job_id)      # caller-owned credential store
        ctx.launch_env["PROXY_HOST"] = "proxy.internal"
        ctx.launch_env["PROXY_PSK"] = psk

    def after_run(self, ctx: BatchContext, verdict) -> None:
        _revoke_session_key(ctx.job_id)           # caller-owned credential store

def make_proxy_hook() -> ProxyHook:
    return ProxyHook()
```

> **Security note.** Values injected through `ctx.launch_env` land in the packaged run config, which is stored as a Device Farm artifact visible to principals with access to the deployment's own Device Farm project. Hooks that inject secrets should mint **short-lived, per-run values** (which is the motivating case) rather than long-lived ones.

### Key-collision guard

On-device, the launch-environment merge order is `{**target_env, **preconditions.app.launch.env}` — a scenario's `preconditions.app.launch.env` wins over the target-level value. If a hook injects a key that a scenario's preconditions also set, the injected value would be silently overridden. To surface this early, `submit` checks the scenario file and **fails loudly at submit time**, naming the scenario, the key, and the file, before any upload happens.

### Checkpoint-resume behaviour

When a run restarts mid-poll (the worker re-leases the job), `before_submit` is skipped — the run is already scheduled and packaged — but `after_run` still runs once the resumed run's verdict is collected. A hook that mints credentials in `before_submit` should persist them keyed by `ctx.job_id` so `after_run` can retrieve and release them after a restart.

## The serial-resolution proof of concept (manual)

The one empirical unknown is whether Bajutsu's Android backend picks up the Device Farm host's
reserved device — the `pre_test` `adb devices` should list it, and `bajutsu run --udid booted`
should resolve it. Proving this end-to-end needs a real AWS account, credentials, a Device Farm
project, and billing, so it is **not** part of the deterministic `make check` gate (which must run
anywhere, with no cloud account) — running it is a manual, human procedure:

1. Create a Device Farm project and a device pool in `us-west-2`; note their ARNs.
2. Build the showcase Compose debug APK: `make -C demos/showcase/android compose-build`.
3. Run the submitter (or the workflow) against one scenario, e.g. `scenarios/firstlook.yaml`.
4. Confirm from the downloaded artifacts that `adb devices` listed the reserved device and the
   scenario produced a `manifest.json` with the expected verdict. The submitter extracts the
   CUSTOMER_ARTIFACT zip (the `runs/` tree with the manifests) into the destination and writes
   Device Farm's plain-file artifacts — the device and test-spec logs that carry the `adb devices`
   output — under a `logs/` subdirectory alongside it.

Once confirmed for your account, the workflow can run the fuller scenario set on demand.

## The iOS device-signing proof of concept (manual)

iOS adds an unknown the Android route never faces: the batch upload must carry a **signed device
build**. Device Farm installs the app on a physical device, so it needs a device `.ipa` rather than
the unsigned Simulator `.app` the Simulator lanes emit, and the XCUITest runner must already carry a
device-valid signature because Device Farm re-signs the app but not the runner (BE-0288). Proving the
iOS route end to end therefore needs both an **Apple Developer account** — to sign the build — and an
**AWS Device Farm account**, so it is **not** part of the deterministic `make check` gate (which stays
unsigned and runs anywhere, with no Apple or AWS account). Running it is a manual, human procedure:

1. Create a Device Farm project and a device pool of iOS devices in `us-west-2`; note their ARNs.
2. Build the two device-signed artifacts. The app takes your 10-character Apple Team ID. Sign that
   team into Xcode, so `-allowProvisioningUpdates` can mint the development profile. The runner
   reads your [signing file](ios-device-cloud.md#the-signing-file) instead (BE-0456). Pass
   `SIGNING=<path>` to name one explicitly:

   ```bash
   make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<your-10-char-team-id>
   make -C demos/showcase runner-build-device
   ```

   The first emits the app `.ipa` at `demos/showcase/ios/swiftui/build/export-device/BajutsuShowcaseSwiftUI.ipa`;
   the second emits the device-signed `BajutsuRunner.xctestrun` under
   `BajutsuKit/Runner/build/dd-device/Build/Products`. An app build with `DEVELOPMENT_TEAM` unset,
   or a runner build with no signing file, fails fast with a clear message rather than producing an
   unsigned artifact.
3. Run the submitter against one scenario, e.g. `scenarios/firstlook.yaml`, with the iOS platform
   selected — the same `--platform ios` command from [Using the submitter](#using-the-submitter)
   above, with its `--package-only` dry-run flag dropped and `--project-arn <project-arn>
   --device-pool-arn <device-pool-arn>` added to submit the run. That command already packages the
   runner's whole `Products` directory (`--package BajutsuKit/Runner/build/dd-device/Build/Products=.`),
   so the `.xctestrun`'s `__TESTROOT__` test bundles beside it travel with the upload; the file alone
   is not enough. `--platform ios` selects the XCUITest backend, the `IOS_APP` upload type, and the
   `--udid "$DEVICEFARM_DEVICE_UDID"` argument the reserved device resolves through.
4. Confirm from the downloaded artifacts that the scenario produced a `manifest.json` with the
   expected verdict. As on the Android route, the submitter extracts the CUSTOMER_ARTIFACT zip (the
   `runs/` tree with the manifests) into the destination and writes Device Farm's plain-file
   artifacts under a `logs/` subdirectory alongside it. Read the verdict from Bajutsu's own
   `manifest.json`, never from Device Farm's PASSED/FAILED classification. Expect the re-signed
   behavior for any feature that depends on a dropped entitlement, and expect a scenario using
   `simctl`-backed device control or a permission grant to be skipped by the preflight — the two
   [real-device caveats](#ios-re-signing-and-real-device-capabilities) above.

Once confirmed for your account, the workflow can run the fuller iOS scenario set on demand, the same
way it does for Android.

**English** · [日本語](BE-0435-devicefarm-batch-lifecycle-hook-ja.md)

# BE-0435 — A server-side batch lifecycle hook for Device Farm runs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0435](BE-0435-devicefarm-batch-lifecycle-hook.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0435") |
| Implementing PR | [#2051](https://github.com/bajutsu-e2e/bajutsu/pull/2051) |
| Topic | Device-cloud execution |
| Related | [BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook.md), [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) |
<!-- /BE-METADATA -->

## Introduction

`DeviceFarmBatchProvider.submit` renders a test spec, builds the package, uploads it,
schedules a Device Farm run, and collects the verdict. It lives in
`bajutsu/serve/batch_provider/device_farm_batch_provider.py`.

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook.md)
added `pre_test_commands` so a caller can splice device-host setup into the `pre_test`
phase. That hook runs *on the Device Farm host*, during the run, as shell.

Some per-run setup cannot run there. It must run where `submit` runs — in the
`serve` process — before the package is built, and its result must reach the app
under test through configuration rather than through a device-host command. Nothing
today lets a deployment participate in the batch lifecycle at that point.

This proposal adds a server-side lifecycle hook: a `before_submit` step that runs in
the `serve` process before packaging and may inject launch environment variables into
the run config, and an `after_run` step that runs after the verdict is collected.
Setup and teardown specific to one deployment's backend then live entirely outside
`bajutsu/`, the same way `pre_test_commands` keeps device-host setup outside `bajutsu/`.

## Motivation

Prime directive 3 keeps Bajutsu app-agnostic. Per-app differences live in
configuration or in caller-supplied hooks, never inside the tool. BE-0432 established
this for device-host setup. A second, distinct case has surfaced from the same
integration: a staging backend behind an IP allowlist, reached from a Device Farm
device through a per-run authenticated relay.

That relay authenticates each run with a short-lived, per-run credential. Two
properties of that credential are what BE-0432's `pre_test_commands` cannot satisfy:

- **It must be minted where the credentials to mint it live.** Minting a per-run
  credential is itself a privileged operation against the deployment's own
  infrastructure. The `serve` process holds that authority (for example, a federated
  cloud identity). The Device Farm host does not, and deliberately must not — BE-0432
  already refuses to route caller input into a host that could then wield the run's
  cloud credentials. So minting has to happen in `serve`, before the run starts.

- **Its result must reach the app under test, not the host.** The app configures its
  own network stack from launch environment variables (`launchEnvironment` on iOS,
  launch intent extras on Android — both already carried by the config's
  `targets.<name>.launchEnv`). A device-host `pre_test` command cannot put a value
  there; only the run config, packaged and re-parsed on the device by `bajutsu run`,
  can.

There is also a symmetric teardown need: once the run ends, the per-run credential
should be released rather than left to expire. `submit` is the only place that knows
the run has ended.

As with BE-0432, none of this belongs inside Bajutsu. Another deployment might mint a
different credential, inject different variables, or need nothing at all. Bajutsu must
offer one generic seam and stay out of what a caller does with it.

## Detailed design

### The hook protocol

A new `BatchLifecycleHook` protocol, with a `BatchContext` passed to both steps:

```python
@dataclass
class BatchContext:
    request: BatchRequest       # read-only view of the run request
    work_dir: Path              # the project dir that build_package packs
    job_id: str                 # the serve job id (the key the checkpoint is stored under)
    launch_env: dict[str, str] = field(default_factory=dict)  # additions, applied before packaging

class BatchLifecycleHook(Protocol):
    def before_submit(self, ctx: BatchContext) -> None: ...
    def after_run(self, ctx: BatchContext, verdict: Verdict | None) -> None: ...
```

`before_submit` may perform external setup and populate `ctx.launch_env`. `after_run`
may perform external teardown; it receives the collected `Verdict`, or `None` when the
run failed before a verdict existed (see the provider flow below), and runs on both
paths.

The `...` bodies are no-op defaults **only for a hook that explicitly subclasses**
`BatchLifecycleHook`. A structurally-typed hook — the shape a `BAJUTSU_BATCH_HOOKS`
factory most naturally returns — that defines only one method is not a
`BatchLifecycleHook`: `mypy --strict` rejects it at the assignment site, and at runtime
the provider's unconditional `hook.after_run(...)` call would raise `AttributeError`
*inside the teardown `finally`*, destroying the run's real outcome. So a hook that wants
to implement only one step must inherit from the protocol class to pick up the no-op
default for the other.

`ctx.job_id` is the stable identifier the serve layer already assigns each job
(`bajutsu/serve/state/job.py`), and the same key the batch checkpoint is stored under
(`_RepositoryBatchCheckpoint`, `bajutsu/serve/jobs.py`). It is identical on the original
submit and on a later checkpoint-resume, which is what makes teardown work across a
restart (see *The checkpoint-resume path*).

### Provider changes

`DeviceFarmBatchProvider.__init__` gains `hooks: Sequence[BatchLifecycleHook] = ()`, and
`submit` receives the job id so it can build the context. `submit` invokes the hooks
around the existing flow:

1. Build `ctx = BatchContext(request, work_dir, job_id)`.
2. Run `hook.before_submit(ctx)` for each hook, in order.
3. If `ctx.launch_env` is non-empty, merge it into the packaged config **without
   mutating `work_dir`**. `work_dir` is the shared package/binding root
   (`bajutsu/serve/jobs.py` passes `state.devicefarm_package_root or job.cwd or
   state.binding.cwd`), and concurrent batch jobs pack the same directory, so rewriting
   the config file there would let one run's minted credential land in another run's zip
   and would leave a per-run value behind in the user's project config. Instead:
   - load `work_dir / request.config`, merge the entries into
     `targets[request.target].launchEnv` (caller entries win on key collision), and
   - hand the merged text to `build_package` as a per-submit overlay via `extra_texts`,
     **excluding the config's arcname from the walked `entries`** so the zip holds the
     merged config exactly once (`build_package`'s `extra_texts` loop *adds* an arcname
     rather than replacing a walked one — `bajutsu/common/cloud/devicefarm/_functions.py`).
4. **Collision guard (fail loudly).** On the device, `bajutsu run` merges the two launch
   env sources as `{**target_env, **scenario.preconditions.launch_env}`
   (`bajutsu/run/cli.py:993`), so a scenario that sets the same key in its own
   `preconditions.launchEnv` silently shadows the injected value — the app would launch
   with the scenario's stale value and the relay would reject the run with nothing
   pointing at the cause. To keep this deterministic, `submit` loads the referenced
   scenarios' `preconditions.launchEnv` from `work_dir` and, if any scenario declares a
   key present in `ctx.launch_env`, raises at submit time with a message naming the
   scenario, the key, and the file — before packaging. (This gives the provider a new,
   narrow read of scenario preconditions; today it handles scenarios only as file paths.)
5. Render the spec, build the package, upload, schedule, and collect — unchanged.
6. In a `finally`, run `hook.after_run(ctx, verdict)` for each hook, in reverse order,
   so teardown mirrors setup and always runs. `verdict` is `Verdict | None`: it is
   `None` when packaging, the upload, or collection raised before a verdict existed, so
   the teardown still runs and the original exception still propagates unchanged (rather
   than being replaced by an `UnboundLocalError` from an unbound `verdict`).

With no hooks (the default `()`), `submit` behaves exactly as today and the packaged
config is byte-identical.

### The checkpoint-resume path

A checkpoint-resume happens after a restart: `submit` finds a persisted run ARN
(`checkpoint.load()`), returns `collect_run(...)` for the already-scheduled run, and
skips rendering, packaging, upload, and therefore `before_submit` and the config merge
(`bajutsu/serve/batch_provider/device_farm_batch_provider.py`). The hooks are fresh
objects in a new process; `before_submit` never ran here, so `ctx.launch_env` is empty
and the in-memory hook holds no handle on whatever the pre-restart `before_submit`
minted.

`after_run` still runs on this path, in the same `finally`, with the resumed run's
verdict. For teardown to actually release the credential, `before_submit` must persist
the minted credential's identity to **durable per-job state keyed by `ctx.job_id`**, and
`after_run` looks it up by that same `job_id` and releases it. Because `job_id` is stable
across the restart, the resumed `after_run` — though it runs in a fresh process with an
empty `ctx.launch_env` — can still find and release the credential.

That durable store is the caller's own, outside `bajutsu/` (a deployment might use a
row in its own database, a DynamoDB item, etc.), consistent with prime directive 3.
Bajutsu's only contribution is the guarantee that `ctx.job_id` is the same value on the
original submit and on the resume, giving the hook a stable correlation key.

### Loading hooks — server-side only

Hooks are wired at process start, never from a request. `batch_bootstrap` reads an
environment variable — `BAJUTSU_BATCH_HOOKS`, a comma-separated list of
`module:factory` paths — and `importlib`-loads each factory, passing the results to
`DeviceFarmBatchProvider`. A deployment ships its hook module in the image and names
it in that variable.

This mirrors BE-0432's trust boundary exactly. `DeviceFarmBatchProvider` builds its
`render_test_spec` arguments from an HTTP request body; a hook, by contrast, mints
credentials and mutates the packaged config, so routing hook selection or hook
configuration through the request would hand a client the same host-credential and
config-tampering reach BE-0432 refuses. Therefore:

- no `serve` endpoint, no config field, and no `BatchRequest` field may select or
  configure a hook;
- hook identity comes only from deploy-time environment.

A unit test walks the wiring to confirm no request-sourced value reaches hook
selection, in the same spirit as BE-0432's AST check on `render_test_spec`.

### What reaches the device

The per-run values injected via `ctx.launch_env` land in the packaged config and
therefore in the run's Device Farm artifacts, visible to principals with access to
the deployment's own Device Farm project. This is the same exposure `pre_test_commands`
content already has, and it is within the deployment's own cloud account — not visible
to other tenants sharing device egress. Hooks that inject secrets should mint
short-lived, per-run values (which is the motivating case) rather than long-lived ones.

## Alternatives considered

- **Reuse BE-0432's `pre_test_commands` alone.** Rejected. Those commands run on the
  Device Farm host, which holds no credential to mint a per-run secret and, by
  BE-0432's own decision, must not. They also cannot write into the app's launch
  environment, which is sourced from the packaged config. The two hooks are
  complementary, not substitutes: `pre_test_commands` for device-host setup,
  `before_submit`/`after_run` for server-side setup and config injection.
- **A dedicated `proxy_config` / `vpn_config` / `relay_config` parameter.** Rejected
  outright, for the same reason BE-0432 rejected it: prime directive 3 forbids
  app-specific special-casing. Deployments need entirely different per-run setups;
  Bajutsu offers one generic hook rather than a parameter per backend shape.
- **A callback threaded into `render_test_spec`.** Rejected. `render_test_spec` is a
  pure function of its inputs. Setup and teardown are lifecycle concerns of `submit`,
  not of rendering; placing them in the provider keeps the pure function pure and the
  side effects in one place.
- **Mutating the config in `work_dir` instead of an `extra_texts` overlay.** Rejected.
  `work_dir` is the shared package root that concurrent batch jobs pack, so an in-place
  rewrite races across jobs and leaves per-run values behind on disk. The overlay
  packages the merged config for one submit only and never touches the shared tree.
- **Injecting at the scenario `preconditions.launchEnv` layer (so the hook value wins
  the on-device merge).** Rejected in favor of the collision guard. Reaching the
  scenario layer would mean the provider rewriting each scenario file — deeper mutation
  of caller content than injecting one target-level key. The guard keeps injection at
  `targets.<name>.launchEnv` and turns the one collision that layer loses into a loud
  submit-time failure, per determinism-first.
- **Skipping `after_run` on the checkpoint-resume path.** Rejected. It would leak the
  per-run credential until expiry — the exact outcome the teardown exists to avoid.
  Persisting the credential's identity under `ctx.job_id` lets the resumed `after_run`
  release it.
- **Selecting or configuring the hook from the request.** Rejected on security
  grounds, consistent with BE-0432. Hook identity is deploy-time only.
- **A build/entry-point plugin registry instead of an env var.** Rejected as
  heavier than needed. Bajutsu's existing provider registry is populated
  imperatively at bootstrap; `BAJUTSU_BATCH_HOOKS` follows the same
  configured-at-startup shape without adding packaging-metadata machinery.

## Progress

- [x] Add `BatchLifecycleHook` protocol and `BatchContext` dataclass (`request`,
  `work_dir`, `job_id`, `launch_env`); `after_run` takes `Verdict | None`. Document that
  no-op defaults require explicitly subclassing the protocol class.
- [x] Add `hooks: Sequence[BatchLifecycleHook] = ()` to
  `DeviceFarmBatchProvider.__init__`, and thread the stable `job_id` into `submit`/`ctx`.
- [x] Invoke `before_submit` before packaging; merge `ctx.launch_env` into the packaged
  config's `targets[request.target].launchEnv` and package it via `build_package`'s
  `extra_texts` overlay, excluding the config arcname from `entries`, without mutating
  `work_dir`.
- [x] Collision guard: load the referenced scenarios' `preconditions.launchEnv` from
  `work_dir`; raise at submit (naming scenario, key, file) if any collides with an
  injected key, since `bajutsu run`'s merge would otherwise silently shadow it.
- [x] Invoke `after_run` in a `finally`, in reverse order, with `verdict: Verdict | None`
  (`None` on a pre-verdict failure), including on run failure and the checkpoint-resume
  path.
- [x] Checkpoint-resume: guarantee `ctx.job_id` is identical on submit and resume so a
  hook can release, via caller-owned durable per-job state, the credential its original
  `before_submit` minted.
- [x] Load hooks from `BAJUTSU_BATCH_HOOKS` (`module:factory`, comma-separated) in
  `batch_bootstrap`, and pass them to the provider.
- [x] Unit test: default (no hooks) leaves the packaged config and behavior
  byte-identical to today.
- [x] Unit test: `before_submit`'s `ctx.launch_env` appears merged in the packaged
  config (via the overlay, `work_dir` unchanged), caller entries winning on key
  collision.
- [x] Unit test: the collision guard raises at submit, with a message naming the
  scenario, key, and file, when a scenario's `preconditions.launchEnv` collides.
- [x] Unit test: `after_run` runs on success, on run failure (`verdict is None`), and on
  the checkpoint-resume path; teardown order is the reverse of setup; a pre-verdict
  failure propagates unchanged (no `UnboundLocalError`).
- [x] Unit test: no `serve` endpoint, config field, or `BatchRequest` field selects
  or configures a hook (walks the wiring, like BE-0432's AST check).
- [x] Update `docs/devicefarm.md` and its Japanese mirror.

Log:

- [#2051](https://github.com/bajutsu-e2e/bajutsu/pull/2051) — Units 1-13, the whole item. Added
  `BatchLifecycleHook`/`BatchContext` (`bajutsu/serve/batch_provider/batch_lifecycle_hook.py`),
  wired `hooks` into `DeviceFarmBatchProvider.__init__`, and invoked `before_submit`/`after_run`
  around `submit` exactly as designed: the launch-env merge lands via a `build_package`
  `extra_texts` overlay with the config's own arcname excluded from `entries`, the collision guard
  raises at submit time on a colliding scenario `preconditions.launchEnv` key, and `after_run` runs
  in a `finally` in reverse hook order on every path (success, a pre-verdict failure with
  `verdict=None`, and checkpoint-resume). `batch_bootstrap` loads hooks from `BAJUTSU_BATCH_HOOKS`.
  Documented in `docs/devicefarm.md` and its Japanese mirror.

## References

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook.md)
adds the device-host `pre_test_commands` hook this proposal complements.
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduces `render_test_spec`, `build_package`, and `DeviceFarmBatchProvider`.

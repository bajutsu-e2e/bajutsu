**English** · [日本語](BE-0435-devicefarm-batch-lifecycle-hook-ja.md)

# BE-0435 — A server-side batch lifecycle hook for Device Farm runs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0435](BE-0435-devicefarm-batch-lifecycle-hook.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0435") |
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
    work_dir: Path              # the project dir that build_package will zip
    launch_env: dict[str, str] = field(default_factory=dict)  # additions, applied before packaging

class BatchLifecycleHook(Protocol):
    def before_submit(self, ctx: BatchContext) -> None: ...
    def after_run(self, ctx: BatchContext, verdict: Verdict) -> None: ...
```

`before_submit` may perform external setup and populate `ctx.launch_env`. `after_run`
may perform external teardown; it receives the collected `Verdict` and runs even when
the run failed. Both default to no-ops via the protocol, so a hook may implement only
one.

### Provider changes

`DeviceFarmBatchProvider.__init__` gains `hooks: Sequence[BatchLifecycleHook] = ()`.
`submit` invokes them around the existing flow:

1. Build `ctx = BatchContext(request, work_dir)`.
2. Run `hook.before_submit(ctx)` for each hook, in order.
3. If `ctx.launch_env` is non-empty, merge it into the packaged config: load
   `work_dir / request.config`, merge the entries into
   `targets[request.target].launchEnv` (caller entries win on key collision), and
   write the config back into `work_dir` so `build_package` zips the merged form.
4. Render the spec, build the package, upload, schedule, and collect — unchanged.
5. In a `finally`, run `hook.after_run(ctx, verdict)` for each hook, in reverse
   order, so teardown mirrors setup and always runs.

The checkpoint-resume path (an already-scheduled run) skips `before_submit` and the
config merge — the package is already uploaded — but still runs `after_run` once the
resumed run's verdict is collected.

With no hooks (the default `()`), `submit` behaves exactly as today and the packaged
config is byte-identical.

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
- **Selecting or configuring the hook from the request.** Rejected on security
  grounds, consistent with BE-0432. Hook identity is deploy-time only.
- **A build/entry-point plugin registry instead of an env var.** Rejected as
  heavier than needed. Bajutsu's existing provider registry is populated
  imperatively at bootstrap; `BAJUTSU_BATCH_HOOKS` follows the same
  configured-at-startup shape without adding packaging-metadata machinery.

## Progress

- [ ] Add `BatchLifecycleHook` protocol and `BatchContext` dataclass.
- [ ] Add `hooks: Sequence[BatchLifecycleHook] = ()` to `DeviceFarmBatchProvider.__init__`.
- [ ] Invoke `before_submit` before packaging; merge `ctx.launch_env` into the
  packaged config under `targets[request.target].launchEnv`.
- [ ] Invoke `after_run` in a `finally`, in reverse order, including on the
  checkpoint-resume path and on run failure.
- [ ] Load hooks from `BAJUTSU_BATCH_HOOKS` (`module:factory`, comma-separated) in
  `batch_bootstrap`, and pass them to the provider.
- [ ] Unit test: default (no hooks) leaves the packaged config and behavior
  byte-identical to today.
- [ ] Unit test: `before_submit`'s `ctx.launch_env` appears merged in the packaged
  config, caller entries winning on key collision.
- [ ] Unit test: `after_run` runs on success, on run failure, and on the
  checkpoint-resume path; teardown order is the reverse of setup.
- [ ] Unit test: no `serve` endpoint, config field, or `BatchRequest` field selects
  or configures a hook (walks the wiring, like BE-0432's AST check).
- [ ] Update `docs/devicefarm.md` and its Japanese mirror.

## References

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook.md)
adds the device-host `pre_test_commands` hook this proposal complements.
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduces `render_test_spec`, `build_package`, and `DeviceFarmBatchProvider`.

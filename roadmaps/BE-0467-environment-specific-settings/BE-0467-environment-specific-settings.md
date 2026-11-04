**English** · [日本語](BE-0467-environment-specific-settings-ja.md)

# BE-0467 — Environment-specific settings: a support table, target-declared ignored settings, and checks at lint and dispatch

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0467](BE-0467-environment-specific-settings.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0467") |
| Implementing PR | [#2249](https://github.com/bajutsu-e2e/bajutsu/pull/2249) (units 1–3), [#2254](https://github.com/bajutsu-e2e/bajutsu/pull/2254) (unit 4), [#2257](https://github.com/bajutsu-e2e/bajutsu/pull/2257) (unit 5), [#2260](https://github.com/bajutsu-e2e/bajutsu/pull/2260) (units 6–10) |
| Topic | Platform support |
| Related | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md), [BE-0457](../BE-0457-real-device-step-lint/BE-0457-real-device-step-lint.md) |
<!-- /BE-METADATA -->

## Introduction

A [scenario](../../docs/glossary.md#scenario-authoring) runs in environments that differ. The environments are the iOS Simulator, a real iPhone, a device behind a WebDriver endpoint, an Android emulator, and a web browser.

Some *settings* apply in one environment alone. `run.erase` drives `simctl erase`, and `seedPhotos` drives `simctl addmedia`. Both reach the Simulator and nothing else.

This item makes those settings behave the same way everywhere, by one rule:

- **A step is never skipped.** A step the routed [target](../../docs/glossary.md#target-app-device)
  cannot run is an error, with no opt-out.
- **A setting the environment cannot apply is an error by default.** The one opt-out is to declare the setting ignored, with a reason. The declaration lives in the target config (`targets.<name>`), never in the scenario.
- **Both checks run before any device work.** They run in `bajutsu lint --config --target`, and when serve hands a job to a worker. A job reaches only a worker that can run every step and apply every setting it needs.

One support table per environment route decides all three, so the lint, the run, and the dispatch
cannot disagree.

## Motivation

Today an environment that cannot apply a setting does one of three things, and which one depends on
the setting and the route:

| Setting | iOS Simulator | iOS real device | Live WebDriver | Android | Web |
|---|---|---|---|---|---|
| `run.erase` | Applies | Raises at launch | Raises at launch | Applies | Ignored |
| `app.path` install, `app.reinstall` | Applies | Raises at launch | Ignored | Applies | Ignored |
| `permissions` | Applies | Fails at preflight (BE-0082) | Fails at preflight (BE-0082) | Applies | Fails at preflight (BE-0082) |
| `runsOn.ios.seedPhotos` | Applies | Not reached | Not reached | Not settable | Not settable |
| `runsOn.ios.locale` | Applies | Passed to the app alone | Raises at launch | Not settable | Not settable |
| `app.launch.env`, `args`, `deeplink` | Applies | Applies | Raises at launch | Applies | Ignored |

The sources are `xcuitest_environment.py:352-372`, `xcuitest_live.py:73-125`, `android_environment.py:186-243`, and `web.py:40-61`. All four live under `bajutsu/common/platform_lifecycle/environments/`.

"Not settable" marks an iOS-only field, which the loader keeps out of an Android or web block (BE-0452). Platform keying is not a gap. Three costs follow from the mix of behaviors:

- **A silent ignore hides a broken precondition.** On the live WebDriver route the target's `app.path` goes unused. The run then drives whichever build the device already holds, not the one just built. On the web, `app.launch.env` never reaches the page. The result looks green while the scenario tested something else.
- **A raise surfaces late.** The error comes after the run leases a device and the app is about to launch. The capability preflight of BE-0082 covers `permissions` alone, not `erase` or the install.
- **Nothing checks before dispatch.** `bajutsu lint` reads no config, so it never sees the target. serve routes a job by its platform, host, environment, and machine tokens (`serve/capabilities.py:94-178`). None of those tokens names a step capability or a setting. A `setLocation` job thus can reach a worker that drives a real iPhone, and it fails there.

BE-0457 proposed a `skipOnRealDevice` modifier that skips a *step* on a real device. Skipping a step
makes the result of one scenario depend on where it ran. The same file then passes on a real device
because it checked less, which confuses everyone reading the result. This item replaces BE-0457, now
rejected.

Once this item ships, one command shows the change. `bajutsu lint --config <file> --target
<real-device>` exits 1. It names each step and each setting the target cannot honour, before the run leases any device.

A web run of a scenario that sets `app.launch.env` fails before the browser starts. The exception is a web target that declares the setting ignored. The report then lists the ignored setting with its reason. serve never hands a `setLocation` job to a worker whose environment lacks the capability.

## Detailed design

The design has six parts: the support table, the ignore declaration, the run, the lint, the dispatch, and the evidence kinds. No part adds a model call or a fixed pause. Every difference between environments stays in the table and the target config.

### The support table

A new module, `bajutsu/common/capability/settings_support.py`, names each environment-specific
setting and answers, per environment route, how the route handles it:

| Support | Meaning | Run behaviour |
|---|---|---|
| `supported` | The route applies the setting | Apply it |
| `satisfied` | The route already guarantees what the setting asks for | Do nothing; never an error |
| `unsupported` | The route cannot apply it | Error, unless the target declares it ignored |

`satisfied` covers a request the route meets by construction. A web run starts a fresh browser
context every time, so `erase: true` is already true there. Treating that case as `unsupported` would
force a meaningless declaration onto every web target.

The settings are these, each keyed by the name a declaration uses:

| Key | Source field |
|---|---|
| `erase` | `run.erase` (scenario or target) |
| `install` | the target's `app.path` install and `app.reinstall` |
| `permissions.<service>` | each service in the scenario's `permissions` |
| `seedPhotos` | `runsOn.ios.seedPhotos` |
| `locale` | `runsOn.ios.locale` |
| `launchEnv`, `launchArgs`, `deeplink` | `app.launch.env`, `app.launch.args`, `app.launch.deeplink` |

The route is the one `capabilities_for_run` (`bajutsu/common/backends.py:270`) already resolves. It combines the actuator, the target's `runsOn.kind`, and a WebDriver endpoint. One function, `settings_support(actuator, eff, udid_spec)`, returns the table for that route.

A permission is a setting applied before the steps, so its rows move here from `capability_preflight._REQUIREMENTS`. The per-service capability tokens stay the route's statement of support. The BE-0082 step check no longer judges `permissions`. An ignored `permissions.<service>` is thus honoured by one check alone. `erase_precondition_supported` (`backends.py:326`) becomes a lookup in the same table.

The table, not each environment, now decides. Each environment's launch reads the resolved plan and applies the `supported` settings. It raises for a setting that the plan marks `unsupported` and the target does not ignore.

The raise stays because the preflight is skippable. A lease driven directly has no actuator, so `caps` is `None` (`bajutsu/common/runner/pipeline.py:1989`). The raise is the runtime backstop the environment modules already name. The plan replaces the per-environment conditions and the silent ignores, not the failure itself.

### The ignore declaration

`RunPolicy` (`bajutsu/common/config/schema/run_policy.py:14`) gains one map:

```yaml
targets:
  iphone-device:
    run:
      ignoredSettings:
        erase: "A real device keeps app data; this lane accepts state from earlier runs"
        seedPhotos: "The photo library on this device is curated by hand"
```

| Rule | Behaviour |
|---|---|
| Key | A setting key from the table above; an unknown key fails at config load |
| Value | A non-blank reason, shown in the report |
| A key the route supports or satisfies | An error at the run preflight and in `bajutsu lint --config`, once the route is resolved, so a stale declaration cannot hide a regression |
| Scope | The one target; `defaults.run` may not carry it, since an ignored setting belongs to one environment |

The scenario never declares a setting ignored. The environment owner, who knows what the environment cannot do,
accepts the trade-off. The scenario stays a single, environment-independent definition of the test.

### The run

The run preflight (`bajutsu/common/runner/pipeline.py`, `_preflight_targets` and the single-target
path) adds one check beside the step check of BE-0082. For each target, it resolves the settings
the scenario puts in effect, then looks each one up in that target's table:

- `supported` or `satisfied`: no finding.
- `unsupported` and declared: recorded as ignored, and not applied.
- `unsupported` and not declared: the scenario fails before any device work. The reason names the
  setting, the target, and the declaration that would ignore it.

`RunResult` gains `ignored_settings: list[IgnoredSetting]`. Each entry names the target, the setting key, and the reason. This is the shape `skipped_captures` already uses (`bajutsu/common/orchestrator/types/run_result.py:83`). Two targets that ignore the same setting for different reasons thus both reach the report. Each report renders the list without changing the verdict:

- The manifest writes it as `ignoredSettings`.
- The HyperText Markup Language (HTML) report lists it beside the run's environment.
- JUnit records it as a `bajutsu.ignoredSettings` property.
- The Common Test Report Format (CTRF) puts it in the test's `extra`.

Steps keep today's behaviour: the BE-0082 preflight fails a scenario holding a step its
target cannot run. This item adds no modifier to a step.

### The lint

`bajutsu lint` gains `--config <path>` and `--target <name>`
(`bajutsu/cli/commands/lint.py`). With them, the lint resolves the targets the way the run does:

- A scenario that declares its own `targets` needs no `--target`; the lint resolves each declared
  target.
- A scenario that declares none uses the named target. `--config` without `--target` for such a
  scenario exits 2, so a forgotten flag cannot hide a finding.

The lint then runs the two checks on the expanded scenario, per target. The first is the BE-0082 step check on the steps routed to that target. The second is the settings check above.

The lint also runs `check_preconditions` (`bajutsu/common/config/preconditions.py:230`), which today only the run calls. Each finding names its location. A finding inside a component lands on the `use:` line that pulled it in. Without `--config`, the lint behaves as today.

The serve editor's lint (`bajutsu/serve/operations/lint.py`) sends the editor's selected target with
the `/api/lint` request. The server resolves it against the bound config. With no target selected,
the editor's lint reports nothing new.

### The dispatch

serve routes a job by capability tokens (BE-0166). `can_serve` (`serve/capabilities.py:178`) hands a job to a worker that advertises every token the job requires. This item adds two kinds of token:

| Token | The job requires it when | The worker advertises it when |
|---|---|---|
| `cap:<route>:<capability>` | A step routed to a target on `<route>` needs the capability | The worker can drive `<route>`, and that route supports the capability |
| `setting:<route>:<key>` | A setting is in effect on a target on `<route>` that does not declare it ignored | The worker can drive `<route>`, and that route supports or satisfies the setting |

Each token names its route, one of the columns of the Motivation table: `ios-simulator`, `ios-device`, `ios-live`, `android`, or `web`. A platform alone is not enough, because the three iOS routes support different settings.

A worker advertises one set per route it can drive. A Mac that serves both the Simulator and an attached iPhone thus never takes an `erase` job on the device route. It also never turns away a Simulator job it can run. Intersecting the sets of two routes would instead hide the Simulator's `setLocation`.

`required_capabilities` derives the job's tokens from the expanded scenario and the target config. `worker_capabilities` (`bajutsu/serve/capabilities.py:195`) derives the worker's tokens from its `worker.yaml` environment and drivers (BE-0450). It uses the same support table and `capabilities_for_run`.

A `local` iOS worker thus advertises the Simulator's set. It also advertises the device route's set when its `worker.yaml` lists a real device. An `appium` worker advertises the live route's set, which lacks `cap:ios-live:deviceControl.setLocation`.

A job whose tokens no live worker advertises stays queued and counts toward `bajutsu_unroutable_jobs` (BE-0166). A submission during a cold start or a worker restart is therefore never rejected. Submission fails when a live worker already drives the job's route and none of those workers advertises every token the job needs. The failure lists each missing token and the steps or settings that need it.

The worker re-runs the same checks when it claims a job, before it leases a device. A worker's environment can change between registration and claim.

Both `cap:` and `setting:` join the reserved routing prefixes (`reserved_routing_tokens`, `bajutsu/common/capability/worker_capability.py:61`). The worker computes them itself. The deprecated `--capabilities` override may not carry them, so no operator pin can claim a capability the worker's route lacks.

A worker from before this item advertises no `cap:` or `setting:` token. The server matches such a worker on today's tokens alone and counts it as able to serve, so an upgrade never strands a queue.

That worker runs pre-item code. It keeps today's BE-0082 step preflight and the launch-time raises, but not this item's settings check. Its silent ignores persist until it upgrades. This item accepts that residual gap for the rollout and keeps it visible: the server records on the job that an unchecked worker ran it, and the report shows that note.

### The evidence kinds

`video`, `deviceLog`, and `appTrace` stay outside the error rule. Evidence never decides a verdict,
so a route that cannot record one should not fail the scenario. The gap must still be visible.
`resolve_evidence_providers` (`bajutsu/common/backends.py:631`) computes a skip reason only for the kinds in `KIND_CAPABILITY` (`backends.py:589`). That table holds `network` alone today. `bajutsu/common/runner/pool.py:325` discards even that reason.

This item keeps the reason on the lease's `skipped_captures`, which the report already renders. It also gives `video`, `deviceLog`, and `appTrace` a capability each, so the same resolution can answer for them.

### Out of scope

- **Android emulator versus device.** The Android target config has no field that tells them apart,
  so both share one table. A later item can add the field and split the rows.
- **A real-device alternative branch.** Running a different step on another environment would
  extend `if`. That extension changes the steps a scenario runs, which this item rules out.
- **Live WebDriver wiring.** The rows this item gives the live route reflect what `xcuitest_live.py`
  wires today. Wiring more of them is BE-0238's work; each one wired flips its row to `supported`.

## Alternatives considered

| Alternative | Summary | Why not adopted |
|---|---|---|
| Skip steps per environment (BE-0457) | A `skipOnRealDevice` / `skipIfUnsupported` step modifier | The result of one scenario then depends on where it ran; a run passes because it checked less |
| Declare ignored settings in the scenario | A reason beside the setting in the scenario file | The scenario stops being environment-independent, and the author decides for environments they may never see |
| Declare ignored settings in `worker.yaml` | Each worker states what it may ignore | The same target then behaves differently by worker, and a lint without a worker cannot know the verdict |
| Warn instead of failing | Report an unsupported setting and continue | A warning does not change the verdict, so a silently broken precondition still passes |
| Reject at claim alone | Keep routing as is; the worker refuses an unrunnable job | The job still waits for, and then reaches, a worker that cannot run it; the user learns late |

The worker-level declaration becomes worth revisiting if one target is ever served by workers whose
environments genuinely differ.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `settings_support.py`: the setting keys, the three support states, and the table per route,
  moving the permission rows out of the step check and replacing `erase_precondition_supported`
- [x] Add `run.ignoredSettings` to `RunPolicy` with its load-time rules
- [x] Add the settings check to the run preflight, and make each environment apply the `supported`
  settings and raise for an unsupported one that is not ignored, in place of its own conditions and
  silent ignores
- [x] Add `RunResult.ignored_settings` and render it in the manifest, HTML, JUnit, and CTRF reports
- [x] Give `video`, `deviceLog`, and `appTrace` a capability each, and keep the evidence skip reasons
  on the lease instead of discarding them in `pool.py`
- [x] Add `--config` and `--target` to `bajutsu lint`, running the step check, the settings check,
  and `check_preconditions` per target on the expanded scenario
- [x] Send the selected target with the serve editor's `/api/lint` request
- [x] Add the `cap:` and `setting:` tokens to the job's required set and to the worker's advertised
  set, reserve both prefixes, keep a job no live worker can serve queued, fail it at submission only
  when a live worker drives its route but none advertises every token, and match a pre-item worker on today's tokens while recording on the job that it ran unchecked
- [x] Re-run the step and settings checks when a worker claims a job, before it leases a device
- [x] Document the table, the declaration, the lint flags, and the dispatch tokens in `docs/` and
  `docs/ja/` (`configuration.md`, `cli.md`, `drivers.md`, `scenarios.md`); update `DESIGN.md` and
  `docs/architecture.md`

Log:

- [#2249](https://github.com/bajutsu-e2e/bajutsu/pull/2249) — Units 1–3. Added `bajutsu/common/capability/settings_support.py` with the per-route table, the check, and the launch-time backstop. Moved the `permissions` rows out of the BE-0082 step check. Made `erase_precondition_supported` a lookup in the table. Added `run.ignoredSettings` to `RunPolicy`, refused under `defaults`. The run preflight now judges every target's settings before any lease. The iOS Simulator, real-device, live WebDriver, Android, and web launches read the table instead of their own conditions, device-group members included. Documented `run.ignoredSettings` in `docs/configuration.md` and its Japanese mirror.

  Five points depart from the design text:
  - Android's `launchArgs` is `unsupported`, since Android has no `app.launch.args` and `am start` takes none.
  - A scenario's `app.launch` value is in effect only on a target whose platform has that field, as BE-0452 keys it. An iOS-and-web scenario's deeplink therefore does not fail the web target.
  - The real-device `locale` is `supported`, since it reaches the app as a launch argument.
  - The live route keeps refusing `extra_env` outside the table, because that value is Bajutsu's own injection, not a setting. For the same reason, the `BAJUTSU_*` keys the run writes into a launch environment (`BAJUTSU_MOCKS` and others) are never a `launchEnv` setting. The live route still refuses them at launch.
  - A web target's own `app.launch.env` is not in effect either, since its schema keeps it for Playwright code generation. A scenario's `app.launch.env` still is. The showcase `web` target therefore declares `launchEnv` ignored for `cross_platform_app_web.yaml`.

  `record`, `crawl`, `repl`, and `doctor` report a refused setting as a clean error. Until unit 4 renders the ignored settings in the report, a launch logs each one as a warning.
  as a warning. Documented `run.ignoredSettings` in `docs/configuration.md` and its Japanese mirror.

- [#2254](https://github.com/bajutsu-e2e/bajutsu/pull/2254) — Unit 4. Added `IgnoredSetting` (target, key, reason) and `RunResult.ignored_settings`. The run records each declared target's ignored settings once the scenario launches, a crashed attempt included. The record is judged by the same `check_settings` the preflight ran. A scenario the preflight refuses records none, since no launch dropped anything.

  The manifest writes the list per scenario, and the loader reads it back. The HTML report lists it in the Environment panel. JUnit carries a `bajutsu.ignoredSettings` property whose value is a JSON array. CTRF puts it in the test's `extra` as `ignoredSettings`.

  One point departs from the design text: the manifest key is `ignored_settings`, not `ignoredSettings`. Every per-scenario manifest field, `skipped_captures` included, is the snake_case `asdict` name the loader reads back. The launch-time warning stays for a lease with no report, such as `record` or `crawl`. Documented the report fields in `docs/reporting.md` and `docs/configuration.md`, with their Japanese mirrors.
  `docs/configuration.md`, with their Japanese mirrors.

- [#2257](https://github.com/bajutsu-e2e/bajutsu/pull/2257) — Unit 5. Added the `video`, `deviceLog`, and `appTrace` capabilities and declared them per driver. The XCUITest backend records all three through simctl. The adb and Playwright backends record `video` and `deviceLog`. The live WebDriver driver records none. `capabilities_for_run` drops all three on a real iOS device, since simctl reaches the Simulator alone.

  `KIND_CAPABILITY` now covers the three kinds, and `resolve_evidence_providers` judges the actuator by its route-narrowed capabilities. The pool keeps the skip reasons for the kinds the scenario asked for on `Lease.skipped_captures`, which the report already renders. `network` is never disclosed there, since the run gets it natively or from the pool's app-side collector. A device-group member's lease adds only the kinds no earlier lease on that device disclosed, so the group discloses each gap once.

  Two points go beyond the design text:
  - Only `network` takes a same-platform fallback provider. The pool wires a provider's collector, and nothing yet hands a provider's recorder to the sink.
  - The `FileSink` never starts a kind the route cannot record. A real-device run therefore no longer points a simctl recorder at the device.

  The fake driver declares none of the three, since its environment records nothing. Documented the per-route table in `docs/evidence.md` and `docs/drivers.md`, with their Japanese mirrors.
  their Japanese mirrors.

- [#2260](https://github.com/bajutsu-e2e/bajutsu/pull/2260) — Units 6–10. `bajutsu lint --config <file> --target <name>` resolves each scenario's targets the way `run` does. Per target, on the expanded scenario with lifecycle hooks folded in, it runs the BE-0082 step check, the settings check, and `check_preconditions` (`bajutsu/common/target_lint.py`). `steps_for_target` moved from the pipeline to `capability_preflight` so both share it. A step finding carries its authored line, and the `use:` line for a step a component produced.

  The serve editor sends its selected target and scenario path with `/api/lint`. The server adds the same findings against the bound config. Each scenario scope now names the directory its file refs resolve in.

  `bajutsu/common/capability/route_tokens.py` derives the `cap:` and `setting:` tokens. A run job requires them on each target's route. A worker advertises one set per route its drivers drive, through `capabilities_for_route` and `route_settings`. `capabilities_for_run` and `settings_support` now read those two as well. Both prefixes are reserved. The lease hands the worker the job's routing set. The worker re-checks its route tokens before it fetches anything or leases a device.

  Documented the lint flags, the editor lint, the dispatch tokens, and `realDevices` in `docs/cli.md`, `docs/configuration.md`, `docs/scenarios.md`, `docs/drivers.md`, `docs/self-hosting.md`, and `docs/architecture.md`, with their Japanese mirrors, and in `DESIGN.md`.

  Six points depart from the design text:
  - `worker.yaml` has no real-device listing, so it gains `realDevices`. Omitted, it counts as true, so a Mac that served real-device jobs before keeps them after an upgrade. `false` keeps a Simulator-only Mac off them.
  - A worker's routes come from every driver its file and host allow, not its `--platform` alone. An iOS-and-web scenario therefore still reaches the Mac that ran it before.
  - Submission also requires that no live worker can serve the job at all. A pre-item worker that still serves the job thus keeps a mixed fleet from refusing it.
  - An appium job's iOS targets go on `ios-live`, since the grid endpoint lives in the worker's file, not the target's config.
  - The claim-time check compares route tokens, since the run subprocess already re-runs the step and settings checks before its lease.
  - The "unchecked worker" note is the provenance entry `workerRouting: unchecked` in the run's `manifest.json`. A report rendered on view lists the entry under a new run-level *notices* section. The report baked at run time cannot show it, since the old worker that triggers the note bakes the report before serve merges the entry.

## References

- `bajutsu/common/platform_lifecycle/environments/` — where each environment applies or rejects a
  setting today (`xcuitest/xcuitest_environment.py:352-372`, `xcuitest_live.py:73-125`,
  `android/android_environment.py:186-243`, `web.py:40-61`)
- `bajutsu/common/backends.py:270-350` — `capabilities_for_run` and `erase_precondition_supported`
- `bajutsu/common/capability/capability_preflight.py` — `_REQUIREMENTS` and `unsupported()`
- `bajutsu/common/config/preconditions.py:230` — `check_preconditions`, run-only today
- `bajutsu/common/config/schema/run_policy.py:14` — `RunPolicy`
- `bajutsu/common/lint.py`, `bajutsu/cli/commands/lint.py`, `bajutsu/serve/operations/lint.py` —
  today's config-free lint
- `bajutsu/serve/capabilities.py:161-215` — job and worker routing tokens
- `bajutsu/common/runner/pool.py:325` — the discarded evidence skip reasons

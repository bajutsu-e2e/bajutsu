**English** · [日本語](BE-0465-serve-ui-device-farm-fan-out-ja.md)

# BE-0465 — Dispatch a Device Farm fan-out from the serve web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0465](BE-0465-serve-ui-device-farm-fan-out.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0465") |
| Implementing PR | [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

This item gives the serve web UI an entry point for a Device Farm fan-out. Today an operator can
fan a target out to AWS Device Farm, one run per scenario, through a hand-written call to
`POST /api/run-set` alone
([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md)).
The Replay tab gains an execution-environment choice. With a device-cloud environment chosen, the
operator picks several scenarios, and one press of Run dispatches the fan-out. A run-set panel then
follows every scenario's job in one list. The choice offers a device-cloud environment while a live
worker advertises it, and hides it otherwise, so an operator no longer dispatches into an
environment that was never served. A worker that stops after the list was fetched still leaves its
jobs queued, and the panel's no-worker notice names that case.

## Motivation

BE-0336 set out to let the serve web UI dispatch Device Farm runs directly. The work shipped the
server half: the `POST /api/run-set` route, the per-scenario fan-out, and the device-cloud worker of
[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) that submits
each job. No page of the web UI calls the route. The Replay tab's Run button posts a single scenario
to `POST /api/run`, and its request body carries no `environment` field. An operator who wants a
fan-out has to leave the browser and write a JSON body by hand:

```json
{ "target": "showcase-compose", "environment": "devicefarm", "scenarios": ["firstlook.yaml", "controls.yaml"] }
```

The hand-written request hides three facts the server already holds:

- **Which environments a worker serves.** A worker advertises the routing token
  `environment:devicefarm` once it has registered the Device Farm provider. The server records that
  token for every live worker, yet no read route returns it. An operator learns that no Device Farm
  worker is running when the queued jobs report that no live worker can serve them.
- **Which scenario names the target accepts.** The request names scenario files relative to the
  target's scenarios directory. One unknown name fails the whole request, which keeps the fan-out
  safe. The operator still has to know the file names in advance.
- **How much of the fan-out went out.** The server's global, per-user, and per-org caps can cut a
  large fan-out short. The response then lists fewer job ids than the request named scenarios, with
  status 200. Nothing in the response says that the server dropped the rest.

Once this item ships, an operator dispatches a Device Farm fan-out from the Replay tab without
writing a request body, and follows every scenario's job in one list. A later reader can check the
result in two places. The environment choice lists `devicefarm` exactly when a live Device Farm worker
exists. The run-set panel reports the number of scenarios that the server did not dispatch, instead
of showing a shorter list without comment.

## Detailed design

### Before and after

| Concern | Today | With this item |
|---|---|---|
| Starting a fan-out | `curl` or a script against `POST /api/run-set` | The Replay tab, with a device-cloud environment chosen |
| Choosing the environment | The request body names it; a typo or a missing worker shows up after dispatch | A selector lists `local` and each device-cloud environment that a live worker advertises |
| Choosing the scenarios | File names typed into the body | A multi-select over the target's scenarios, or every scenario |
| Following progress | One `GET /api/jobs/{id}` per job id | A run-set panel with one row per scenario |
| A fan-out cut short by a cap | A shorter `jobIds` list, status 200 | The panel states how many scenarios the server did not dispatch |

The `POST /api/run-set` contract, the worker, and the Device Farm submission stay unchanged. This
item adds one read route and a log-free form of the job read. Every other change stays inside the
web UI.

### Listing the available environments

A new read route, `GET /api/environments`, returns every environment that a live worker advertises:

```json
{
  "environments": [
    { "name": "devicefarm", "kind": "batch", "liveWorkers": 2 },
    { "name": "appium", "kind": "grid", "liveWorkers": 1 }
  ]
}
```

The server derives the list from the workers table that capability routing already keeps. A new
repository method reads the `environment:<name>` tokens of every worker seen within the lease
timeout. The no-worker notice judges liveness by the same window (`lacks_live_worker`). The method
reads the capability column, never a job's spec. Each entry carries the environment's kind:
`batch` for a provider in `BATCH_PROVIDERS`, and `grid` for the `appium` grid that `POST /api/run`
already accepts
([BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)). The route
filters nothing by kind. Each caller picks the kinds it can dispatch, so a later entry point for the
grid reads the same route without changing what the route means. A server without a queue has no
workers table, so the route
returns an empty list there. That answer matches the `run-set` refusal on a server without a queue.

The list carries environment names, kinds, and worker counts. It never carries an Amazon Resource Name (ARN) or a
credential: those values live in each worker's own environment and never reach the server. The route
sits behind the same authentication as the other read routes. The response leaves `local` out. The
web UI always offers `local`, since the plain `POST /api/run` path serves it.

### The Replay tab in a device-cloud environment

The Run form gains an **Environment** selector next to Target. `local` stays the default, and
choosing it keeps today's form unchanged. The web UI fills the selector from `GET /api/environments`
when the tab opens, and again when the operator presses a refresh button next to the selector. The
selector offers the `batch` entries alone, since `run-set` refuses a fan-out to a grid.

Choosing a device-cloud environment changes the form in three ways:

1. **Scenarios.** The single-scenario select becomes a multi-select over the target's scenarios,
   with an *All scenarios* option. *All scenarios* sends every name the scenario list showed, so the
   request names exactly the files the panel labels its rows with. Omitting `scenarios` would let the
   server list the directory again at dispatch, and a scenario saved from the Editor tab in between
   would shift every later row onto the wrong file. The server resolves every name before
   dispatching any job, as it does today. It then dispatches the jobs in the request's order and
   stops at the first one the concurrency cap rejects, so `jobIds` always pairs, position by
   position, with the leading names of `scenarios`.
2. **Fields the fan-out ignores.** The form hides the device picker and the Workers count, since the
   device-cloud worker reserves the device. It also hides the per-run options `erase`, `headed`,
   and `systemAlertHandling`. `run-set` reads none of them, so a value set there would be dropped
   without notice. The form also hides the Readiness panel, whose checks probe the serve host rather
   than the device cloud. View scenario, the grade badge, and codegen stay bound to the
   single-scenario select, which the form restores when the operator switches back to `local`.
3. **The request.** Run posts `{ target, environment, scenarios? }` to `POST /api/run-set`.

`GET /api/targets` already marks a web target by its backend, so the form turns the device-cloud
environments off for one and names the reason next to the selector. The form makes no check of its own
for `app.path`: the server refuses a target without one before dispatching any job, and the form
shows that refusal like any other. The server stays the authority: its own error response for any
other refusal (a 400, or a 429 when the concurrency cap rejects the first job) appears in the form's
status line unchanged.

### The run-set panel

A successful dispatch opens a run-set panel in place of the single-run log. The panel holds one row
per scenario. Each row shows the scenario's file name and its job's status as `GET /api/jobs/{id}`
reports it: running from dispatch until the run ends, then done with a verdict. A row with a finished
run links to its report. A job still queued with no live worker shows the no-worker notice that the
same route already returns, so a row stuck without a worker reads differently from one in flight.

The panel polls `GET /api/jobs/{id}` for each row at an interval, and stops polling a row once its
job is done. The poll reads the status without the log buffer. `GET /api/jobs/{id}` answers with
the job's whole log today, so one poll per row would re-send every row's log on every tick. Unit 1
adds a query parameter that leaves the log out (`Job.view(include_lines=False)`, the shape the
terminal-status payload already stores). When the operator expands a row to read its log, the panel
opens that job's event stream, the one the single-run view uses, which replays the log from its
first line. The number of open connections thus stays independent of the number of scenarios. A **Stop all** button cancels every job still queued or running through the existing cancel route.
For a scheduled Device Farm run, the worker then stops the run, exactly as a single cancel does.

The panel compares the number of job ids in the response with the number of scenarios the operator
chose. When the server dispatched fewer, the panel states how many scenarios it did not dispatch,
and that the concurrency cap cut the fan-out short. The operator can then re-dispatch the remainder
later. For *All scenarios*, the web UI counts the scenarios that the target's scenario list showed
when the operator pressed Run.

The panel lives in the browser. A reload loses the grouping, while every run stays in the History
list as before. A server-side run-set record stays out of scope; *Alternatives considered* gives the
reason.

### Boundaries

- **No change to dispatch, with one exception.** The worker and the Device Farm client stay as they
  are. `POST /api/run-set` keeps its request body. Its jobs needed one fix, found while verifying
  this item, and the Log records it. A job off a config on the server's disk named its files by
  their paths there. That pinned the job to the server's machine. A Device Farm worker never takes
  such a pin. The fan-out this item's UI starts thus never ran. The server now ships the config's
  directory as a bundle. It refuses a config the bundle cannot carry.
- **No device filters.** The fan-out keeps reserving one device per run by platform. Choosing a
  model or an operating system version stays out of scope.
- **No grid in the Replay selector.** The selector lists the `batch` entries, which follow
  `BATCH_PROVIDERS`. Today that set holds `devicefarm` alone. Offering the `appium` grid in Replay
  stays out of scope, although `GET /api/environments` already lists it.

### Prime-directive compliance

- **AI never judges.** The change adds a selector, a read route, and a progress view. The verdict
  still comes from each run's own `manifest.json`, and no large language model (LLM) call enters the
  dispatch or the panel.
- **Determinism first.** The environment list follows the lease timeout that routing already uses,
  so the selector and the queue agree on which workers count as live. The server resolves the
  scenario names before any dispatch, as before.
- **App-agnostic.** The form reads the target's platform from `GET /api/targets`. No per-app
  code enters the web UI.

### Work breakdown (MECE)

1. **Environment listing.** Add the repository method that reads live `environment:` tokens, and
   the `GET /api/environments` route that returns them with their kinds. Cover a live worker, an
   expired worker, an `appium` worker listed as `grid`, and a server without a queue in tests. Add
   the query parameter on `GET /api/jobs/{id}` that leaves the log buffer out.
2. **Replay form.** Add the Environment selector, limited to the `batch` entries, and its refresh button, the multi-select with
   *All scenarios*, the hidden fields and the hidden Readiness panel, the turned-off state for a web target, and the
   `POST /api/run-set` request.
3. **Run-set panel.** Add the per-scenario rows, the per-row polling, the event stream for an expanded row, the report links, the
   no-worker notice, **Stop all**, and the partial-dispatch message.
4. **Tests and documentation.** Extend the serve-UI dogfood scenarios under `demos/serve-ui/` to
   cover the device-cloud form against a faked worker. Update `docs/web-ui.md` and
   `docs/devicefarm.md` with their Japanese mirrors.

## Alternatives considered

- **A dedicated cloud-run tab.** A separate tab would keep the Replay form small. A separate tab
  would also duplicate the target and scenario pickers, and split the run history across two
  places. An operator already starts runs from Replay, so we extend Replay instead.
- **A fixed environment list.** The web UI could always offer `local` and `devicefarm`, and leave a
  missing worker to the no-worker notice after dispatch. That choice saves one read route. It also
  lets an operator queue a whole fan-out that no worker will ever lease. Listing live environments
  catches the missing worker before dispatch.
- **One event stream per row.** Each row could hold the event stream that the single-run view uses.
  Serve answers over HTTP/1.1, where a browser allows about six connections to one origin. A
  fan-out of more than a handful of scenarios would hold every connection, and the **Stop all**
  requests would wait behind the streams.
- **History alone for progress.** The web UI could report the number of dispatched jobs and leave
  the rest to the History list. History shows each run once the run finishes. A queued job stuck
  without a worker, or a fan-out cut short by a cap, would stay invisible.
- **A server-side run-set record.** Storing each fan-out as its own record would let the panel
  survive a reload and let History group runs by fan-out. That record needs a new table, a
  migration, and a new read path. The browser-side panel covers the operator who watches a fan-out
  started a moment ago. A run-set record can follow as its own item once grouped history proves needed.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Environment listing (`GET /api/environments`)
- [x] Unit 2 — Replay form in a device-cloud environment
- [x] Unit 3 — Run-set panel
- [x] Unit 4 — Tests and documentation

Log:

- [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) (Units 1–4) — `GET /api/environments` lists the batch and grid environments.
  A live worker must advertise one for it to appear. The response gives each a live worker count and carries no ARN or credential.
  `GET /api/jobs/{id}?lines=0` omits the log buffer. The Replay tab gains the Environment selector
  and the device-cloud form, and Run posts the chosen scenarios to `POST /api/run-set`.
  The run-set panel tracks one row per job.
  `POST /api/run-set` now requires the editor role, like `POST /api/run`. Before, any signed-in
  role could call it. The `replay-device-cloud.yaml` dogfood scenario runs against an inner config
  with a `zz-fanout-android` target. The web UI and Device Farm pages describe the flow, in both
  languages.
- [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) (dispatch fix) — A manual run against a
  local `make serve` left every fan-out job queued. Such a job named the config's directory on the
  server's disk. The queue thus pinned the job with `machine:<id>` (BE-0449). A Device Farm worker
  refuses that pin. The server now zips that directory into its object store. The archive leaves
  out what a Device Farm package leaves out. Each job carries the archive as its bundle. The bundle
  holds the config as `bajutsu.config.yaml`. A scenario's fixtures, components, and binary travel
  with it. `POST /api/run-set` refuses the request when the server has no object store. It also refuses
  a config path the archive cannot carry.
- [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) (packaging fix) — A worker-built package
  ran on real Device Farm devices for the first time. Bajutsu did not install there. The package took `pyproject.toml`,
  `tests/`, and `bajutsu/` from the source tree (BE-0448). Hatchling refuses to build without the
  readme and license files `pyproject.toml` names. The package now carries `DESIGN.md`, `LICENSE`,
  and `NOTICE` too.
- [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) (application fix) — With Bajutsu
  installed, the on-device run stopped at `app.path not found`. The run installs the binary that
  `app.path` names, as a local run does. The worker had left that binary out of the package, since it
  also goes up as the app upload (BE-0448). Device Farm installs that upload before the run starts,
  so the package now carries a `devicefarm-host` worker file, and every run takes it as
  `--worker-config`. Its device provider reports the device booted with the app installed. The run
  launches that build and installs nothing, on Android and iOS alike. An iOS real device refused an
  `app.path` outright, so this also lets an iOS target fan out.
- [#2215](https://github.com/bajutsu-e2e/bajutsu/pull/2215) (iOS runner fix) — The first iOS run on
  a real Device Farm iPhone stopped before Bajutsu started: the iPhone refused the runner
  (`0xe8008012`), whose profile did not list it. An `APPIUM_PYTHON` run delivers the runner with the
  signature it was built with, and the run reserves whichever iPhone is free. The worker now submits
  an iOS job as an `XCTEST_UI` run, whose test package Device Farm re-signs for the reserved device.
  The package carries the runner app at its root and Bajutsu under `bajutsu-package/`. The test spec
  copies the re-signed runner to the path `driver.runner.testRunner` names. Android keeps
  `APPIUM_PYTHON`.

## References

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) — the serve-driven fan-out and `POST /api/run-set`.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) — the device-cloud worker and its `environment:` routing token.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) — the environment as a choice per run rather than a target property.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) — the queue and the no-worker notice.
- [`docs/devicefarm.md`](../../docs/devicefarm.md) — dispatching from serve.
- [`docs/web-ui.md`](../../docs/web-ui.md) — the Replay tab.

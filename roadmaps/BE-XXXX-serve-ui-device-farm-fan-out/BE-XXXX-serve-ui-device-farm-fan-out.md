**English** · [日本語](BE-XXXX-serve-ui-device-farm-fan-out-ja.md)

# BE-XXXX — Dispatch a Device Farm fan-out from the serve web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-ui-device-farm-fan-out.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
item adds one read route, and every other change stays inside the web UI.

### Listing the available environments

A new read route, `GET /api/environments`, returns the environments the web UI may offer:

```json
{ "environments": [ { "name": "devicefarm", "liveWorkers": 2 } ] }
```

The server derives the list from the workers table that capability routing already keeps. A new
repository method reads the `environment:<name>` tokens of every worker seen within the lease
timeout. The no-worker notice judges liveness by the same window (`lacks_live_worker`). The method
reads the capability column, never a job's spec. The route keeps a name when `run-set` accepts it,
meaning the name belongs to `BATCH_PROVIDERS`. An `appium` worker thus never shows up, since
`run-set` refuses a fan-out to a grid. A server without a queue has no workers table, so the route
returns an empty list there. That answer matches the `run-set` refusal on a server without a queue.

The list carries provider names and counts. It never carries an Amazon Resource Name (ARN) or a
credential: those values live in each worker's own environment and never reach the server. The route
sits behind the same authentication as the other read routes. The response leaves `local` out. The
web UI always offers `local`, since the plain `POST /api/run` path serves it.

### The Replay tab in a device-cloud environment

The Run form gains an **Environment** selector next to Target. `local` stays the default, and
choosing it keeps today's form unchanged. The web UI fills the selector from `GET /api/environments`
when the tab opens, and again when the operator presses a refresh button next to the selector.

Choosing a device-cloud environment changes the form in three ways:

1. **Scenarios.** The single-scenario select becomes a multi-select over the target's scenarios,
   with an *All scenarios* option. *All scenarios* omits `scenarios` from the request, so the server
   resolves the directory itself, as it does today.
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
job is done. When the operator expands a row to read its log, the panel opens that job's event
stream, the one the single-run view uses. The number of open connections thus stays independent of
the number of scenarios. A **Stop all** button cancels every job still queued or running through the existing cancel route.
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

- **No change to dispatch.** `POST /api/run-set`, its refusals, the worker, and the Device Farm
  client stay as they are.
- **No device filters.** The fan-out keeps reserving one device per run by platform. Choosing a
  model or an operating system version stays out of scope.
- **No new environment kind.** The selector lists what `BATCH_PROVIDERS` holds. Today that set
  holds `devicefarm` alone.

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
   the `GET /api/environments` route that filters them through `BATCH_PROVIDERS`. Cover a live
   worker, an expired worker, an `appium` worker, and a server without a queue in tests.
2. **Replay form.** Add the Environment selector and its refresh button, the multi-select with
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

- [ ] Unit 1 — Environment listing (`GET /api/environments`)
- [ ] Unit 2 — Replay form in a device-cloud environment
- [ ] Unit 3 — Run-set panel
- [ ] Unit 4 — Tests and documentation

## References

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) — the serve-driven fan-out and `POST /api/run-set`.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) — the device-cloud worker and its `environment:` routing token.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) — the environment as a choice per run rather than a target property.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) — the queue and the no-worker notice.
- [`docs/devicefarm.md`](../../docs/devicefarm.md) — dispatching from serve.
- [`docs/web-ui.md`](../../docs/web-ui.md) — the Replay tab.

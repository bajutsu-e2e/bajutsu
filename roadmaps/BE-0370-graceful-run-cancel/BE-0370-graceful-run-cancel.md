**English** · [日本語](BE-0370-graceful-run-cancel-ja.md)

# BE-0370 — Finish a cancelled run gracefully so it is recorded as a failed run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0370](BE-0370-graceful-run-cancel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0370") |
| Topic | Verification & coverage |
| Related | [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md), [BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The `serve` Web UI lets an operator cancel a run job — a `bajutsu run` suite — while it is still
in progress. Today that cancellation kills the underlying process outright, before it ever writes a
verdict, so the cancelled run leaves no trace in the run history: it disappears as if it had never
started. This item makes cancellation cooperative instead of abrupt: the run pipeline notices the
cancel request at a safe boundary, closes out every scenario that did not finish as failed with
reason `"cancelled"`, and writes the same `manifest.json`, `report.html`, and database record an
ordinary failing run would — so a run cancelled once it has begun executing scenarios always lands
in history as a failed run, never as a silent gap.

## Motivation

The Web UI's Cancel button sets `Job.cancelled` and sends `SIGTERM` to the run process's whole
process group (`cancel_job` at [`jobs.py:99`](../../bajutsu/serve/jobs.py), which signals via
[`jobs.py:79`](../../bajutsu/serve/jobs.py)). Python's default response to `SIGTERM` is to end the
process at once, with none of its own cleanup code running. The `bajutsu run` subprocess therefore
dies wherever it happens to be — usually mid-scenario — long before `pipeline.py`'s
`run_and_report` calls `_assemble_report` to write `manifest.json`, and long before the process
prints its final `PASS/FAIL  runs/<id>/manifest.json` line.

That final line is load-bearing. `jobs.py`'s `_persist_run` — the function that writes a run's
`RunRecord` to the serve database — learns the run's id by matching that exact line (`_RUN_ID_RE`
at [`jobs.py:39`](../../bajutsu/serve/jobs.py)). When the job never produced one, `_persist_run`
returns immediately and persists nothing ([`jobs.py:279`](../../bajutsu/serve/jobs.py)). A run
cancelled mid-flight therefore leaves no `manifest.json`, no `report.html`, and no database row at
all — it is absent from the run history list, from the flakiness ranking, and from the triage view
([BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md)), exactly as if it had never run.

An operator who cancels a stuck or slow-running suite loses the record of having tried it. Nothing
in the history distinguishes "this scenario has never been run" from "this scenario was started and
then cancelled" — a distinction that matters when the same operator, or a teammate, later asks
whether a scenario has coverage at all. Recording the cancelled attempt as a failed run closes that
gap: it costs the operator nothing they were not already choosing (a cancelled run is not a pass),
and it gives every downstream consumer of run history — the dashboard, the flakiness ranking, an
audit of what ran overnight — one consistent answer instead of a silent hole.

## Detailed design

Cancellation becomes a cooperative shutdown that the runner completes on its own terms, bounded by a
short grace period, rather than a `SIGTERM` that ends the process wherever it stands.

- **Catch the signal, don't die from it.** `bajutsu run`'s entry point installs a `SIGTERM` handler
  that sets a `threading.Event` instead of falling back to Python's default disposition (immediate
  termination).
- **Signal the run process alone first, not its process group.** `serve/jobs.py` spawns every job —
  run, record, and crawl alike — in its own session (`start_new_session=True`); today's cancel path
  relies on that grouping so `_terminate`'s group-wide `killpg` reaps a `record` job's
  authoring-agent children (`claude -p`) instead of orphaning them
  ([`jobs.py:71`](../../bajutsu/serve/jobs.py), [`jobs.py:417`](../../bajutsu/serve/jobs.py)). For a
  `run` job the same grouping also happens to hold the backend driver a scenario is actuating
  through — a Playwright-launched browser, an `xcodebuild test` process — so killing the group would
  kill that driver out from under an in-flight scenario, crashing it before the next safe boundary:
  exactly the abrupt failure this item removes. The cancel path for a `run` job therefore signals
  only the leading process first (`proc.send_signal`), not the group; the driver keeps running until
  the runner itself, noticing the event at its next boundary, tears it down through its own ordinary
  end-of-scenario teardown. Once the leader has exited — cleanly, or via the grace-period escalation
  below — the cancel path still sweeps the group with `killpg`, so a driver child that outlived that
  teardown is reaped rather than left orphaned on the serve host. Routing a cancel to the right one
  of those two paths needs a discriminator `Job` does not carry today: it has no job-kind field
  ([`state.py:54`](../../bajutsu/serve/state.py)), and `job.proc` holds *either* the on-demand build
  subprocess or the spawned run ([`state.py:71`](../../bajutsu/serve/state.py),
  [`jobs.py:192`](../../bajutsu/serve/jobs.py)) — so this item adds one, and the leader-only path
  applies only while `job.proc` is the run itself. `_terminate`'s group-wide `killpg` stays exactly as
  it is today for `record`/`crawl` jobs and for a `run` job's build phase, which this item leaves
  unaffected.
- **Check the event at safe boundaries.** The event is read at the top of each scenario in
  `run_all`'s dispatch loop ([`pipeline.py:822`](../../bajutsu/runner/pipeline.py)), between steps
  within a scenario, and inside the poll loops that already back every condition wait. A scenario
  waiting on a condition notices cancellation within one polling tick; one blocked inside a single
  driver call — an `xcuitest` HTTP request, an `adb` `subprocess.run`, a Playwright call — notices
  only once that call returns, so the grace period below must exceed the longest such call for the
  cooperative path to win rather than escalate. No new wait is introduced; an existing bounded poll
  loop gains one more exit condition.
- **A cancelled scenario is a failed scenario.** A scenario interrupted this way, or one that never
  started because the event was already set, becomes a `RunResult(ok=False,
  failure="cancelled", ...)` — the same shape `pipeline.py` already builds for a crash or a preflight
  failure ([`pipeline.py:271,281,313,545`](../../bajutsu/runner/pipeline.py)). `run_all` still
  returns exactly one result per scenario, in declaration order, so nothing downstream needs to know
  that cancellation happened at all. An operator who cancels early in a long suite therefore sees
  every scenario that never got to run counted as a failure too, in the report and in the flakiness
  ranking alike — an accepted consequence of treating a cancelled run as failed at all, not a
  special exemption for the scenarios cancellation reached before they started.
- **Downstream of `run_all`, nothing changes.** `run_and_report` receives a complete, ordinary-looking
  `results` list, so `_assemble_report` writes `manifest.json`, `report.html`, and the JUnit XML
  exactly as it does for any other run; `manifest["ok"]` is already `False`
  ([`manifest.py:145`](../../bajutsu/report/manifest.py)) because at least one scenario carries
  `ok=False`. `_finish` ([`run.py:746`](../../bajutsu/cli/commands/run.py)) prints the `FAIL
  <manifest>` line and exits 1 exactly as it would for any other failed run, so `_RUN_ID_RE` matches
  and `_persist_run` records the run precisely as it would any other failure — with no changes
  needed to the database write path itself.
- **The shutdown stays bounded — with or without `serve` watching.** The `SIGTERM` handler lives in
  `bajutsu run`'s entry point, not behind any `serve`-only gate, so it answers every `SIGTERM` the
  process receives — a `docker stop`, a systemd unit stop, a CI job cancellation — not only the one
  `cancel_job` sends. `serve`'s side of the bound is external: after sending the signal, a grace
  window runs to completion — and, past its deadline, escalates to today's unconditional, group-wide
  `killpg` — off the request thread, the same guarantee `record`'s human handoff already gives
  ([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)). It has to: `POST
  /api/jobs/{job_id}/cancel` calls `cancel_job` synchronously
  ([`routes.py:492`](../../bajutsu/serve/routes.py),
  [`reads.py:636`](../../bajutsu/serve/operations/reads.py)), and that window must exceed the longest
  driver call (the previous bullet) — tens of seconds for an `xcuitest` HTTP timeout or an
  `xcodebuild test` — so blocking the response for it would leave the Cancel button unacknowledged
  long enough that an operator clicks it again. `cancel_job` therefore still returns as soon as the
  signal is sent, exactly as it does today; the grace window and its escalation run on a separate
  timer, alongside the job thread that already owns `proc.wait()`
  ([`jobs.py:445`](../../bajutsu/serve/jobs.py)). A `run` invoked
  outside `serve` has no such external escalator watching it, so the handler enforces the same bound
  on itself: a second, internal timer started when the event is set that — if the graceful shutdown
  has not finished by its own deadline — restores `SIGTERM`'s default disposition and re-raises it,
  so a genuinely wedged runner dies exactly as it would have without this item, instead of outliving
  the signal indefinitely. The two deadlines must not be picked independently: were the handler's
  internal one shorter than `serve`'s own grace window, the run would kill itself before
  `_assemble_report` ever wrote a manifest, reproducing the exact silent-gap failure this item removes
  — for every ordinary `serve` cancel too, since `serve`'s longer window could never rescue a run that
  already killed itself. `cancel_job` therefore passes its own grace window to the spawned run (an
  environment value on the job spec) and the handler binds its internal deadline strictly beyond the
  value it receives, rather than an independently chosen constant; a `run` invoked outside `serve`,
  which receives no such value, falls back to a fixed default long enough to clear the longest driver
  call on its own. Either way, a cancel request is delayed by at most one grace period beyond today's
  immediate kill.
- **Scope: `run` jobs only.** This covers `run` jobs and the per-engine passes of a cross-browser
  matrix — `run_matrix_and_report` calls `run_all` once per engine, and each pass returns the same
  `RunResult` shape. Its engine loop ([`pipeline.py:938`](../../bajutsu/runner/pipeline.py)) checks
  the event too, before starting the next pass: each pass first builds a whole `device_pool` —
  resolving the environment, reading the device catalog, starting the per-device collectors
  ([`run.py:653`](../../bajutsu/cli/commands/run.py),
  [`pool.py:128`](../../bajutsu/runner/pool.py)) — so without that check a cancel during the first
  engine would still pay every remaining engine's bring-up and teardown before the run could finish,
  overrunning the grace period on a matrix run. `record` and `crawl` jobs have no pass/fail verdict
  to preserve: `record.py`
  authors a scenario file and produces no `RunResult` or manifest at all. Cancelling one of those
  jobs keeps today's behavior unchanged — `Job.cancelled` set, process terminated. Whether a
  cancelled `record`/`crawl` job should be visible in some other way is a separate concern this item
  leaves untouched. This bounds which job *type* gets the cooperative treatment, not which `SIGTERM`
  *sender* triggers it — the handler installed in `bajutsu run`'s entry point (previous bullet)
  answers any sender, not only `serve`'s Cancel button.
- **Before the pipeline starts, cancellation still kills outright.** `_run_job` boots devices and
  builds the app before it ever spawns the run process
  ([`jobs.py:399,401`](../../bajutsu/serve/jobs.py)), and `_register_proc`
  ([`jobs.py:85`](../../bajutsu/serve/jobs.py)) kills a process cancelled before it got a chance to
  register; its caller then only reaps it with `proc.wait()` without ever reading `proc.stdout`, so
  routing that narrow race through the cooperative shutdown would leave the runner blocked writing to
  a full pipe with no reader. Cancelling during device boot, app build, or that brief pre-registration
  window keeps
  today's behavior unchanged: `Job.cancelled` set, process killed outright, nothing persisted. This
  item closes the gap once a run has begun executing scenarios, not the window before it starts one.

This respects the prime directives by construction. The verdict a cancelled run receives still
comes from the deterministic pipeline — a scenario that did not finish is marked failed, never
judged by a large language model (LLM). No new indeterminate wait is introduced; an existing,
already-bounded wait gains one more exit condition. And nothing here is per-app: the signal
handling and the `RunResult` shape are the same for every target.

## Alternatives considered

- **Fabricate a FAIL `RunRecord` in `serve/jobs.py` when a cancelled job never produced a run id.**
  Rejected. The database row would say "failed" with none of the evidence the Replay and triage views
  depend on — no `manifest.json`, `report.html`, screenshots, or per-step evidence — so the record
  would be a label with nothing to click through to. It would also do nothing for `bajutsu run`
  executed directly outside `serve`, since the fabrication would live in the Web UI's job layer, not
  in the runner.
- **Interrupt a scenario immediately, mid-step, instead of at a step or poll boundary.** Rejected.
  Stopping partway through an actuation, or while a network exchange is being written to evidence,
  could leave a driver session or a captured artifact in an inconsistent state — in tension with
  determinism-first (prime directive 2 bars a fixed sleep, and by the same logic bars cutting off an
  in-flight actuation at an arbitrary point). Waiting for the current step, poll tick, or driver call
  to finish — at most one polling interval or one driver call, not the scenario's own timeout — stops
  the run exactly where the pipeline already tolerates a pause.
- **Model cancellation as a third run-level verdict, distinct from pass and fail.** Rejected for this
  item. Counting a cancelled run against the suite matches how an operator reads the history: a
  cancelled run is not a success. `failure: "cancelled"` already gives the report and the Web UI
  everything they need to show a distinct label or icon from an assertion failure, without a new
  top-level verdict rippling through the database schema, the flakiness ranking
  ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)), and
  every report renderer that assumes `ok: bool`.
- **Also catch `SIGINT`, so a bare `bajutsu run` cancelled with Ctrl-C at a terminal gets the same
  treatment.** Out of scope here — this item wires only `SIGTERM`, which already reaches every
  external cancellation path (`serve`'s Cancel button, `docker stop`, systemd, a CI job
  cancellation) — but the mechanism is the same signal-to-event bridge, so a follow-up item can wire
  `SIGINT` to the identical `threading.Event` with no new design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `bajutsu/cancellation.py` — the whole cancellation vocabulary in one module that imports
  nothing from Bajutsu, so the deterministic core, the command line, and `serve` can all reach it.
  It holds the `CancelSource` the runner polls, the `RunCancelled` exception a poll loop raises, the
  `"cancelled"` failure spelling, and the `graceful_sigterm()` context manager `bajutsu run`'s entry
  point installs. The handler answers every sender, and it absorbs a second `SIGTERM` during the
  window rather than escalating on it, so an operator clicking Cancel twice does not lose the
  manifest.
- [x] Two fields on `Job` rather than one job-kind field, the second option the design allowed by
  asking for a job-kind field or any signal that answers the same question. `graceful_cancel` carries the dispatcher's declaration that this job's spawn answers a cancel
  cooperatively (it travels in the job spec, so a worker registers its spawn the same way), while
  `proc_graceful` carries the live fact of whether the subprocess registered right now is that spawn.
  The pair is what separates a `run` job's own on-demand build phase, which keeps today's kill, from
  the run itself.
- [x] `cancel_job` signals the leading process alone through `_request_graceful_stop` while
  `proc_graceful` holds, and `_run_job` sweeps the process group with `killpg` once the leader has
  exited. `_pgid_of` reads that group while the leader is still alive, since a reaped pid can find a
  new owner before the sweep runs. `record` / `crawl` / triage jobs and the build phase keep
  `_terminate`'s group-wide kill unchanged.
- [x] The pipeline reads the event at the top of `_ScenarioRunner.run_one`, at each step boundary
  in
  `_StepRunner.exec_steps`, in all six of `waits.py`'s poll loops (`for`, `gone`, `request`,
  `screenChanged`, and both settle paths), in `_poll_asserts`, which a step-level `assert` polls on,
  and in `_do_email`'s mailbox poll — a third polling step kind, and the one whose budget the scenario
  sets itself, so a wait for a one-time password can run well past the grace window. Every check sits *after* its own condition check, so a wait already satisfied on that poll still
  passes. `_settle_extract_read` stays out of that list by choice: it settles a read rather than
  deciding a condition, and the same wait floor already bounds it.
- [x] `RunCancelled` unwinds to `run_scenario`, which records `failure: "cancelled"` — one exit line
  per poll loop instead of a new return shape, and the sink teardown the unwind passes through still
  finalizes the scenario's intervals. `run_one` fails a scenario that never started before ever
  attempting its first lease. The trailing scenario-level `expect` runs to completion by choice, so a
  scenario whose every step passed keeps its real verdict rather than a cancellation label.
- [x] `_request_graceful_stop` starts a daemon `threading.Timer` for the grace window, and `POST
  /api/jobs/{job_id}/cancel` still returns the moment the signal goes out. Past the deadline
  `_escalate` sends the group-wide SIGTERM, gives the group a brief window to unwind on it, and then
  ends the run with SIGKILL. The design called that escalation "today's unconditional, group-wide
  `killpg`", and the SIGKILL is what keeps it unconditional now that the run answers SIGTERM
  cooperatively: the handler absorbs a second signal by design, and a run wedged past executing
  Python never runs the handler at all, so it would answer neither SIGTERM — leaving the operator
  with a job that the Web UI cannot end, which is worse than today. The group-wide SIGTERM keeps its
  place ahead of the SIGKILL because the leader never reached its own teardown, so the driver
  children it would have stopped are still live, and a driver killed outright can leave a Simulator
  wedged for the next run. The escalation first checks that the process is still running, so a run
  that closed itself out inside the window survives the timer's late tick.
- [x] The handler's internal deadline is `handler_deadline(grace_seconds())` — the received window
  plus a fixed margin. Setting the event arms it with `signal.setitimer`, and a `SIGALRM` handler
  enforces it by restoring `SIGTERM`'s default disposition and re-raising it. An interval timer
  rather than a second thread, because restoring a disposition is legal on the main thread alone,
  which is where a Python signal handler runs. `_spawn_env` passes `BAJUTSU_CANCEL_GRACE` down to a
  `graceful_cancel` job's spawn; a `run` invoked outside `serve` receives no value and falls back to
  the 60-second default, wide enough to clear the longest single driver call (the XCUITest channel's
  30-second actuation timeout, or a read riding the BE-0207 retry inside its 60-second recovery
  timeout).
- [x] `run_matrix_and_report` reads the event between engine passes and fails every scenario of the
  engines that never ran, rather than leaving those engines out of the report. Leaving them out is a
  hazard the design did not foresee: an engine missing from the results takes its scenarios' verdicts
  with it, so the manifest's all-must-pass `ok` aggregates the passes that ran and nothing more — and
  a cancel landing once a green first engine has finished would then record a `PASS` for a run that
  never executed most of the matrix, this item's own silent-gap failure inverted. Synthesizing one
  cancelled result per skipped engine x scenario keeps the aggregation pure and the matrix complete: a
  cell reading `cancelled` states plainly that the run named that axis and never executed it. The loop
  logs which engines it failed that way.
- [x] Confirmed by test: a cooperatively cancelled run still prints its `FAIL
  runs/<id>/manifest.json` line, so `run_job` parses `job.run_id` from it and `_persist_run` records
  the run with `ok=False`. No change to the database write path.

### What the implementation settled

- **A cancel that lands during a lease bring-up is noticed only once that lease returns.** The
  cancellation event reaches the step loop and its condition waits, not the device bring-up beneath
  them, so a cancel arriving during an XCUITest cold spawn waits that spawn out before the scenario's
  first boundary fails it. The bound is the lease's own readiness ceiling, and past the grace window
  `serve`'s escalation applies regardless — the same residual the design already accepts for a cancel
  arriving before the pipeline starts a scenario at all.
- **The backend-crash retry loop reads the event too.** A retry leases afresh, and on XCUITest that
  bring-up is a cold respawn with a forced erase on top — long enough to outlive the grace window a
  canceller is waiting out, so the escalation would end the run before it wrote its manifest. So a
  cancelled run stops recovering and fails the crashed scenario at once, keeping every verdict it had
  already reached, and the failure names the cancel rather than a budget that was nowhere near spent.
  The design's own boundary list does not mention the crash path, which reaches `lease()` from inside
  `run_one` rather than from the dispatch loop the list names.
- **A cancelled scenario records only the steps it completed.** The interrupted step raises before
  the step loop appends its outcome, so the report shows the steps that finished rather than one it
  would otherwise have to render as attempted-but-unknown.

## References

- [BE-0179 — Human-in-the-loop handoff during record (pause / hand off / resume)](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)
  — the "bounded and cancelable" precedent this item's grace period follows.
- [BE-0147 — Triage failed runs in the serve Web UI](../BE-0147-serve-triage/BE-0147-serve-triage.md)
  — the failure investigator that a cancelled run cannot reach today, for lack of a `manifest.json`.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — the run-level `ok` verdict and the flakiness ranking this item keeps working unchanged.

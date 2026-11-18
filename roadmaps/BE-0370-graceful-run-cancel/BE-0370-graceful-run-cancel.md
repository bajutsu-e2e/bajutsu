**English** · [日本語](BE-0370-graceful-run-cancel-ja.md)

# BE-0370 — Finish a cancelled run gracefully so it is recorded as a failed run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0370](BE-0370-graceful-run-cancel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0370") |
| Implementing PR | [#1667](https://github.com/bajutsu-e2e/bajutsu/pull/1667) |
| Topic | Verification & coverage |
| Related | [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md), [BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The `serve` Web UI lets an operator cancel a run job (a `bajutsu run` suite) while it is still in
progress. Today the cancellation kills the underlying process outright, before it writes a verdict.
The cancelled run then leaves no trace in the run history, as if it had never started.

This item makes cancellation cooperative instead of abrupt. The run pipeline notices the cancel
request at a safe boundary. It closes out every [scenario](../../docs/glossary.md#scenario-authoring)
that did not finish as failed with reason `"cancelled"`. It then writes the same `manifest.json`,
`report.html`, and database record an ordinary failing run would write. A run cancelled after it
began executing scenarios always lands in history as a failed run, never as a silent gap.

## Motivation

The Web UI's Cancel button sets `Job.cancelled` and sends `SIGTERM` to the whole process group of the
run process. `cancel_job` at [`jobs.py:99`](../../bajutsu/serve/jobs.py) does this, signaling via
[`jobs.py:79`](../../bajutsu/serve/jobs.py). Python's default response to `SIGTERM` is to end the
process at once, with none of its own cleanup code running.

The `bajutsu run` subprocess dies wherever it happens to be, usually mid-scenario. It dies
long before `run_and_report` in `pipeline.py` calls `_assemble_report` to write `manifest.json`. It
also dies long before the process prints its final `PASS/FAIL  runs/<id>/manifest.json` line.

That final line is load-bearing. `_persist_run` in `jobs.py` writes a run's `RunRecord` to the serve
database. It learns the run's id by matching that exact line (`_RUN_ID_RE` at
[`jobs.py:39`](../../bajutsu/serve/jobs.py)). When the job never produced the line, `_persist_run`
returns immediately and persists nothing ([`jobs.py:279`](../../bajutsu/serve/jobs.py)).

A run cancelled mid-flight leaves no `manifest.json`, no `report.html`, and no database
row. The run is absent from the run history list, from the flakiness ranking, and from the triage
view ([BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md)). It looks as if it had
never run.

An operator who cancels a stuck or slow-running suite loses the record of having tried it. The
history cannot distinguish "this scenario has never been run" from "this scenario was started and
then cancelled". That distinction matters when the same operator, or a teammate, later asks whether a
scenario has coverage at all.

Recording the cancelled attempt as a failed run closes that gap. It costs the operator nothing they
were not already choosing, because a cancelled run is not a pass. And every downstream consumer of
run history gets one consistent answer instead of a silent hole. Those consumers include the
dashboard, the flakiness ranking, and an audit of what ran overnight.

## Detailed design

Cancellation becomes a cooperative shutdown that the runner completes on its own terms, bounded by a
short grace period. It replaces a `SIGTERM` that ends the process wherever it stands.

- **Catch the signal, don't die from it.** The entry point of `bajutsu run` installs a `SIGTERM`
  handler. The handler sets a `threading.Event`. It does not fall back to Python's default
  disposition, which is immediate termination.
- **Signal the run process alone first, not its process group.** `serve/jobs.py` spawns every job
  in its own session (`start_new_session=True`). That covers run, record, and crawl jobs alike.
  - Today's cancel path relies on that grouping. The group-wide `killpg` in `_terminate` reaps the
    authoring-agent children of a `record` job (`claude -p`). Without it, they would be orphaned
    ([`jobs.py:71`](../../bajutsu/serve/jobs.py), [`jobs.py:417`](../../bajutsu/serve/jobs.py)).
  - For a `run` job the same group also holds the backend driver. Examples are a Playwright-launched
    browser and an `xcodebuild test` process. A scenario actuates through that driver.
  - Killing the group would kill that driver out from under an in-flight scenario. The
    scenario would crash before the next safe boundary. That is the abrupt failure this item
    removes.
  - The cancel path for a `run` job signals only the leading process first (`proc.send_signal`),
    not the group. The driver keeps running until the runner itself notices the event at its next
    boundary. The runner then tears the driver down through its own ordinary end-of-scenario
    teardown.
  - Once the leader has exited, the cancel path still sweeps the group with `killpg`. The exit may
    be clean, or it may come from the grace-period escalation below. A driver child that outlived the
    teardown is reaped, not left orphaned on the serve host.
  - Routing a cancel to the right path needs a discriminator that `Job` does not carry today. `Job`
    has no job-kind field ([`state.py:54`](../../bajutsu/serve/state.py)). And `job.proc` holds
    *either* the on-demand build subprocess or the spawned run
    ([`state.py:71`](../../bajutsu/serve/state.py), [`jobs.py:192`](../../bajutsu/serve/jobs.py)).
    This item adds a discriminator. The leader-only path applies only while `job.proc` is the run
    itself.
  - The group-wide `killpg` in `_terminate` stays as it is today for `record` and `crawl`
    jobs, and for the build phase of a `run` job. This item leaves those unaffected.
- **Check the event at safe boundaries.** The runner reads the event at three places. The first is
  the top of each scenario in the dispatch loop of `run_all`
  ([`pipeline.py:822`](../../bajutsu/runner/pipeline.py)). The second is between steps within a
  scenario. The third is inside the poll loops that already back every condition wait.
  - A scenario waiting on a condition notices cancellation within one polling tick.
  - A scenario blocked inside a single driver call notices only once that call returns. Such calls
    are an `xcuitest` HTTP request, an `adb` `subprocess.run`, or a Playwright call.
  - The grace period below must exceed the longest such call. Otherwise the escalation
    wins instead of the cooperative path.
  - No new wait is introduced. An existing bounded poll loop gains one more exit condition.
- **A cancelled scenario is a failed scenario.** A scenario interrupted this way becomes a
  `RunResult(ok=False, failure="cancelled", ...)`. So does a scenario that never started because the
  event was already set. `pipeline.py` already builds the same shape for a crash or a preflight
  failure ([`pipeline.py:271,281,313,545`](../../bajutsu/runner/pipeline.py)).
  - `run_all` still returns exactly one result per scenario, in declaration order. Nothing
    downstream needs to know that cancellation happened.
  - An operator who cancels early in a long suite sees every unrun scenario counted as a failure.
    The count appears in the report and in the flakiness ranking alike.
  - That is an accepted consequence of treating a cancelled run as failed at all. It is not a
    special exemption for the scenarios that cancellation reached before they started.
- **Downstream of `run_all`, nothing changes.** `run_and_report` receives a complete, ordinary-looking
  `results` list. `_assemble_report` writes `manifest.json`, `report.html`, and the JUnit
  XML as it does for any other run.
  - `manifest["ok"]` is already `False` ([`manifest.py:145`](../../bajutsu/report/manifest.py)),
    because at least one scenario carries `ok=False`.
  - `_finish` ([`run.py:746`](../../bajutsu/cli/commands/run.py)) prints the `FAIL <manifest>` line
    and exits 1, as it would for any other failed run.
  - `_RUN_ID_RE` then matches, and `_persist_run` records the run as it would any other failure.
    The database write path needs no change.
- **The shutdown stays bounded, with or without `serve` watching.** The `SIGTERM` handler lives in
  the entry point of `bajutsu run`, not behind a `serve` gate. It answers every `SIGTERM` the process
  receives. Examples are a `docker stop`, a systemd unit stop, and a CI job cancellation. The
  signal from `cancel_job` is one sender among them.
  - **The bound on the `serve` side is external.** After sending the signal, `serve` runs a grace
    window off the request thread. Past the deadline, the window escalates to today's unconditional,
    group-wide `killpg`. The human handoff in `record` already gives the same guarantee
    ([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)).
  - **The window cannot block the request.** `POST /api/jobs/{job_id}/cancel` calls `cancel_job`
    synchronously ([`routes.py:492`](../../bajutsu/serve/routes.py),
    [`reads.py:636`](../../bajutsu/serve/operations/reads.py)). The window must exceed the longest
    driver call (the previous bullet), which takes tens of seconds for an `xcuitest` HTTP timeout
    or an `xcodebuild test`. Blocking the response that long would leave the Cancel button
    unacknowledged, and an operator would click it again.
  - **`cancel_job` still returns as soon as the signal is sent**, as it does today. The grace
    window and its escalation run on a separate timer, alongside the job thread that already owns
    `proc.wait()` ([`jobs.py:445`](../../bajutsu/serve/jobs.py)).
  - **A `run` invoked outside `serve` has no external escalator.** The handler enforces the
    same bound on itself. It starts a second, internal timer when the event is set. If the graceful
    shutdown has not finished by that deadline, the timer restores the default disposition of
    `SIGTERM` and re-raises the signal. A genuinely wedged runner then dies as it would
    have without this item. It does not outlive the signal indefinitely.
  - **The two deadlines must not be picked independently.** Suppose the internal deadline of the
    handler were shorter than the grace window of `serve`. The run would kill itself before
    `_assemble_report` wrote a manifest. That reproduces the silent-gap failure this item removes.
    It would do so for every ordinary `serve` cancel too. The longer window of `serve` could never
    rescue a run that had already killed itself.
  - **The handler derives its deadline from the grace window it receives.** `cancel_job` passes its
    own grace window to the spawned run. The value travels as an environment value on the job spec.
    The handler binds its internal deadline strictly beyond that value. It uses no independently
    chosen constant.
  - **A `run` outside `serve` falls back to a fixed default.** It receives no such value. The default
    is long enough to clear the longest driver call on its own.
  - Either way, a cancel request is delayed by at most one grace period beyond today's immediate
    kill.
- **Scope: `run` jobs only.** This covers `run` jobs and the per-engine passes of a cross-browser
  matrix. `run_matrix_and_report` calls `run_all` once per engine, and each pass returns the same
  `RunResult` shape.
  - The engine loop ([`pipeline.py:938`](../../bajutsu/runner/pipeline.py)) checks the event too,
    before starting the next pass.
  - Each pass first builds a whole `device_pool`. It resolves the environment, reads the device
    catalog, and starts the per-device collectors
    ([`run.py:653`](../../bajutsu/cli/commands/run.py), [`pool.py:128`](../../bajutsu/runner/pool.py)).
  - Without the check, a cancel during the first engine would still pay the bring-up and teardown of
    every remaining engine. The run could not finish before then. A matrix run would overrun the
    grace period.
  - `record` and `crawl` jobs have no pass/fail verdict to preserve. `record.py` authors a scenario
    file and produces no `RunResult` or manifest at all.
  - Cancelling one of those jobs keeps today's behavior: `Job.cancelled` is set and the process is
    terminated. Whether a cancelled `record` or `crawl` job should be visible some other way is a
    separate concern, which this item leaves untouched.
  - The scope bounds which job *type* gets the cooperative treatment. It does not bound which
    `SIGTERM` *sender* triggers it. The handler in the entry point of `bajutsu run` (previous
    bullet) answers any sender, not only the Cancel button of `serve`.
- **Before the pipeline starts, cancellation still kills outright.** `_run_job` boots devices and
  builds the app before it spawns the run process ([`jobs.py:399,401`](../../bajutsu/serve/jobs.py)).
  `_register_proc` ([`jobs.py:85`](../../bajutsu/serve/jobs.py)) kills any process cancelled before
  it could register.
  - The caller of `_register_proc` only reaps that process with `proc.wait()`. It never reads
    `proc.stdout`.
  - Routing that narrow race through the cooperative shutdown would leave the runner blocked writing
    to a full pipe with no reader.
  - A cancel during device boot, app build, or that brief pre-registration window keeps today's
    behavior. `Job.cancelled` is set. The process is killed outright. Nothing is persisted.
  - This item closes the gap once a run has begun executing scenarios. It does not close the window
    before a run starts one.

This design respects the prime directives by construction. The verdict a cancelled run receives still
comes from the deterministic pipeline. A scenario that did not finish is marked failed, never judged
by a large language model (LLM). No new indeterminate wait is introduced. An existing, already-bounded
wait gains one more exit condition. And nothing here is per-app: the signal handling and the
`RunResult` shape are the same for every target.

## Alternatives considered

- **Fabricate a FAIL `RunRecord` in `serve/jobs.py` when a cancelled job never produced a run id.**
  Rejected.
  - The database row would say "failed" without the evidence that the Replay and triage views
    depend on. It would have no `manifest.json`, `report.html`, screenshots, or per-step evidence.
    The record would be a label with nothing to click through to.
  - The fabrication would live in the job layer of the Web UI, not in the runner. It would also do
    nothing for a `bajutsu run` executed directly outside `serve`.
- **Interrupt a scenario immediately, mid-step, instead of at a step or poll boundary.** Rejected.
  - Stopping partway through an actuation could leave a driver session inconsistent. So could
    stopping while a network exchange is written to evidence. A captured artifact could be left
    inconsistent too.
  - That conflicts with determinism-first. Prime directive 2 bars a fixed sleep, and by the same
    logic it bars cutting off an in-flight actuation at an arbitrary point.
  - Waiting for the current step, poll tick, or driver call to finish stops the run where the
    pipeline already tolerates a pause. The wait lasts at most one polling interval or one driver
    call. It never lasts as long as the scenario's own timeout.
- **Model cancellation as a third run-level verdict, distinct from pass and fail.** Rejected for this
  item.
  - Counting a cancelled run against the suite matches how an operator reads the history: a
    cancelled run is not a success.
  - `failure: "cancelled"` already lets the report and the Web UI show a label or icon distinct
    from an assertion failure.
  - A new top-level verdict would ripple through three places. One is the database schema. Another
    is the flakiness ranking
    ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).
    The third is every report renderer that assumes `ok: bool`.
- **Also catch `SIGINT`, so a bare `bajutsu run` cancelled with Ctrl-C at a terminal gets the same
  treatment.** Out of scope here.
  - This item wires only `SIGTERM`. It already reaches every external cancellation path: the Cancel
    button of `serve`, `docker stop`, systemd, and a CI job cancellation.
  - The mechanism is the same signal-to-event bridge. A follow-up item can wire `SIGINT` to the
    identical `threading.Event` with no new design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `bajutsu/cancellation.py` holds the whole cancellation vocabulary in one module. The module
  imports nothing from Bajutsu, so the deterministic core, the command line, and `serve` can all
  reach it. The module holds four names:
  - `CancelSource`, which the runner polls
  - `RunCancelled`, the exception a poll loop raises
  - the `"cancelled"` failure spelling
  - `graceful_sigterm()`, the context manager that the entry point of `bajutsu run` installs

  The handler answers every sender. It absorbs a second `SIGTERM` during the window instead of
  escalating on it. An operator who clicks Cancel twice thus does not lose the manifest.
- [x] `Job` gains two fields rather than one job-kind field. The design allowed either a job-kind
  field or any signal that answers the same question. `graceful_cancel` carries the declaration of
  the dispatcher that the spawn of this job answers a cancel cooperatively. The declaration travels
  in the job spec, so a worker registers its spawn the same way. `proc_graceful` carries the live
  fact of whether the subprocess registered right now is that spawn. The pair separates two phases.
  One is the on-demand build phase of a `run` job, which keeps today's kill. The other is the run
  itself.
- [x] `cancel_job` signals the leading process alone through `_request_graceful_stop` while
  `proc_graceful` holds. `_run_job` sweeps the process group with `killpg` once the leader has
  exited. `_pgid_of` reads that group while the leader is still alive, because a reaped pid can find
  a new owner before the sweep runs. `record`, `crawl`, and triage jobs keep the group-wide kill of
  `_terminate` unchanged, and so does the build phase.
- [x] The pipeline reads the event at these places:
  - the top of `_ScenarioRunner.run_one`
  - each step boundary in `_StepRunner.exec_steps`
  - all six poll loops of `waits.py` (`for`, `gone`, `request`, `screenChanged`, and both settle
    paths)
  - `_poll_asserts`, which a step-level `assert` polls on
  - the mailbox poll of `_do_email`

  The `email` step is a third polling step kind. The scenario sets its budget itself, so a wait for a
  one-time password can run well past the grace window. Every check sits *after* its own condition
  check, so a wait already satisfied on that poll still passes. `_settle_extract_read` stays out of
  the list by choice. It settles a read rather than deciding a condition, and the same wait floor
  already bounds it.
- [x] `RunCancelled` unwinds to `run_scenario`, which records `failure: "cancelled"`. Each poll loop
  needs one exit line instead of a new return shape. The sink teardown that the unwind passes through
  still finalizes the scenario's intervals. `run_one` fails a scenario that never started before it
  attempts its first lease. The trailing scenario-level `expect` runs to completion by choice. A
  scenario whose every step passed thus keeps its real verdict rather than a cancellation label.
- [x] `_request_graceful_stop` starts a daemon `threading.Timer` for the grace window. `POST
  /api/jobs/{job_id}/cancel` still returns the moment the signal goes out. Past the deadline,
  `_escalate` sends the group-wide SIGTERM and gives the group a brief window to unwind on it. It
  then ends the run with SIGKILL.
  - The design called that escalation "today's unconditional, group-wide `killpg`". The SIGKILL keeps
    it unconditional now that the run answers SIGTERM cooperatively.
  - The handler absorbs a second signal by design. A run wedged past executing Python never runs the
    handler at all, so it would answer neither SIGTERM. The operator would be left with a job that
    the Web UI cannot end, which is worse than today.
  - The group-wide SIGTERM keeps its place ahead of the SIGKILL. The leader never reached its own
    teardown, so the driver children it would have stopped are still live. A driver killed outright
    can leave a Simulator wedged for the next run.
  - The escalation first checks that the process is still running. A run that closed itself out
    inside the window thus survives the late tick of the timer.
- [x] The internal deadline of the handler is `handler_deadline(grace_seconds())`: the received
  window plus a fixed margin. Setting the event arms the deadline with `signal.setitimer`. A
  `SIGALRM` handler enforces it by restoring the default disposition of `SIGTERM` and re-raising it.
  - The design uses an interval timer rather than a second thread. Restoring a disposition is legal
    on the main thread alone, and the main thread is where a Python signal handler runs.
  - `_spawn_env` passes `BAJUTSU_CANCEL_GRACE` down to the spawn of a `graceful_cancel` job. A `run`
    invoked outside `serve` receives no value and falls back to the 90-second default.
  - Two spans have to fit inside the window, not one.
    - The first is the longest single driver call. It is either the 30-second actuation timeout of
      the XCUITest channel, or a read riding the BE-0207 retry inside its 60-second recovery
      timeout.
    - The second is the shutdown tail behind that call. The runner fails the remaining scenarios,
      assembles the report, and prints the verdict line from which `serve` reads the run id.
  - A window equal to the call alone would leave the tail nothing. The escalation would then end a
    run blocked in a 60-second read with its manifest still unwritten.
- [x] `run_matrix_and_report` reads the event between engine passes. It fails every scenario of the
  engines that never ran, rather than leaving those engines out of the report.
  - Leaving them out is a hazard the design did not foresee. An engine missing from the results takes
    its scenarios' verdicts with it. The all-must-pass `ok` of the manifest then aggregates the
    passes that ran and nothing more.
  - A cancel landing after a green first engine would record a `PASS` for a run that never ran most
    of the matrix. That is the silent-gap failure of this item, inverted.
  - The loop synthesizes one cancelled result per skipped engine and scenario. The aggregation stays
    pure and the matrix stays complete. A cell reading `cancelled` states plainly that the run named
    that axis and never executed it.
  - The loop logs which engines it failed that way.
- [x] Confirmed by test: a cooperatively cancelled run still prints its `FAIL
  runs/<id>/manifest.json` line. `run_job` parses `job.run_id` from the line, and
  `_persist_run` records the run with `ok=False`. The database write path does not change.

### What the implementation settled

- **A cancel that lands during a lease bring-up goes unnoticed until that lease returns.** The
  cancellation event reaches the step loop and its condition waits. It does not reach the device
  bring-up beneath them. A cancel that arrives during an XCUITest cold spawn waits that
  spawn out before the first boundary of the scenario fails it. The bound is the readiness ceiling of
  the lease itself. Past the grace window, the escalation of `serve` applies regardless. The design
  already accepts the same residual for a cancel that arrives before the pipeline starts a scenario.
- **The Web UI needed wiring the design did not list.** `serve.panels.mjs` short-circuited on
  `j.cancelled` before it reached `setReport`. It did so in both the run pane of the Replay tab and
  the in-place run of the Record tab. The report that a cancelled run now writes would have stayed
  invisible on the one surface where the Cancel button lives.
  - Both handlers now fall through instead. The run keeps its `cancelled` label. It also gets its
    report, its `open full report` link, and the triage bar.
  - The design read "no further changes" for `_persist_run` and the history summary as meaning that
    nothing downstream had to move. That held for the run record but not for the pane beside the log.
- **The backend-crash retry loop reads the event too.** A retry leases afresh. On XCUITest the
  bring-up is a cold respawn with a forced erase on top. It lasts long enough to outlive the grace
  window that a canceller is waiting out, so the escalation would end the run before it wrote its
  manifest.
  - A cancelled run stops recovering and fails the crashed scenario at once. It keeps every
    verdict it had already reached.
  - The failure names the cancel rather than a budget that was nowhere near spent.
  - The boundary list of the design does not mention the crash path. That path reaches `lease()` from
    inside `run_one`, not from the dispatch loop that the list names.
- **A cancelled scenario records only the steps it completed.** The interrupted step raises before the
  step loop appends its outcome. The report thus shows the steps that finished. Otherwise it would
  have to render one step as attempted-but-unknown.

## References

- [BE-0179 — Human-in-the-loop handoff during record (pause / hand off / resume)](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)
  — the "bounded and cancelable" precedent this item's grace period follows.
- [BE-0147 — Triage failed runs in the serve Web UI](../BE-0147-serve-triage/BE-0147-serve-triage.md)
  — the failure investigator that a cancelled run cannot reach today, for lack of a `manifest.json`.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — the run-level `ok` verdict and the flakiness ranking this item keeps working unchanged.

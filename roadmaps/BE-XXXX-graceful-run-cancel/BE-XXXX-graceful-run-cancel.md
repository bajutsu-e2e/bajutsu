**English** · [日本語](BE-XXXX-graceful-run-cancel-ja.md)

# BE-XXXX — Finish a cancelled run gracefully so it is recorded as a failed run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-graceful-run-cancel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
ordinary failing run would — so a cancelled run always lands in history as a failed run, never as a
silent gap.

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
- **Signal the run process alone, not its process group.** `serve/jobs.py` spawns a run job in its
  own session (`start_new_session=True`) precisely so `_terminate`'s `killpg` can stop its children
  too — the backend driver a scenario is actuating through, such as a Playwright-launched browser or
  an `xcodebuild test` process. Killing that driver out from under an in-flight scenario would crash
  it before the next safe boundary, which is exactly the abrupt failure this item removes. The
  cancel path for a `run` job therefore signals only the leading process (`proc.send_signal`), not
  the group; the driver keeps running until the runner itself, noticing the event at its next
  boundary, tears it down through its own ordinary end-of-scenario teardown. `_terminate`'s
  group-wide `killpg` stays exactly as it is today for `record`/`crawl` jobs (unaffected by this
  item) and for the grace-period escalation below.
- **Check the event at safe boundaries.** The event is read at the top of each scenario in
  `run_all`'s dispatch loop ([`pipeline.py:822`](../../bajutsu/runner/pipeline.py)) and inside the
  poll loops that already back every condition wait, so a scenario mid-step notices cancellation
  within one polling tick instead of riding out its own timeout. No new wait is introduced; an
  existing bounded poll loop gains one more exit condition.
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
- **The shutdown stays bounded.** A cancel request must resolve in bounded time, the same guarantee
  `record`'s human handoff already gives ([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)).
  `cancel_job` keeps a short grace window after the signal; if the process has not exited by that
  deadline — a genuinely wedged runner, not a slow scenario — it escalates to today's unconditional,
  group-wide `killpg`, so in the worst case a cancel request is delayed by at most the grace period
  beyond today's immediate kill.
- **Scope: `run` jobs only.** This covers `run` jobs and the per-engine passes of a cross-browser
  matrix — `run_matrix_and_report` calls `run_all` once per engine, and each pass returns the same
  `RunResult` shape. `record` and `crawl` jobs have no pass/fail verdict to preserve: `record.py`
  authors a scenario file and produces no `RunResult` or manifest at all. Cancelling one of those
  jobs keeps today's behavior unchanged — `Job.cancelled` set, process terminated. Whether a
  cancelled `record`/`crawl` job should be visible in some other way is a separate concern this item
  leaves untouched.

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
  in-flight actuation at an arbitrary point). Waiting for the current step or poll tick to finish —
  at most one polling interval, not the step's own timeout — stops the run exactly where the pipeline
  already tolerates a pause.
- **Model cancellation as a third run-level verdict, distinct from pass and fail.** Rejected for this
  item. Counting a cancelled run against the suite matches how an operator reads the history: a
  cancelled run is not a success. `failure: "cancelled"` already gives the report and the Web UI
  everything they need to show a distinct label or icon from an assertion failure, without a new
  top-level verdict rippling through the database schema, the flakiness ranking
  ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)), and
  every report renderer that assumes `ok: bool`.
- **Also catch `SIGINT`, so a bare `bajutsu run` cancelled with Ctrl-C gets the same treatment.** Out
  of scope here — this item is the Web UI's Cancel button specifically — but the mechanism is the
  same signal-to-event bridge, so a follow-up item can wire `SIGINT` to the identical
  `threading.Event` with no new design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Install a `SIGTERM` handler in `bajutsu run`'s entry point that sets a `threading.Event`
  instead of letting the process die immediately.
- [ ] Change a `run` job's cancel path to signal the run process alone (not its process group),
  reserving the group-wide `killpg` for the grace-period escalation.
- [ ] Check that event at the top of each scenario in `run_all`'s dispatch loop and inside the
  existing condition-wait poll loops.
- [ ] Synthesize `RunResult(ok=False, failure="cancelled")` for a scenario interrupted this way or
  one that never started.
- [ ] Add a bounded grace period to `serve/jobs.py`'s `cancel_job`, escalating to today's
  unconditional kill only past the deadline.
- [ ] Confirm `_persist_run` and the run-history summary read the resulting `manifest.json`
  correctly with no further changes.

## References

- [BE-0179 — Human-in-the-loop handoff during record (pause / hand off / resume)](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)
  — the "bounded and cancelable" precedent this item's grace period follows.
- [BE-0147 — Triage failed runs in the serve Web UI](../BE-0147-serve-triage/BE-0147-serve-triage.md)
  — the failure investigator that a cancelled run cannot reach today, for lack of a `manifest.json`.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — the run-level `ok` verdict and the flakiness ranking this item keeps working unchanged.

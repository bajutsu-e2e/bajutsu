**English** · [日本語](BE-XXXX-job-endpoint-org-scoping-ja.md)

# BE-XXXX — Scope the job endpoints to the caller's org

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-job-endpoint-org-scoping.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) |
<!-- /BE-METADATA -->

## Introduction

Four endpoints of a hosted `bajutsu serve` resolve a job by its id alone, with no check that the job
belongs to the caller's org. A signed-in member of one tenant can read another tenant's job, cancel
its run, answer its handoff prompt, and stream its live log — needing nothing but the job id. This
item closes that by scoping all four to the org the caller is currently acting as, which is the rule
every neighbouring read already applies.

## Motivation

Multi-tenancy in serve is enforced per operation, not by a single gate. Each read resolves the
caller's org and scopes its own store access: `list_scenarios`, `read_scenario`, and the artifact
routes all pass through `state.org_of(actor)`, and `runs_payload` through the `state.org_for(actor,
machine_org)` below, before touching anything
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). The job endpoints are
the exception. They read `state.jobs.get(job_id)` and go straight to work:

| Endpoint | Operation | Org check today |
|---|---|---|
| `GET /api/jobs/{job_id}` | `job_view` | machine principal only |
| `POST /api/jobs/{job_id}/cancel` | `cancel_job` | none |
| `POST /api/jobs/{job_id}/respond-human` | `respond_human` | none |
| `GET /api/jobs/{job_id}/events` | `job_sse` / `job_log_events` | none |

The role gate does not cover the gap. `required_role` marks the two writes editor-only, so a viewer
is refused — but it answers "is this caller an editor", never "is this caller an editor *of the org
that owns this job*". An editor of any tenant clears it for every tenant's jobs.

[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) unit 3 closed the
first row for a CI machine principal, because its endpoint allowlist opens the job poll and a
pipeline had to be confined to the org its token was exchanged for. It deliberately left the human
path alone: narrowing that is a tenant-boundary change of its own, and widening a machine-session
item to carry it would have mixed two decisions in one change. This item is that follow-up.

The exposure is bounded by the job id, which is opaque and unguessable, so this is a confidentiality
and integrity gap rather than an open door. It still reaches further than a read: cancelling another
tenant's run destroys work in progress, and answering its handoff prompt injects input into a run
that tenant is watching.

**Verifiable outcome.** A member of org A, holding the id of a job dispatched by org B, is refused
on all four endpoints while acting as org A — and the refusal is a 404, not a 403, so the reply does
not confirm that the id names a real job. The same member switching to org B reaches the same job
normally, so the boundary follows the org the caller is acting as rather than shutting that member
out permanently.

## Detailed design

The work is mutually exclusive and collectively exhaustive (MECE) across three units: the three
operations the uniform route table serves, the streaming route each backend serves with its own
plumbing, and the tests plus documentation.

### The rule: the org the caller is acting as

Every one of the four endpoints compares the job's own org against `state.org_for(actor,
machine_org)` — the resolver BE-0414 unit 3 introduced as the one place a human's org and a machine
session's org converge. A job carries the org it was dispatched for (`Job.org`, stamped at dispatch
by `start_run`, `start_run_set`, `start_record`, `start_crawl`, and `start_triage`, and travelling in the job spec to
a worker), so the comparison needs no new state.

**A caller who switches org loses sight of the job, and gets it back by switching back.** That
follows from the rule rather than working against it, and it is the same behaviour the rest of the
interface already has: the run history, the scenario list, and the target list all re-scope when the
active org changes. A job view that kept answering across a switch would be the odd one out.

The switch is fully reversible, which is what makes the rule safe for the two writes as well as the
two reads. `set_active_org` rewrites `users.org_id` and `users.role` together, so `user_role` always
reports the role held in the org the caller is currently acting as. Dispatching a run in org A,
switching to org B to look at something, switching back to A, and cancelling the run therefore
works: back in A the org comparison passes and the editor check runs against the role held in A.
Visibility is a function of where the caller is now, never of where that caller has been.

**Refuse with 404, not 403.** A job id is opaque, so answering "forbidden" would confirm that this
particular id names a live job in some other tenant — the one fact the refusal exists to withhold.
`job_view` already answers 404 for a machine principal under BE-0414, and the three remaining
endpoints adopt the same answer, matching what each already returns for an id that names no job at
all. The two are then indistinguishable, which is the point.

### Unit 1 — The three uniform operations

`job_view` (`bajutsu/serve/operations/reads.py`) already carries the check, but for a machine
principal alone: it compares `job.org` against `machine_org` and takes no `actor` at all. This
unit gives it the `actor` argument its sibling reads take and compares against
`state.org_for(actor, machine_org)`, so the check runs on the resolved org whoever the caller is.

`cancel_job` takes only `(state, job_id)` today and `respond_human` only `(state, job_id, body)` —
neither takes an actor. Both gain the same `actor` and
`machine_org` keyword arguments the sibling reads take, and the route table
(`bajutsu/serve/routes/_shared.py`) passes `ctx.actor()` to all three — alongside the
`ctx.machine_org()` it already hands `job_view`.

A machine principal reaches neither write: the BE-0414 allowlist names neither path, so
`machine_org` arrives None for them in practice. They take the argument anyway rather than assuming
that: the allowlist is a separate gate that could open a path later, and an operation that resolves
its own org correctly does not depend on which gate admitted the request.

`job_view`'s docstring, which records the narrowing as deliberate and the widening as a separate
change, is rewritten to the rule above.

### Unit 2 — The live log stream

`GET /api/jobs/{job_id}/events` is `off_loop`: each backend serves it with its own streaming
plumbing, the stdlib handler through `_sse_job` → `ops.job_log_events` and the FastAPI app through
`ops.job_sse`. Neither resolves an org, and neither takes an actor.

Both entry points gain the same pair of arguments and the same check, applied **before the first
frame** rather than per frame: the caller's org is fixed for the request, so re-checking it on every
frame would cost a resolution per frame and still not end a stream whose caller switched org
mid-stream. A stream already open when its caller switches org keeps running to the job's end, which
is consistent with the switch taking effect on the next request rather than retroactively.

Refusing before the stream opens lets each backend answer with an ordinary 404 rather than having to
express the refusal inside an event stream a client has already begun reading.

### Unit 3 — Tests and documentation

Covered in the fast Python suite, with no Simulator and no browser:

- A member of org A is refused on all four endpoints for a job dispatched by org B, and the refusal
  is 404 on each.
- That refusal is indistinguishable from the answer for a job id that names nothing at all.
- The same member, switched to org B, reaches the same job on all four.
- A run dispatched in org A is still cancellable after switching to org B and back to A, so the
  boundary is reversible rather than a one-way loss.
- The editor gate still applies within the org: a viewer of org B is refused the two writes on org
  B's own job.
- A machine principal reads only its own org's job, as BE-0414 unit 3 already established, and its
  allowlist still refuses the two writes and the stream.
- A single-tenant deployment — local serve, no database — is unaffected: every caller resolves the
  same `default` org, so all four endpoints behave exactly as before.
- Both backends enforce the stream's check identically.
- The two frontend call sites a refusal would otherwise pass silently — `cancelJob`, which discards
  the response, and `streamJob`, whose `EventSource` has no `error` handler — report it instead, so a
  Stop button in a tab left on the old org says the job is no longer reachable rather than hanging on
  "Stopping…", and a stream refused before its first frame does not leave the panel stuck on
  "running". The handoff reply already reports a non-ok status and needs no change.

`docs/self-hosting.md` and its Japanese mirror gain a short note under the multi-tenancy material:
the job endpoints follow the active org like every other read, and switching org hides a running
job until the caller switches back.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Scope by membership (`eligible_orgs`) rather than the active org | Its one advantage over the active-org rule — it never hides callers from their own jobs — is an advantage over a problem that is already solved: a caller who switches away gets the job back by switching back, and `set_active_org` moves the role with the org, so the editor check is already evaluated against the job's own org. What it adds is a second scoping rule beside the one every neighbouring read uses. A second rule with no remaining problem to solve is cost without benefit. |
| Answer 403 rather than 404 | More honest about why the request failed, and it discloses that the id names a real job in another tenant. The id is the only thing the caller holds, so confirming it is exactly the leak worth avoiding. `job_view` already chose 404 under BE-0414; matching it keeps one answer across the four. |
| Check the org inside `ServeState.jobs` / `JobRegistry` instead of per operation | Appealing, because one guard would cover every caller at once. The registry is a plain id-keyed map that the runner, the worker-result path, and the metrics reader all consult without an actor, so an org-aware lookup there would need an optional actor threaded through every one of them and would silently change what those internal callers see. Serve enforces tenancy per operation everywhere else; this follows that. |
| Re-check the org on every frame of the live stream | Would end a stream whose caller switched org mid-stream. It costs an org resolution per frame, and the switch already takes effect on the caller's next request everywhere else — a stream that died halfway through would be a stricter rule than the rest of the interface applies, not a safer one. |
| Leave the two reads and scope only the writes | Cancelling and answering a handoff are the destructive pair, so the writes matter more. Reading another tenant's job view and its live log still discloses scenario names, step progress, and log output, which is the confidentiality half of the same boundary. Splitting them would leave the item half-done for no saving. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `job_view` widened from a machine principal to every caller, and `cancel_job` /
      `respond_human` given the same `actor` / `machine_org` arguments, the same org comparison, and
      the same 404, wired through the shared route table; `job_view`'s docstring, which records the
      narrowing as deliberate and the widening as a separate change, is rewritten to the rule above.
- [ ] Unit 2 — The live log stream's two entry points (`job_log_events` on the stdlib backend,
      `job_sse` on the FastAPI one) given the same check, applied once before the first frame.
- [ ] Unit 3 — Tests for each endpoint and both backends, including the switch-away-and-back case
      and the single-tenant no-op; `cancelJob` and `streamJob` changed to report a refusal instead of
      discarding it or leaving the stream hanging; plus the self-hosting note in both languages.

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  — the multi-tenancy and role model these endpoints are the exception to.
- [BE-0414 — Authenticate a CI job to serve with a GitHub Actions OIDC token](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)
  — closed `job_view` for a machine principal and introduced `ServeState.org_for`, the resolver this
  item reuses.
- [BE-0375 — Database-backed org lifecycle and membership management for serve](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md)
  — `set_active_org` and the membership roster behind the org a caller is acting as.
- [BE-0370 — Finish a cancelled run gracefully so it is recorded as a failed run](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)
  — what a cancellation does once admitted, which this item only decides who may ask for.

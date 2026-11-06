**English** · [日本語](BE-0425-job-endpoint-org-scoping-ja.md)

# BE-0425 — Scope the job endpoints to the caller's org

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0425](BE-0425-job-endpoint-org-scoping.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0425") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) |
<!-- /BE-METADATA -->

## Introduction

Four endpoints of a hosted `bajutsu serve` resolve a job by its id alone. None checks that the job belongs to the caller's org. A signed-in member of one tenant can read another tenant's job, cancel its run, answer its handoff prompt, and stream its live log, using nothing but the job id. This item closes the gap by scoping all four endpoints to the org the caller is acting as. Every neighbouring read already applies that rule.

## Motivation

Serve enforces multi-tenancy per operation, not through a single gate. Each read resolves the caller's org and scopes its own store access. `list_scenarios`, `read_scenario`, and the artifact routes all pass through `state.org_of(actor)`. `runs_payload` passes through `state.org_for(actor, machine_org)`, described below. Both happen before the read touches anything ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). The job endpoints are the exception. They read `state.jobs.get(job_id)` and go straight to work:

| Endpoint | Operation | Org check today |
|---|---|---|
| `GET /api/jobs/{job_id}` | `job_view` | machine principal only |
| `POST /api/jobs/{job_id}/cancel` | `cancel_job` | none |
| `POST /api/jobs/{job_id}/respond-human` | `respond_human` | none |
| `GET /api/jobs/{job_id}/events` | `job_sse` / `job_log_events` | none |

The role gate does not cover the gap. `required_role` marks the two writes editor-only, so a viewer is refused. That gate answers "is this caller an editor", never "is this caller an editor *of the org that owns this job*". An editor of any tenant clears it for every tenant's jobs.

[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) unit 3 closed the first row for a CI machine principal. Its endpoint allowlist opens the job poll, and a pipeline had to stay confined to the org its token was exchanged for. BE-0414 deliberately left the human path alone. Narrowing that path is a tenant-boundary change of its own, and widening a machine-session item to carry it would have mixed two decisions in one change. This item is that follow-up.

The exposure does not require a caller who already knows an id. A job id is the decimal form of a single counter shared by every org. `JobRegistry._register` assigns `str(self._seq)`, and the counter is seeded across the whole jobs table at startup. Ids therefore run "1", "2", "3", and an editor of any tenant reaches every other tenant's jobs by walking the range. The gap is an open door, not a lock that needs a secret. Beyond the door lies more than disclosure. Cancelling another tenant's run destroys work in progress. Answering its handoff prompt injects input into a run that the tenant is watching.

**Verifiable outcome.** Take an **editor** of org A who holds the id of a job dispatched by org B. While acting as org A, that editor is refused on all four endpoints. The refusal is a 404, not a 403, so the reply does not confirm that the id names a real job. A viewer is refused the two writes by the role gate ahead of this check, with a 403, which is unchanged. The same member switching to org B reaches the same job normally. The boundary follows the org the caller is acting as, and does not shut that member out permanently.

## Detailed design

The work is mutually exclusive and collectively exhaustive (MECE) across three units:

- the three operations the uniform route table serves
- the streaming route each backend serves with its own plumbing
- the tests and documentation

### The rule: the org the caller is acting as

Each of the four endpoints compares the job's own org against `state.org_for(actor, machine_org)`. BE-0414 unit 3 introduced that resolver as the one place where a human's org and a machine session's org converge. A job carries the org it was dispatched for (`Job.org`). The dispatchers `start_run`, `start_run_set`, `start_record`, `start_crawl`, and `start_triage` stamp it, and it travels in the job spec to a worker. The comparison therefore needs no new state.

**A caller who switches org loses sight of the job, and gets it back by switching back.** This follows from the rule. It is also the behaviour the rest of the interface already has: the run history, the scenario list, and the target list all re-scope when the active org changes. A job view that kept answering across a switch would be the odd one out.

The switch is fully reversible, which makes the rule safe for the two writes as well as the two reads. `set_active_org` rewrites `users.org_id` and `users.role` together. `user_role` therefore always reports the role held in the org the caller is acting as. Consider an editor who dispatches a run in org A, switches to org B to look at something, switches back to A, and cancels the run. The cancel works, because back in A the org comparison passes and the editor check runs against the role held in A. Visibility is a function of where the caller is now, never of where that caller has been.

**Refuse with 404, not 403.** The reason is not that 404 withholds much. While ids are sequential, an enumerating caller learns which ids are live either way. The shared counter already leaks cross-tenant volume: a caller's own ids jumping from 41 to 58 discloses how many jobs other tenants dispatched in between. 404 is chosen for three reasons:

- It costs nothing.
- It matches what each of the four endpoints already returns for an id that names no job.
- It matches the answer `job_view` adopted for a machine principal under BE-0414.

Nothing here argues for making job ids unguessable. An id is not a credential, and a design whose safety rested on keeping one secret would be the defect rather than the fix. The tenant check is the whole of what is missing.

### Unit 1 — The three uniform operations

`job_view` (`bajutsu/serve/operations/reads.py`) already carries the check, but for a machine principal alone. It compares `job.org` against `machine_org` and takes no `actor` at all. This unit gives `job_view` the `actor` argument its sibling reads take. It then compares against `state.org_for(actor, machine_org)`, so the check runs on the resolved org whoever the caller is.

`cancel_job` takes just `(state, job_id)` today, and `respond_human` takes just `(state, job_id, body)`. Neither takes an actor. Both gain the same `actor` and `machine_org` keyword arguments the sibling reads take. The route table (`bajutsu/serve/routes/_shared.py`) passes both `ctx.actor()` and `ctx.machine_org()` to all three operations. Today `job_view` receives the latter alone.

A machine principal reaches neither write. The BE-0414 allowlist names neither path, so `machine_org` arrives None for them in practice. The operations take the argument anyway, because the allowlist is a separate gate that could open a path later. An operation that resolves its own org correctly does not depend on which gate admitted the request.

`job_view`'s docstring records the narrowing as deliberate and the widening as a separate change. This unit rewrites that docstring to state the rule above.

### Unit 2 — The live log stream

`GET /api/jobs/{job_id}/events` is `off_loop`. Each backend serves it with its own streaming plumbing: the stdlib handler through `_sse_job` → `ops.job_log_events`, and the FastAPI app through `ops.job_sse`. Neither entry point resolves an org, and neither takes an actor.

Both entry points gain the same pair of arguments and the same check. The check runs **before the first frame**, not per frame. The caller's org is fixed for the request. Re-checking on every frame would cost a resolution per frame and still would not end a stream whose caller switched org mid-stream. A stream already open when its caller switches org keeps running to the job's end. This is consistent with the switch taking effect on the next request, not retroactively.

Revocation is the case that this equivalence does not cover. `delete_org` drops every member's session at once. An admin may retire a tenant while someone holds an open stream on one of its long-running jobs. That stream then keeps delivering log lines until the job ends, because the once-only check never re-consults the session. This item accepts that bound and does not close it. Ending a live stream on revocation is a session-lifecycle change, and it belongs with the revocation path, not with the tenant check.

Refusing before the stream opens lets each backend answer with an ordinary 404. Otherwise the refusal would have to be expressed inside an event stream that a client has already begun reading.

### Unit 3 — Tests and documentation

The fast Python suite covers the following, with no Simulator and no browser:

- An editor of org A is refused on all four endpoints for a job dispatched by org B, and the refusal is 404 on each. A viewer is refused the two writes by the role gate first, with a 403. That is the existing behaviour, asserted so the two gates stay distinguishable.
- The refusal is indistinguishable from the answer for a job id that names nothing at all.
- The same member, switched to org B, reaches the same job on all four endpoints.
- A run dispatched in org A is still cancellable after switching to org B and back to A. The boundary is reversible, not a one-way loss.
- The editor gate still applies within the org: a viewer of org B is refused the two writes on org B's own job.
- A machine principal reads its own org's job alone, as BE-0414 unit 3 already established. Its allowlist still refuses the two writes and the stream.
- A single-tenant deployment (local serve, no database) is unaffected. Every caller resolves the same `default` org, so all four endpoints behave exactly as before.
- Both backends enforce the stream's check identically.
- Two frontend call sites would otherwise pass a refusal silently. `cancelJob` discards the response, and `streamJob` has an `EventSource` with no `error` handler. Both now report the refusal. A Stop button in a tab left on the old org says the job is no longer reachable, instead of hanging on "Stopping…". A stream refused before its first frame does not leave the panel stuck on "running". The handoff reply already reports a non-ok status and needs no change.

`docs/self-hosting.md` and its Japanese mirror gain a short note under the multi-tenancy material. The note says that the job endpoints follow the active org like every other read, and that switching org hides a running job until the caller switches back.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Scope by membership (`eligible_orgs`) rather than the active org | Its one advantage over the active-org rule is that it never hides callers from their own jobs. That problem is already solved: a caller who switches away gets the job back by switching back, and `set_active_org` moves the role with the org, so the editor check already runs against the job's own org. What membership scoping adds is a second scoping rule beside the one every neighbouring read uses. A second rule with no remaining problem to solve is cost without benefit. |
| Answer 403 rather than 404 | A 403 is more honest about why the request failed, but it discloses that the id names a real job in another tenant. The id is all the caller holds, so confirming it is exactly the leak worth avoiding. `job_view` already chose 404 under BE-0414, and matching it keeps one answer across the four endpoints. |
| Check the org inside `ServeState.jobs` / `JobRegistry` instead of per operation | This is appealing, because one guard would cover every caller at once. The registry already knows about orgs: `in_flight_by_org` aggregates by org for `/metrics`. But the registry is keyed by id, and the runner, the worker-result path, and the metrics reader all consult it without an actor. An org-aware lookup there would need an optional actor threaded through every one of them, and it would silently change what those internal callers see. Serve enforces tenancy per operation everywhere else, and this item follows that. |
| Re-check the org on every frame of the live stream | This would end a stream whose caller switched org mid-stream. It costs an org resolution per frame, and the switch already takes effect on the caller's next request everywhere else. A stream that died halfway through would be a stricter rule than the rest of the interface applies, not a safer one. |
| Make job ids unguessable instead of adding the tenant check | An opaque id would stop the walk described in Motivation, but it would be the wrong repair. An id is an addressing handle, not a credential. Substituting secrecy for authorization leaves the endpoints willing to serve any caller who obtains an id, through a log, a shared link, or a browser history. A configuration whose safety depends on callers not guessing an identifier is the thing worth fixing. Motivation names sequential ids to show that no secrecy bounds the exposure today. It does not propose adding some. |
| Leave the two reads and scope the writes alone | Cancelling and answering a handoff are the destructive pair, so the writes matter more. But reading another tenant's job view and its live log still discloses scenario names, step progress, and log output. That is the confidentiality half of the same boundary. Splitting the two would leave the item half-done for no saving. |

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

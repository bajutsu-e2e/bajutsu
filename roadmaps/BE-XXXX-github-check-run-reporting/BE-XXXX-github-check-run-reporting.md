**English** · [日本語](BE-XXXX-github-check-run-reporting-ja.md)

# BE-XXXX — Report a CI-dispatched run set to its pull request as a GitHub check run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-github-check-run-reporting.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md), [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md), [BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) |
<!-- /BE-METADATA -->

## Introduction

A GitHub Actions job can already dispatch runs to a self-hosted `bajutsu serve`. It authenticates
with its OpenID Connect (OIDC) token
([BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)), uploads its
build, and starts runs against it
([BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override.md)). The
pull request (PR) learns nothing from serve, though. The job must either poll serve until every run
finishes, holding an Actions runner the whole time, or fire and forget and leave the PR blind.

This item makes serve report back as an external continuous-integration (CI) system. One dispatch
request that names a set of scenarios and a commit becomes **one GitHub check run** on that commit.
Serve creates the check run as `queued` before any job enters the queue. The check run moves to
`in_progress` when a worker leases the first job, and to `completed` once every job has finished.
Its conclusion aggregates the jobs' deterministic verdicts. Serve writes the check run through the
GitHub App the deployment already signs people in with
([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)). Serve reaches that App
through the App credential setting that
[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)
introduced for the private-repository config source.
To carry a whole set in one request, the existing `POST /api/run-set` endpoint grows beyond its
cloud-batch origin ([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md))
to the ordinary worker queue.

## Motivation

A self-hosted deployment runs E2E tests on its own Mac and Linux workers, away from the Actions
runner that built the app. Today the verdict of those tests reaches a PR only through the Actions
job itself. `POST /api/run` answers with a job id, and the pipeline must poll `GET /api/jobs/{id}`
for every scenario until each finishes. An Actions runner sits idle through a queue wait and a
Simulator run, and GitHub bills a hosted runner for that idle time. A pipeline that skips the wait
leaves the PR with no signal at all, so a branch protection rule has nothing to require.

GitHub's answer for an external system is the Checks API. A check run is attached to a commit,
carries a status and a conclusion, and can be named in a branch protection rule as a required check.
Only a GitHub App may create one. A deployment that signs people in with a GitHub App already has one
registered, and serve already mints installation tokens from an App credential (BE-0224). Serve also
already knows every state a check run needs, because the jobs table records `queued`, `leased`,
`done`, and `failed` for every job
([BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)). The missing
piece is the transport from one to the other.

The unit of a check run decides whether that transport can be correct. A check run per target looks
natural but cannot know when it is done. `POST /api/run` takes a single scenario, so a pipeline
dispatches one request per scenario. Serve never learns how many requests belong to a target, and so
it cannot tell whether the third finished scenario is the last one. A check run per scenario has the
opposite problem. A branch protection rule must then list every scenario, and adding a scenario means
editing that rule. One check run per dispatch request avoids both. The request fixes its member
scenarios at dispatch, so completion is decidable. The pipeline names the check run once, so the
branch protection rule names it once.

The reporting stays outside the verdict, the same boundary
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) drew for
webhook notifications. The roadmap's *Not adopting* list places notification in "the domain of the
CI / notification layer". This item does not move Bajutsu into that layer. Serve merely delivers the
verdict it already computed, to the one place the CI layer reads it. No large language model (LLM)
takes part, and a delivery failure never changes a job's verdict.

**Verifiable outcome.** An Actions workflow with `permissions: id-token: write` and no repository
secret calls `POST /api/run-set` with three scenarios and the PR's head commit, then exits. The PR's
Checks tab shows `Bajutsu / <target>` as queued at once. The check run turns in progress within about 30 seconds of a
worker leasing the first job, and completes with `failure` when one of the three scenarios fails. The
check run reaches `completed` even when the control plane restarts mid-run. A branch protection rule
that requires `Bajutsu / <target>` blocks the merge until then.

## Detailed design

The work splits into five units, which are mutually exclusive and collectively exhaustive (MECE):
dispatching a set to the worker queue, recording the set, deriving the check run's state, delivering
that state to GitHub, and the tests plus documentation.

### Unit 1 — `POST /api/run-set` dispatches to the worker queue

`start_run_set` in `bajutsu/serve/operations/dispatch.py` serves cloud-batch targets alone today. It
refuses a target without `cloudBatch`, and it refuses the BE-0431 artifact override fields. This unit
keeps the cloud-batch branch unchanged and adds a second branch for every other target:

| Target | Each member scenario becomes | Artifact overrides |
|---|---|---|
| `cloudBatch` set | a cloud-batch job, as today | refused, as today |
| `cloudBatch` unset | the job `start_run` builds for one scenario | accepted, as `start_run` accepts them |

The worker branch reuses `start_run`'s job construction per scenario rather than copying it. The
existing all-before-any validation stays: every scenario resolves to a runnable before the first job
registers, so an unknown name refuses the whole request. A request naming `scenariosArtifact` must also list `scenarios`, or serve refuses it with 400. Omitting `scenarios` enumerates the bound store, which would silently leave out a scenario that exists only in the uploaded tree.

A machine principal gains the endpoint. `("POST", "/api/run-set")` joins `_MACHINE_PATHS` in
`bajutsu/serve/gate.py`, and `start_run_set` takes the same `machine_org` argument `start_run` takes.

### Unit 2 — A check-bearing request records its set before dispatch

A request opts in with a `check` object, and a request without one behaves exactly as today:

```json
{
  "target": "ios-app",
  "scenarios": ["login.yaml", "checkout.yaml", "search.yaml"],
  "binaryArtifact": "<sha256>",
  "check": { "headSha": "<40 hex characters>", "name": "Bajutsu / ios-app" }
}
```

- **`headSha`** is required. The workflow passes `github.event.pull_request.head.sha`. For a
  `pull_request` event `GITHUB_SHA` names a merge commit, and a check run on that commit never shows
  on the PR. Serve checks the value's shape and does not attest its origin. The repository bound
  below keeps a wrong value inside the caller's own repository, and *Out of scope* names the risk
  that remains there.
- **`name`** defaults to `Bajutsu / <target>`. A pipeline that splits one target across an Actions
  matrix names each part. Serve enforces that distinction instead of assuming it. Among open sets,
  the `check_runs` row is unique on (repository, head SHA, name), through a partial unique index, and
  a second check-bearing request on the same triple is refused with 409 until the first set
  concludes. A re-run after the set concluded records a new row. A row stays open until GitHub confirms `completed` on it, or until the row closes after repeated refused sends (Unit 4). A row that still has no check run id once it is older than the lease timeout, because the control plane stopped before creation returned, has nothing to conclude, so the sweep deletes it and frees the triple.

**The repository comes from the machine session, never from the body.** BE-0414 mints a machine
session whose identity names the repository in the verified OIDC `repository` claim. Serve writes
the check run to that repository alone. A body field naming the repository would let one workflow
write checks on another repository the App can reach. A human session carries no repository, so a
human request with `check` is refused with 400. A request with `check` on a `cloudBatch` target is refused with 400 as well: a machine session exists only on a database-backed serve, and cloud-batch jobs do not yet run from its worker queue (`_run_batch_job` in `bajutsu/serve/jobs.py`). A request with `check` on a SQLite-backed serve is refused with 400 too: SQLite takes no row lock, and Unit 4 takes its claim under one. The refusal sits in the dispatch handler alone; the state derivation and the sweep run on any dialect, which is how Unit 5 tests them on the SQLite gate.

Serve records the set in a new `check_runs` table before any job registers. The row holds the org,
the repository, the head SHA, the name, and the GitHub check run id. It also holds each member as a scenario name paired with its job id, still empty at this point. The row carries the state the later units need as well: an open or closed flag, which is the predicate of the partial unique index; the state GitHub last confirmed; the time of the next attempt; a claim expiry (Unit 4); the count of consecutive client-error refusals, which survives a restart and resets on a success; a creation timestamp; and the time the dispatch last recorded a job id (Unit 3). An Alembic revision under
`bajutsu/serve/server/migrations/versions/` creates the table.

**A check-bearing set dispatches whole or not at all.** `start_run_set` stops part-way today when
the concurrency cap rejects a job, and it returns the jobs dispatched so far. A check run over such a
partial set would conclude without the missing scenarios. Nothing retries them, because the pipeline
has already exited. A request carrying `check` therefore registers every member at once, or none.
`try_register` in `bajutsu/serve/state/job_registry.py` already counts and inserts under one lock. A
set-sized variant counts the whole set against each cap under that same lock. When the set does not
fit, serve deletes the row and answers 429 before it creates any check run, and the pipeline may
retry. A set larger than a cap can never fit, because every member holds a slot from registration
on, even while it waits in the queue. Serve deletes the row and refuses such a set with 400 naming
the cap and the size the set needs, so a pipeline does not retry a request that cannot succeed.

Today that ceiling is low. The global cap (`--max-concurrent-runs`) defaults to 4, and it counts
queued jobs as well as running ones, so a set of five scenarios never fits under the default. The
cap means two things at once here: how many jobs may wait in the queue, and how many may run at the
same time. On a database-backed deployment the number of workers already bounds the second, because each worker leases one job at a time, so only the first binds. Separating the two, with a queue-depth cap at admission and the concurrency cap
enforced when a worker leases a job, is left to
[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md), which plans
holding an over-cap job instead of rejecting it. Until that lands, an operator who wants larger sets
raises `--max-concurrent-runs`, and `BAJUTSU_MAX_CONCURRENT_PER_ORG` where it is set, to at least
the largest set.

The dispatch order follows from that rule. Serve records the row, registers the whole set, creates
the check run (Unit 4), and then enqueues the jobs. A crash part-way through the enqueue leaves a
row whose members lack job ids, and Unit 3 concludes such a row.

### Unit 3 — The check run's state derives from its members' jobs

Serve derives the check run's state from its members, and nothing else sets it:

| Members | Check run `status` | `conclusion` |
|---|---|---|
| a member still has no job id after the grace period | `completed` | `failure` |
| every member queued or not yet enqueued | `queued` | — |
| any job leased or finished, some member unfinished | `in_progress` | — |
| every member's job finished, each run passed | `completed` | `success` |
| every member's job finished, a run failed | `completed` | `failure` |
| every member's job finished, a job failed without a run | `completed` | `failure` |

The first row that matches decides the state. A member not yet enqueued counts as unfinished. The enqueue loop runs within one dispatch request,
so a member that still has no job id once the dispatch has recorded no new job id for the lease timeout (120 seconds by default) means the control plane stopped part-way through that loop. The clock starts when the row is created and restarts each time the request records a job id, so a slow but healthy dispatch of a large set never trips it. Serve concludes the row as
`failure` and names the unenqueued members in the summary. It neither re-enqueues them nor waits for
them. The pipeline can dispatch the set again, and the 409 rule admits it once this row has
concluded. Jobs already enqueued keep running, and their verdicts stay visible in the reports.

The job-failed-without-a-run row covers a job that ended in the `failed` status, such as one past its
lease-attempt cap. Today the jobs table cannot tell that job from a failed scenario: `worker_result`
in `bajutsu/serve/operations/worker.py` records a failed scenario through `fail_job`, which writes
the same `failed` status a reclaim past the cap writes, and which keeps only the error and drops the
run id. This unit therefore keeps the worker's `runId` on a failed job's row, so serve finds each
member's run and verdict from its job. The summary names that cause separately from a scenario failure, since each calls for a different
fix. Every input to the table is a stored job status, a stored run verdict, or a stored timestamp (the row's creation or its last recorded job id), so the
derivation is deterministic and involves no LLM.

Serve stores no derived state. Each delivery derives the state afresh from the member jobs' stored
rows, so a restart loses nothing that a later derivation cannot recover. One job transition needs a
hook all the same: after `worker_result` commits a result, it claims that set's row directly, the same way a sweep claims a row and without the general due-walk, and sends the row's state. Without it, a pool that drains after its last result, such as an on-demand Mac that exits once its queue empties, sees no further lease poll or heartbeat, and the `completed` update waits for the periodic task in Unit 4. One rule keeps the status from moving backward. A job that
`reclaim_expired_leases` returns to the queue would make a set derive `queued` again, so serve never
sends a status earlier than the one GitHub last confirmed.

A job still queued because no worker advertises its capabilities keeps its check run `queued`. The
unroutable-job signal BE-0166 already raises is the operator's cue. A timeout that concludes such a check run is left for later. While such a set stays open, a re-dispatch of the same triple gets 409, and bringing up a worker that advertises the capability concludes the set.

### Unit 4 — Serve delivers the state to GitHub, durably

**Which GitHub App writes the check runs.** Serve reads GitHub credentials from two setting groups,
which are independent today:

| Setting group | Settings | What serve does with it today |
|---|---|---|
| Sign-in (BE-0313) | `BAJUTSU_OAUTH_GITHUB_CLIENT_ID`, `_CLIENT_SECRET`, `_REDIRECT_URI` | signs a person in, then reads the person's organizations and teams with the user token |
| App credential (BE-0224) | `BAJUTSU_GITHUB_APP_ID`, its private key, and an optional `BAJUTSU_GITHUB_APP_INSTALLATION_ID` | mints installation tokens for the private-repository config source |

Check writes use the App credential group, because GitHub accepts a check run from a GitHub App's
installation token alone. A GitHub App registration carries a client ID and secret as well as an App
ID and a private key, so one registration can fill both groups. An OAuth App carries a client ID and
secret alone, and it can never write a check run.

This item assumes the deployment signs people in with a GitHub App, and makes that App the writer of
check runs. The operator sets the sign-in App's App ID and private key as the App credential. A
deployment can take one of these shapes:

| Shape | Sign-in with | App credential names | Check runs written by | Private config source read through |
|---|---|---|---|---|
| **One App (this item's premise)** | GitHub App A | A | A | A |
| Sign-in on an OAuth App | OAuth App | GitHub App B, possibly the config-source App | B | B |
| Two GitHub Apps | GitHub App A | GitHub App B, the config-source App | B | B |

The last row shows a limit. Serve holds a single App credential, so the App that writes check runs is
always the App that reads the config source. Writing check runs with the sign-in App while a separate
App keeps the config source would need a second credential group, which this item leaves out.

**The sign-in documentation recommends a GitHub App.** The "2. Add GitHub OAuth (optional)" section of `docs/self-hosting.md` tells an
operator to create an OAuth App today. GitHub's own guidance prefers a GitHub App, for its
fine-grained permissions, per-repository access, and short-lived tokens. GitHub recommends an OAuth App
only for enterprise-level resources, which sign-in does not read. This item therefore rewrites that
section to register a GitHub App for sign-in, and keeps the OAuth App as a supported alternative.
The same registration then serves the config source and check runs without a second App.

**Setting the App credential moves the config source onto the App as well.** The config source in
`bajutsu/common/config_source/_functions.py` prefers an App installation token over a personal access
token (PAT) whenever `BAJUTSU_GITHUB_APP_ID` is set. A deployment that reads a private config
repository with a PAT today must therefore install App A on that repository with `contents: read`.
Otherwise its config source stops resolving once the App credential is set.

Under the one-App shape, App A carries these permissions and installations:

| Purpose | Repository permission | Installed on |
|---|---|---|
| Sign-in: organization and team mapping | none; GitHub lists no permission for `/user/orgs` or `/user/teams` | every organization that `githubOrgs`, `githubTeams`, `editorTeams`, or `BAJUTSU_OAUTH_ADMIN_TEAMS` names |
| Config source | `contents: read` | the config repository |
| Check runs | `checks: write` | every repository whose workflows dispatch check-bearing runs |

Adding `checks: write` to an installed App needs each installation owner's approval before any check
write succeeds.

Two sign-in behaviors need confirming on a real deployment before this premise holds. Sign-in reads
`/user/orgs` and `/user/teams`, and GitHub lists both as available to a GitHub App user access token.
The `/user/orgs` reference also says that a fine-grained access token receives an empty list, and it
does not say whether a GitHub App user access token counts as one. GitHub further limits such a token
to accounts where the App is installed. The author's deployment signs people in with a GitHub App, and
its organization and team mapping works, which confirms both there. A deployment moving from an OAuth
App must still confirm them against its own organizations first, because an empty organization list
would admit nobody. Sign-in keeps no GitHub token after the callback (`bajutsu/serve/authz.py`), so
the eight-hour expiry of a GitHub App user access token does not affect it.

**The token.** Serve mints each check-write token from the App credential. The check-run repository
may differ from the config repository. Serve therefore resolves the installation from the check-run
repository rather than from the pinned `BAJUTSU_GITHUB_APP_INSTALLATION_ID`, which names the config
repository's installation. `installation_token` in `bajutsu/common/github/app.py` grows the
`permissions` and `repositories` request fields, and its `Fetch` seam gains the JSON request body it lacks today. The check-run writes go through the same seam, authenticated with the installation token instead of the App JWT. A token minted for a check write carries
`checks: write` on that one repository, so reusing the App does not widen any token. Serve caches
each token until shortly before its one-hour expiry.

**Creation is synchronous and fails the request.** Serve creates the check run as `queued` during
the dispatch request, after registering the whole set and before enqueuing any job. The GitHub
response supplies the check run id, which the row stores. The creation call carries the same short timeout as every GitHub call in a sweep, so it returns well before the lease timeout lets a sweep delete a row that has no check run id. A sweep sends nothing for such a row either. When creation fails, serve releases the
registrations, deletes the row, and refuses the request with 502, so no job enters the queue. Every
creation failure is refused this way, a transient GitHub error included, and the pipeline may retry.
The two failures a pipeline most needs to see are misconfigurations — the App is not installed on
the repository, or the permission is unapproved — and the 502 shows either one at once, instead of a
PR whose required check never appears.

**Updates reach GitHub in order, across restarts.** Every update is sent under a claim on its row, by one of two senders: a sweep that `lease_job` runs beside `reclaim_expired_leases` and that `heartbeat_job` runs after its lease renewal commits. The other is `worker_result`, which sends its own row directly after a result commits, without the walk. That send carries the same short timeout as a sweep's calls, and its failure is logged and never raised: the result is already committed, so the response to the worker does not depend on GitHub, and the row stays due for the next sweep. A small periodic task in the database-backed control plane runs the same sweep every recheck
interval as well, so the grace-period failure, the removal of a row without a check run id, and a
retry of a refused send all happen even when no worker polls. The worker-triggered sweeps keep an
update prompt, and the periodic one makes it certain. Each replica runs the task, and the claim keeps their sends in order as it does for every other sender. The `check_runs` row keeps the state GitHub
last confirmed and the time of the next attempt.

A sweep walks the open rows that are due, oldest attempt first. It claims each row with `SELECT … FOR UPDATE SKIP LOCKED` in a short transaction that stamps a claim expiry, the send timeout plus a margin, and commits, then derives the row's state. After each row it visits, the sweep records the
row's next attempt one recheck interval ahead, a few seconds by default, so an idle row is visited
once per interval however many workers poll. A row whose state matches the confirmed one, or whose status is earlier than the confirmed status, costs that single row update, and the sweep moves on. A set whose job `reclaim_expired_leases` returned to the queue derives `queued` while GitHub confirmed `in_progress`, and Unit 3 forbids sending that. The first remaining row whose state differs gets the full state sent, and the sweep stops there. It sends with no transaction open, then records the confirmation, or a backed-off next attempt after a refused send, in a second short transaction that clears the claim. Latency therefore grows with the rows that changed,
not with the rows that are open. The update load is bounded by the open rows per recheck interval,
not by the number of idle workers.

The claim serializes each row's sends across control-plane replicas. A second replica skips a row whose claim has not expired. The next sender derives the state after taking the claim, so it never sends a state older than the last one sent. A crash mid-send leaves the claim to expire, and a later sweep takes the row over. No database connection is held across a GitHub call, so a slow GitHub cannot exhaust the pool that lease polls share. Because a sweep sends for one row at most, a GitHub outage
delays a worker's lease poll or heartbeat by one bounded send at most. The cost is latency: an update
reaches GitHub not at the job transition itself but at the next sweep, which a lease poll, a heartbeat, or the periodic task triggers. A committed result is the exception: `worker_result` sends its own row at once. The recheck interval adds at most its own length to that wait. A worker running a job sends a heartbeat every 30 seconds by default, but the periodic task keeps sweeping even when every worker is busy, so an update waits a few seconds by default, not a heartbeat interval. Each GitHub call inside a sweep carries a short timeout, so a heartbeat
interval plus one send stays well under the 120-second lease timeout.

**The content.** The summary holds one line per member: the scenario, its verdict, and a link to its
report. The report links need serve's public base URL. A new deployment setting supplies that URL,
and without it serve omits the links. `external_id` holds the row's id. `details_url` stays unset,
because the web UI has no page for a set yet.

A delivery that GitHub keeps refusing is logged with the repository and the check run id. Its row closes after a cap on consecutive sends that GitHub rejects with a client error (ten by default). A rate-limit response, a 5xx, or a timeout is retried with backoff but does not count toward the cap. Closing leaves the check run as GitHub last saw it and frees the triple, so that a revoked `checks: write` permission or a head commit that has since been force-pushed away cannot block a re-dispatch forever. The refusal never touches a job's verdict or a run's record.

### Unit 5 — Tests and documentation

- **Tests.** A fake GitHub transport replaces `app.py`'s `fetch` seam, the same seam BE-0224's tests
  use. The tests cover the dispatch branches, each row of Unit 3's table, and the all-or-nothing
  registration. They also cover the repository bound, the 409 on a duplicate triple, the 400 on SQLite, and the 502 on a failed creation. A restart test
  drops a send and checks that the next sweep delivers it. A test without any worker checks that the periodic task concludes a row and retries a refused send, and that the attempt cap closes a row and frees the triple. A drained-pool test checks that a result's own row reaches `completed` from `worker_result` even while another open row with a pending change is due. The derivation, sweep, restart, worker-less, and drained-pool tests drive the repository and the sweep directly, below the dispatch handler's SQLite refusal, so they run on the SQLite `make check` gate. The dispatch-branch and 409 tests, and a two-sender test that checks one row's sends stay in order, run in the Postgres lane (BE-0309), because SQLite refuses a check-bearing dispatch and takes no row lock.
- **Documentation.** `docs/self-hosting.md` and its `docs/ja/` mirror rewrite the sign-in section to
  recommend a GitHub App, with an OAuth App as the alternative, and tell an operator moving from an
  OAuth App to confirm that `/user/orgs` and `/user/teams` return its organizations before cutting
  over. The `read:org` scope sentence applies to the OAuth App path only. They also gain the GitHub App shapes,
  the one-App permission and installation steps, and a workflow example. `docs/architecture.md` records the `check_runs` table
  and the delivery path.

### Out of scope

- **Receiving GitHub webhooks.** Serve starting a run from a `workflow_job` event, and re-running on
  a check run's *Re-run* button, both need serve reachable from GitHub. This item makes serve's
  GitHub traffic outbound-only.
- **Annotations.** Mapping a failed step to a line in a scenario file needs the scenario's path in
  the repository. An uploaded scenario tree does not carry that path.
- **Pull requests from forks.** Such a workflow receives no secrets and a read-only `GITHUB_TOKEN`.
  Whether it can mint an OIDC token for serve is still to be confirmed.
- **Cancelling a check run on an outdated commit** after a new push to the PR.
- **Verifying `headSha` against the workflow's own commit.** The caller's workflow sits inside the
  trust boundary, the stance [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)
  takes for the repository it admits. A workflow that can obtain the OIDC token can name any commit
  in its own repository, so it can turn another pull request's required check green without the
  intended scenarios running. The caller also chooses the scenario list, so checking the SHA would
  not stop a workflow from dispatching a trivially passing set for its own commit. Serve checks the
  value's shape only. A deployment that cannot accept this risk narrows the repository's `allowedRepositories` entry with BE-0414's `job_workflow_ref` or `environment` bound, so only a reviewed workflow, or a job an Environment's reviewers approve, can obtain the token.

## Alternatives considered

| Alternative | Why not adopted |
|---|---|
| One check run per target | `POST /api/run` carries one scenario per request, so serve cannot tell which request is the target's last and cannot conclude the check run. |
| One check run per job | A branch protection rule must list every scenario, and every new scenario edits that rule. |
| An open-then-seal group across requests | A workflow that dies before sealing leaves the check run pending forever. Reaping it needs a timeout as long as the slowest pipeline's dispatch loop, which serve cannot know, whereas this item's grace period is bounded by one dispatch request. |
| The Actions job polls until done, with no check run | Needs no serve change, but holds a billed runner for the whole queue wait and links the PR to Actions logs rather than the report. |
| The Commit Status API | Needs no App, but a status carries one short description and one link, with no room for a per-scenario summary. |
| A separate App for check writes | Keeps App permissions apart, but serve holds one App credential, so that App would take over the config source too. A token scoped by `permissions` and `repositories` already gives per-token least privilege with one App to operate. |
| Extending `POST /api/run` to take a list | Adds a second set-shaped surface beside `POST /api/run-set`, which already validates a set before dispatching any of it. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `POST /api/run-set` dispatches to the worker queue
- [ ] Unit 2 — A check-bearing request records its set before dispatch
- [ ] Unit 3 — The check run's state derives from its members' jobs
- [ ] Unit 4 — Serve delivers the state to GitHub, durably
- [ ] Unit 5 — Tests and documentation

## References

- GitHub Docs, [REST API endpoints for check runs](https://docs.github.com/en/rest/checks/runs) —
  a check run needs a GitHub App with the `checks` permission, a `head_sha`, and optional
  `details_url` and `external_id`.
- GitHub Docs, [Create an installation access token for an app](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app)
  — the `permissions` and `repositories` fields, and the one-hour expiry.
- GitHub Docs, [Modifying a GitHub App registration](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)
  — each installation must approve a newly added permission.
- GitHub Docs, [Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
  — `GITHUB_SHA` for `pull_request` is a merge commit, and a fork's workflow gets no secrets.
- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) — the machine
  session and its repository identity.
- [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md) —
  the App credential setting this item reuses.
- [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) — GitHub sign-in and
  the organization and team mapping the sign-in App serves.
- GitHub Docs, [Differences between GitHub Apps and OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)
  — "In general, GitHub Apps are preferred over OAuth apps", except for enterprise-level resources.
- GitHub Docs, [Authenticating with a GitHub App on behalf of a user](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user)
  — a user access token reaches only accounts where the App is installed.
- GitHub Docs, [Endpoints available for GitHub App user access tokens](https://docs.github.com/en/rest/authentication/endpoints-available-for-github-app-user-access-tokens)
  — lists `/user/orgs` and `/user/teams`.
- GitHub Docs, [List organizations for the authenticated user](https://docs.github.com/en/rest/orgs/orgs#list-organizations-for-the-authenticated-user)
  — a fine-grained access token receives an empty list.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md)
  — the origin of `POST /api/run-set`.
- [BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override.md) — the
  per-job artifact overrides a CI dispatch carries.

**English** · [日本語](BE-XXXX-github-check-run-reporting-ja.md)

# BE-XXXX — Report CI-dispatched runs to the pull request as one GitHub check

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-github-check-run-reporting.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md) |
<!-- /BE-METADATA -->

## Introduction

A GitHub Actions workflow can dispatch runs to a hosted `bajutsu serve` with no stored secret,
through the OpenID Connect (OIDC) machine session of
[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md). The workflow
job ends as soon as serve accepts the runs, so the pull request never learns how they ended.

This item makes serve report such a dispatch back to the pull request as **one GitHub check run**.
A workflow registers several scenarios in one request. serve opens a check run on the pull request's
head commit, keeps it `in_progress` while the runs execute, and completes it once the last run has a
verdict. The check's page lists every scenario with its verdict and links each failed one to its
hosted report. The check carries a fixed name, so a repository can make it a required status check
for merging. The conclusion is a copy of the deterministic verdicts, never a judgment of its own.

## Motivation

The dispatch path of BE-0414 is fire-and-forget by design. A pipeline exchanges its OIDC token,
uploads a build, and calls `POST /api/run`; serve answers with a job id and runs the job on a worker
later. The workflow job then has two choices, and neither serves a pull request well.

| What the workflow job does | What the pull request sees | Cost |
|---|---|---|
| Ends after dispatch | A green job, whatever the runs later conclude | The result never reaches the pull request |
| Polls serve until every run ends | The job's own status | A runner sits idle for the whole run; the failed scenarios surface only in a job log |

The first choice cannot gate a merge at all. The second can, but it pays for an idle runner and
still leaves a reviewer to dig through a log for which scenario failed. serve already holds both
missing pieces. It knows when each job finishes and what each verdict is, and it already mints
GitHub App installation tokens for private configuration sources
([BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)).
The GitHub Checks API accepts exactly that kind of token.

A check run, rather than a commit status, carries the result. A commit status holds a state and one
link. A check run also holds a Markdown summary, which is where the per-scenario table and the
report links belong.

**Verifiable outcome.** A workflow registers several scenarios in one request and finishes within
seconds. The pull request meanwhile shows a check named `bajutsu` as in progress. The check turns
green once every scenario passes. When a scenario fails, the check turns red, and its page names
each failed scenario with a link that opens that scenario's report. A branch protection rule that
requires `bajutsu` blocks the merge until the check completes successfully.

## Detailed design

The design splits into four units: one request that registers a group, a record that tracks the
group, a reporter that writes the check run, and the tests and documentation.

### Unit 1 — Register several scenarios as one group

`POST /api/run-set` already takes a `target` and a `scenarios` list and fans them out into one job
per scenario ([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md)).
Today the request requires `environment: devicefarm`. It also refuses the artifact override fields
(`binaryArtifact`, `scenariosArtifact`). A pipeline that wants one check over its scenarios gains
nothing from Device Farm in particular. The pipeline needs the scenarios registered as one group.
Any worker that can run them may run them.

With this unit, `POST /api/run-set` differs from `POST /api/run` in count alone. The per-request
`environment` still picks where the runs happen. `POST /api/run` already offers that choice
([BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)):

| `environment` | `POST /api/run` | `POST /api/run-set` |
|---|---|---|
| Absent | One job on the worker queue | One job per scenario on the worker queue (new) |
| `devicefarm` | `400` | One Device Farm job per scenario (unchanged) |
| Any other value | `appium` routes to the grid; others return `400` | `400` (unchanged) |

With `environment` absent, the set behaves as follows:

- Each scenario becomes an ordinary job. The same code path as `POST /api/run` builds the job.
  Each job requires the capabilities a single run of that scenario requires. Any live worker that
  advertises them leases the job. The jobs thus spread across every capable worker at once. A
  single worker runs them one after another.
- The override fields apply to every job in the set. A pipeline uploads its build once and runs
  every scenario against it. A job that ships its files this way requires no `machine:` token. A
  worker on another machine can thus take the job.
- serve never picks a different environment on its own. A set with `environment` absent never falls
  back to Device Farm. A `devicefarm` set never falls back to the queue. Each run happens where the
  request chose. Two runs of the same workflow thus stay comparable.

A Device Farm set keeps its current refusal of the override fields
([BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md)). Mapping
an override onto a Device Farm request is separate work.

Both kinds of set take one more change. A machine principal may call `POST /api/run-set`. The
route joins the machine allowlist in `bajutsu/serve/gate.py`. Today that list admits
`POST /api/run` but not the set. `start_run_set` also takes the machine's org the way `start_run`
does, so the set runs in the pipeline's own org.

The request gains one optional object, `githubCheck`:

```json
{
  "target": "app",
  "scenarios": ["scenarios/login.yaml", "scenarios/checkout.yaml"],
  "binaryArtifact": "sha256:…",
  "githubCheck": { "headSha": "<pull request head commit>", "name": "bajutsu" }
}
```

- `headSha` is required within `githubCheck`. The workflow passes
  `github.event.pull_request.head.sha`. Its own `GITHUB_SHA` will not do, because on a
  `pull_request` event that is the test merge commit, and a check on it never appears on the pull
  request.
- `name` defaults to `bajutsu`. A repository that dispatches from several workflows, such as one for
  iOS and one for Android, gives each its own name, so each can be required on its own.

serve accepts `githubCheck` only from a machine principal. The repository the check is written to
is the one in that principal's identity (`repo:<owner>/<repo>`), which serve verified from the OIDC
token's claims. The request never names a repository, so a workflow can write a check only to its
own repository. A request with `githubCheck` from a human session returns `400`.

serve binds the commit to the token the same way. Without that binding, any workflow in the
repository could post a passing check on the head of another open pull request. The required check
of that pull request would then pass, with none of its scenarios run. serve thus checks `headSha`
against the verified claims of the machine's token:

| `ref` claim | Commit serve accepts |
|---|---|
| `refs/pull/<N>/merge` (a `pull_request` event) | The current head of pull request `<N>`, which serve reads from the GitHub API with the installation token |
| `refs/heads/<branch>` (a `push` event) | The `sha` claim |
| Any other ref | None; the request returns `400` |

A `headSha` that differs from the accepted commit returns `409`. A pull request whose head moved
after the workflow started hits this case. The newer push starts its own workflow, which reports
on the new head. serve already keeps both claims from the token: `ref` and `sha`.

The binding limits who can write a check. It does not limit what the check covers. The workflow
file and the scenario list both come from the tree of the pull request. A contributor who can push
a branch can edit the workflow to dispatch fewer scenarios. Every required check from GitHub Actions
shares this boundary. The check proves one fact: the scenarios the request named passed on the head
commit. A repository that needs more protects the workflow file. A `CODEOWNERS` entry for
`.github/workflows/` serves that purpose.

The design covers pull requests from branches of the repository itself. On a pull request from a
fork, GitHub makes every write permission of the workflow token read-only. The `id-token: write`
permission is no exception. An administrator can lift that rule with a repository setting, which
sends write tokens to fork workflows. Without that setting, a fork workflow gets no OIDC token, so
it cannot dispatch. The `pull_request_target` event would grant the token. It would also run
untrusted fork code with that token, so this item rules that event out. A repository that accepts
fork pull requests thus cannot require the `bajutsu` check on each of them. Such a repository keeps
the check optional. Or it requires the check through a ruleset that excludes fork pull requests.

### Unit 2 — Track the group until its last job ends

A group is the set of job ids one request created. serve records the group in the SQL job store, in
the same database as the jobs. The record holds:

- the job ids, each with its scenario, fixed when the request is accepted;
- the repository, the head commit, and the check name;
- the time serve registered the group;
- the state of the check run's creation: pending, created (with the id GitHub returned), or
  abandoned after its retries.

The in-memory store gains no group record. A machine principal exists only on a deployment with a
database, so a request with `githubCheck` never reaches a deployment without one.

Because the request fixes the job ids, the group knows its size from the start. No later
registration can join the group, so the check cannot complete while a scenario is still waiting to
be dispatched.

A worker has no database access. It posts its result to serve, and serve's result handler writes
the job's terminal row. A job can also end with no result at all: when a worker dies, its lease
expires, and the lease reclaim fails the job once its attempts run out. The replica that writes the
terminal row is not necessarily the one that registered the group, so the group's completion is
detected in two steps:

1. Every write of a terminal row marks the job's slot in its group, in the same transaction. Both
   writers do this: the result handler (`complete_job` and `fail_job`) and the lease reclaim that
   fails an exhausted job.
2. serve periodically sweeps for groups whose every slot is marked. It claims a group's row before
   reporting, so when several serve replicas sweep at once, only one of them completes the check.

These two steps alone can leave a group open forever. A job that no live worker can lease never
takes a lease, so lease exhaustion never reaches the job. Today such a job stays `queued`. serve
reports it as the `bajutsu_unroutable_jobs` metric and does nothing else. The lease reclaim also
runs when a worker asks for a job, and at no other time. When every worker is down, no reclaim runs.
In both cases a slot stays unmarked. The required check then stays `in_progress`, with no reason
shown.

A group thus also has a deadline, `BAJUTSU_GITHUB_CHECK_DEADLINE`. Once a group is older than the
deadline, the sweep completes it, even with slots unmarked. The default is six hours. Six hours is
the longest a single GitHub Actions job may run. The sweep runs on a timer in serve, apart from any
worker. The deadline thus holds with no worker alive. The deadline leaves the jobs themselves alone.
A verdict that arrives later does not reopen the check.

A group row stays after its check completes, so the sweep can tell a reported group from an open
one. The run retention sweep (`BAJUTSU_RUN_RETENTION_DAYS`) removes the row of a completed group.
It does so together with runs of the same age.

### Unit 3 — Write the check run from serve

The reporter runs in serve, not in the run process. A worker holds no long-lived credential
([BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md)),
and the group spans several workers, so no single run process sees the whole group.

The reporter makes two calls to the Checks API. Both use an installation token from
`bajutsu/common/github/app.py`. Today that module asks for a token with the full grant of the
installation. The reporter narrows the token to the repository of the group. It also narrows the
token to two permissions: `checks: write` and `pull_requests: read`. A leaked token can then write
checks on one repository, and nothing more. The two calls are:

1. **On dispatch**, create the check run on `headSha` with `status: in_progress` and a `details_url`
   that opens serve's Web UI.
2. **When the sweep finds the group complete**, update the check run to `status: completed` with a conclusion and an
   `output.summary`.

The conclusion follows the verdicts mechanically:

| Group outcome | `conclusion` |
|---|---|
| Every job passed | `success` |
| Any job failed, or ended in an error | `failure` |
| The group's deadline passed with a job unfinished | `failure` |

GitHub treats `failure` as a failed required check, so it blocks a merge. A cancel does not reach a
remote worker today: cancellation flags the job inside serve but sends nothing to the worker running
it. A group job thus ends by its own verdict, by lease exhaustion, or by the group's deadline, and
the table needs no `cancelled` row.

The summary is a Markdown table with one row per scenario: its verdict and a link to its report. The
scenario comes from the group record. The link comes from the run id the worker posted with its
result. A scenario that did not end before the deadline shows as not run, with no link. Failed rows
come first. GitHub caps `output.summary` at 65,535 characters. Past that cap, the table keeps every
failed row and drops passing rows. It states how many rows it dropped.

The links need serve's public origin. No setting in serve names that origin on its own today. The
OAuth redirect URI (`BAJUTSU_OAUTH_GITHUB_REDIRECT_URI`) embeds it, and the run notifier of
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) accepts a
report URL but receives none. A new setting, `BAJUTSU_PUBLIC_URL`, supplies the origin. It defaults
to the origin of the OAuth redirect URI, so a deployment with OAuth configured needs no second
setting to keep in step. A link opens only for a viewer who can sign in to the job's org; this check
makes the reports reachable, not public.

The GitHub App needs two permissions on each repository it reports to. It needs `checks: write` to
write the check. It needs `pull_requests: read` to resolve the head of a pull request. serve checks
both at dispatch. The installation lookup for the repository returns the permissions of the App
there. Without an App, or without either permission, serve refuses a request with `githubCheck`. The
answer is `400`. A late refusal would leave the check silently absent.

A failed call to GitHub is retried with a bounded backoff, then logged. If the create call at
dispatch was abandoned after its retries, the completing call creates the check run directly as
`completed`, so a lost create still ends in a conclusion. The sweep skips a group whose create is
still pending, so a create that is still retrying never races the completing call into two check
runs of the same name.

A pending create thus carries a claim with an expiry, like the claim of the sweep. The replica that
retries the create holds the claim. Suppose that replica restarts mid-backoff. The claim expires, and
the sweep of another replica moves the create to abandoned. Without the expiry, no replica would
drive the pending create. Every later sweep would then skip the group.

The failure never changes a verdict. A check that serve could not complete stays `in_progress`,
which a required check treats as not passing. A lost report thus blocks the merge rather than
letting it through.

Two limits remain outside this item:

- serve does not subscribe to GitHub webhooks. The "Re-run" button on the check page thus does
  nothing. A user re-runs the workflow instead.
- A newer push does not cancel the group of the older head. That group runs to the end. It reports
  on the older commit, which no longer gates the pull request.

### Unit 4 — Tests and documentation

- Tests fake the Checks API at the HTTP boundary. They cover:
  - the conclusion table;
  - the binding to the head commit and the repository;
  - the `409` for a `headSha` that is not the accepted commit, and the `400` for an unsupported ref;
  - the token narrowed to one repository and two permissions;
  - the refusal of a human principal;
  - the summary cap;
  - the retry that ends in a log line;
  - the all-or-nothing `429` when the caps cannot take the whole set;
  - the sweep's claim, so two replicas never both complete one check;
  - a job failed by lease exhaustion, which completes its group as `failure`;
  - a group with an unroutable job, which the deadline completes as `failure` with no worker alive;
  - a sweep that skips a group whose create is still pending;
  - a pending create whose claim expired, which another replica moves to abandoned;
  - a create that failed and is recovered at completion.
- `docs/self-hosting.md` (and its `docs/ja/` mirror) gains a workflow example: the OIDC exchange,
  the upload, and one `POST /api/run-set` with `githubCheck`. The example closes with the
  branch-protection setting that requires the check. The page also states the fork limit and the
  advice to protect the workflow file.
- `docs/architecture.md` records the group record and the reporter.

### Prime directives

- **AI never judges.** The conclusion is a function of the deterministic verdicts. No large language
  model is on this path.
- **Determinism first.** Reporting happens after a verdict is fixed and cannot change one. A delivery
  failure leaves the check incomplete, never green.
- **App-agnostic.** The check name and the head commit come from the request. Nothing about a
  particular app enters the tool, the drivers, or the runner.

## Alternatives considered

- **Have the workflow job poll serve and act as the required check.** The job needs no GitHub App
  permission, but it occupies a runner for the whole run, and it shows failed scenarios only in a
  log. Polling stays possible; this item removes the need for it.
- **One check run per scenario.** Each failed scenario would stand out in the Checks tab. A required
  check is named, though, so every new scenario would need a new branch-protection entry. A scenario
  that is missing from a run would also leave its required check pending forever. A single named
  check, with the scenarios in its summary, stays stable as scenarios come and go.
- **Group by the workflow run id, or by an open–add–seal sequence.** Grouping by the run id cannot
  tell when the last run has been registered, so the check could complete early. An explicit seal
  call fixes that, but a workflow that forgets to seal leaves the check pending. One request that
  names every scenario fixes the group's size up front, with no extra call to forget. This holds because the request is all or nothing, as Unit 1 describes.
- **Commit statuses instead of check runs.** Commit statuses work with a plain token, but they carry
  only a state and one link, not the per-scenario summary a reviewer needs to find the failure.
- **Report from the run process, like the BE-0099 notifier.** Each run process sees one job, not
  the group, and a worker holds no GitHub credential.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `POST /api/run-set` dispatches to the worker queue when `environment` is absent, joins the machine allowlist, accepts overrides, takes `githubCheck` all or nothing, and binds `headSha` to the token's claims
- [ ] Unit 2 — group record in the SQL job store, slots marked with each terminal row, a claiming sweep in serve, and the group deadline
- [ ] Unit 3 — Checks API reporter with a narrowed token, conclusion mapping, summary, the pending-create claim, and `BAJUTSU_PUBLIC_URL`
- [ ] Unit 4 — tests and documentation

## References

- GitHub REST API, check runs: <https://docs.github.com/en/rest/checks/runs>
- GitHub, about protected branches and required status checks:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- GitHub Actions, events that trigger workflows (`pull_request` and `GITHUB_SHA`):
  <https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#pull_request>
- GitHub Actions, workflow syntax, `permissions` (write permissions on pull requests from forks):
  <https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#permissions>
- GitHub REST API, create an installation access token (`repositories` and `permissions` narrowing):
  <https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app>
- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) — the OIDC machine session this dispatch rides on.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) — the scenario-set fan-out this item extends to the worker queue.

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
| Related | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md), [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md) |
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
Today that fan-out runs only through a cloud-batch provider and refuses the artifact override fields
(`binaryArtifact`, `scenariosArtifact`). This unit lifts both limits for a target that declares no
`cloudBatch`:

- Each scenario becomes an ordinary job on the worker queue, built by the same code path as
  `POST /api/run`.
- The override fields apply to every job in the set, so a pipeline uploads its build once and runs
  every scenario against it.
- A machine principal may call it. `POST /api/run-set` joins the machine allowlist in
  `bajutsu/serve/gate.py`, which today admits `POST /api/run` but not the set. `start_run_set` also
  takes the machine's org the way `start_run` does, so the set runs in the pipeline's own org.

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

A request with `githubCheck` is all or nothing. Today `POST /api/run-set` stops the fan-out when a
concurrency cap (global, per user, or per org) is reached, and still answers `200` with the shorter
list of job ids. A check over that shorter list could pass while the remaining scenarios never ran.
With `githubCheck`, serve instead reserves the whole set in one step under the job registry's lock,
so a concurrent dispatch cannot take a slot between the cap check and the last registration. When the caps
cannot take the whole set, serve dispatches nothing and answers `429`, so the workflow fails visibly.

### Unit 2 — Track the group until its last job ends

A group is the set of job ids one request created. serve records the group in the SQL job store, in
the same database as the jobs. The record holds:

- the job ids, fixed when the request is accepted;
- the repository, the head commit, and the check name;
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

### Unit 3 — Write the check run from serve

The reporter runs in serve, not in the run process. A worker holds no long-lived credential
([BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md)),
and the group spans several workers, so no single run process sees the whole group.

The reporter makes two calls to the Checks API, both with an installation token for the group's
repository from `bajutsu/common/github/app.py`:

1. **On dispatch**, create the check run on `headSha` with `status: in_progress` and a `details_url`
   that opens serve's Web UI.
2. **When the sweep finds the group complete**, update the check run to `status: completed` with a conclusion and an
   `output.summary`.

The conclusion follows the verdicts mechanically:

| Group outcome | `conclusion` |
|---|---|
| Every job passed | `success` |
| Any job failed, or ended in an error | `failure` |

GitHub treats `failure` as a failed required check, so it blocks a merge. A cancel does not reach a
remote worker today: cancellation flags the job inside serve but sends nothing to the worker running
it. A group job thus always ends by its own verdict or by lease exhaustion, and the table needs no
`cancelled` row.
The summary is a Markdown table with one row per scenario: its verdict, its duration, and a link to
its report. Failed rows come first. GitHub caps `output.summary` at 65,535 characters. Past that
cap, the table keeps every failed row, drops passing rows, and states how many it dropped.

The links need serve's public origin. No setting in serve names that origin on its own today. The
OAuth redirect URI (`BAJUTSU_OAUTH_GITHUB_REDIRECT_URI`) embeds it, and the run notifier of
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) accepts a
report URL but receives none. A new setting, `BAJUTSU_PUBLIC_URL`, supplies the origin. It defaults
to the origin of the OAuth redirect URI, so a deployment with OAuth configured needs no second
setting to keep in step. A link opens only for a viewer who can sign in to the job's org; this check
makes the reports reachable, not public.

The GitHub App needs the `checks: write` permission on the repositories it reports to. A deployment
that has not configured an App, or whose App lacks the permission, refuses a request carrying
`githubCheck` with `400` at dispatch. A late failure would leave the check silently absent.

A failed call to GitHub is retried with a bounded backoff, then logged. If the create call at
dispatch was abandoned after its retries, the completing call creates the check run directly as
`completed`, so a lost create still ends in a conclusion. The sweep skips a group whose create is
still pending, so a create that is still retrying never races the completing call into two check
runs of the same name. The failure never changes a verdict. A check that serve could not complete stays `in_progress`, which a required check treats
as not passing. A lost report thus blocks the merge rather than letting it through.

### Unit 4 — Tests and documentation

- Tests fake the Checks API at the HTTP boundary. They cover:
  - the conclusion table;
  - the binding to the head commit and the repository;
  - the refusal of a human principal;
  - the summary cap;
  - the retry that ends in a log line;
  - the all-or-nothing `429` when the caps cannot take the whole set;
  - the sweep's claim, so two replicas never both complete one check;
  - a job failed by lease exhaustion, which completes its group as `failure`;
  - a sweep that skips a group whose create is still pending;
  - a create that failed and is recovered at completion.
- `docs/self-hosting.md` (and its `docs/ja/` mirror) gains a workflow example: the OIDC exchange,
  the upload, and one `POST /api/run-set` with `githubCheck`. The example closes with the
  branch-protection setting that requires the check.
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

- [ ] Unit 1 — `POST /api/run-set` dispatches to the worker queue, joins the machine allowlist, accepts overrides, and takes `githubCheck` all or nothing
- [ ] Unit 2 — group record in the SQL job store, slots marked with each terminal row, and a claiming sweep in serve
- [ ] Unit 3 — Checks API reporter, conclusion mapping, summary, and `BAJUTSU_PUBLIC_URL`
- [ ] Unit 4 — tests and documentation

## References

- GitHub REST API, check runs: <https://docs.github.com/en/rest/checks/runs>
- GitHub, about protected branches and required status checks:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- GitHub Actions, events that trigger workflows (`pull_request` and `GITHUB_SHA`):
  <https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#pull_request>
- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) — the OIDC machine session this dispatch rides on.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) — the scenario-set fan-out this item generalizes.

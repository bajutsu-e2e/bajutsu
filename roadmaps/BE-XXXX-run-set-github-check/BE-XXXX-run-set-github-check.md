**English** · [日本語](BE-XXXX-run-set-github-check-ja.md)

# BE-XXXX — Publish a run-set group as a GitHub check run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-run-set-github-check.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md), [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets `serve` publish a `run-set` group as one GitHub check run on the commit that a pull request tests. The check moves through `queued`, `in_progress`, and `completed` as the group does. Its output lists each scenario that did not pass, with its outcome and the reason, and counts the scenarios that passed.

A `run-set` registers one job per scenario of a target, as a **group** with its own id ([BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md)). The item adds three **group events** (`queued`, `started`, and `completed`) and a **sink**, an operator-defined destination that receives them. A sink's `format` names the adapter that turns an event into calls to one service. The first format is `github-check`. A later format, such as a generic signed webhook, adds one adapter and touches neither the events nor the run path.

## Motivation

A GitHub Actions job can already dispatch a group. The job authenticates to `serve` with its OpenID Connect (OIDC) token ([BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)) and calls `POST /api/run-set`. The result then reaches the pull request through one path alone: the job polls `GET /api/run-set/<groupId>` until the group concludes, then exits with a matching status. The runner sits idle for the whole group. A group of forty scenarios on a device cloud can hold a runner for an hour, and that runner time is billed while it does nothing.

The group already carries what a check needs. `GET /api/run-set/<groupId>` returns a `conclusion` of `pending`, `success`, `failure`, or `cancelled`. BE-0484 chose those names after a GitHub check run's conclusion values, and left publishing out of its scope. What is missing is a component that watches the group and writes to GitHub. `serve` holds the group, so `serve` is the one place that can write the check without a waiting runner.

The existing notification feature does not reach this case. The `notify:` list of [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) fires inside one `bajutsu run` process, once per run, and posts to a URL. A group spans many runs on many workers, and its lifecycle exists on `serve` alone. A GitHub check run is also not a single POST to a URL. The caller creates a check run, receives its id, and updates that id later. The caller also needs a GitHub App installation token, since GitHub lets only a GitHub App create a check run.

The observable difference has three parts. The pull request's checks list shows a `bajutsu / <target>` check that moves from queued to in progress to its conclusion. The check's output names each scenario that did not pass, with the reason it did not. And the Actions job that dispatched the group finishes right after the dispatch, instead of waiting for the group.

## Detailed design

### Terms

- **Group event**: one of three points in a group's life. `queued` fires when the group registers. `started` fires when a worker first leases a job of the group. `completed` fires when the group's `conclusion` leaves `pending`. Each event fires at most once per group and sink.
- **Sink**: a named destination the operator defines. A sink has a `name` and a `format`, plus the fields its format needs.
- **Adapter**: the code for one `format`. An adapter receives an event, a snapshot of the group, the sink, the request's context for the sink, and the latest reference that any earlier event of the same group and sink returned. It returns the reference to keep. For `github-check`, the reference is the check run id.

### Defining sinks

The operator lists sinks in a file and passes its path to `serve` with `--group-notify-config`, or with the environment variable `BAJUTSU_GROUP_NOTIFY_CONFIG`. The file is operator configuration, like the GitHub App credentials, and lives outside every org's config.

```yaml
groupNotify:
  - name: github
    format: github-check
    checkName: "bajutsu / {target}"
```

- `checkName` is the check run's name. `{target}` is the one placeholder, and it expands to the group's target. The default is `bajutsu / {target}`.
- `format` accepts `github-check` alone in this item. An unknown format stops `serve` at startup, with an error that names the sink.
- A `github-check` sink needs the GitHub App that the Git config source already reads ([BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)): `BAJUTSU_GITHUB_APP_ID`, plus `BAJUTSU_GITHUB_APP_PRIVATE_KEY` or `BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE`. `serve` refuses to start when a `github-check` sink exists and the App is not configured. The App needs the `checks: write` permission on each repository it publishes to.

A request names a sink and never a URL. A URL taken from a request would let any caller make `serve` send requests into its own network, a server-side request forgery (SSRF). Defining every destination in operator configuration closes that path.

### Selecting a sink in the request

`POST /api/run-set` takes one more optional field, `notify`, a list of sink selections:

```json
{"notify": [{"sink": "github", "headSha": "<40 hex digits>"}]}
```

- `sink` must name a sink in the operator's file. An unknown name gets status 400.
- A `github-check` selection needs `headSha`, 40 lowercase hexadecimal digits. The server refuses any other value with status 400.
- The repository comes from the caller's machine session: the `repository` claim that the OIDC exchange verified. A caller with no such session (a browser session, or the shared token) gets status 400 when it names a `github-check` sink. A workflow can thus write checks to its own repository alone.
- The request takes `headSha` rather than the token's `sha` claim. On a `pull_request` event, `GITHUB_SHA` and the `sha` claim name the merge commit GitHub creates for the run. A check run on that commit does not appear on the pull request. The workflow passes `github.event.pull_request.head.sha` instead.
- The `notify` list joins the request digest, and so does the repository the machine session resolved, since that repository decides where the check is written. A repeated group key whose request differs in either gets status 409, as any other difference does today.
- The group stores its selections and the repository in a new `notify` column of `job_groups`.

### Recording events

A group event is a row in a new table, `group_events`. A row holds the group, the sink, the event, a delivery state, an attempt count, the time of the next attempt, the adapter's reference, and, for `completed`, the snapshot of the group that the adapter renders. The row lands in the same store transaction as the change that causes the event:

- `queued`: the transaction that registers the group and its jobs;
- `started`: the lease transaction that leases the group's first job;
- `completed`: the transaction that moves the group's last unfinished job to a terminal status. The three terminal paths are `complete_job`, `fail_job`, and `reclaim_expired_leases`, plus `cancel_job` and `cancel_leased_job` for a cancel.

Writing the event in the same transaction means a crash never loses an event that the store already reflects.

The check output must tell a lost worker from a reported error without parsing prose. Today `reclaim_expired_leases` writes only `{"error": "lease expired after N attempts"}`, and `fail_job` writes the worker's free text under the same key. This item therefore adds a structured reason and the attempt count to the reclaim result, for example `{"reason": "lease-expired", "attempts": N}`. The adapter reads that field and never matches the message.

The `completed` snapshot is built once, when the row is first claimed, and stored on the row. A retry after a long backoff therefore never reads an artifact again. The scenario name comes from the run intent that the job's stored spec carries. The `failed` row's failure line comes from the run's manifest, which the job's run id locates. When the manifest is unavailable, the row shows the exit code and the note `no failure detail recorded`.

### Delivering events

A loop on the `serve` process delivers pending rows. The loop claims a row the way a worker claims a job, with a claim that expires. Two `serve` processes on one store thus never deliver the same row at once. A claim that expires makes delivery at-least-once, so an adapter whose creating call is not idempotent must recover the reference rather than create a second object. The `github-check` adapter lists all check runs of `head_sha` for the sink's check name (`filter=all`, since the default returns only the latest) and adopts the one whose `external_id` is the group id, before it creates one.

- Events of one group and sink deliver in order. A `started` row waits until the `queued` row of the same group and sink is delivered or abandoned.
- A process claims only the rows whose sink its own file defines. A row whose sink no process defines goes unclaimed, so it raises no error on any pass. Once such a row has outlived the window that the backoff allows, it becomes `abandoned`, and operational logging records the sink name. A replica that still runs the old file thus never abandons a row for a sink that an updated replica has just gained. The abandonment metric counts it under `format="unknown"`, since no definition names its format.
- A failed delivery retries with a bounded exponential backoff. After its last attempt, the row becomes `abandoned`. Operational logging records the abandonment, and a counter metric, `bajutsu_group_events_abandoned_total`, counts it by format.
- An adapter that finds no reference from an earlier event starts from scratch. For `github-check`, a `completed` event whose `queued` row was abandoned creates the check run in its completed state.
- A delivery never changes a job, a group, or a conclusion. The group's verdict is fixed before its `completed` row exists.

### The `github-check` adapter

The adapter resolves the App's installation for the stored repository and mints an installation token. `bajutsu/common/github/app.py` mints one today for a Git config source. The adapter needs a variant keyed by repository.

| Event | GitHub call | Fields |
|---|---|---|
| `queued` | `GET /repos/{owner}/{repo}/commits/{head_sha}/check-runs?check_name=…&filter=all` to adopt a run whose `external_id` is the group id, else `POST /repos/{owner}/{repo}/check-runs` | `name`, `head_sha`, `status: queued`, `external_id` set to the group id |
| `started` | `PATCH /repos/{owner}/{repo}/check-runs/{id}` | `status: in_progress`, `started_at` |
| `completed` | `PATCH /repos/{owner}/{repo}/check-runs/{id}` | `status: completed`, `conclusion`, `completed_at`, `output` |

The group's `conclusion` maps to the check's `conclusion` with the same name: `success`, `failure`, or `cancelled`.

### The check output

`output.title` gives the counts, such as `3 of 40 scenarios did not pass`. `output.summary` holds a table with one row per scenario that did not pass, followed by the count of passing scenarios. Each row names the scenario, its outcome, and the reason:

| Outcome | When | Reason shown |
|---|---|---|
| failed | the job is `done` with exit code 1 | the first failure line of the run's manifest |
| error | the job is `done` with another non-zero exit code | the exit code and its meaning, such as a usage error (2) or a signal |
| worker lost | the job is `failed` and its result carries the lease-expiry reason | the attempt count |
| worker error | the job is `failed` with an error a worker reported | the reported error, cut to one line |
| cancelled | the job ended cancelled and its `cancelReason` is not `failFast` | none |
| skipped | the job ended cancelled and its `cancelReason` is `failFast` | none; the failed rows name the failures |

Every reason is untrusted text: a worker reports its own error, and a manifest holds a scenario's own message. The adapter strips control characters, escapes `|`, and wraps the reason in a code span whose delimiter is longer than any run of backticks inside it. No reported error can then split a row or render Markdown in the check.

A job that ended cancelled takes the `cancelled` or `skipped` row, whatever its exit code.

The `skipped` row depends on the item that adds `failFast` and `cancelReason` to `run-set` (slug `run-set-fail-fast`). Until that item lands, the adapter never writes the `skipped` row, and every job that ended cancelled shows as `cancelled`.

GitHub caps `summary` at 65,535 characters. The adapter stops adding rows before the cap and ends the table with the count of rows left out. The adapter writes no annotations, since a scenario has no file and line in the pull request's diff.

### Prime directives

- **No model on the verdict.** The group's `conclusion` and every row of the output come from job statuses, exit codes, and manifests. No language model writes or reads any part of the check.
- **Determinism.** Delivery runs after the verdict and never feeds back into it. A delivery that never succeeds leaves the group, its jobs, and its runs as they were.
- **App-agnostic.** Sinks are operator configuration. The runner, the drivers, and the worker are unchanged.

### Documentation

`docs/self-hosting.md` gains a section on the sink file, the App permission, and a workflow example that passes `headSha`. `docs/devicefarm.md` gains the `notify` field of `run-set`. `docs/architecture.md` gains the group events and the delivery loop. Each page changes in both languages.

## Alternatives considered

- **Have the Actions job poll and set its own status.** This needs no server change, and the job's own result already appears as a check. But the runner waits for the whole group, which is the cost this item removes.
- **Take a webhook URL in the request.** A URL in the request would make the abstraction a plain POST. A GitHub check run does not fit that shape: it needs an App token, and each update addresses an id the creation returned. A request-supplied URL is also an SSRF path into the network `serve` runs in.
- **Extend the `notify:` list of BE-0099.** That list belongs to a target config and fires inside one `bajutsu run`. A group's lifecycle exists on `serve` alone, so the trigger cannot live in the run process. The new sinks reuse the word `format` for the same idea, an adapter per destination.
- **Name the events `hooks`.** The scenario and target vocabulary already uses `hooks` for steps that run before and after a scenario. An event delivered to a sink is a notification, not a step, so this item keeps the notification vocabulary.
- **Publish one check run per scenario.** Forty scenarios would add forty entries to the pull request's checks list. A single check with a table keeps the list readable, and its conclusion can serve as one required check.
- **Answer GitHub's re-run button.** A re-run request reaches the App as a `check_run` webhook with the `rerequested` action. `serve` would then need an inbound webhook endpoint and a way to register the group again. A re-run of the workflow already registers a fresh group, because the group key carries `GITHUB_RUN_ATTEMPT`. This item leaves the button out.
- **Give a queued job a deadline, so that "no worker" becomes an outcome.** `POST /api/run-set` already refuses a scenario that no live worker can serve, with status 400. The dispatching Actions job then fails, and its log names the missing tokens. A worker that goes away after registration still leaves a job waiting with no outcome. A deadline would close that gap, but it changes the queue's semantics for every job, not only for group jobs. This item leaves the deadline to a separate item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] The sink file, its validation at startup, and the App check
- [ ] The `notify` request field, its validation, its column, and the request digest
- [ ] The `group_events` table and the rows written by registration, lease, and the terminal paths, cancels included
- [ ] A structured lease-expiry reason in the reclaim result
- [ ] The `completed` snapshot: scenario names and failure lines, built once and stored on the row
- [ ] The delivery loop: claim, order, retry, abandonment, and the metric
- [ ] The `github-check` adapter: installation by repository, the recovery lookup and the three calls, and the output
- [ ] Documentation in both languages

## References

- [BE-0484 — Register jobs as a group and let a worker lease a group together](../BE-0484-job-group-lease/BE-0484-job-group-lease.md) — the group, its `conclusion`, and the group key.
- [BE-0099 — Webhook notifications for run results](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) — the per-run `notify:` list and its `format` field.
- [BE-0414 — Authenticate a CI job to serve with a GitHub Actions OIDC token](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md) — the machine session and its `repository` claim.
- [BE-0224 — Granting private-repository access for the GitHub config source](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md) — the GitHub App credentials this item reuses.
- [GitHub REST API: check runs](https://docs.github.com/en/rest/checks/runs) — the create, update, and list calls, their status and conclusion values, the `filter` default, and the output limits.
- [GitHub Actions: events that trigger workflows, `pull_request`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request) — `GITHUB_SHA` on a pull request names the merge commit.

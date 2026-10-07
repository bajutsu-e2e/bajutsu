**English** · [日本語](BE-XXXX-ci-job-audit-provenance-ja.md)

# BE-XXXX — Record which CI job acted in a machine principal's audit entries

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ci-job-audit-provenance.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is open |
| Topic | Hosting the web UI |
| Related | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

A continuous-integration (CI) job authenticates to a hosted `bajutsu serve` by exchanging the
OpenID Connect (OIDC) token GitHub Actions issues it for a short-lived machine session
([BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)). Each audit
entry that session writes names the repository and nothing narrower. This item records the GitHub
Actions job as well: the workflow run, its attempt, the job, and a link to that job's page. The
audit trail then answers "which job did this", where today it stops at "some job in this
repository".

## Motivation

An audit entry exists to let an operator trace an action back to whoever performed it. For a human
caller the entry's `actor_id` names a user. A pipeline has no user row, so BE-0414 leaves
`actor_id` null and writes the repository into the entry's detail payload instead. That repository
is too coarse to trace anything once a repository runs more than one workflow. Concurrent jobs, a
rerun of a failed job, and a pull request build next to a main-branch build all share one
repository. Their dispatched runs and uploaded artifacts produce audit entries an operator cannot
tell apart.

The token the job presents already says which job it is. GitHub Actions signs `run_id`,
`run_attempt`, and `check_run_id` into every OIDC token, beside the `repository` BE-0414 reads
([GitHub's claim reference](https://docs.github.com/en/actions/reference/security/oidc)).
`check_run_id` is the job's own identifier, the one in the job page's URL. `serve` verifies the
token's signature at the exchange, so these claims are as trustworthy as the repository is. `serve`
discards them today.

The outcome a later reader can check is the audit entry itself. An entry a machine session writes
for `POST /api/run` carries a link of the form
`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<check_run_id>`. That link opens the
exact job that dispatched the run, so two jobs from one repository write two distinguishable entries.

## Detailed design

The change follows the path the job's identity already takes: the token's claims, the machine
session, and the audit entry. The units below follow that path in order.

### Unit 1 — Read the job's claims at the exchange

The provider-independent `WorkloadClaims` (in `bajutsu/serve/oidc.py`) gains five fields read from
claims, plus a sixth, `job_url`, derived from them below. They follow the existing optional ones
(`environment`, `ref`, and `workflow_ref`).

| Field | GitHub Actions claim | Meaning |
|---|---|---|
| `run_id` | `run_id` | The workflow run |
| `run_attempt` | `run_attempt` | Which attempt of that run; a rerun reuses the `run_id` |
| `check_run_id` | `check_run_id` | The job inside the run |
| `sha` | `sha` | The commit the run built |
| `triggered_by` | `actor` | The GitHub account that started the run |

The field keeps GitHub's name `check_run_id` rather than a neutral `job_id`. `POST /api/run` answers
with serve's own `jobId`, so an audit key of that name would invite joining the two. Each field stays optional. A provider that emits no such claim, or a GitHub token minted before a
claim existed, maps it to `None`. The exchange never refuses a token for lacking one, because these
fields describe the job rather than authorize it. For the same reason the three ids accept a number
as well as a string: GitHub documents no type for them, and dropping a number would silently drop
the job link.

`OidcProvider` names each claim, the same way it already names `repository_claim`. It also carries
a `job_url` template, which turns the fields into the job page's URL. For GitHub Actions the template is
`https://github.com/{repository}/actions/runs/{run_id}/job/{check_run_id}`. The exchange leaves the URL
out unless `run_id` and `check_run_id` are both present. A template belongs on the provider table for the same
reason the claim names do: a later provider differs in that one string alone.

### Unit 2 — Carry the job on the machine session

The exchange flattens the claims into one job record. The record maps camel-case keys to strings
and omits each absent field. Its keys are `jobUrl`, `runAttempt`, `workflowRef`, `ref`, `sha`,
`environment`, and `triggeredBy`.

The record leaves out the repository, `run_id`, and `check_run_id`, because `jobUrl` already spells
all three, and the audit entry names the repository on its own. `runAttempt` stays, since a rerun
keeps the same URL. The cost is that a lookup by run id matches inside `jobUrl` rather than on a key
of its own. No code reads the audit log back today, so that cost has no caller yet.

The session stores the record beside the `org` and `kind` it already carries.

- **SQL store.** A nullable JSON column `ci_job` on `sessions`, added by a new migration. A row
  written before the migration reads as carrying no job.
- **Redis store.** A `ciJob` key in the session's JSON value.
- **In-memory store.** A field on `Principal`.

`Principal` gains the field `ci_job`, and `Principal.from_stored` narrows it on read. A value that
is not a mapping of strings to strings reads as absent. The same rule already narrows `identity`
and `org`.

The session is the right carrier because the claims exist only at the exchange. Every later
request presents the session cookie, never the token. A human session never carries a job.

### Unit 3 — Write the job into the audit entry

Both request backends already resolve a machine principal's org once, at the gate, and hand it to
the operations behind the machine allowlist as `machine_org`. The job record travels the same way, and both backends answer it from the principal the gate
already read. Every machine call that writes an audit entry carries the job.

- **`POST /api/run` and the artifact probe.** Both go through `RequestCtx`, which gains `ci_job()`.
- **The three artifact uploads.** Each backend streams these through a raw-body handler outside
  `RequestCtx`, and that handler passes the job to `bind_artifact` beside `machine_org`.

`GET /api/runs` and the job poll also take `machine_org`, but they write no audit entry and need no
job.

`_record_audit` (in `bajutsu/serve/authz.py`) takes the record as an optional `ci_job` argument. For
a machine principal it keeps writing `repository` into the detail payload, which BE-0414 documents,
and adds the record under `actor`. The key `actor` says what the record stands in for. The record
fills the role `actor_id` fills for a person, in the one place a pipeline can carry it.

```json
{
  "repository": "acme/app",
  "actor": {
    "jobUrl": "https://github.com/acme/app/actions/runs/123/job/456",
    "runAttempt": "1",
    "ref": "refs/heads/main",
    "sha": "...",
    "triggeredBy": "octocat"
  }
}
```

The exchange also writes an audit entry of its own, action `oidc.exchange`, with the repository as
its target and the same detail payload. Today `serve` logs an exchange to the operator log alone.
An entry in the audit log ties each later entry's job back to the moment its session began.

### Unit 4 — Tests and documentation

Tests cover each unit. One test checks the claim mapping, absent claims included. Another runs a
session round-trip through each store, a pre-migration row included. A third covers the audit
detail a machine-dispatched run writes. The last covers the `oidc.exchange` entry.

The "An audit entry names the repository" paragraph of `docs/self-hosting.md` describes the job
record, and `docs/ja/self-hosting.md` mirrors the change.

### Out of scope

- **A column on the run.** The audit entry for `POST /api/run` already names the run as its
  target. An operator finds the job by looking up that entry, so a run column would duplicate the
  same fact.
- **Showing the job in the web UI.** No page reads the audit log today. A reader for the audit
  log would be a feature of its own.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Write the job URL into `actor_id` | `actor_id` is a foreign key to `users.id`. An insert naming a job URL fails, and a synthetic user row per job would put pipelines in the roster `/api/orgs` discloses — the reason BE-0414 left the column null. |
| A dedicated audit column for the machine actor | No code reads the audit log back today, so nothing needs to filter on such a column yet. The detail payload already carries the repository, and a column would split one actor across two places. |
| Encode the job into the session identity (`repo:acme/app#123`) | Revocation matches sessions by identity. A per-job identity would make "revoke this repository's sessions" match nothing. |
| Read the job from a request-scoped context variable instead of passing it | The context variable would hide a dependency that `machine_org` already passes explicitly through `RequestCtx`, and both backends would need to reset it per request. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Read the job's claims at the exchange
- [x] Unit 2 — Carry the job on the machine session
- [x] Unit 3 — Write the job into the audit entry
- [x] Unit 4 — Tests and documentation

## References

- [BE-0414 — Authenticate a CI job to serve with a GitHub Actions OIDC token](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)
- [GitHub Docs — OpenID Connect reference](https://docs.github.com/en/actions/reference/security/oidc) (the claim table, `check_run_id` included)
- [GitHub Docs — OpenID Connect](https://docs.github.com/en/actions/concepts/security/openid-connect) (an example token payload, `sha` included)
- `bajutsu/serve/oidc.py`, `bajutsu/serve/operations/oidc.py`, `bajutsu/serve/authz.py`

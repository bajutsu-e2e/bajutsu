**English** · [日本語](BE-0160-worker-credential-free-uploads-ja.md)

# BE-0160 — Credential-free worker uploads via presigned URLs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0160](BE-0160-worker-credential-free-uploads.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0160") |
| Implementing PR | [#655](https://github.com/bajutsu-e2e/bajutsu/pull/655) |
| Topic | Hosting the web UI |
| Related | [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

Make the `bajutsu worker` hold **no cloud object-storage credentials at all**. Today a worker in
the hosted topology (`serve --backend=server` + `bajutsu worker`) reads and writes the object store
directly with its own credentials, to upload each run's artifact tree, download the org's visual
baselines before a run, and persist an authored scenario after a `record`. This proposal moves all
three onto the **presigned-URL brokering** that [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)
introduced for evidence upload: the **control plane holds the credentials** and vends short-lived
presigned GET/PUT URLs (and the list of baseline keys), so the worker uploads and downloads over
plain HTTP and needs only an HTTP client — no cloud SDK, no credentials.

## Motivation

BE-0110 established the security win — *the worker never needs cloud credentials* — but only for the
new **evidence** destination. For the existing artifact/baseline/scenario surface the worker still
constructs an object-store client from `object_store_from_env()` (`BAJUTSU_S3_BUCKET` +
`AWS_ACCESS_KEY_ID` / …), so an ephemeral Mac worker still carries the object store's secrets. That
is the larger exposure: workers are the numerous, disposable, GUI-session machines in the fleet, and
distributing long-lived bucket credentials to each is exactly what BE-0110's presigned design set out
to avoid.

Concretely, `bajutsu/serve/server/worker_job.py` and `bajutsu/cli/commands/worker.py` use the
worker-side store for three things:

- **Artifact upload** (`_upload_runs`) — the finished `runs/<id>/` tree is written under the
  artifact prefix so the control plane's `ObjectStorageArtifactStore` can serve reports.
- **Baseline download** (`_materialize_baselines`) — the org's visual baselines are read into the
  workspace before a run (a WRITE-less READ + LIST).
- **Authored-scenario save** (`_save_authored`) — a `record` job's output is written under the
  per-project scenario prefix.

Removing the worker's credentials for all three finishes the credential-free-worker goal, lets the
worker run without any cloud SDK (`boto3`) at all — only an HTTP client — and lets the object store's
credentials live in exactly one place — the control plane — which can then use a stricter-permissioned
or separate-account bucket without re-distributing secrets to the fleet.

## Detailed design

The control plane already holds the credentials and already owns the `ObjectStore` seam per org
(`ServeState.org_stores` / `for_org`). Each worker↔store interaction becomes: the worker asks the
control plane for signed URLs, then talks to object storage over plain HTTP. All key-building and
validation stays server-side (as in BE-0110's `generate_upload_urls`), so a worker can never escape
its org's prefix.

### 1. Artifact upload via presigned PUT

After a run, the worker enumerates `runs/<id>/**` and requests presigned PUT URLs for those keys
**under the org's artifact prefix** (`artifact_prefix(org_prefix(prefix, org))`), then PUTs each file
— the same shape BE-0110's worker uploader already uses for evidence. This generalizes BE-0110's
`POST /api/runs/<run_id>/upload-urls`: the endpoint gains a notion of *which* destination it signs
for (artifact vs evidence), or a sibling endpoint signs the artifact prefix. The server derives the
org from the request's auth/job, never from the worker.

### 2. Baseline download via presigned GET

The control plane knows the run's org at lease time, so it lists that org's baselines (a credentialed
LIST it can do) and returns `{name: presigned GET URL}` — either embedded in the lease response
(`/api/worker/lease`) or from a small `GET /api/runs/<job>/baseline-urls`. The worker downloads each
baseline over plain HTTP into its workspace before the run, replacing `_materialize_baselines`' direct
`ObjectBaselineStore` reads. `presigned_url` (the GET signer) already exists on the `ObjectStore`
protocol.

### 3. Authored-scenario save via presigned PUT

For a `record` job, the worker requests one presigned PUT URL for the authored scenario's key (under
the org's scenario prefix) and PUTs the file, replacing `_save_authored`'s direct
`ObjectScenarioStorage.save`.

### 4. Drop the worker's cloud client

With 1–3 in place, remove `_object_store()` / `object_store_from_env()` from the worker path
(`worker.py`, `worker_job.py`), so the worker constructs no cloud client and reads no
`BAJUTSU_S3_*` / AWS credentials. The worker no longer needs a cloud SDK (`boto3`) at runtime — the
`worker` extra is already empty, so this just makes that true in practice — and its only network
dependency stays its existing HTTP client. The **control plane** keeps the `server` extra (boto3 / GCS)
as today.

### 5. Endpoint + key-building generalization

Factor BE-0110's server-side key-builder + validator (`generate_upload_urls`) to serve multiple
destinations (evidence, artifacts, scenarios) rather than duplicating it. Each destination fixes its
own base prefix server-side; the worker supplies only relative keys, which the server re-validates
(`valid_relative_key`) so the org/prefix boundary holds. Presigned GET for baselines reuses the same
validation for the returned names.

### 6. Auth and org resolution

The worker authenticates to the control plane with the operator token it already sends
(`Authorization: Bearer`); the control plane resolves the org from the leased job (not from any
worker-supplied value), so multi-tenancy isolation is unchanged. No cloud credential ever leaves the
control plane.

## Alternatives considered

### A. Short-lived scoped credentials (STS / GCS token downscoping)

The control plane vends short-lived, prefix-scoped credentials (AWS STS `AssumeRole` with a session
policy, or GCS credential downscoping) and the worker uses the SDK with them. This is the cloud-native
least-privilege answer and handles LIST/GET/PUT uniformly, but it is heavier (IAM role + STS setup),
keeps the SDK dependency on the worker, and still places *credentials* (however short-lived) on the
worker — not the zero-credential end state this item targets. Presigned URLs need no IAM plumbing
beyond the credentials the control plane already has.

### B. Route bytes through the control plane

The worker POSTs artifact bytes to the control plane, which writes them to storage. This makes the
worker trivially credential-free but doubles bandwidth and load on the control plane and defeats
direct-to-storage upload — every run's video and screenshots would transit the control plane. BE-0110
rejected the analogous "direct-write sink" for the same reason; brokering signed URLs keeps the bytes
flowing worker→storage directly.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Generalize the presigned key-builder/validator to serve multiple destinations (evidence /
      artifacts / scenarios) server-side
- [x] Artifact upload via presigned PUT (worker requests URLs under the org artifact prefix, PUTs)
- [x] Baseline download via presigned GET (control plane lists + signs; worker GETs before the run)
- [x] Authored-scenario save via presigned PUT (`record` jobs)
- [x] Remove `object_store_from_env()` / `_object_store()` from the worker so its runtime needs no
      cloud SDK (`boto3`); the `worker` extra stays dependency-free
- [x] Tests — presigned artifact/baseline/scenario paths against a real HTTP server (no worker
      credentials); org-boundary re-validation
- [x] Documentation — update `docs/self-hosting.md` (worker needs no `BAJUTSU_S3_*` / AWS creds) and
      its Japanese mirror

### Log

- Shipped in one change: factored the presigned PUT signer into `operations/presign.py` (shared by
  evidence + artifacts); added the `worker_artifact_urls` / `worker_scenario_url` operations and
  routes, org resolved from the leased job; embedded baseline GET URLs in `/api/worker/lease`;
  replaced `execute_job_spec`'s object-store client with the injected `WorkerIO` seam and its
  presigned-URL-backed `PresignedWorkerIO` in `bajutsu worker`, dropping `object_store_from_env()` /
  `_object_store()` from the worker path; updated `docs/self-hosting.md` and its Japanese mirror.

## References

- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) — Evidence upload via presigned URLs (the pattern this generalizes; the credential-free-worker goal it began)
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) — Post-completion worker model (the worker↔control-plane HTTP loop this extends)
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — Public hosting (the multi-tenant server topology and per-org object-store prefixes)
- `bajutsu/serve/server/worker_job.py` — `_upload_runs` / `_materialize_baselines` / `_save_authored` (the three credentialed worker↔store paths this removes)
- `bajutsu/cli/commands/worker.py` — `_object_store()` (the worker's cloud client to drop) and the presigned uploader BE-0110 added
- `bajutsu/object_store.py` — the `ObjectStore` protocol with `presigned_url` (GET) and `presigned_put_url` (PUT)

**English** · [日本語](BE-0110-evidence-store-uri-ja.md)

# BE-0110 — Evidence upload to object storage via URI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0110](BE-0110-evidence-store-uri.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0110") |
| Implementing PR | [#531](https://github.com/bajutsu-e2e/bajutsu/pull/531), [#636](https://github.com/bajutsu-e2e/bajutsu/pull/636), [#638](https://github.com/bajutsu-e2e/bajutsu/pull/638) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
<!-- /BE-METADATA -->

## Introduction

Add the ability to upload run evidence (screenshots, element trees, reports, video, logs) to
S3-compatible or GCS object storage after a run completes. The destination is expressed as a
single URI (`s3://bucket/prefix` or `gs://bucket/prefix`), and the upload path controls
which cloud lifecycle policy applies — enabling teams to retain main-branch evidence
permanently while auto-deleting feature-branch evidence after a short period.

## Motivation

When `serve` runs in a hosted / CI context, test evidence lives on the worker's local
filesystem and disappears when the container or VM is recycled. Teams need evidence to
persist for auditing, debugging, and compliance — but not all evidence equally:

- **Main-branch merges** produce evidence that should be retained long-term (regression
  history, audit trail).
- **Feature-branch PR runs** produce evidence that is useful only during review and can be
  deleted after a few days to reduce storage costs.

Cloud object storage (S3, GCS) supports prefix-based lifecycle rules. By letting the caller
control the upload path, Bajutsu delegates retention policy entirely to the cloud provider —
no TTL logic, no garbage collection, no deletion code in Bajutsu itself.

The current `serve` architecture (BE-0015, BE-0106) already has an `ObjectStore` protocol
for S3 in `serve/server/object_store.py`, used for artifact reads, scenario storage, and
visual-baseline writes. This proposal extends that seam to cover post-run evidence upload,
adds GCS support, and unifies the configuration under a single URI flag on `serve`.

## Detailed design

### 1. URI scheme and parsing

A new URI type encodes the storage backend, bucket, and prefix in one string:

```
s3://my-bucket/evidence/main/
gs://my-bucket/evidence/feature/pr-123/
```

The scheme (`s3` or `gs`) selects the backend (`gs://` maps to the `gcs` backend
internally — the scheme follows the `gsutil` / `gcloud` convention while the backend name
matches the library). The first path segment is the bucket name; the remainder is the key
prefix. A trailing slash is normalized (always present internally).

A thin parser (`bajutsu/object_store.py`) produces a `StoreURI` dataclass:

```python
@dataclasses.dataclass(frozen=True)
class StoreURI:
    backend: Literal["s3", "gcs"]
    bucket: str
    prefix: str  # always ends with "/"
```

### 2. Unified `ObjectStore` protocol

The existing `serve/server/object_store.py` `ObjectStore` protocol is promoted to a
top-level module (`bajutsu/object_store.py`) so both `run` and `serve` can use it.

The protocol retains the current surface (`exists`, `get_bytes`, `put_bytes`, `put_file`,
`presigned_url`, `list_keys`) and extends it with:

- A `content_type` keyword argument on `put_bytes` and `put_file` so the upload step can
  set the correct MIME type per artifact.
- A `presigned_put_url` method for generating signed PUT URLs (the existing `presigned_url`
  is for GET).

```python
class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None: ...
    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None: ...
    def presigned_url(self, key: str) -> str: ...
    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = 3600) -> str: ...
    def list_keys(self, prefix: str) -> list[str]: ...
```

Two implementations, constructed from a `StoreURI`:

| Implementation | Backend | Dependency |
|---|---|---|
| `S3ObjectStore` | S3-compatible (AWS, MinIO, R2) | `boto3` (optional) |
| `GCSObjectStore` | Google Cloud Storage | `google-cloud-storage` (optional) |

A factory function (`object_store_from_uri(uri: StoreURI) -> ObjectStore`) selects the
implementation and raises a clear error if the required library is not installed.

### 3. Two-tier upload architecture

Evidence upload uses two modes depending on the deployment topology. The key design goal is
that the **worker never needs cloud credentials** when running through `serve`.

| Mode | Who holds credentials | Worker dependency | When |
|---|---|---|---|
| **Presigned URL** (serve) | Server (control plane) | `httpx` (promoted to runtime dep for the worker extra) | `serve` runs |
| **Direct SDK** (standalone) | The runner itself | `boto3` / `google-cloud-storage` (optional) | `bajutsu run --evidence-store` |

#### 3a. Presigned URL mode (serve — recommended)

The server holds the `ObjectStore` credentials and issues presigned PUT URLs. The worker
uploads via plain HTTP PUT — no SDK, no credentials.

```
1. Run completes on worker → evidence at runs/<run_id>/ locally
2. Worker → Server:  POST /api/runs/<run_id>/upload-urls
                     { "files": ["00-login/step-1/after.png", ...] }
3. Server validates each relative path (rejects empty, leading "/", ".." traversal)
   and generates a presigned PUT URL per file
   (bucket + prefix + evidence_prefix + run_id + relative_path)
4. Server → Worker:  { "urls": { "00-login/step-1/after.png": "https://...", ... } }
5. Worker uploads each file via HTTP PUT to the presigned URL
6. Worker → Server:  upload complete
```

Presigned PUT URLs are supported by both S3 (`generate_presigned_url("put_object", ...)`)
and GCS (V4 signed URLs). The TTL defaults to 1 hour, which is sufficient for uploading a
typical run's artifacts in a single batch.

#### 3b. Direct SDK mode (standalone `bajutsu run`)

For local or standalone CI usage (no `serve`), the runner uploads directly via the SDK:

```bash
bajutsu run --evidence-store gs://bucket/feature/pr-42/ scenarios/
```

This path requires `boto3` or `google-cloud-storage` and the corresponding credentials in
the environment. It is the same sequential upload as described below, just without the
presigned URL indirection.

### 4. Post-run upload step

In both modes, the upload runs **after** the run pipeline completes and the verdict is
final. It walks the local `runs/<run_id>/` directory tree and uploads each file, preserving
the relative path structure under the configured prefix:

```
Local:  runs/20260702-143000/00-login/step-1/after.png
Remote: s3://bucket/evidence/main/20260702-143000/00-login/step-1/after.png
```

Key behaviors:

- **Upload failure never changes the run verdict.** The run result is already written
  locally and reported. An upload error is logged as a warning and surfaced in the run
  summary, but the exit code stays as the verdict decided.
- **Content types are inferred** from file extensions (`.png` → `image/png`,
  `.json` → `application/json`, `.html` → `text/html`, etc.).
- **Concurrent upload** is acceptable as a future optimization but not required for the
  initial implementation — a sequential walk is fine.

The upload is wired into `runner/pipeline.py` as the last step, after report generation.

### 5. `serve` configuration

When running through `serve`, the evidence store is a server-level setting — not per-job.
The `serve` command accepts:

```bash
bajutsu serve --evidence-store s3://bucket/evidence/
```

or equivalently via environment variable:

```bash
BAJUTSU_EVIDENCE_STORE=s3://bucket/evidence/ bajutsu serve ...
```

The server holds the SDK credentials and the `ObjectStore` instance. Every job that
completes on this `serve` instance gets presigned PUT URLs for uploading evidence. The
run ID is always part of the path, so runs never collide.

CI controls the prefix by passing it as a job parameter when kicking the run (the
`/api/runs` endpoint accepts an optional `evidence_prefix` override that is appended to the
server's base URI). The server validates `evidence_prefix` as a safe relative path segment
(no leading `/`, no `..` traversal) before appending it to the base URI to prevent
key-escape. For example:

```bash
# CI kicks a run on serve, requesting a specific prefix
curl -X POST https://serve.example.com/api/runs \
  -d '{"config": "...", "evidence_prefix": "main/abc1234/"}'
```

This way the `serve` instance owns the bucket/credentials configuration, and CI only
controls the path — a clean separation of concerns. The worker never touches cloud
credentials.

### 6. Optional dependencies

Neither `boto3` nor `google-cloud-storage` becomes a required dependency. They are declared
as optional extras and are needed **only on the server (control plane) or in standalone
mode** — never on the worker:

```toml
[project.optional-dependencies]
s3 = ["boto3"]
gcs = ["google-cloud-storage"]
cloud = ["boto3", "google-cloud-storage"]
```

`uv sync --extra s3` or `uv sync --extra cloud` installs what is needed. If
`--evidence-store` is passed but the library is missing, the error message names the
exact install command.

### 7. Authentication

Bajutsu does **not** manage credentials. It delegates to the standard credential chains:

- **S3**: boto3's credential chain (env vars, `~/.aws/credentials`, IAM role, OIDC)
- **GCS**: `google-cloud-storage`'s ADC (env vars, `GOOGLE_APPLICATION_CREDENTIALS`,
  Workload Identity Federation, metadata server)

In the `serve` topology, only the **server** needs these credentials — the worker uploads
via presigned URLs and requires no cloud SDK or credentials at all. This keeps the worker
lightweight and avoids distributing secrets to ephemeral containers.

## Alternatives considered

### A. Config-file-based storage specification

```yaml
evidenceStore:
  backend: s3
  bucket: my-bucket
  region: ap-northeast-1
  prefix: evidence/
```

Rejected: splitting bucket/region/prefix across config fields is less intuitive than a
single URI. The URI is self-contained, greppable, and familiar from tools like `aws s3 cp`.
Region and endpoint can be set via the environment variables the existing
`s3_client_from_env()` already consumes (`BAJUTSU_S3_REGION` / `AWS_REGION`,
`BAJUTSU_S3_ENDPOINT`).

### B. Direct-write `ObjectStoreSink` (skip local filesystem)

Write evidence directly to object storage during the run, bypassing local `FileSink`. This
would avoid the local disk round-trip but:

- Complicates error handling (a network blip mid-run could lose evidence)
- Breaks the report generator, which reads local files to build `manifest.json` and
  `report.html`
- Makes local debugging harder (no local copy)

The post-run upload is simpler, safer, and keeps the existing pipeline unchanged.

### C. Separate upload command (`bajutsu upload`)

A standalone command that uploads an existing `runs/` directory. This could coexist with the
integrated upload but adds another step to CI pipelines. The integrated `--evidence-store`
flag is zero-friction — no extra step, no risk of forgetting it.

The standalone command could be added later as a convenience (e.g. retroactively uploading
old runs) without conflicting with this design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] URI scheme parsing and `StoreURI` dataclass
- [x] Promote `ObjectStore` protocol to top-level module
- [x] `S3ObjectStore` implementation (reuse existing code, add `presigned_put_url` + `content_type`)
- [x] `GCSObjectStore` implementation (including V4 signed PUT URLs)
- [x] Presigned URL upload endpoint (`POST /api/runs/<run_id>/upload-urls`)
- [x] Worker-side HTTP PUT uploader (presigned URL mode)
- [x] Direct SDK upload fallback (standalone `bajutsu run` mode)
- [x] Post-run upload step (mode selection) — landed at the seam each mode actually uses, not in
      `runner/pipeline.py`: standalone at the CLI layer (`run.py`, after the verdict, mirroring
      `--zip`), and the serve/presigned mode in the `bajutsu worker` HTTP loop after the run,
      alongside `console.log`
- [x] `serve` `--evidence-store` flag and `BAJUTSU_EVIDENCE_STORE` env
- [x] `bajutsu run` `--evidence-store` CLI flag (with `BAJUTSU_EVIDENCE_STORE` env)
- [x] `evidence_prefix` parameter on the `/api/run` endpoint
- [x] Optional dependency declarations (`s3` / `gcs` / `cloud` extras)
- [x] Tests — standalone (URI parsing, S3/GCS stores incl. `content_type` + presigned GET/PUT
      generation, `upload_tree`), the serve endpoint (`generate_upload_urls` keying/validation, both
      HTTP shells, `evidence_prefix` carry, store wiring), and the worker uploader (file enumeration,
      presigned PUT with matching content-type, best-effort per-file failure) against a real HTTP server
- [x] Documentation — `run --evidence-store` and `serve --evidence-store` in `docs/cli.md`, and the
      presigned serve topology in `docs/self-hosting.md` (English + Japanese mirrors)

Log:

- **Slice 1 — foundation + standalone direct-SDK upload**: promoted the `ObjectStore`
  protocol and `S3ObjectStore` to the top-level `bajutsu/object_store.py` (`serve/server/object_store.py`
  now re-exports them), added `StoreURI` + `parse_store_uri`, a `GCSObjectStore`, `content_type` on
  the write methods, `presigned_put_url` (S3 + GCS V4), the `object_store_from_uri` factory, and an
  `upload_tree` helper. Wired `bajutsu run --evidence-store` to upload the finished run tree after the
  verdict (a failure only warns, never flips pass/fail). Added the `s3` / `gcs` / `cloud` extras. The
  presigned-URL serve path (endpoint, worker HTTP PUT uploader, `serve --evidence-store`,
  `evidence_prefix`) is a follow-up slice.
- **Slice 2a — presigned serve endpoint (server side)**: added the
  `POST /api/runs/<run_id>/upload-urls` operation (`generate_upload_urls`) — the server holds the
  evidence store's credentials and returns one presigned PUT URL per file, re-validating the run id,
  the per-run `evidence_prefix`, and every file path so a worker can't escape the run's key namespace
  (empty URLs when no store is configured, so a worker can always ask). Wired the route into both the
  stdlib handler and the FastAPI app. Added the `serve --evidence-store` flag / `BAJUTSU_EVIDENCE_STORE`
  env (resolved to an `EvidenceTarget` in the CLI, failing fast on a bad URI / missing SDK), the
  `evidence_prefix` parameter on `/api/run` (validated, carried onto the `Job` and the job spec), and
  the `EvidenceTarget` + `evidence_target_from_uri` / `content_type_for` helpers. The worker-side
  HTTP PUT uploader and the serve-topology docs are the next slice.
- **Slice 2b — worker presigned uploader + docs** (completes the item): wired the `bajutsu worker`
  loop to upload a finished run's evidence via the presigned endpoint — after the run and
  `console.log`, it enumerates the run tree, requests one PUT URL per file, and uploads over plain
  HTTP with the matching content-type (no cloud credentials of its own), best-effort so a failure only
  warns. Documented `serve --evidence-store` and the presigned serve topology in `docs/cli.md` and
  `docs/self-hosting.md` (English + Japanese). This completes BE-0110.

## References

- `bajutsu/evidence.py` — `EvidenceSink` / `FileSink` (current local write path)
- `bajutsu/serve/server/object_store.py` — existing `ObjectStore` protocol and `S3ObjectStore`
- `bajutsu/serve/artifacts.py` — `ArtifactStore` protocol (read-back side)
- `bajutsu/report/archive.py` — ZIP archiving of a run directory
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — Public hosting (the server topology that motivates remote storage)
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) — Post-completion worker model (the async job pipeline where upload fits)
- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) — Run report zip export (related: portable artifact packaging)

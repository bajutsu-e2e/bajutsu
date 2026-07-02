**English** · [日本語](BE-XXXX-evidence-store-uri-ja.md)

# BE-XXXX — Evidence upload to object storage via URI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-evidence-store-uri.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Topic | Hosting the web UI (cloud / self-hosted) |
| Related | [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
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
for S3 in `serve/server/object_store.py`, but it is used only for the server's internal
artifact reads. This proposal extends that seam to cover post-run evidence upload, adds GCS
support, and unifies the configuration under a single URI flag on `serve`.

## Detailed design

### 1. URI scheme and parsing

A new URI type encodes the storage backend, bucket, and prefix in one string:

```
s3://my-bucket/evidence/main/
gs://my-bucket/evidence/feature/pr-123/
```

The scheme (`s3` or `gs`) selects the backend. The first path segment is the bucket name;
the remainder is the key prefix. A trailing slash is normalized (always present internally).

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

The protocol keeps its current surface:

```python
class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None: ...
    def put_file(self, key: str, path: Path) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
```

Two implementations, constructed from a `StoreURI`:

| Implementation | Backend | Dependency |
|---|---|---|
| `S3ObjectStore` | S3-compatible (AWS, MinIO, R2) | `boto3` (optional) |
| `GCSObjectStore` | Google Cloud Storage | `google-cloud-storage` (optional) |

A factory function (`object_store_from_uri(uri: StoreURI) -> ObjectStore`) selects the
implementation and raises a clear error if the required library is not installed.

### 3. Post-run upload step

The upload runs **after** the run pipeline completes and the verdict is final. It walks
the local `runs/<run_id>/` directory tree and uploads each file, preserving the relative
path structure under the configured prefix:

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

### 4. `serve` configuration (unified)

When running through `serve`, the evidence store is a server-level setting — not per-job.
The `serve` command accepts:

```bash
bajutsu serve --evidence-store s3://bucket/evidence/
```

or equivalently via environment variable:

```bash
BAJUTSU_EVIDENCE_STORE=s3://bucket/evidence/ bajutsu serve ...
```

Every job that completes on this `serve` instance uploads its evidence to this store. The
run ID is always part of the path, so runs never collide.

CI controls the prefix by passing it as a job parameter when kicking the run (the
`/api/runs` endpoint accepts an optional `evidence_prefix` override that is appended to the
server's base URI). For example:

```bash
# CI kicks a run on serve, requesting a specific prefix
curl -X POST https://serve.example.com/api/runs \
  -d '{"config": "...", "evidence_prefix": "main/abc1234/"}'
```

This way the `serve` instance owns the bucket/credentials configuration, and CI only
controls the path — a clean separation of concerns.

### 5. Standalone `bajutsu run` support

For local / standalone CI usage (no `serve`), the same flag works on `bajutsu run`:

```bash
bajutsu run --evidence-store gs://bucket/feature/pr-42/ scenarios/
```

This is the same mechanism, just wired at the CLI level instead of the server level.

### 6. Optional dependencies

Neither `boto3` nor `google-cloud-storage` becomes a required dependency. They are declared
as optional extras:

```toml
[project.optional-dependencies]
s3 = ["boto3"]
gcs = ["google-cloud-storage"]
cloud = ["bajutsu[s3]", "bajutsu[gcs]"]
```

`uv sync --extra s3` or `uv sync --extra cloud` installs what is needed. If
`--evidence-store` is passed but the library is missing, the error message names the
exact install command.

### 7. Authentication

Bajutsu does **not** manage credentials. It delegates to the standard credential chains:

- **S3**: boto3's credential chain (env vars, `~/.aws/credentials`, IAM role, OIDC)
- **GCS**: `google-cloud-storage`'s ADC (env vars, `GOOGLE_APPLICATION_CREDENTIALS`,
  Workload Identity Federation, metadata server)

This keeps secrets out of Bajutsu's config and lets CI platforms use their native
credential mechanisms (GitHub Actions OIDC, etc.).

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
Region and endpoint can be set via the standard SDK environment variables
(`AWS_DEFAULT_REGION`, `BAJUTSU_S3_ENDPOINT`).

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

- [ ] URI scheme parsing and `StoreURI` dataclass
- [ ] Promote `ObjectStore` protocol to top-level module
- [ ] `S3ObjectStore` implementation (reuse existing code)
- [ ] `GCSObjectStore` implementation
- [ ] Post-run upload step in `runner/pipeline.py`
- [ ] `serve` `--evidence-store` flag and `BAJUTSU_EVIDENCE_STORE` env
- [ ] `bajutsu run` `--evidence-store` CLI flag
- [ ] `evidence_prefix` parameter on the `/api/runs` endpoint
- [ ] Optional dependency declarations (`s3` / `gcs` / `cloud` extras)
- [ ] Tests (URI parsing, upload logic with mocked store, serve integration)
- [ ] Documentation (English + Japanese)

## References

- `bajutsu/evidence.py` — `EvidenceSink` / `FileSink` (current local write path)
- `bajutsu/serve/server/object_store.py` — existing `ObjectStore` protocol and `S3ObjectStore`
- `bajutsu/serve/artifacts.py` — `ArtifactStore` protocol (read-back side)
- `bajutsu/report/archive.py` — ZIP archiving of a run directory
- [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — Public hosting (the server topology that motivates remote storage)
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) — Post-completion worker model (the async job pipeline where upload fits)
- [BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) — Run report zip export (related: portable artifact packaging)

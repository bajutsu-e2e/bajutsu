**English** · [日本語](BE-0204-server-storage-gcs-support-ja.md)

# BE-0204 — GCS support for server-side object storage

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0204](BE-0204-server-storage-gcs-support.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0204") |
| Implementing PR | [#838](https://github.com/bajutsu-e2e/bajutsu/pull/838) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal adds a Google Cloud Storage (GCS) backend to the **server-side** object-storage seam
(`bajutsu/serve/server/object_store.py`). That seam backs `ArtifactStore`, `ScenarioStore`, and the
visual-baseline store for `bajutsu serve --backend=server`, and today it builds an
`S3ObjectStore` and nothing else. [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) already
built a backend-agnostic `ObjectStore` protocol, a `GCSObjectStore`, and a single-URI factory
(`object_store_from_uri`, `s3://…` / `gs://…`) for the separate evidence-upload path. This
proposal reuses that machinery for server storage, so a self-hosted deployment on Google Cloud is
not forced to also stand up an S3-compatible bucket.

## Motivation

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (Bajutsu's own
hosted service) chose Cloudflare R2 and
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting) chose
MinIO, both S3-compatible, for the server-side artifact store. Both items explicitly rejected GCS
as an alternative. That was a reasonable default for Bajutsu's own recommended stack, but the
**implementation** hard-codes the choice. `object_store_from_env()`
(`bajutsu/serve/server/object_store.py`) constructs an `S3ObjectStore` and nothing else, and
`_build_server_state` (`bajutsu/serve/__init__.py:268-270`) raises `ValueError` unless
`BAJUTSU_S3_BUCKET` is set. A self-hoster who already runs the rest of their stack on Google Cloud
(GKE control plane, Cloud SQL, …) has no way to point the server's own storage at GCS, even though
nothing in the seams that read and write that storage depends on S3 specifically.

BE-0110 already solved the identical problem for a different seam, evidence upload. It promoted
`ObjectStore` to a top-level, backend-agnostic protocol so that `run` and `serve` could share one
seam. It also added a `StoreURI` parser and an `object_store_from_uri` factory, which build either
an `S3ObjectStore` or a working, tested `GCSObjectStore` from one URI, and it wired both to
`--evidence-store` / `BAJUTSU_EVIDENCE_STORE`. The server-side consumers are
`ObjectStorageArtifactStore` (`bajutsu/serve/server/artifacts.py`), `ObjectScenarioStorage`
(`bajutsu/serve/server/scenarios.py`), and `ObjectBaselineStore`
(`bajutsu/serve/server/baselines.py`). They already depend on the same `ObjectStore` protocol, not
on `S3ObjectStore` directly. They receive an `S3ObjectStore` every time because the server's own factory
predates BE-0110's GCS work and never adopted it.

The result is an inconsistency inside one deployment. Evidence can already be uploaded to
`gs://…`, but the same server is forced onto an S3-compatible bucket for everything else it
stores (run artifacts, scenarios, visual baselines). Closing that gap removes an unnecessary
two-cloud-vendor requirement for GCS-based self-hosters. It does so by reusing code that already
exists, is already tested, and already ships behind the optional `gcs` extra.

## Detailed design

### 1. A URI-based server-storage setting

Configure server storage the way `--evidence-store` is already configured: one URI names the
backend, bucket, and prefix (for example `gs://bucket/prefix` or `s3://bucket/prefix`). The URI is
the value of the server's **own**, independent setting (a `BAJUTSU_SERVER_STORE`-style env var;
exact name TBD at implementation time). The setting is deliberately **not** `BAJUTSU_EVIDENCE_STORE`.
The two stay independent so that each deployment can point them at different buckets or backends,
and both now accept either scheme (see *Alternatives considered* for why the settings are not
merged).

### 2. Rebuild the server factory on the existing URI machinery

`object_store_from_env()` in `bajutsu/serve/server/object_store.py` is rebuilt on top of
`bajutsu.object_store.parse_store_uri` / `object_store_from_uri`, the functions `--evidence-store`
already uses. This replaces the hand-rolled, S3-only `boto3` client built from
`BAJUTSU_S3_BUCKET` / `BAJUTSU_S3_ENDPOINT` / `BAJUTSU_S3_REGION`. The factory returns the
`ObjectStore` implementation (`S3ObjectStore` or `GCSObjectStore`) that the URI's scheme selects.
When the corresponding SDK is not installed, the factory raises the same "install the missing
extra" error that `object_store_from_uri` already raises for evidence storage.

### 3. No changes needed downstream of the factory

The consumers already depend on the `ObjectStore` protocol, not on `S3ObjectStore`.
`ObjectStorageArtifactStore`, `ObjectScenarioStorage`, and `ObjectBaselineStore` call nothing
beyond `exists` / `get_bytes` / `put_bytes` / `presigned_url` / `list_keys`, all of which are
backend-agnostic. The key-prefix helpers (`artifact_prefix`, `scenario_prefix`, `baseline_prefix`,
`org_prefix`) are pure string functions with no storage dependency at all. Swapping the factory in
`object_store_from_env()` is the entire functional change, and nothing else needs to know which
backend is in play.

### 4. Wiring and error messages

`_build_server_state` (`bajutsu/serve/__init__.py`) drops its S3-specific error message
(`"BAJUTSU_S3_BUCKET is required for --backend=server"`) for one naming the new URI-based
setting. `s3_prefix()` is replaced by reading the prefix out of the parsed `StoreURI` (which
already normalizes a trailing `/`), so a separate `BAJUTSU_S3_PREFIX` env var is no longer needed.

### 5. Documentation

`docs/self-hosting.md` (+ Japanese mirror) gains a GCS example for server storage alongside the
existing S3/R2/MinIO one, mirroring how BE-0110 documented the GCS option for `--evidence-store`.

## Alternatives considered

### A. Default server storage to `BAJUTSU_EVIDENCE_STORE` when unset

Rejected. Evidence retention is deliberately per-run-path and often short-lived: BE-0110's whole
point is to let feature-branch evidence auto-expire via a cloud lifecycle rule on its prefix.
Server storage (scenarios, visual baselines, and the artifacts users browse in the report viewer)
is long-lived and org-scoped. Defaulting one setting from the other would let a deployment's
evidence-retention lifecycle rule silently apply to data it was never meant to expire. An operator
who wants one bucket for both can already point both settings at the same URI explicitly.

### B. Add a parallel `BAJUTSU_GCS_BUCKET` / `BAJUTSU_GCS_*` env-var family

Rejected. This duplicates the `BAJUTSU_S3_*` sprawl (bucket/endpoint/region/prefix) for a second
backend instead of reusing the single-URI form that BE-0110 already proved out for evidence
storage. A URI also keeps the two storage settings visually and operationally consistent.

### C. Leave server storage S3-only; treat GCS support as out of scope

Rejected. None of the seams that consume `ObjectStore` is actually S3-specific, because BE-0110
already made the abstraction backend-agnostic and shipped a tested `GCSObjectStore`. The only place
that hard-codes S3 is the small `object_store_from_env` factory. The cost of adding GCS there is
low relative to the real deployment constraint it removes for self-hosters running on Google
Cloud.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Decide the final env-var / URI naming for server storage (replacing or living alongside
      `BAJUTSU_S3_BUCKET` / `BAJUTSU_S3_ENDPOINT` / `BAJUTSU_S3_REGION` / `BAJUTSU_S3_PREFIX`)
- [x] Rebuild `object_store_from_env()` on `parse_store_uri` / `object_store_from_uri`
      (`bajutsu/serve/server/object_store.py`)
- [x] Update `_build_server_state` wiring and error messages (`bajutsu/serve/__init__.py`)
- [x] Tests: server backend construction from a `gs://` URI (fake `GCSObjectStore`), missing-extra
      error message, and the existing S3 path staying green
- [x] Documentation: `docs/self-hosting.md` + Japanese mirror get a GCS example for server storage

[#838](https://github.com/bajutsu-e2e/bajutsu/pull/838) — `BAJUTSU_SERVER_STORE` (`s3://bucket/prefix`
or `gs://bucket/prefix`) replaces `BAJUTSU_S3_BUCKET`/`BAJUTSU_S3_PREFIX`;
`BAJUTSU_S3_ENDPOINT`/`BAJUTSU_S3_REGION` are unchanged, still
read by `object_store_from_uri` for the S3-compatible client. `bajutsu.object_store` gained a new
neutrally-named `store_target_from_uri` (returning `(ObjectStore, prefix)`) that both
`evidence_target_from_uri` (`--evidence-store`) and the server's `object_store_from_env()` now build
on — avoiding the evidence-flavored `EvidenceTarget` name leaking into a second, independent setting
while still sharing the URI-parsing composition (raised during review). Downstream consumers
(`ObjectStorageArtifactStore`, `ObjectScenarioStorage`, `ObjectBaselineStore`) needed no change,
matching the proposal. `deploy/self-host/.env.example` and `docker-compose.yml`'s `minio-init` (bucket
name extracted from the URI, with a scheme/bucket guard) were updated alongside the bilingual docs.

## References

- `bajutsu/serve/server/object_store.py` — the server-side factory this proposal extends
- `bajutsu/serve/__init__.py` — `_build_server_state`, which wires the factory into `--backend=server`
- `bajutsu/serve/server/artifacts.py`, `bajutsu/serve/server/scenarios.py`,
  `bajutsu/serve/server/baselines.py` — the `ObjectStore`-protocol consumers, unchanged by this
  proposal
- `bajutsu/object_store.py` — the backend-agnostic `ObjectStore` protocol, `StoreURI`,
  `object_store_from_uri`, and `GCSObjectStore` this proposal reuses
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — chose an
  S3-compatible bucket (Cloudflare R2) for the hosted server's artifact storage
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — chose an
  S3-compatible bucket (MinIO) for the self-hosted server's artifact storage
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) — built the
  backend-agnostic `ObjectStore` / `StoreURI` / `GCSObjectStore` machinery this proposal reuses

**English** · [日本語](BE-0190-org-scoped-crawl-history-ja.md)

# BE-0190 — Org-scoped crawl history on the server backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0190](BE-0190-org-scoped-crawl-history.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0190") |
| Implementing PR | [#781](https://github.com/bajutsu-e2e/bajutsu/pull/781) |
| Topic | Hosting the web UI |
| Related | [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md) |
| Origin | Review of [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md) |
<!-- /BE-METADATA -->

## Introduction

Make the Crawl tab's history list ([BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md))
work on the **server backend** the same way it works locally. BE-0180 shipped a read-only crawl
history viewer keyed on each run's `screenmap.json`, but its listing scans the local `runs_dir`
directly. On the server backend, run artifacts live in an org-scoped object store, not on the local
filesystem, so BE-0180 deliberately returns an empty list there
([`crawl_runs_payload`](../../bajutsu/serve/operations/reads.py) gates on `state.repository`). This
item closes that gap: it moves crawl-history listing onto the same org-scoped `ArtifactStore` seam
that already backs `/api/runs` and `/runs/<id>/...`, so hosted deployments get the same history —
correctly tenant-scoped.

## Motivation

On the server backend ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) multi-tenancy), the run history and every run artifact are served
from the actor's org-scoped store: `runs_payload` reads the org's recorded runs, and `/runs/<id>/...`
serves bytes through `state.for_org(state.org_of(actor)).artifacts`. Crawl history is the one
run-history surface that does not follow this pattern — it scans `state.runs_dir`, a local path.

That leaves two problems on the hosted backend, both flagged in the BE-0180 review:

1. **Non-functional.** A crawl run's artifacts (`screenmap.json`, `crashes/*.yaml`, `flows/*.yaml`)
   are written to the object store, not `runs_dir`, so a local scan finds nothing — the Crawl tab's
   History is always empty for a hosted user, even though their crawls produced maps.
2. **Tenant safety.** If `runs_dir` ever held mixed-org scratch data, a local scan would surface run
   ids across org boundaries — the exact cross-tenant leak the org-scoped store exists to prevent.

BE-0180 chose the safe interim: disable the endpoint when a repository is wired, rather than leak or
mislead. This item removes that limitation by giving the store itself a crawl-listing capability, so
the history works on every backend without a special case.

## Detailed design

**1. Extend the `ArtifactStore` seam with a crawl listing.** Add `list_crawl_runs()` to the
[`ArtifactStore`](../../bajutsu/serve/artifacts.py) Protocol, mirroring the existing `list_runs()`
but keyed on `screenmap.json` instead of `manifest.json`. It returns the same summary shape BE-0180's
helper already produces — `id`, `screens`, `transitions`, `crashes`, `crashFiles`, `flowFiles` — so
the `/api/crawl/runs` payload and the Crawl-tab JS are unchanged.

**2. Implement it for both stores.**
- [`LocalArtifactStore`](../../bajutsu/serve/artifacts.py) delegates to the existing
  `list_crawl_runs(runs_dir)` helper (BE-0180), so the local path is unchanged and already tested.
- [`ObjectStorageArtifactStore`](../../bajutsu/serve/server/artifacts.py) enumerates run prefixes that
  contain a `<runId>/screenmap.json` object, reads each map once for the counts, and lists the
  `<runId>/crashes/*.yaml` and `<runId>/flows/*.yaml` object keys for the file names. The store is
  already org-scoped (the org prefix is baked into the store instance), so the listing is tenant-safe
  by construction — no run id from another org is reachable.

**3. Make `crawl_runs_payload` org-scoped.** Replace the `state.repository`-gated stub in
[`reads.py`](../../bajutsu/serve/operations/reads.py) with the same pattern `runs_payload` uses:
resolve the actor's org, and call `state.for_org(org).artifacts.list_crawl_runs()`. The local backend
(no repository) resolves to the default org and its `LocalArtifactStore`, preserving today's behavior.

**4. Thread the actor through both transports.** `crawl_runs_payload` takes `actor`, and the stdlib
handler ([`handler.py`](../../bajutsu/serve/handler.py)) and the FastAPI app
([`server/app.py`](../../bajutsu/serve/server/app.py)) forward `self._actor()` / `_actor(request)`,
exactly as they do for `/api/runs`. Local mode ignores the actor; the server backend uses it to pick
the org store.

**5. Tests.** A unit test over `ObjectStorageArtifactStore.list_crawl_runs()` against the in-memory /
local object-store double the server tests already use (screenmaps in two orgs' prefixes → each store
sees only its own). An operations test that `crawl_runs_payload` lists an org's crawls and excludes
another org's, mirroring `test_runs_payload_lists_from_the_repository_scoped_to_the_org`. The local
path stays covered by BE-0180's existing tests.

The listing stays read-only and AI-free end to end — it enumerates and summarizes stored artifacts
and never touches the `run`/CI verdict path, so prime directive 1 holds exactly as it did for BE-0180.

## Alternatives considered

- **Leave the endpoint disabled on the server backend (BE-0180's interim).** Rejected as the end
  state: it means hosted users never see their crawl history, a permanent gap in the multi-tenant Web
  UI. Acceptable only as the stopgap it was.
- **Record crawl runs in the system of record (the database), like finished replay runs.** Rejected:
  a crawl has no pass/fail verdict and no manifest, so it doesn't fit the `RunRecord` shape, and the
  `screenmap.json` (the summary source) already lives in the object store. Listing from the store is
  the smaller, truthful change; a database row would duplicate state the store already holds.
- **A separate object-store scan outside the `ArtifactStore` seam.** Rejected: it would reintroduce a
  second, unscoped path to run artifacts — the very thing that made the local scan unsafe. Routing
  through the org-scoped store is what makes the listing tenant-safe.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `list_crawl_runs()` on the `ArtifactStore` Protocol
- [x] `LocalArtifactStore.list_crawl_runs()` (delegates to the BE-0180 helper)
- [x] `ObjectStorageArtifactStore.list_crawl_runs()` (scan by `<runId>/screenmap.json`, list crash/flow keys)
- [x] `crawl_runs_payload` made org-scoped via `state.for_org(org).artifacts`
- [x] Actor threaded through the stdlib handler and the FastAPI app routes
- [x] Tests: object-store listing (org isolation) + org-scoped payload

Log:

- [#781](https://github.com/bajutsu-e2e/bajutsu/pull/781) — Shipped the org-scoped crawl listing: added `list_crawl_runs()` to the `ArtifactStore`
  seam (local delegates to the BE-0180 helper; the object store scans `<runId>/screenmap.json` in one
  pass and indexes each run's direct `crashes/*.yaml` / `flows/*.yaml` keys), extracted the shared
  `helpers.crawl_run_summary` so both backends emit an identical entry, rewired `crawl_runs_payload`
  onto `state.for_org(state.org_of(actor)).artifacts`, and threaded the actor through both transports.

## References

- [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md) — the crawl history
  viewer this item extends; its `crawl_runs_payload` gates off on the server backend, which this
  item removes.
- [`bajutsu/serve/artifacts.py`](../../bajutsu/serve/artifacts.py) — the `ArtifactStore` Protocol and
  `LocalArtifactStore`, where `list_runs()` lives and `list_crawl_runs()` would join it.
- [`bajutsu/serve/server/artifacts.py`](../../bajutsu/serve/server/artifacts.py) — the object-storage
  artifact store to implement the crawl listing for.
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) — `runs_payload`
  (the org-scoped pattern to mirror) and `crawl_runs_payload` (the stub to replace).
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) multi-tenancy — the
  org-scoping model (`state.for_org` / `state.org_of`) this listing joins.

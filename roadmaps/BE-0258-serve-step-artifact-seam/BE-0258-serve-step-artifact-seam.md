**English** · [日本語](BE-0258-serve-step-artifact-seam-ja.md)

# BE-0258 — Route serve step-artifact reads through the ArtifactStore seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0258](BE-0258-serve-step-artifact-seam.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0258") |
| Implementing PR | [#1075](https://github.com/bajutsu-e2e/bajutsu/pull/1075) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState.artifacts` (`bajutsu/serve/state.py:346-348`) is the seam that BE-0015 built. It lets
run artifacts be read back the same way from either backend:

- `LocalArtifactStore` (`bajutsu/serve/artifacts.py`) reads files confined to `runs_dir`.
- `ObjectStorageArtifactStore` (`bajutsu/serve/server/artifacts.py`) fetches the same run-relative
  paths from S3-compatible object storage. It hands back a signed-URL redirect and does not inline
  bytes.

`_persist_run` and `_read_manifest` (`bajutsu/serve/jobs.py:338-343`) already go through the seam
correctly. So do `_run_manifests` and `run_set_manifests` (`bajutsu/serve/operations/reads.py:183-227`).
`_read_manifest` reads the `manifest.json` of a run via `state.artifacts.open_bytes(...)`, not via a
`runs_dir` path.

Several other read paths in the same package bypass the seam and reach straight into the local
filesystem:

- `_step_artifacts` (`bajutsu/serve/operations/reads.py:289-342`) builds the per-step artifact list
  that the `/api/scenario?runId=...` response of the scenario editor embeds. It reads
  `state.runs_dir / run_id / "manifest.json"` (`reads.py:301`) directly. It also probes
  `state.runs_dir / run_id / step_id / "after.png"` for existence (`reads.py:327`) directly.
- `resolve_scenario_pick` (`bajutsu/serve/operations/reads.py:461-520`) backs
  `POST /api/scenario/resolve`. That endpoint serves the "pick an element from a stored screenshot"
  flow, used when editing a scenario without a live driver. The function reads
  `state.runs_dir / run_id / step_id / "elements.json"` directly (`reads.py:497`).
- `coverage_view` (`bajutsu/serve/operations/coverage.py:66-72`) passes `state.runs_dir` straight
  into `bajutsu.coverage.read_exchanges` and `read_observed_ids`. Those functions glob
  `network.json` and `elements.json` files under it (`bajutsu/coverage.py:341-380`).
- `start_capture` (`bajutsu/serve/operations/capture.py:62-65`) writes the live screenshot of a
  capture session under `state.runs_dir / "_capture"`. A later read in
  `bajutsu/serve/handler.py:605-611` reads it back.

The hosted `server` backend (`bajutsu/serve/server/app.py`) reaches two of these reads most directly:

- `read_scenario` (`reads.py:265-286`, which calls `_step_artifacts`) is wired to `/api/scenario` at
  `app.py:222-240`.
- `resolve_scenario_pick` is wired to `/api/scenario/resolve` at `app.py:438-440`.

On the hosted backend, `state.hosted` is `True`. `state.artifacts` is an `ObjectStorageArtifactStore`,
and no local `runs_dir` tree exists to read from.

This proposal routes those reads through the seam, the way `_read_manifest` already does. The
abstraction that BE-0015 built then holds for every step-artifact read path, including the paths
added since.

## Motivation

Take a hosted deployment whose artifacts live in object storage. There, `_step_artifacts` and
`resolve_scenario_pick` read `state.runs_dir` directly. They do not raise. They silently read past
an empty or non-existent local directory.

- `_step_artifacts` returns an empty step list. The editor shows no per-step artifact handles for a
  run that in fact has them.
- The `elements_path.is_file()` check in `resolve_scenario_pick` is always `False`. Every
  element-pick request returns `{"error": "elements.json not found for this step"}, 404`, even when
  the `elements.json` of the run sits in object storage.

Both failures look like missing data, not like a wiring bug. That makes them slow to diagnose from
the outside.

This is the drift that the `ArtifactStore` seam exists to prevent: a hosted backend silently
falling back to empty results, instead of erroring loudly or working correctly. `_persist_run` and
`_run_manifests` already go through `state.artifacts` in the same package. So the inconsistency is
internal to this one module and is not a limitation of the seam itself.

`coverage_view` reads `runs_dir` directly, and the scratch screenshot of `start_capture` shares the
same `runs_dir`-bypassing shape. Fixing only the two most visibly broken endpoints would leave the
module inconsistent. The next contributor is likely to copy from that inconsistency.

## Detailed design

The work is MECE across four units. They are ordered by how directly the hosted backend reaches
each one today.

### 1. Route `_step_artifacts` through `state.for_org(org).artifacts`

`_step_artifacts` (`reads.py:289-342`) already has a `ServeState` in scope. Its manifest read
becomes `state.for_org(org).artifacts.open_bytes(f"{run_id}/manifest.json")`. The content is parsed
the way `_read_manifest` already parses it, with a `json.JSONDecodeError`/`OSError`-guarded
`json.loads`. This replaces the direct `manifest_path.is_file()` and `.read_text()` pair.

`read_scenario` (`reads.py:265-286`) is the only caller of `_step_artifacts`. It already resolves
`org` via `state.org_of(actor)` a few lines above the call. Threading `org` through therefore costs
one added parameter and no new lookup.

The per-step existence probes (`elements_file.is_file()` and `screenshot_file.is_file()` at
`reads.py:326-339`) need a lightweight existence method on the seam. `get()` is not that method.

- On the local backend, `LocalArtifactStore.get` (`bajutsu/serve/artifacts.py:125-130`) builds
  `Artifact(body=target.read_bytes())`. It reads the whole file.
- Using `store.get(...) is not None` as an existence probe would pull each `after.png` body into
  memory just to decide whether to emit a URL. That costs strictly more than the `is_file()` it
  replaces.
- The two `get` implementations also diverge. `ObjectStorageArtifactStore.get`
  (`server/artifacts.py:56-61`) is a cheap `exists` plus a presigned redirect. Keying off `get()`
  would be free on one backend and a full read on the other. The seam should not bake in that kind
  of hidden asymmetry.

The fix therefore adds an `exists(rel) -> bool` method (HEAD-style) to the `ArtifactStore`
protocol. The local store implements it as `Path.is_file`. The object store implements it as an
object `exists`. The probes route through `state.for_org(org).artifacts.exists(...)`.

`elementsUrl` and `screenshotUrl` keep pointing at the `/runs/<run_id>/<step_id>/...` HTTP path.
The `GET /runs/...` route of `handler.py` already serves through `state.artifacts.get`. Only the
existence probe that decides whether to emit the URL changes. The URL shape stays.

### 2. Route `resolve_scenario_pick` through the same seam

`resolve_scenario_pick` (`reads.py:461-520`) already resolves `_org` via `_resolve_org_or_forbid`
before it reads `elements.json` (`reads.py:487-499`). The fix reuses that `_org`. It calls
`state.for_org(_org).artifacts.open_bytes(f"{run_id}/{step_id}/elements.json")` in place of the
direct `elements_path.is_file()` and `.read_text()` pair. The existing `json.loads` and
`isinstance(raw, list)` validation stays unchanged.

### 3. Route the coverage evidence reads through the seam

`coverage_view` (`operations/coverage.py:66-72`) passes `state.runs_dir` into `read_exchanges` and
`read_observed_ids` (`bajutsu/coverage.py:341-380`). Both functions glob for `network.json` and
`elements.json` under an explicit run-id set. The glob is `runs_dir.glob(f"*/{name}")`, issued
through the shared `_evidence_files` at `coverage.py:330-338`.

`ArtifactStore` has no glob or directory-listing primitive today. It has only `open_bytes(rel)` for
one known path and `list_runs()` for run summaries. This unit is therefore the one place that needs
a new capability, and cannot reuse an existing method.

The unit does not add a generic glob to the protocol, because a glob would let a store leak its
internal layout across the seam. It derives the step ids to probe from data the module already has.

- `run_set_manifests` (`reads.py:202-227`, already seam-routed) gives the manifest of each run.
- The `scenarios[].sid` entries of the manifest, plus the step names of each scenario, give exactly
  the `sid/step` paths that `_evidence_files` was globbing for.
- `read_exchanges` and `read_observed_ids` gain an `ArtifactStore`-based variant (or an overload).
  The variant takes that explicit step-id list and calls `open_bytes` per path. It does not glob
  `runs_dir`.
- The existing glob-based signature stays for any caller outside `ServeState` that legitimately owns
  a local `runs_dir` (a CLI command, a test).

### 4. Capture's session-scoped scratch screenshot

`start_capture` sets `shot_dir = state.runs_dir / "_capture"` (`operations/capture.py:62-65`). Later,
`handler.py` calls `session.screenshot_path.read_bytes()` (`handler.py:605-611`). Both share the
`runs_dir`-relative shape of the other three units.

This path is not a stored *run* artifact at all. A capture session is a live, in-process object
(`state.capture: CaptureSession | None`). Its driver and HTTP handler run in the same process for
the lifetime of the session. A `run` or `record` job differs: BE-0015 already lets such a job
execute on a separate worker.

Routing this path through `state.artifacts` would need one of two larger changes:

- keying capture scratch files by a synthetic run id under the same store, which widens what the
  seam means by "a run";
- adding a second, session-scoped store.

Both exceed the scope of this proposal. The job of this unit is narrower, and has two parts:

- Keep the `_capture` scratch directory under a path that stays writable when hosted. That already
  holds: `state.runs_dir` is a local, writable directory on whichever host or worker holds the live
  driver.
- Add a comment at `capture.py:62` that says why this path deliberately stays outside the
  `ArtifactStore` seam. A future reader then does not mistake its `runs_dir` use for the oversight
  that units 1-3 fix.

### Path-safety checks move alongside the seam calls

`valid_run_id` and `_valid_step_id` (`reads.py:358-363`) keep gating every `run_id` and `step_id`
before it becomes part of an `open_bytes` or `get` key. The implementations of `ArtifactStore`
independently re-apply the same containment discipline at their own boundary
(`LocalArtifactStore._resolve`, `ObjectStorageArtifactStore._key`).

Two independent checks of the same shape are intentional defense in depth, and the proposal should
not remove either as redundancy.

- The caller-side check turns a crafted id into a `400`/`404` before a request reaches the store.
- The store-side check keeps every other seam consumer safe, even if a future caller forgets to
  check.

## Alternatives considered

- **Keep the direct `runs_dir` reads and document them as local-only.** Rejected. `_step_artifacts`
  and `resolve_scenario_pick` are both wired into the hosted `server` backend today
  (`app.py:222-240`, `app.py:438-440`). No gate disables them when `state.hosted` is `True`. Such
  documentation would call a code path "local-only" while the path stays reachable, and silently
  wrong, on the backend the documentation warns about. That is a correctness bug wearing a
  disclaimer, and it is not a scoping decision.
- **Add a generic `list_files(prefix) -> list[str]` (or glob) method to `ArtifactStore`.**
  Considered for unit 3, and rejected in favor of deriving step ids from the manifest that
  `run_set_manifests` already reads. A glob-shaped method would work for the local store by
  construction. It would force `ObjectStorageArtifactStore` to implement key-prefix listing with
  the same traversal semantics as `Path.glob`. That surface is wider and leakier than the seam
  otherwise commits to. Every other method reads one caller-named path and never enumerates what
  exists under one.
- **Fold capture's scratch screenshot into the `ArtifactStore` seam as a fourth unit shipped with
  units 1-3.** Rejected for the scope of this proposal. A capture session is not a stored run, and
  its driver and handler always share a process. The hosted-correctness argument that motivates
  units 1-3 does not apply to it the same way. It stays a documented, deliberate exception, instead
  of being silently folded in or left silently unexplained.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — Route `_step_artifacts` (`reads.py`) through `state.for_org(org).artifacts`, replacing its
      direct `runs_dir` manifest read and per-step existence probes with `open_bytes`/`get`.
- [x] 2 — Route `resolve_scenario_pick` (`reads.py`) through `state.for_org(_org).artifacts` for its
      `elements.json` read.
- [x] 3 — Give `coverage_view` (`operations/coverage.py`) a seam-routed path to the evidence files
      `read_exchanges`/`read_observed_ids` (`bajutsu/coverage.py`) currently glob directly, deriving
      step ids from an already-seam-routed manifest read instead of adding a glob primitive to
      `ArtifactStore`.
- [x] 4 — Document, at `operations/capture.py:62`, why the live capture session's scratch
      screenshot deliberately stays outside the `ArtifactStore` seam.

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) —
  introduces the `ArtifactStore` seam (local filesystem vs. object storage) this proposal routes the
  remaining read paths through.
- `bajutsu/serve/state.py` (`ServeState.artifacts`, `for_org`) — the seam field and the org-scoping
  helper unit 1 and unit 2 call through.
- `bajutsu/serve/artifacts.py` (`ArtifactStore`, `LocalArtifactStore`) — the protocol and its
  filesystem-confined default implementation.
- `bajutsu/serve/server/artifacts.py` (`ObjectStorageArtifactStore`) — the hosted implementation
  whose `get`/`open_bytes` never touch `runs_dir`, and the concrete reason the bypassing reads
  silently return empty/404 today.
- `bajutsu/serve/operations/reads.py` (`_step_artifacts`, `resolve_scenario_pick`,
  `run_set_manifests`, `_run_manifests`) — the module holding both the already-seam-routed reads
  and the ones this proposal fixes.
- `bajutsu/serve/jobs.py` (`_persist_run`, `_read_manifest`) — the existing correct precedent this
  proposal's units 1-2 follow.
- `bajutsu/coverage.py` (`read_exchanges`, `read_observed_ids`, `_evidence_files`) — the
  glob-based evidence readers unit 3 gives a seam-routed alternative to.
- `bajutsu/serve/operations/capture.py`, `bajutsu/serve/handler.py` — the capture-session scratch
  read/write unit 4 documents as a deliberate exception.
- This proposal is behavior-preserving on the local `serve` backend (every read still resolves to
  the same bytes) and fixes hosted-backend correctness; serve is periphery to the deterministic
  `run`/CI verdict path (prime directive 1, [CLAUDE.md](../../CLAUDE.md)), which this proposal does
  not touch.

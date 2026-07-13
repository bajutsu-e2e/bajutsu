**English** · [日本語](BE-XXXX-serve-step-artifact-seam-ja.md)

# BE-XXXX — Route serve step-artifact reads through the ArtifactStore seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-step-artifact-seam.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState.artifacts` (`bajutsu/serve/state.py:346-348`) is the seam BE-0015 built so run
artifacts can be read back the same way from either backend: `LocalArtifactStore`
(`bajutsu/serve/artifacts.py`) reads files confined to `runs_dir`, and
`ObjectStorageArtifactStore` (`bajutsu/serve/server/artifacts.py`) fetches the same run-relative
paths from S3-compatible object storage instead, handing back a signed-URL redirect rather than
inlining bytes. `_persist_run`/`_read_manifest` (`bajutsu/serve/jobs.py:338-343`) and
`_run_manifests`/`run_set_manifests` (`bajutsu/serve/operations/reads.py:183-227`) already go
through it correctly — `_read_manifest` reads a run's `manifest.json` via
`state.artifacts.open_bytes(...)`, not a `runs_dir` path.

Several other read paths in the same module bypass the seam and reach straight into the local
filesystem instead:

- `_step_artifacts` (`bajutsu/serve/operations/reads.py:289-342`), which builds the per-step
  artifact list the scenario editor's `/api/scenario?runId=...` response embeds, reads
  `state.runs_dir / run_id / "manifest.json"` (`reads.py:301`) and probes
  `state.runs_dir / run_id / step_id / "after.png"` for existence (`reads.py:327`) directly.
- `resolve_scenario_pick` (`bajutsu/serve/operations/reads.py:461-520`), backing
  `POST /api/scenario/resolve` (the "pick an element from a stored screenshot" flow used when
  editing a scenario without a live driver), reads
  `state.runs_dir / run_id / step_id / "elements.json"` directly (`reads.py:497`).
- `coverage_view` (`bajutsu/serve/operations/coverage.py:66-72`) passes `state.runs_dir` straight
  into `bajutsu.coverage.read_exchanges`/`read_observed_ids`, which glob `network.json` and
  `elements.json` files under it (`bajutsu/coverage.py:341-380`).
- `start_capture` (`bajutsu/serve/operations/capture.py:62-65`) writes (and a later read in
  `bajutsu/serve/handler.py:605-611` reads back) a capture session's live screenshot under
  `state.runs_dir / "_capture"`.

`read_scenario` (`reads.py:265-286`, which calls `_step_artifacts`) is wired to `/api/scenario` at
`app.py:222-240`, and `resolve_scenario_pick` is wired to `/api/scenario/resolve` at
`app.py:438-440` — both reachable from the hosted `server` backend
(`bajutsu/serve/server/app.py`), where `state.hosted` is `True` and `state.artifacts` is an
`ObjectStorageArtifactStore` with no local `runs_dir` tree to read from at all. This proposal
routes those reads through the seam the same way `_read_manifest` already does, so the abstraction
BE-0015 built holds for every step-artifact read path, not only for the ones added since.

## Motivation

On a hosted deployment whose artifacts live in object storage, `_step_artifacts` and
`resolve_scenario_pick` reading `state.runs_dir` directly do not raise — they silently read past an
empty or non-existent local directory. `_step_artifacts` returns an empty step list (the editor
shows no per-step artifact handles for a run that in fact has them), and
`resolve_scenario_pick`'s `elements_path.is_file()` check is always `False`, so every element-pick
request returns `{"error": "elements.json not found for this step"}, 404` even when the run's
`elements.json` is sitting in object storage. Both failures look like missing data rather than a
wiring bug, which makes them slow to diagnose from the outside. This is exactly the drift the
`ArtifactStore` seam exists to prevent — a hosted backend silently falling back to empty results
instead of erroring loudly or working correctly — and the fact that `_persist_run`/`_run_manifests`
already go through `state.artifacts` in the very same package shows the inconsistency is internal
to this one module, not a limitation of the seam itself.

`coverage_view`'s direct `runs_dir` reads and `start_capture`'s scratch screenshot share the same
`runs_dir`-bypassing shape, so fixing only the two most visibly broken endpoints would leave the
module inconsistent in a way the next contributor is likely to copy from.

## Detailed design

The work is MECE across four units, ordered by how directly each is reachable from the hosted
backend today.

### 1. Route `_step_artifacts` through `state.for_org(org).artifacts`

`_step_artifacts` (`reads.py:289-342`) already has a `ServeState` in scope; its manifest read
becomes `state.for_org(org).artifacts.open_bytes(f"{run_id}/manifest.json")` (parsed the same way
`_read_manifest` already parses that same content — a `json.JSONDecodeError`/`OSError`-guarded
`json.loads`), replacing the direct `manifest_path.is_file()` + `.read_text()` pair. `read_scenario`
(`reads.py:265-286`), `_step_artifacts`'s only caller, already resolves `org` via `state.org_of(actor)`
a few lines above where it calls `_step_artifacts`, so threading `org` through costs one added
parameter, not a new lookup.

The per-step existence probes (`elements_file.is_file()`, `screenshot_file.is_file()` at
`reads.py:326-339`) need no new protocol method: `ArtifactStore.get(rel)`
(`bajutsu/serve/artifacts.py:46-47`) already returns `None` for a missing path on both
implementations, and `ObjectStorageArtifactStore.get` (`server/artifacts.py:56-61`) resolves that
with a plain `store.exists(key)` HEAD-style check, not a body fetch — so
`store.get(f"{run_id}/{step_id}/after.png") is not None` is exactly as cheap as the existing
`is_file()` check was, on both backends. `elementsUrl`/`screenshotUrl` keep pointing at the
`/runs/<run_id>/<step_id>/...` HTTP path (`handler.py`'s own `GET /runs/...` route already serves
through `state.artifacts.get`), so only the existence probe that decides whether to emit that URL
changes, not the URL shape itself.

### 2. Route `resolve_scenario_pick` through the same seam

`resolve_scenario_pick` (`reads.py:461-520`) already resolves `_org` via `_resolve_org_or_forbid`
before it reads `elements.json` (`reads.py:487-499`); the fix reuses that `_org` for
`state.for_org(_org).artifacts.open_bytes(f"{run_id}/{step_id}/elements.json")` in place of the
direct `elements_path.is_file()` + `.read_text()` pair, with the existing `json.loads` /
`isinstance(raw, list)` validation unchanged.

### 3. Route the coverage evidence reads through the seam

`coverage_view` (`operations/coverage.py:66-72`) passes `state.runs_dir` into
`read_exchanges`/`read_observed_ids` (`bajutsu/coverage.py:341-380`), both of which glob
(`runs_dir.glob(f"*/{name}")` via the shared `_evidence_files` at `coverage.py:330-338`) for
`network.json`/`elements.json` under an explicit run-id set. `ArtifactStore` has no glob or
directory-listing primitive today — only `open_bytes(rel)` for one known path and `list_runs()` for
run summaries — so this unit is the one place a new capability is actually needed, not a reuse of
an existing method. Rather than adding a generic glob to the protocol (which would let a store leak
its internal layout across the seam), this unit derives the step ids to probe from data the module
already has: `run_set_manifests` (`reads.py:202-227`, already seam-routed) gives each run's manifest,
whose `scenarios[].sid` plus each scenario's step names give exactly the `sid/step` paths
`_evidence_files` was globbing for. `read_exchanges`/`read_observed_ids` gain an
`ArtifactStore`-based variant (or an overload) that takes that explicit step-id list and calls
`open_bytes` per path instead of globbing `runs_dir`, keeping the existing glob-based signature for
any caller (a CLI command, a test) that legitimately owns a local `runs_dir` outside `ServeState`.

### 4. Capture's session-scoped scratch screenshot

`start_capture`'s `shot_dir = state.runs_dir / "_capture"` (`operations/capture.py:62-65`) and
`handler.py`'s later `session.screenshot_path.read_bytes()` (`handler.py:605-611`) share the same
`runs_dir`-relative shape as the other three, but they are not a stored *run* artifact at all: a
capture session is a live, in-process object (`state.capture: CaptureSession | None`) whose driver
and HTTP handler run in the same process for the lifetime of the session, unlike a `run`/`record`
job, which BE-0015 already lets execute on a separate worker. Routing this path through
`state.artifacts` would require either keying capture scratch files by a synthetic run id under the
same store (widening what the seam means by "a run") or adding a second, session-scoped store — both
larger changes than this proposal's scope. This unit's job is narrower: keep the `_capture` scratch
directory under a path that stays writable when hosted (already true — `state.runs_dir` is a local,
writable directory on whichever host/worker holds the live driver), and note in a comment at
`capture.py:62` why this path deliberately stays outside the `ArtifactStore` seam, so a future reader
does not read its `runs_dir` use as the same oversight as units 1–3.

### Path-safety checks move alongside the seam calls

`valid_run_id` and `_valid_step_id` (`reads.py:358-363`) keep gating every `run_id`/`step_id` before
it becomes part of an `open_bytes`/`get` key — the same containment discipline
`ArtifactStore`'s own implementations independently re-apply at their boundary
(`LocalArtifactStore._resolve`, `ObjectStorageArtifactStore._key`). Two independent checks of the
same shape is intentional defense in depth, not redundancy to be removed: the caller-side check is
what turns a crafted id into a `400`/`404` before a request even reaches the store, and the
store-side check is what keeps every other seam consumer safe even if a future caller forgets to
check.

## Alternatives considered

- **Keep the direct `runs_dir` reads and document them as local-only.** Rejected: `_step_artifacts`
  and `resolve_scenario_pick` are both wired into the hosted `server` backend today
  (`app.py:222-240`, `app.py:438-440`) with no gate that disables them when `state.hosted` is
  `True`. Documenting a code path as "local-only" while it stays reachable and silently wrong on the
  backend that documentation warns about is a correctness bug wearing a disclaimer, not a scoping
  decision.
- **Add a generic `list_files(prefix) -> list[str]` (or glob) method to `ArtifactStore`.** Considered
  for unit 3. Rejected in favor of deriving step ids from the manifest already read by
  `run_set_manifests`: a glob-shaped method would work for the local store by construction but would
  force `ObjectStorageArtifactStore` to implement key-prefix listing with the same traversal
  semantics as `Path.glob`, a wider and leakier surface than the seam otherwise commits to (every
  other method reads one caller-named path, never enumerates what exists under one).
- **Fold capture's scratch screenshot into the `ArtifactStore` seam as a fourth unit shipped with
  units 1-3.** Rejected for this proposal's scope: a capture session is not a stored run and its
  driver and handler always share a process, so the hosted-correctness argument that motivates units
  1-3 does not apply to it the same way. Left as a documented, deliberate exception rather than
  silently folded in or silently left unexplained.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — Route `_step_artifacts` (`reads.py`) through `state.for_org(org).artifacts`, replacing its
      direct `runs_dir` manifest read and per-step existence probes with `open_bytes`/`get`.
- [ ] 2 — Route `resolve_scenario_pick` (`reads.py`) through `state.for_org(_org).artifacts` for its
      `elements.json` read.
- [ ] 3 — Give `coverage_view` (`operations/coverage.py`) a seam-routed path to the evidence files
      `read_exchanges`/`read_observed_ids` (`bajutsu/coverage.py`) currently glob directly, deriving
      step ids from an already-seam-routed manifest read instead of adding a glob primitive to
      `ArtifactStore`.
- [ ] 4 — Document, at `operations/capture.py:62`, why the live capture session's scratch
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

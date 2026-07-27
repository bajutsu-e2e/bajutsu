**English** · [日本語](BE-0324-hosted-scenario-local-read-ja.md)

# BE-0324 — Read hosted scenarios from the bound config's local cache tree instead of object storage

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0324](BE-0324-hosted-scenario-local-read.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0324") |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

Hosted `bajutsu serve` (the server backend, [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md))
extracts a bound config's scenario tree onto local disk. It does this before it serves a single
request. Three bind paths do this today: a Git source
([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)), an uploaded zip
([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)), and a composed
artifact triple ([BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)).
Each places the config and its `scenarios` directory into the same content-addressed cache
directory. The config file itself comes from that same directory. Yet the control plane's scenario
listing, scenario reading, and run-time scenario lookup never touch it. All three go through
`ObjectScenarioStorage`, a store backed by the object-storage bucket the server also uses for run
artifacts and baselines. This proposal has those three read operations resolve a scenario from the
config's already-extracted local directory instead. The bucket then stops holding a second,
independent copy of scenario content a Git or zip source already put on disk.

## Motivation

The duplication is not an architectural blemish alone; it leaves a functional gap.
`ObjectScenarioStorage` gains an entry in two cases: someone saves a scenario through the hosted
web editor, or a `record` job authors one
(`bajutsu/serve/server/scenarios.py`, `bajutsu/serve/operations/worker_uploads.py`). Neither
`bind_git_config` nor the zip and composition bind paths write anything into that bucket
(`bajutsu/serve/operations/config.py`, `bajutsu/serve/operations/upload.py`). Take a scenario
shipped inside a Git repository or an uploaded bundle. It stays invisible to the hosted UI's
scenario list. It stays invisible to a direct read and to a run, too. Nothing changes until someone
re-authors that same scenario by hand through the web editor. The scenario tree sits on the control
plane's own disk the whole time. Nothing ever reads it.

The local (non-hosted) backend carries no such gap. `ServeState` wires its scenario store to
`LocalScenarioStore(lambda target: _scenarios_dir_for(self, target))` (`bajutsu/serve/state.py`).
`_scenarios_dir_for` resolves each target's scenario directory against `state.cwd`. For a
Git-sourced config, that directory is the checkout root; otherwise it sits beside the config file.
The server backend's `_build_server_state` (`bajutsu/serve/__init__.py`) never reuses this
resolver. It wires every org's scenario store to `StorageScenarioStore(ObjectScenarioStorage(...))`
without exception, no matter where the active config came from. This proposal gives the server
backend the same locally-resolved read path the local backend already has.

One real difference between the two backends shapes the design. A hosted run happens on a
separate worker process (`bajutsu/serve/server/db_executor.py`,
`bajutsu/serve/server/worker_job.py`). That worker shares no filesystem with the control plane. A
scenario handed to a run must still travel as text in the job's `materials`; a bare local path
would not reach the worker at all.

## Detailed design

### 1. A local-tree scenario reader for the server backend

Add a `ScenarioStorage` implementation named `LocalTreeScenarioStorage`, beside
`ObjectScenarioStorage` (`bajutsu/serve/server/scenarios.py`). Construct it with the live
`ServeState` and the same `apps` lookup `ObjectScenarioStorage` already takes. It answers `has_app`
by checking that same `apps` lookup, unchanged. For `list` and `read`, it delegates to the local
backend's own machinery instead of an object-store call, so a Git- or zip-sourced scenario appears
in the hosted UI's list, not only on a direct read or a run. For an org's target, it calls
`_scenarios_dir_for(state, target)` (`bajutsu/serve/state.py`) to get that target's scenario
directory. It then hands the result to a `LocalScenarioScope` (`bajutsu/serve/scenarios.py`) and
calls its `list` or `read`. This reuses `LocalScenarioScope` directly, rather than re-deriving the
same directory-resolution and path-containment logic inside `bajutsu/serve/server/scenarios.py`.
That keeps the BE-0051 path-containment guard in the one place that already implements and tests
it, instead of a second copy that could drift out of sync with it.

`_build_server_state` resolves `cwd` from whichever config the operator has bound at that moment: a
Git checkout root, a zip extraction root, or a composition root. The new reader needs no branch on
config-source kind for this reason. Every bind path the server backend supports already extracts
its tree to the same place. One implementation covers a Git source, a zip source, and a
composed-artifact source alike.

`LocalTreeScenarioStorage` still needs a `save` method. The `ScenarioStorage` protocol
(`bajutsu/serve/server/scenarios.py`) requires one, since `StorageScenarioStore` and
`StorageScenarioScope` each hold a single injected `ScenarioStorage` and call `save` on that same
object. `LocalTreeScenarioStorage` takes an `ObjectScenarioStorage` instance as a constructor
argument for this single purpose. Its `save` method delegates to that instance, unchanged. `has_app`,
`list`, and `read` never touch that delegate; `save` alone does. One object answers the whole
`ScenarioStorage` protocol this way: it reads from the local tree and writes through
`ObjectScenarioStorage`, with no new protocol and no second object for `StorageScenarioScope` to
hold.

### 2. `runnable()` keeps shipping materials; only their source changes

`StorageScenarioScope.runnable()` (`bajutsu/serve/server/scenarios.py`) still returns a
`materials`-based `Runnable`. The new reader supplies the scenario's text by reading it off local
disk, replacing an object-store `get_bytes` call. That text still travels to the worker as a
`materials` entry, the same way it does today.
`bajutsu/serve/server/worker_job.py`'s `_materialize` writes that text into the worker's own
workspace before the run starts. The worker-side contract stays the same; the control plane's read
source is what changes.

### 3. `save` and `authored` stay out of scope

This proposal changes the read path alone: `list`, `read`, and the text `runnable()` ships as
materials. Authoring a scenario through the hosted web editor, or through a `record` job, still
saves it through `ObjectScenarioStorage`, unchanged. This split matches how the reporting user's
own workflow runs today. Someone authors a scenario on a local machine, then brings it to the
control plane by re-uploading the bundle or by pushing to the Git source; nobody edits it in place
on the server. The write side has no local-tree counterpart to move to yet, for that reason.
Reconciling `save` and `authored` with a local-tree scenario source is a separate question. That
question also has to settle what happens when the control plane runs as more than one replica with
no shared disk between them. This proposal leaves both questions to a follow-up item, once the
deployment shape settles, rather than design against an untested premise now.

### 4. Wiring

`_build_server_state` (`bajutsu/serve/__init__.py`) changes what the per-org `StorageScenarioStore`
builds. Today it constructs `StorageScenarioStore(ObjectScenarioStorage(...))` directly; this
proposal has it construct the `ObjectScenarioStorage` instance first, same as today, then wrap it
in `LocalTreeScenarioStorage` and pass that to `StorageScenarioStore` instead. `save` and
`authored` still reach the same `ObjectScenarioStorage` object, one call deeper.

### Determinism, the gate, and app-agnosticism

This item changes one thing: how the control plane locates and reads a scenario's bytes. It adds no
LLM call anywhere, and it leaves what a run asserts unchanged. Pass/fail stays as machine-checked as
before (directive 1). The item also removes a hidden failure mode instead of adding one. Without
it, `StorageScenarioScope.read`/`runnable` return "no such scenario" for a Git- or zip-sourced
scenario that does exist in the bound tree, a confusing result an operator has to debug by hand;
this item makes that same scenario resolve deterministically instead (directive 2). The new reader
adds no app-specific branch: it resolves the same `scenarios` directory field every target's config
already names (directive 3).

## Alternatives considered

- **Keep the status quo.** Rejected: this is the gap the item exists to close. A Git- or
  zip-sourced scenario stays unreachable through the hosted UI's list, read, and run operations
  until someone re-authors it by hand into the object-storage bucket.
- **Copy the extracted tree into `ObjectScenarioStorage` at bind time, and leave every read path
  unchanged.** Rejected: this recreates the duplication the item removes, and the copy would not
  stay in sync. A composition's local directory is content-addressed, and extraction makes it
  immutable (`materialize_composition`, `bajutsu/serve/operations/composition.py`). Nothing would
  re-trigger the copy if a later `save` edited that same directory. The bucket and the disk would
  then drift apart.
- **Move `save` and `authored` onto the local tree in this same item.** Rejected for now: this
  requires settling how a scenario edit stays durable and visible when the control plane runs as
  more than one replica. The reporting user has not decided that deployment question yet. Scoping
  this item to the read path ships the clear, low-risk half now, and the write side waits for a
  follow-up once that question has an answer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — `LocalTreeScenarioStorage`: a `ScenarioStorage` implementation answering `has_app` from
  the same `apps` lookup `ObjectScenarioStorage` uses, `list` and `read` by delegating to
  `_scenarios_dir_for` and `LocalScenarioScope`, and `save` by delegating to an injected
  `ObjectScenarioStorage`.
- [ ] 2 — `runnable()` sourcing its `materials` text from `LocalTreeScenarioStorage`. The
  worker-side contract (`_materialize` writing materials into the run workspace) stays unchanged.
- [ ] 3 — `_build_server_state` wrapping its constructed `ObjectScenarioStorage` in
  `LocalTreeScenarioStorage` before handing it to `StorageScenarioStore`.

## References

- [CLAUDE.md](../../CLAUDE.md): determinism first; app-agnostic.
- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md):
  the Git bind path whose checkout root `_scenarios_dir_for` already resolves against, for the
  local backend.
- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md),
  [BE-0243 — Persist uploaded zip config bundles to object storage](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md),
  and [BE-0268 — Upload config, scenarios, and app binary as independent content-addressed artifacts composed per run](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md):
  the zip and composed-artifact bind paths that extract a scenario tree onto the control plane's
  disk the same way.
- [BE-0015 — Web UI public hosting](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md):
  the server backend, and the control-plane/worker split this item's `runnable()` design respects.
- Surfaces this item touches: `bajutsu/serve/state.py`'s `LocalScenarioStore` wiring and
  `_scenarios_dir_for`; `bajutsu/serve/scenarios.py`'s `LocalScenarioScope` and its `ScenarioStore`
  and `ScenarioScope` protocols; `bajutsu/serve/server/scenarios.py`'s `ObjectScenarioStorage`,
  `StorageScenarioScope`, and `StorageScenarioStore`; `bajutsu/serve/__init__.py`'s
  `_build_server_state`; `bajutsu/serve/server/db_executor.py`'s `DbQueueExecutor`; and
  `bajutsu/serve/server/worker_job.py`'s `_materialize` and `execute_job_spec`.

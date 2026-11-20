**English** · [日本語](BE-0340-replay-scenario-upload-ja.md)

# BE-0340 — Upload a scenario file directly into Replay

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0340](BE-0340-replay-scenario-upload.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0340") |
| Implementing PR | [#1653](https://github.com/bajutsu-e2e/bajutsu/pull/1653) |
| Topic | Surfacing CLI features in the serve Web UI |
| Related | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's **Replay** tab runs a scenario from the bound config's target scenario scope.
Locally, that scope is the scenarios directory. On a hosted deployment, the scope is per-project
storage ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). Replay has
no way to add a scenario to that scope.

This proposal adds an upload affordance to Replay. The operator picks a local `.yaml` scenario file,
or a `.zip` bundling several. The file lands directly in the scope. It becomes selectable and
runnable at once.

The upload always targets the config already open, and it adds no new way to bind a config. A
same-named file is overwritten in place, and the UI states that an overwrite happened.

## Motivation

Replay's scenario list always comes from `GET /api/scenarios` (`bajutsu/serve/operations/reads.py`).
That endpoint is backed by `state.for_org(org).scenarios.scope(target)`. An unbound config resolves
that scope to `None`, so the list comes back empty with no error raised. The explicit `"open a
config first"` message gates `start_run` and `start_record` instead. Those two actions
(`bajutsu/serve/operations/dispatch.py`) run or author a scenario.

Three paths add a scenario to that scope today:

- An operator places the file on the server's filesystem by hand. A hosted deployment offers no such
  affordance ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).
- An operator runs `record` and has AI author one.
- An operator replaces the whole config through the zip/compose upload ceremony
  ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) /
  [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)).

The third path is disproportionate for the common case. An operator already has a config open:
local, Git-sourced, or from an earlier upload. The operator wants to add or refresh a scenario file
that someone wrote by hand or brought in from elsewhere.

The compose flow (BE-0268) still demands a `config` artifact leg. It does so even when rebinding the
config is not the point. `bind_composition` turns away a request that supplies none
(`bajutsu/serve/operations/upload.py`). The legacy bundle upload (BE-0073) discards whatever
configuration is active. Neither path means "add a scenario to what is already open"; both mean
"open a different project".

The gap is sharpest on a hosted deployment. There, the operator cannot place a file on the
filesystem directly. AI authoring through `record` does not suit every scenario either. A scenario
already reviewed and hand-tuned outside Bajutsu should not need AI re-authoring to reach Replay. An
upload affordance scoped to the open config closes this gap. It leaves config binding untouched.

## Detailed design

Both upload paths resolve through the existing `ScenarioScope` / `ScenarioStore` seam
(`bajutsu/serve/scenarios.py`). `save_scenario`, `start_run`, and `start_record` already share this
seam. [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)'s
path confinement and BE-0015's org scoping keep applying unchanged.

Neither path opens a new trust boundary. Both add a new way to populate a scope that an
already-bound config opened. Both need `state.config` already bound and a valid `target`. When that
scope fails to resolve, the single-file path inherits the `save_scenario` error `"path must be a
*.yaml under the scenarios dir"`. The new zip endpoint returns a matching error of its own. This
proposal adds no way to bind or replace a config.

Role-based access control (RBAC, BE-0015 7c-2) is a separate mechanism from the `ScenarioScope`
seam. It needs its own update. `bajutsu/serve/authz.py` gates a mutating `POST` by an explicit
allowlist (`_ADMIN_PATHS` / `_EDITOR_PATHS`). A path in neither falls through ungated.

The single-file path reuses `POST /api/scenario`, which is already in `_EDITOR_PATHS`, so it needs
no change. The new `POST /api/scenarios/upload` route does not exist yet. This proposal adds the
route to `_EDITOR_PATHS` at the same tier as `/api/scenario`. That closes what would otherwise be a
silent gap.

- **Report the overwrite on the existing single-scenario save path.** `POST /api/scenario` already
  writes an arbitrarily named `.yaml` file into the target's scope (`save_scenario` in
  `bajutsu/serve/operations/reads.py`). Its response carries no signal today that a save replaced an
  existing file. Add that signal. Before `scope.save(ref, text)`, check whether `scope.read(ref)`
  already returns content. If so, the response reports `overwritten: true`. Otherwise it reports
  `false`. The Author editor's Save button calls `save_scenario` today. The button sees no change
  beyond the new response field.

- **A single-file "Upload scenario" affordance in the Replay tab.** Add an upload control next to
  the scenario picker in the Replay Form. The markup lives in `bajutsu/templates/serve.html.j2`. The
  wiring lives in `bajutsu/templates/serve.panels.mjs`. The control mirrors the file-input pattern
  of the compose picker (`cmp-scenarios-file`).

  The control reads the chosen `.yaml` file's text on the client. It posts that text to
  `/api/scenario`, the endpoint that the Author editor's Save button uses. The file's own name is
  the target path. A scenario that reaches the scope through Upload is indistinguishable from one
  that reaches it through Save or `record`.

  On success, the control reports "added" or "overwrote" from the new `overwritten` flag. It then
  reloads the scenario list, so the file is selectable and runnable at once. The control activates
  once a config and a target are both in place. Every other Replay affordance already applies that
  precondition.

- **A zip upload for a whole scenario set.** A new endpoint, `POST /api/scenarios/upload`, takes a
  raw request body. It dispatches off the main request loop, as `/api/upload` and `/api/artifacts/*`
  already do. The endpoint accepts a `.zip` of one or more `.yaml` files for the given target's
  scope.

  A new operation, `upload_scenarios`, resolves the same scope that `save_scenario` resolves. It
  reads the archive's top-level `*.yaml` entries alone. A scope has no subdirectory concept, which
  matches the flat `glob("*.yaml")` of `list_scenarios`.

  Extraction applies three resource bounds sized for scenario text: entry count, per-entry size, and
  total size. Each bound is far smaller than the bundle bounds of BE-0073, which exist for app
  binaries. Extraction also applies the zip-slip containment of `bajutsu/serve/uploads.py`, which
  already protects a bundle.

  The operation parses every entry with `load_scenario_file` before it writes anything. One entry
  that fails to parse aborts the whole zip and writes nothing. A bad batch never leaves a partial
  overwrite behind. `start_run_set` (`bajutsu/serve/operations/dispatch.py`) already applies the
  same check-every-item-before-touching-any pattern to a scenario fan-out.

  On success, the operation writes each file through `scope.save(name, text)`. The response lists
  each name as created or overwritten. The route joins `_EDITOR_PATHS` in `bajutsu/serve/authz.py`,
  the RBAC tier of `/api/scenario`. A viewer on a hosted deployment cannot reach it.

  The Replay Form gains a second upload control. Alternatively, the same control dispatches on the
  picked file's extension. The control posts a `.zip` to the new endpoint. It renders the same
  created/overwritten summary as the single-file path.

- **Tests and docs.** Unit tests cover the new `overwritten` field of `save_scenario`. The field is
  false on a first save and true on a second save to the same ref. Unit tests also cover
  `upload_scenarios`:
  - Two scenarios in one zip both land and both appear in `list_scenarios`.
  - A zip where one entry fails to parse writes nothing.
  - A zip-slip entry and an oversized entry both get turned away.

  New `data-testid`s mark the upload controls. A dogfood E2E scenario alongside the existing Replay
  fixtures asserts the presence and enabled state of the upload control. The web backend has no step
  to drive a native file picker. The Python unit and HTTP tests therefore cover the write path end
  to end instead: add, overwrite, all-or-nothing, zip-slip, and oversized entry.

  `docs/architecture.md` and its `docs/ja/` mirror gain a note. Replay can populate a bound config's
  scenario scope directly, with no config rebind.

## Alternatives considered

- **Running a scenario with no config bound at all.** A scenario stays backend-agnostic. Running one
  still needs a target's backend, device, and app path, and a bare scenario file supplies none of
  these. An upload resolves against an already-bound config's target and cannot stand alone. This
  item leaves what a scenario needs to run unchanged. It changes how a scenario file reaches a scope
  that already holds that information.

- **Reusing the BE-0268 compose/artifact-upload flow for this too.** `POST /api/artifacts/scenarios`
  already caches an uploaded `.zip`/`.yaml` by content hash. But `bind_composition` still demands a
  `config` artifact leg. It does so even when the operator does not intend to rebind the config, and
  it turns away a request that supplies none.

  Extending that flow would mean building a way to supply a placeholder config leg, purely to reach
  the scenarios leg. That adds more surface than a narrower endpoint. The narrower endpoint assumes
  a config is already bound, the case this proposal exists for.

- **Accepting a nested-directory zip layout.** A scenario scope has no subdirectory concept today.
  `list_scenarios` globs the directory flatly, so a nested entry would never appear in the listing.
  A flat top-level `*.yaml` reading matches the existing model. Revisiting this makes sense if
  scopes gain subdirectories elsewhere, and not before.

- **Best-effort partial writes when a zip has entries that fail to parse.** Set aside in favor of
  all-or-nothing. A batch that lands partially leaves the scope in a state that the uploader did not
  ask for. The uploader cannot readily reason about that state. `start_run_set` already checks every
  item before it touches any of them, for the same reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add an `overwritten` field to `save_scenario`'s response.
- [x] Add the single-file "Upload scenario" affordance to the Replay Form, posting to
      `POST /api/scenario`.
- [x] Add the `POST /api/scenarios/upload` endpoint and `upload_scenarios` operation for a `.zip` of
      scenarios, with check-before-write and zip-slip/size bounds; add the route to `_EDITOR_PATHS`
      in `bajutsu/serve/authz.py`.
- [x] Add the zip-upload control to the Replay Form, sharing the created/overwritten summary
      rendering with the single-file path.
- [x] Add unit tests for both paths, `data-testid`s, and a dogfood E2E scenario. The dogfood
      scenario asserts the control's presence and enabled state rather than driving an actual
      upload: the web backend has no step to operate a native file picker, so the add /
      overwrite / all-or-nothing / zip-slip / oversized-entry behavior is instead covered end to
      end by the Python unit and HTTP tests.
- [x] Update `docs/architecture.md` and its `docs/ja/` mirror.

Log:

- Landed the `overwritten` response field, both upload paths (`POST /api/scenario` for a single
  `.yaml`, the new `POST /api/scenarios/upload` for a `.zip`), the `_EDITOR_PATHS` entry, the
  Replay Form control, unit and HTTP tests, the dogfood scenario, and the bilingual docs update.

## References

- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the legacy
  whole-bundle zip upload (config + scenarios + binary) this proposal's zip-slip and resource-bound
  handling for scenario text mirrors at a smaller scale.
- [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) — the
  compose/artifact-upload flow this proposal sets aside in *Alternatives considered*, because it
  still needs a `config` artifact leg.
- [BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer.md) — the
  Replay scenario viewer, the most recent addition to the same Replay Form this proposal's upload
  controls join.
- [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) — the
  path-confinement guarantee the `ScenarioScope` seam this proposal reuses already provides.
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — public hosting of
  the Web UI, the deployment mode where the filesystem workaround this proposal replaces is not even
  available to the operator.
- Existing endpoints: `POST /api/scenario` (`save_scenario`) and `GET /api/scenarios`
  (`list_scenarios`), both in `bajutsu/serve/operations/reads.py`; the `ScenarioScope` /
  `ScenarioStore` seam in `bajutsu/serve/scenarios.py`; `start_run_set`'s
  check-before-dispatch pattern in `bajutsu/serve/operations/dispatch.py`.

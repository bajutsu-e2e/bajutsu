**English** · [日本語](BE-XXXX-replay-scenario-upload-ja.md)

# BE-XXXX — Upload a scenario file directly into Replay

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-replay-scenario-upload.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Surfacing CLI features in the serve Web UI |
| Related | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0273](../BE-0273-serve-replay-scenario-viewer/BE-0273-serve-replay-scenario-viewer.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's **Replay** tab runs a scenario that already sits in the bound config's target
scenarios directory on disk. It has no way to add one. This proposal adds an upload affordance to
Replay: pick a local `.yaml` scenario file, or a `.zip` bundling more than one, and it lands
directly in that directory. It becomes selectable and runnable at once. The upload always targets
the config already open; it adds no new way to bind a config. A same-named file gets overwritten in
place, and the UI states that an overwrite happened.

## Motivation

Replay's scenario list always comes from `GET /api/scenarios`, backed by
`state.for_org(org).scenarios.scope(target)` (`bajutsu/serve/operations/reads.py`). An unbound
config resolves that scope to `None`, so the list comes back empty with no error raised; the
explicit `"open a config first"` message instead gates `start_run` and `start_record`
(`bajutsu/serve/operations/dispatch.py`), the actions that actually run or author a scenario. Three
paths add a scenario to that scope today. An operator can place the file on the server's filesystem by hand,
an affordance a hosted deployment does not have
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). An operator can run
`record` and have AI author one. Or an operator can replace the whole config through the
zip/compose upload ceremony
([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) /
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)).

The third path is disproportionate for the common case. An operator already has a config open —
local, Git-sourced, or from an earlier upload — and wants to add or refresh a scenario file someone
wrote by hand or brought in from elsewhere. The compose flow (BE-0268) still demands a `config`
artifact leg even when rebinding the config is not the point: `bind_composition` turns away a
request that supplies none (`bajutsu/serve/operations/upload.py`). The legacy bundle upload
(BE-0073) discards whatever configuration is active. Neither path is "add a scenario to what is
already open"; both are "open a different project".

The gap is sharpest on a hosted deployment, where the operator has no filesystem access to place a
file directly. AI authoring through `record` is not the right tool for every scenario, either: one
already reviewed and hand-tuned outside Bajutsu should not need AI re-authoring to reach Replay. An
upload affordance scoped to the config already open closes this gap, and it leaves config binding
untouched.

## Detailed design

Both upload paths resolve through the existing `ScenarioScope` / `ScenarioStore` seam
(`bajutsu/serve/scenarios.py`). `save_scenario`, `start_run`, and `start_record` already share this
seam, so [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)'s
path confinement and BE-0015's org scoping keep applying unchanged. Neither path opens a new trust
boundary; both add a new way to populate a scope that an already-bound config opened. Both need
`state.config` already bound and a valid `target`. The single-file path inherits whatever
`save_scenario` already returns when that scope fails to resolve — `"path must be a *.yaml under the
scenarios dir"` — and the new zip endpoint returns a matching error of its own for the same case.
This proposal adds no way to bind or replace a config.

Role-based access control (RBAC, BE-0015 7c-2) is a separate mechanism from the `ScenarioScope` seam
above, and it needs its own update: `bajutsu/serve/authz.py` gates a mutating `POST` by an explicit
allowlist (`_ADMIN_PATHS` / `_EDITOR_PATHS`), and a path in neither falls through ungated. The
single-file path reuses `POST /api/scenario`, already in `_EDITOR_PATHS`, so it needs no change. The
new `POST /api/scenarios/upload` route does not exist yet, so this proposal adds it to
`_EDITOR_PATHS` at the same tier as `/api/scenario`, closing what would otherwise be a silent gap.

- **Report the overwrite on the existing single-scenario save path.** `POST /api/scenario`
  (`save_scenario` in `bajutsu/serve/operations/reads.py`) already writes an arbitrarily-named
  `.yaml` file into the target's scope. Its response carries no signal today that a save replaced an
  existing file. Add that signal: before `scope.save(ref, text)`, check whether `scope.read(ref)`
  already returns content. If so, the response reports `overwritten: true`; otherwise `false`. The
  Author editor's Save button, `save_scenario`'s caller today, sees no change beyond the new
  response field.

- **A single-file "Upload scenario" affordance in the Replay tab.** Add an upload control next to
  the scenario picker in the Replay Form. The markup lives in `bajutsu/templates/serve.html.j2`; the
  wiring lives in `bajutsu/templates/serve.core.mjs`. It mirrors the file-input pattern the compose
  picker already uses (`cmp-scenarios-file`). The control reads the chosen `.yaml` file's text on
  the client. It posts that text to the same `/api/scenario` endpoint the Author editor's Save
  button uses, with the file's own name as the target path. A scenario landing on disk through
  Upload is then indistinguishable from one landing there through Save or `record`. On success the
  control reports "added" or "overwrote" from the new `overwritten` flag, then reloads the scenario
  list so the file is selectable and runnable at once. It activates once a config and a target are
  both in place, the precondition every other Replay affordance already applies.

- **A zip upload for a whole scenario set.** A new endpoint, `POST /api/scenarios/upload`, takes a
  raw request body and dispatches off the main request loop, the way `/api/upload` and
  `/api/artifacts/*` already do. It accepts a `.zip` of one or more `.yaml` files for the given
  target's scope. A new operation, `upload_scenarios`, resolves the same scope `save_scenario`
  resolves. It reads the archive's top-level `*.yaml` entries: a scope has no subdirectory
  concept, matching `list_scenarios`'s flat `glob("*.yaml")`. Resource bounds sized for scenario
  text — entry count, per-entry size, total size, each far smaller than BE-0073's bundle bounds,
  which exist for app binaries — apply during extraction, alongside the zip-slip containment
  `bajutsu/serve/uploads.py` already applies to a bundle. Every entry gets parsed with
  `load_scenario_file` before anything gets written. One entry that fails to parse aborts the whole
  zip and writes nothing, so a bad batch never leaves a partial overwrite behind — the same
  check-every-item-before-touching-any pattern `start_run_set`
  (`bajutsu/serve/operations/dispatch.py`) already applies to a scenario fan-out. On success each
  file gets written through `scope.save(name, text)`, and the response lists each name as created or
  overwritten. The route joins `_EDITOR_PATHS` in `bajutsu/serve/authz.py`, the same RBAC tier
  `/api/scenario` already sits at, so a viewer on a hosted deployment cannot reach it. The Replay
  Form gains a second upload control — or the same control, dispatching on the picked file's
  extension — that posts a `.zip` here and renders the created/overwritten summary the single-file
  path already renders.

- **Tests and docs.** Unit tests cover `save_scenario`'s new `overwritten` field: false on a first
  save, true on a second save to the same ref. Unit tests cover `upload_scenarios`: two scenarios in
  one zip both land and both appear in `list_scenarios`; a zip where one entry fails to parse writes
  nothing; a zip-slip entry and an oversized entry both get turned away. New `data-testid`s mark the
  upload controls. A dogfood E2E scenario alongside the existing Replay fixtures exercises the
  single-file upload path from end to end. `docs/architecture.md` and its `docs/ja/` mirror gain a
  note that Replay can populate a bound config's scenarios directory directly, with no config
  rebind.

## Alternatives considered

- **Running a scenario with no config bound at all.** A scenario stays backend-agnostic, but
  running one needs a target's backend, device, and app path. A bare scenario file supplies none of
  these. An upload resolves against an already-bound config's target rather than standing alone.
  This item leaves what a scenario needs to run unchanged; it changes how a scenario file reaches a
  scope that already holds that information.

- **Reusing the BE-0268 compose/artifact-upload flow for this too.** `POST /api/artifacts/scenarios`
  already caches an uploaded `.zip`/`.yaml` by content hash, but `bind_composition` still demands a
  `config` artifact leg even when the operator has no intention of rebinding the config, turning away
  a request that supplies none. Extending that flow would mean
  building a way to supply a placeholder config leg, purely to reach the scenarios leg — more
  surface than a narrower endpoint that assumes a config is already bound, the case this proposal
  exists for.

- **Accepting a nested-directory zip layout.** A scenario scope has no subdirectory concept today;
  `list_scenarios` globs the directory flatly. A nested entry would never appear in the listing. A
  flat top-level `*.yaml` reading matches the existing model. Revisiting this makes sense if scopes
  gain subdirectories elsewhere, and not before.

- **Best-effort partial writes when a zip has entries that fail to parse.** Set aside in favor of
  all-or-nothing. A batch that lands partially leaves the scenarios directory in a state the
  uploader did not ask for and cannot readily reason about. `start_run_set` already checks every
  item before it touches any of them, for the same reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add an `overwritten` field to `save_scenario`'s response.
- [ ] Add the single-file "Upload scenario" affordance to the Replay Form, posting to
      `POST /api/scenario`.
- [ ] Add the `POST /api/scenarios/upload` endpoint and `upload_scenarios` operation for a `.zip` of
      scenarios, with check-before-write and zip-slip/size bounds; add the route to `_EDITOR_PATHS`
      in `bajutsu/serve/authz.py`.
- [ ] Add the zip-upload control to the Replay Form, sharing the created/overwritten summary
      rendering with the single-file path.
- [ ] Add unit tests for both paths, `data-testid`s, and a dogfood E2E scenario.
- [ ] Update `docs/architecture.md` and its `docs/ja/` mirror.

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

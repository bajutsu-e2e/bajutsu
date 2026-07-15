**English** · [日本語](BE-XXXX-serve-ui-project-add-remove-ja.md)

# BE-XXXX — Manage projects (add / remove) from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-ui-project-add-remove.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Surfacing CLI features in the serve Web UI |
| Related | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) |
<!-- /BE-METADATA -->

## Introduction

The config project hub ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md))
turned `serve` into a hub over several named config bindings: a header switcher and a **Projects**
modal let you list projects, switch the active one, and run it, all from the browser. But the two
operations that grow and shrink the hub — **adding** a project and **removing** one — were left out
of that UI. The Projects modal only lists, switches, and runs; its own hint tells you to reach for
the terminal: *"Add or remove projects with `bajutsu project add` / `rm`."*

This item closes that gap: an **Add project** form in the Projects modal and a per-row **Remove**
control, so the full lifecycle of a hub — create, switch, run, delete — lives in one surface. It is
a pure UI follow-up: the endpoints already exist.

## Motivation

The registration and deregistration APIs shipped with BE-0225 unit 3 —
`POST /api/projects` (register/rebind, screened against the BE-0108 config-source allowlist) and
`DELETE /api/projects/<name>` (deregister, run history retained). BE-0225 unit 4 deliberately
shipped an MVP UI (*"MVP scope confirmed with the author"*) that consumed only the *read/switch*
side of those endpoints:

- `loadProjects` calls `GET /api/projects`; `switchProject` calls `POST /api/projects/<name>/activate`.
- The Projects modal renders the list and a **Run** button per row. There is no add form and no
  remove control; the design comment in `serve.core.mjs` records the boundary explicitly — *"Projects
  are added/removed with the `bajutsu project` CLI (unit 5), not here — this surface switches and
  inspects them."*

The result is an asymmetry that undercuts the "hub" framing. A team using `serve` as the shared
place to see their configs side by side must still drop to a terminal on the host to add or retire
one — the exact context switch the hub was meant to remove. It is also awkward for the hosted
topology ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), where the
browser *is* the only surface a user has: there is no shell to run `bajutsu project add` in, so
today a hosted hub cannot be grown at all through its own UI even though the registration endpoint
is already reachable and allowlist-screened for exactly that case.

Everything the missing UI needs already exists: the endpoints (BE-0225 unit 3), the config-source
input widgets the launcher uses (a Git-repo field with an optional credential, a `.zip` upload — 
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) /
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)), the allowlist
screening ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)),
and the RBAC gate. This item wires them together into the two controls the modal is missing.

This stays within the prime directives. Adding or removing a project is fully deterministic — it
writes a named config binding to the same `ProjectRegistry` seam the CLI writes to; no LLM enters
any path. The hub itself stays app-agnostic: per-app differences live in each project's own config,
and this UI only names a config source and binds it.

## Detailed design

The work is MECE across three units: the add form, the remove control, and the affordance/role
plumbing that reveals them.

### 1. Add-project form

A new **Add project** affordance in the Projects modal opens a small form with:

- a **name** field (the project's unique-within-org name), and
- a **config source** picker that reuses the launcher's existing source widgets rather than
  inventing new ones: the Git-repo field (spec + optional credential,
  [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) and the `.zip` upload
  ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)); a local
  filesystem path is offered only when the server permits it (unit 3).

Submitting `POST`s to `/api/projects` with the name and the discriminated source record, then
refreshes the list through the existing `loadProjects`. Because `POST /api/projects` is idempotent
by `(org, name)` (BE-0225 unit 1/3), re-submitting an existing name **rebinds** its source rather
than erroring — the form surfaces that as an explicit "rebind" outcome, not a silent duplicate. A
server-side rejection (a name clash under a different id, an allowlist refusal, a bad Git spec) is
shown inline in the form, reusing the launcher's error affordances.

### 2. Remove-project control

Each row in the projects list gains a **Remove** control alongside the existing **Run**/active
markers. Activating it asks for confirmation (deregistration is destructive to the *binding*, though
not to history), then `DELETE`s `/api/projects/<name>` and refreshes the list. The UI states the
BE-0225 contract plainly: **run history is retained**, only the binding is removed. Removing the
active project follows whatever the endpoint already does for that case (fall back to no active
project / another project); the UI reflects the post-delete `GET /api/projects` state rather than
guessing.

### 3. Affordance visibility + RBAC

The add/remove controls obey the same rules the rest of the hub UI already follows:

- **Hub-gated reveal.** Like the switcher and the Projects button, the controls are meaningful only
  in the modal, which is itself revealed once a hub exists. The **Add** affordance is the one
  exception worth considering: a single-config `serve` may want to *grow into* a hub from the UI, so
  the design should decide whether Add is reachable before the second project exists (e.g. surfaced
  even when the switcher is still hidden) — resolved during implementation, noted here as the one
  open UI question.
- **RBAC.** `POST`/`DELETE /api/projects` are **admin**-gated on the server (BE-0225 unit 3, like
  `/api/config`). The client mirrors that: the add form and the remove control are hidden or
  disabled for a non-admin session, so a viewer/editor sees the same read-only hub they do today and
  never a control that would 403. This reuses the role signal the UI already consumes for other
  admin-only surfaces.
- **Hosted allowlist.** The source picker hides the filesystem option when the server disallows it
  ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)),
  matching the server-side screening so a hosted user is never offered a source the API will refuse.

No new endpoints, no schema change, no CLI change — this is UI plus the small amount of client state
the two forms need.

## Alternatives considered

- **Leave add/remove CLI-only (status quo).** Rejected: it is the asymmetry this item exists to
  fix, and it makes the hosted hub ungrowable through its own UI, where no shell is available.
- **A separate "manage projects" page instead of extending the modal.** Rejected as heavier than the
  gap warrants: the modal already lists projects and is where a user goes to reason about the hub;
  add/remove belong next to the list they act on, not on a second screen.
- **A dedicated add-project source picker distinct from the launcher's.** Rejected: the launcher
  already has a reviewed, allowlist-aware source UI (Git + credential + `.zip` upload). Reusing it
  keeps one source-input concept and one screening path, and avoids a second widget to keep in step
  with [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md).
- **Surface project management in the MCP/CLI-feature sweep generically.** Rejected as too coarse:
  this is one concrete, high-value control pair with existing endpoints, worth landing on its own
  rather than waiting on a generic "expose every CLI subcommand" effort.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — Add-project form in the Projects modal, reusing the launcher's config-source widgets; `POST /api/projects`, rebind-aware, inline errors.
- [ ] 2 — Per-row Remove control; confirm + `DELETE /api/projects/<name>`; state that history is retained.
- [ ] 3 — Affordance visibility (hub-gate + the single-config "grow into a hub" question) and RBAC/allowlist mirroring so non-admins and hosted sessions see only permitted controls.

## References

`bajutsu/templates/serve.html.j2` (the Projects modal + the launcher's config-source widgets),
`bajutsu/templates/serve.core.mjs` (`loadProjects` / `switchProject` / the project-hub section),
`bajutsu/serve/operations/projects.py` (the `POST` / `DELETE` endpoints this consumes),
[cli](../../docs/cli.md#serve), [architecture](../../docs/architecture.md);
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (the hub this completes),
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
(the config-source allowlist the add form honors),
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) and
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) (the Git and
`.zip` config sources the picker reuses).

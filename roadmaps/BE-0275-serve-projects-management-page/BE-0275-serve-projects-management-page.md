**English** · [日本語](BE-0275-serve-projects-management-page-ja.md)

# BE-0275 — A projects management page in serve (a top-level view, not a modal)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0275](BE-0275-serve-projects-management-page.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0275") |
| Implementing PR | [#1123](https://github.com/bajutsu-e2e/bajutsu/pull/1123) |
| Topic | Surfacing CLI features in the serve Web UI |
| Related | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) |
| Superseded by | [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md) |
<!-- /BE-METADATA -->

## Introduction

The config project hub ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md))
turned `serve` into a hub over several named config bindings. The hub appears as a header switcher
and a **Projects** modal that lists projects, switches the active one, and runs it. The modal covers
only that read, switch, and run slice. The two operations that grow and shrink the hub, adding a
project and removing one, stay with the `bajutsu project` CLI, and the modal's own hint says so
(*"Add or remove projects with `bajutsu project add` / `rm`."*).

This item closes that gap by **promoting project management to a top-level page** in the serve
shell, not by adding controls to the modal. A **Projects** view sits alongside Record / Replay /
Crawl / Stats / Metrics. It lists projects and lets the user add, remove, switch to, and run each one.
The page absorbs the modal, which is retired. The reason is information architecture: a **project is
the concept above a config**, not a feature of one, so it belongs at the top level of the shell and
not inside a transient overlay.

## Motivation

In the shell's information architecture, a modal is an overlay bound to a *function*. It appears in
service of an action on the current context and dismisses back to that context (View config,
Settings, a run's Replay). The project hub does not fit that shape. A project is not an action
performed against the active config. **It is the container the active config lives inside.** Every
top-level view (Record, Replay, Crawl, Stats) already operates *within* the selected project. The
thing that owns and organizes those views cannot be a modal launched *from* them without inverting
the hierarchy. The BE-0225 modal works for a quick list-and-switch, but it misplaces the hub
structurally by rendering the superordinate concept as a subordinate overlay.

The misplacement has two concrete consequences:

- **No home for lifecycle actions.** Adding and removing a project are hub-level lifecycle
  operations, and a dismissible overlay suits them poorly, which is part of why BE-0225 left them in
  the CLI. A user who manages the hub from the browser has no place in the UI for the most basic
  thing a hub needs, growing and shrinking. In the hosted topology
  ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)) the browser is the
  *only* surface, with no shell to run `bajutsu project add` in.
- **The hub has no durable surface of its own.** The sibling cross-project metrics view
  ([BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md))
  is already a top-level view *about the hub's projects*, yet the projects it ranks have only an
  overlay. The list a user reasons about the hub from should be a place to navigate to, linkable and
  persistent, not something that evaporates on a click.

Everything the page needs already exists:

- the endpoints (BE-0225 unit 3): `GET` / `POST` / `DELETE /api/projects`,
  `POST /api/projects/<name>/activate`, and `POST /api/projects/<name>/run`
- the config source that the CLI's `bajutsu project add --config` takes: a Git spec with an optional
  private-repo credential ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)), or a
  local path
- the allowlist screening
  ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md))
- the role-based access control (RBAC) gate

This item re-homes those pieces onto a page and adds the two lifecycle controls the hub was missing.

The change stays within the prime directives. A project page is fully deterministic. It lists, binds,
and unbinds named config sources through the same `ProjectRegistry` seam the CLI writes to, and no
LLM enters any path. The hub stays app-agnostic: per-app differences live in each project's config,
and the page only names config sources and binds them.

## Detailed design

The work divides into four units that are mutually exclusive and collectively exhaustive (MECE): the
page itself, the two lifecycle controls it hosts, and the role and allowlist plumbing that governs
them.

### 1. The Projects top-level view

A new top-level view in the serve shell becomes the hub's home. The view is a `data-view="projects"` tab in
the header nav. It renders into its own main region, like the other views. The view lists each
registered project with the information needed to reason about it:

- name
- config source, as a human-readable summary of the Git, upload, or file binding
- whether the project is active
- its latest run verdict

The BE-0225 header **switcher** stays. It is a legitimate quick-switch control, like a
context selector. The **Projects modal is retired**: its list, its per-row Run, and its switching now
live on the page. Activating a project rebinds the live config through the existing
`POST /api/projects/<name>/activate`, exactly as the modal did. Every other view then operates
against the switched-to project with no restart.

The Metrics view (BE-0226) and the header switcher appear only for a real hub. A real hub means more than one
project. The Projects tab differs: it shows for **any** project count, single-config `serve`
included. Unit 4 records this as a deliberate reveal decision. The Add form is how a single-config
`serve` grows *into* a hub, so the page must be reachable before the second project exists, and the
hosted topology has no CLI to create that project.

### 2. Adding a project on the page

The page hosts an **Add project** form that mirrors the CLI twin this topic surfaces. `bajutsu
project add --config <local path | Git spec>` takes a single config-source string, so the form takes
one too. The form has these fields:

- a name field
- one **config-source** input: a Git spec `github:owner/repo[@ref][:path]`, or a local path when the
  server permits the `fs` source (unit 4)
- an optional private-repo credential
  ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)), stored write-once through
  the launcher's existing credential flow

Submitting sends `{name, sourceSpec}` to `POST /api/projects`. The server normalizes the string to
the stored `{kind, locator}` record through the one canonical `source_from_config` parser. The
browser never re-implements Git-spec parsing, and the same `_validate_source` allowlist screening
runs whether the source arrives as a string or a record. The endpoint, its route, and the registry
schema are unchanged. `sourceSpec` is an additive request field. It mirrors how `/api/config` already
accepts a `git` spec string.

`POST /api/projects` is idempotent by `(org, name)` (BE-0225 units 1 and 3). Re-submitting an
existing name therefore **rebinds** its source instead of erroring. The form surfaces that outcome as
an explicit "rebind", and a server-side rejection (allowlist refusal, malformed record) shows inline.

The `.zip` upload that the earlier framing named is **not** an Add source, for two reasons:

- `bajutsu project add` has no upload option, since it takes a `--config` string.
- `POST /api/projects/<name>/activate` cannot round-trip an uploaded bundle without a
  content-addressed object store and returns 409 otherwise (BE-0243 and BE-0268). An upload-backed
  project would register but could not be switched to in the common local topology.

Uploading a bundle remains the *config launcher's* way to bind the active config. Registering an
upload as a first-class project is left to a follow-up if a real need appears.

### 3. Removing a project on the page

Each row gains a **Remove** control. Activating the control asks for confirmation, because
deregistration is destructive to the *binding* and not to history. The page then `DELETE`s
`/api/projects/<name>` and refreshes the list. The page states the BE-0225 contract plainly. **Run
history is retained**, and the page removes the binding alone. Removing the active project follows whatever
the endpoint already does for that case, and the page reflects the post-delete `GET /api/projects`
state instead of guessing.

### 4. Reveal rules, RBAC, and the allowlist

The page and its controls obey the rules the rest of the hub UI already follows:

- **Reveal (decided).** The switcher and the Metrics tab track "a hub exists" (more than one project)
  and stay hidden for a single-config `serve`. The Projects tab does **not**. It shows whenever a
  project exists to manage, single-config included, and stays hidden only when no registry is wired.
  The Add form is the only UI-native way to create the second project that first reveals the
  switcher, and the hosted topology has no CLI, so the page must be reachable before the hub exists.
- **RBAC (server enforces; client shows and renders 403 inline).** `POST` / `DELETE /api/projects` are
  **admin**-gated on the server (`authz.required_role`, BE-0225 unit 3, like `/api/config`), and that
  gate is the real enforcement. The page follows the grain of the rest of the hub UI and does **not**
  hide the Add and Remove controls by role. The serve UI has no client-side role signal today, so
  every admin-only surface (config bind, upload, compose, Settings) is shown to all and the server
  answers 403. A refused write surfaces inline, exactly as on those surfaces. A genuinely read-only
  projects page for a non-admin session needs a client role signal that the UI does not yet expose.
  That signal would also gate the other admin surfaces uniformly, so it is **deferred to a separate
  BE item** and not invented ad hoc here.
- **Hosted allowlist.** The Add form drops the "local path" affordance from its hint when the server
  disallows the `fs` source
  ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)).
  The server refuses a hand-crafted local path regardless (`_validate_source`), so the screening is
  real, not merely cosmetic.

The change adds no new endpoints and alters no registry schema. It consists of four parts: a UI
relocation (modal to page), the two lifecycle controls, their small client state, and one additive
`sourceSpec` request field on the existing register endpoint, normalized through the CLI's own
`source_from_config`.

## Alternatives considered

- **Extend the existing Projects modal with add/remove (keep it a modal).** Rejected on information
  architecture. A modal is an overlay bound to a function, but a project is the concept *above* a
  config, not an action on one. Every view already runs inside the selected project, so the surface
  that owns and organizes those views should be a top-level page, not an overlay launched from within
  them. This was an earlier framing of this very proposal, and the page supersedes it.
- **Leave add/remove CLI-only (status quo).** Rejected. This is the gap the item exists to fix, and
  it leaves the hosted hub, where no shell exists, unable to grow through its own UI.
- **A full launcher-style source picker in the Add form (Git + credential + `.zip` upload + fs
  browse).** Rejected as over-reach. The CLI twin, `bajutsu project add`, takes a single `--config`
  string (a Git spec or a local path) and has no upload option. An upload-backed project also cannot
  be switched to without an object store (409). The form mirrors the CLI with one config-source
  string, normalized server-side by the same `source_from_config`. That keeps one parser and one
  allowlist path
  ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md))
  with no second widget to keep in step. Uploading stays the config launcher's way to bind the active
  config.
- **Gate the Add/Remove controls client-side by role now.** Rejected for this item. The serve UI has
  no client role signal, and every existing admin-only surface is shown and answered with a server
  403. A role signal invented just for this page would either duplicate a concept that should be
  shared or leave the other admin surfaces inconsistent. The server RBAC gate already enforces. The
  client role signal, and the read-only rendering it enables across all admin surfaces, is deferred
  to its own BE item.
- **Fold the projects page into the Metrics view (BE-0226).** Rejected, because the two views address
  distinct concerns. Metrics *ranks* projects (read-only, advisory), while the projects page
  *manages* their lifecycle (register, switch, run, remove). They are `Related` siblings over the
  same project list, not one view.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — The Projects top-level view: a `data-view="projects"` tab (new `serve.projects.mjs` module) that lists projects (name, source, active, latest verdict) and switches them; the BE-0225 modal is retired into it, the header switcher stays.
- [x] 2 — Add a project on the page: a name + single config-source string mirroring `bajutsu project add`; `POST /api/projects {name, sourceSpec}` normalized server-side via `source_from_config`, rebind-aware, inline errors. (`.zip` upload dropped — see Detailed design.)
- [x] 3 — Remove a project on the page: per-row control, confirm + `DELETE /api/projects/<name>`, history retained.
- [x] 4 — Reveal (Projects tab shown for any project count; switcher/Metrics stay hub-only), RBAC (server-enforced; controls shown + inline 403; client role gating deferred to a follow-up BE), and allowlist mirroring for the `fs` source.

**Log**
- [#1123](https://github.com/bajutsu-e2e/bajutsu/pull/1123) — the top-level Projects view, the add/remove lifecycle controls, the additive `sourceSpec` register field, and the modal retirement; docs (`web-ui` / `architecture` / `cli`, both languages) updated in step.

## References

`bajutsu/templates/serve.html.j2` (the top-level nav + the Projects view + the retired modal),
`bajutsu/templates/serve.projects.mjs` (the new Projects-page module: list / add / remove),
`bajutsu/templates/serve.core.mjs` (`showView` / `loadProjects` / `switchProject` / the shared
project-hub section), `bajutsu/templates/serve.metrics.mjs` (the sibling top-level hub view),
`bajutsu/serve/operations/projects.py` (the endpoints this consumes; the `sourceSpec` normalization),
`bajutsu/cli/commands/project.py` (the CLI twin the Add form mirrors),
[cli](../../docs/cli.md#serve), [architecture](../../docs/architecture.md);
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (the hub this re-homes),
[BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md) (the
sibling top-level view over the same projects),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the hosted topology
where the browser is the only surface, a core motivation for a UI-native lifecycle),
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) (the
config-source allowlist the add form honors),
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) and
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) (the Git and `.zip`
config sources the picker reuses).

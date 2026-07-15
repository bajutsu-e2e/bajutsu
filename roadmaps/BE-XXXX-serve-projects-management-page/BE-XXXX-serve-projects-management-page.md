**English** · [日本語](BE-XXXX-serve-projects-management-page-ja.md)

# BE-XXXX — A projects management page in serve (a top-level view, not a modal)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-projects-management-page.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Surfacing CLI features in the serve Web UI |
| Related | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) |
<!-- /BE-METADATA -->

## Introduction

The config project hub ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md))
turned `serve` into a hub over several named config bindings, and surfaced it as a header switcher
plus a **Projects** modal that lists projects, switches the active one, and runs it. The modal
covers only that read/switch/run slice; the two operations that grow and shrink the hub — adding a
project and removing one — were left to the `bajutsu project` CLI, and the modal's own hint says so
(*"Add or remove projects with `bajutsu project add` / `rm`."*).

This item proposes closing that gap not by adding controls to the modal, but by **promoting project
management to a top-level page** in the serve shell — a **Projects** view alongside Record / Replay /
Crawl / Stats / Metrics — that lists projects and lets you add, remove, switch to, and run each one.
The modal is retired into that page. The reason is information architecture: a **project is the
concept above a config**, not a feature of one, so it belongs at the top level of the shell, not
inside a transient overlay.

## Motivation

A modal is, in the shell's information architecture, an overlay bound to a *function*: it appears in
service of some action on the current context and dismisses back to it (View config, Settings, a
run's Replay). The project hub does not fit that shape. A project is not an action performed against
the active config — **it is the container the active config lives inside**. Every top-level view
(Record, Replay, Crawl, Stats) already operates *within* the selected project; the thing that owns
and organizes those views cannot itself be a modal launched *from* them without inverting the
hierarchy. The BE-0225 modal works for a quick list-and-switch, but it structurally misplaces the
hub: it renders the superordinate concept as a subordinate overlay.

The consequences of that misplacement are concrete:

- **No home for lifecycle actions.** Adding and removing a project are hub-level lifecycle
  operations, and a dismissible overlay is an awkward place for them — which is part of why BE-0225
  left them in the CLI. A user managing the hub from the browser (and, in the hosted topology
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), the browser is the
  *only* surface, with no shell to run `bajutsu project add` in) has no place in the UI to do the
  most basic thing a hub needs: grow and shrink.
- **The hub has no durable surface of its own.** The sibling cross-project metrics view
  ([BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md))
  is already a top-level view *about the hub's projects*; it is odd that the projects it ranks have
  no top-level view of their own, only an overlay. The list you reason about the hub from should be
  a place you navigate to, linkable and persistent, not something that evaporates on a click.

Everything the page needs already exists: the endpoints (BE-0225 unit 3 — `GET`/`POST`/`DELETE
/api/projects`, `POST /api/projects/<name>/activate`, `POST /api/projects/<name>/run`), the
config-source input widgets the launcher uses (Git repo + optional credential, `.zip` upload —
[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) /
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)), the allowlist
screening ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)),
and the RBAC gate. This item re-homes them onto a page and adds the two lifecycle controls the hub
was missing.

This stays within the prime directives. A project page is fully deterministic — it lists, binds, and
unbinds named config sources through the same `ProjectRegistry` seam the CLI writes to; no LLM enters
any path. The hub stays app-agnostic: per-app differences live in each project's config, and the page
only names config sources and binds them.

## Detailed design

The work is MECE across four units: the page itself, the two lifecycle controls it hosts, and the
role/allowlist plumbing that governs them.

### 1. The Projects top-level view

A new top-level view in the serve shell — a `data-view="projects"` tab in the header nav, rendered
into its own main region like the other views — becomes the hub's home. It lists each registered
project with the information needed to reason about it: name, config source (a human-readable summary
of the Git/upload/file binding), whether it is active, and its latest run verdict. The BE-0225 header
**switcher** stays (it is a legitimate quick-switch control, like a context selector), but the
**Projects modal is retired**: its list, its per-row Run, and the switching it did now live on this
page. Selecting/activating a project rebinds the live config through the existing
`POST /api/projects/<name>/activate`, exactly as the modal did, so every other view then operates
against the switched-to project with no restart.

Like the Metrics view (BE-0226) and the header switcher, the Projects tab is revealed only when a hub
is worth showing, so a single-config `serve` is visually unchanged — with the one deliberate exception
in unit 4 (letting a single-config `serve` grow *into* a hub).

### 2. Add a project (on the page)

The page hosts an **Add project** form: a name field and a config-source picker that reuses the
launcher's existing widgets rather than inventing new ones — the Git-repo field (spec + optional
credential, [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)) and the `.zip`
upload ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)); a local
filesystem path is offered only when the server permits it (unit 4). Submitting `POST`s to
`/api/projects` and refreshes the list. Because `POST /api/projects` is idempotent by `(org, name)`
(BE-0225 unit 1/3), re-submitting an existing name **rebinds** its source rather than erroring; the
form surfaces that as an explicit "rebind", and a server-side rejection (name clash under a different
id, allowlist refusal, bad Git spec) shows inline, reusing the launcher's error affordances.

### 3. Remove a project (on the page)

Each row gains a **Remove** control. Activating it asks for confirmation (deregistration is
destructive to the *binding*, not to history), then `DELETE`s `/api/projects/<name>` and refreshes
the list. The page states the BE-0225 contract plainly: **run history is retained**, only the binding
is removed. Removing the active project follows whatever the endpoint already does for that case; the
page reflects the post-delete `GET /api/projects` state rather than guessing.

### 4. Reveal rules, RBAC, and the allowlist

The page and its controls obey the rules the rest of the hub UI already follows:

- **Reveal.** The Projects tab tracks the same "a hub exists" signal the switcher and Metrics tab use.
  The one open question, resolved in implementation: whether the tab (or at least its **Add** form) is
  reachable for a single-config `serve` so a user can grow *into* a hub from the UI, rather than being
  forced to the CLI to create the second project that first reveals the hub. Recorded here as the one
  deliberate UI decision.
- **RBAC.** `POST`/`DELETE /api/projects` are **admin**-gated on the server (BE-0225 unit 3, like
  `/api/config`). The page mirrors that: the Add form and the Remove controls are hidden or disabled
  for a non-admin session, which sees a read-only projects page (list, switch, run) and never a
  control that would 403. This reuses the role signal the UI already consumes for other admin-only
  surfaces.
- **Hosted allowlist.** The source picker hides the filesystem option when the server disallows it
  ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)),
  matching the server-side screening so a hosted user is never offered a source the API will refuse.

No new endpoints, no schema change, no CLI change — this is a UI relocation (modal → page) plus the two
lifecycle controls and the small client state they need.

## Alternatives considered

- **Extend the existing Projects modal with add/remove (keep it a modal).** Rejected on information
  architecture: a modal is an overlay bound to a function, but a project is the concept *above* a
  config, not an action on one. Every view already runs inside the selected project, so the surface
  that owns and organizes those views should be a top-level page, not an overlay launched from within
  them. This was an earlier framing of this very proposal; the page supersedes it.
- **Leave add/remove CLI-only (status quo).** Rejected: it is the gap this item exists to fix, and it
  makes the hosted hub — where no shell exists — ungrowable through its own UI.
- **A dedicated add-project source picker distinct from the launcher's.** Rejected: the launcher
  already has a reviewed, allowlist-aware source UI (Git + credential + `.zip` upload). Reusing it keeps
  one source-input concept and one screening path, avoiding a second widget to keep in step with
  [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md).
- **Fold the projects page into the Metrics view (BE-0226).** Rejected: they are distinct concerns —
  Metrics *ranks* projects (read-only, advisory), the projects page *manages* their lifecycle
  (register/switch/run/remove). They are `Related` siblings over the same project list, not one view.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — The Projects top-level view: a `data-view="projects"` tab that lists projects (name, source, active, latest verdict) and switches/runs them; retire the BE-0225 modal into it, keep the header switcher.
- [ ] 2 — Add a project on the page: a form reusing the launcher's config-source widgets; `POST /api/projects`, rebind-aware, inline errors.
- [ ] 3 — Remove a project on the page: per-row control, confirm + `DELETE /api/projects/<name>`, history retained.
- [ ] 4 — Reveal rules (hub-gate + the single-config "grow into a hub" decision), RBAC, and allowlist mirroring so non-admins and hosted sessions see only permitted controls.

## References

`bajutsu/templates/serve.html.j2` (the top-level nav + the Projects modal this replaces + the
launcher's config-source widgets), `bajutsu/templates/serve.core.mjs` (`showView` / `loadProjects` /
`switchProject` / the project-hub section), `bajutsu/templates/serve.metrics.mjs` (the sibling
top-level hub view), `bajutsu/serve/operations/projects.py` (the endpoints this consumes),
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

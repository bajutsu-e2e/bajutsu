**English** · [日本語](BE-XXXX-comparison-row-navigation-vs-activate-ja.md)

# BE-XXXX — Split the comparison view's row click into read-only navigation and an explicit activate action

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-comparison-row-navigation-vs-activate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1806](https://github.com/bajutsu-e2e/bajutsu/pull/1806) |
| Topic | Authoring experience |
| Related | [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md), [BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) |
<!-- /BE-METADATA -->

## Introduction

The `bajutsu serve` web UI's comparison view ranks a hub's registered projects side by side, and
today a click on one of its rows *activates* that project: it rebinds the config the whole
deployment reads. This item splits that single gesture in two. A row click opens a read-only
drill-down into the clicked project's run history, and so does the keyboard equivalent the rows
lack today. Rebinding the deployment moves to an explicit per-row button that asks for
confirmation first and that, for a reader who may not use it, says so before the press rather than
after. The tab takes the view's own name at the same time, so
its label no longer reads as the Prometheus scrape endpoint `serve` exposes at `/metrics`.

## Motivation

A gesture that looks like navigation must not write. The comparison view's rows carry no control
and no affordance beyond the pointer cursor, so a reader ranking projects by pass-rate has every
reason to read a row click as "show me this one". What the click actually does is
`POST /api/projects/<name>/activate`, which repoints the hub's active project — server state, shared
by every tab against that deployment, not a per-viewer preference. Someone scanning the ranking can
thus change which project a colleague's Run tab acts on, having pressed nothing that announced a
write.

The same rows are unreachable without a pointer. A row is a plain table row with a click
listener. It carries no `tabindex`, no role, and no key handler, so keyboard-only navigation skips
it. The drill-down the comparison view exists to offer is available to some of its readers and
not others.

Two smaller frictions ride along. Nothing tells a reader whether they may activate at all: the UI
surfaces the transport's bare `forbidden` after the fact, and that names neither the refused action
nor the right it needs. And the tab reads `Metrics`, colliding with the Prometheus scrape endpoint
at `/metrics` while the view's own heading already reads `Project comparison`. In one deployment, "the metrics tab" and
"the metrics endpoint" name two unrelated surfaces.

Once this lands, a reader can open the comparison view, walk every row with the keyboard, and
drill into any project's history while the hub's active project stays put. Changing the active
project takes a button press and a confirmation. A reader without that right finds the button
disabled and carrying the reason.

## Detailed design

### 1. Read-only drill-down on the row

A row click opens a per-project panel inside the comparison view: the clicked project's run history,
newest first, each entry showing its verdict and scenario summary. The panel is read-only and
carries a control that returns to the ranking.

The data comes from `GET /api/projects/{name}/runs`, which the project hub already serves and no
part of the UI consumes yet. The endpoint answers with the same run-summary shape the Replay tab's
history renders, so the panel reuses that shape rather than defining one. No route, parameter, or
operation is added server-side.

### 2. An explicit activate control

Each row that is not already active gains an `Activate` button; the active row keeps a plain
`active` marker instead, mirroring how the Projects page distinguishes the two. Pressing the button
raises a confirmation that names the project and states the consequence — the deployment's live
config is rebound, for everyone — and only a confirmed press calls the existing activation path.
Activation itself is unchanged: the same request, the same admin gate, the same re-sync of the
config label and the switcher afterward.

### 3. A control that says, before the press, who may use it

Activation stays admin-only on the server. The reader learns that from the boot read rather than
from a refusal, through the capability block `GET /api/config` already carries: a per-caller report
of what this deployment can serve, each unavailable entry paired with the reason a disabled control
shows. A new `activate` entry joins `capture` and `orgs` there, computed through the same
`forbidden_for_role` gate the transport applies to the activate route, so the flag and the endpoint
cannot disagree. The button renders disabled, carrying that reason, for a reader who may not press
it. The block reports and never gates: the endpoint still refuses on its own.

The refusal is the second line, for the role that changed since boot. Both transports answer a
role-gated request with `{"error": "forbidden"}` and status 403, and the shared switch helper renders
that one value as a sentence naming the admin requirement, the way the trash view's permanent delete
already renders its own 403. The mapping lives in the shared helper, so the header's project switcher
and the Projects page's `Switch` button report the refusal the same way. The two lines together are
how the Orgs page already handles the same question.

### 4. Keyboard access

The drill-down's control is a real `button` in the row's name cell, wearing the look that cell
already had, with a visible focus style. Being a button, it brings Enter and Space with it and needs
no key handler of its own. Clicking elsewhere on the row stays a pointer convenience.

Giving the `tr` itself `tabindex` and a button role would reach the keyboard in fewer lines, and it
is the wrong trade twice over. It overrides the row's implicit table semantics, so a screen reader
loses the row and cell navigation over the very ranking this view exists to present; and it nests
the row's own activate control inside an Accessible Rich Internet Applications (ARIA) button, which
the ARIA specification forbids. A control that is already a control avoids both.

### 5. A name that does not collide

The tab reads `Comparison`, taken from the view it opens rather than from the endpoint it collided
with, and the heading — already `Project comparison` — gains a line distinguishing the view from the
Prometheus metrics endpoint the same server exposes. Element ids
and `data-testid` values keep their current names: they are the handle the existing tests and the
view-switching code hold, and renaming them would churn a surface no reader sees.

### 6. Tests

The repository runs no JavaScript test runtime, so the serve UI's modules are pinned by structural
tests that fetch the served module and assert on its text. The existing metrics UI test module gains
cases for the split. The row handler no longer reaches the activation path; the row keeps its table
semantics; the name cell's keyboard-reachable button, the activate control, and its confirmation all
ship; the refusal maps to the admin sentence; and the view's new name reaches the browser. The test
asserting the old deep-link behavior is replaced rather than kept, since the behavior it pins is the
behavior this item removes.

## Alternatives considered

- **Give `/stats` a project filter.** Rejected. The `/stats` dashboard aggregates one organization's
  whole run history and takes no project argument, so honoring the drill-down through it means
  adding one. [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md)
  already considered and rejected extending that dashboard in place, because the comparison is its
  own surface rather than a filtered variant of one config's dashboard. Reusing
  the per-project runs endpoint honors that reasoning and leaves the server untouched.
- **Keep the activation deep link and confirm it instead.** Rejected. A confirmation on a row click
  would ask every reader to dismiss a dialog to do the one thing the view is for, and it would still
  offer no read-only path. The problem is that navigation and mutation share one gesture, which a
  dialog does not separate.
- **Hide the activate control outright from readers who may not use it.** Rejected. The capability
  block the design above reads would support hiding as easily as disabling, so the choice is about
  what serves the reader, not about what the server can report. A control that vanishes leaves a
  reader wondering whether the feature exists; a disabled one carrying its reason answers the
  question the absence would raise. That is the position `serve_capabilities` states for itself —
  a control the reader cannot use should say why rather than just going grey — and this item follows
  it.
- **Surface only the refusal, and render the button the same for everyone.** Rejected. It costs
  nothing server-side, but it tells the reader after the press and the confirmation rather than
  before, which is the wrong order for a control the confirmation exists to slow down. The refusal
  is still worth mapping, as the design above keeps it, for a role that changed since the boot read.
- **Probe an admin-gated read to infer the right.** Rejected: it infers one permission from another
  endpoint's answer. The capability block reports the very gate the activate route applies, so there
  is nothing to infer.
- **Drop the row click entirely and leave only the button.** Rejected. It fixes the write-behind-navigation problem by removing
  the drill-down that made the comparison view an entry point, a loss with no compensating gain.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The read-only drill-down panel, rendered from `GET /api/projects/{name}/runs`
- [x] The per-row activate button and its confirmation
- [x] The `activate` capability in the boot read, and the button disabled with its reason
- [x] The `forbidden` refusal mapped to an admin sentence in the shared switch helper
- [x] Keyboard access through a real button in the name cell, with a focus style
- [x] The tab renamed to `Comparison`, and the note distinguishing the view from `/metrics`
- [x] Structural tests for the split, replacing the deep-link test

## References

- [BE-0226 — Cross-project metrics comparison dashboard](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md)
  — the item that built the comparison view, and whose row-click design this one revises.
- [BE-0275 — A projects management page in serve (a top-level view, not a modal)](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md)
  — the projects page whose per-row `Switch` button and `active` marker this design mirrors.
- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)
  — the role-based access control model that keeps permission judgment on the server.
- [Issue #1720](https://github.com/bajutsu-e2e/bajutsu/issues/1720) — the report this item answers.

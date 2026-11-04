**English** · [日本語](BE-0263-serve-author-tiling-layout-ja.md)

# BE-0263 — Bring the Author view into the tiling layout

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0263](BE-0263-serve-author-tiling-layout.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0263") |
| Implementing PR | [#1164](https://github.com/bajutsu-e2e/bajutsu/pull/1164) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI gives its three interactive views a tiling layout. The views are Replay, Record,
and Crawl. `initTiling` in `bajutsu/templates/serve.author.js` registers each view in a `SPECS`
list. Each view becomes a tree of resizable panes that the user can drag to split or swap, and the
layout persists in localStorage.

The Author view ([BE-0098](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md))
is **not** in `SPECS`. It keeps a fixed CSS grid:
`#view-author{grid-template-columns:var(--rec-left,340px) var(--gw) 1fr}`. The grid stacks six
elements into one column about 340px wide:

- the controls
- the step list
- the YAML editor
- the determinism badge
- the codegen panel
- the enrich panel

The screenshot pane, often empty, takes the entire remaining width.

The view therefore reads as broken. The YAML editor, the primary work surface, is jammed into a
narrow column with a horizontal scrollbar. The space allocation is the inverse of what authoring
needs.

This item brings Author into the same tiling system as the other editors. Its panes then resize and
rearrange, and the editor gets the room it deserves.

## Motivation

Author is the most editing-heavy view in serve. Its whole purpose is to look at a screen and write
YAML. Yet it is the only interactive view denied the tiling that makes the others usable at
different window sizes and task focuses.

The current fixed grid was a reasonable first cut ("form on the left, screen on the right"). It
puts the two things an author works with most, the YAML and the screen, in a fixed 340px-versus-rest
split that cannot be adjusted. It also crams the steps, audit, codegen, and enrich panels into the
same narrow column.

Beyond the immediate "it looks broken" symptom, this is an **affinity gap**. Record and Replay
already solve exactly this problem with `initTiling`, and Author reimplements a lesser layout
instead of joining them. Registering Author in `SPECS` reuses the drag-split, resize, persistence,
and reduced-motion handling that the tiler already provides. The responsive small-screen path from
BE-0072 and the themable transitions from BE-0191 come along for free. The codebase no longer grows
a second, weaker layout code path.

No prime directive is touched, because the change is presentation only. The tiler already honors a
reduced-motion guard that matters for determinism (BE-0191 and BE-0072), and Author inherits it. On-device
authoring therefore stays as deterministic as the other views.

## Detailed design

1. **Register `view-author` in `SPECS`.** Add a spec that describes Author's panes and a sensible
   default layout tree. The candidate panes are:
   - controls (target, scenario, mode)
   - step list
   - YAML editor, with its badge, problems, audit, codegen, and enrich affordances
   - the screen (screenshot or picker)

   Mark the panes that come and go `optional`, as Record's run-result pane already is. The screen
   when no session or run is active is one example. Hiding such a pane then keeps the tree valid.
2. **Make the YAML editor a first-class pane.** Give the editor its own resizable pane, so it can be
   the dominant surface instead of the third card in a 340px stack.
3. **Reconcile the markup and CSS.** Adjust the Author section's structure and the `#view-author`
   grid rule so that the tiler owns the layout. This is how Record and Replay work, through the
   block-level `<main>` and `.tile-root`. The change removes the now-redundant fixed-column grid and
   keeps the non-tiled narrow-tier stack (BE-0072) working.
4. **Persist and validate like the other views.** Author's tree persists under the same
   `bajutsu-tiles` key, with the same validity check: leaves are unique, known, and all required
   leaves are present. A stale saved layout then degrades gracefully to the default.
5. **Verification.** Layout is hard to unit-test, so verify in the browser, per the project's
   serve-UI norm:
   - The editor pane is dominant and resizable.
   - Panes split and swap.
   - The layout persists.
   - The narrow-tier stack is unaffected.

   Add or adjust any existing tiler tests that enumerate `SPECS`.

## Alternatives considered

- **Keep the fixed grid and just widen the left column or flip the split.** This is a quick cosmetic
  patch. It leaves Author with a bespoke, non-adjustable layout and the affinity gap intact, so the
  next window size or task focus is wrong again. Reusing the tiler is the durable fix.
- **A dedicated Author-only resizable layout.** This adds a second layout engine to maintain
  alongside `initTiling`, for no benefit that the shared tiler does not already provide.
- **Defer until an ES-module frontend (BE-0247) lands.** BE-0247 restructures how the JavaScript is
  delivered, not the tiler's behavior. Author can join `SPECS` now and ride whatever module story
  arrives later.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — register `view-author` in `SPECS` with a default tree. All four panes are always
  present, so none is marked `optional` (nothing shows/hides like Record's Run-result pane).
- [x] Unit 2 — YAML editor as a first-class resizable pane (its own `yaml` leaf, dominant at 3/6 in
  the default tree).
- [x] Unit 3 — reconcile markup (steps / YAML / screen moved into `.rec-stack`) + drop the redundant
  `#view-author` fixed grid; `#view-author` is now block-level like the other tiled views.
- [x] Unit 4 — persistence + validity reuse under `bajutsu-tiles` (inherited unchanged from the tiler).
- [x] Unit 5 — browser verification (narrow-tier pane switching + spec-resolution) + tiler-spec test
  coverage in `tests/serve/test_http_author_ui.py`.

**Log**

- Author registered in `initTiling`'s `SPECS`. Markup and CSS were reconciled to drop the fixed
  grid. The YAML editor, steps, and screen became first-class resizable panes.
  ([#1164](https://github.com/bajutsu-e2e/bajutsu/pull/1164))

## References

- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md) (the Author view)
- [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md) (the narrow-tier stack the tiler defers to)
- [BE-0191 — Pluggable theme system for the serve Web UI](../BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui.md) (the tiler's themable, reduced-motion-aware transitions)
- [BE-0202 — Split serve.js into section files](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) (where `initTiling` lives)
- `bajutsu/templates/serve.author.js` (`initTiling` / `SPECS`), `bajutsu/templates/serve.html.j2` (`#view-author`), `bajutsu/templates/serve.css` (`#view-author` grid)

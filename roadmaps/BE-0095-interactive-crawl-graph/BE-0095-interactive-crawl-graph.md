**English** · [日本語](BE-0095-interactive-crawl-graph-ja.md)

# BE-0095 — Interactive crawl graph (draggable nodes + realign)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0095](BE-0095-interactive-crawl-graph.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0095") |
| Implementing PR | [#398](https://github.com/bajutsu-e2e/bajutsu/pull/398) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Let a viewer drag individual nodes of the crawl graph to any position, and add a
**realign** button that snaps the whole graph back to its automatic layout. The crawl
report's screen map already supports zoom, pan, group expand/collapse, and edge
highlighting, but the nodes themselves are pinned to a fixed BFS-layered grid. This
proposal adds one more degree of freedom — free per-node placement — without touching the
crawl engine or the runner: it is purely a change to the report's rendering layer.

## Motivation

The screen map is laid out as a deterministic BFS grid: each BFS depth is a column, and
units stack vertically within a column. That layout is reproducible and reads well for
small maps, but on a real app it gets crowded — edges cross, related screens land far
apart, and there is no way to pull two nodes side by side to compare them or to untangle a
busy region while reading. Zoom and pan move the *whole* view together; they cannot
reposition a single node relative to its neighbours.

Letting the viewer drag nodes makes the map a workspace, not just a snapshot: you can pull
the screens you are reasoning about together, drag an over-plotted node out of a pile to
read its label, or lay out a branch the way it makes sense to you. The catch is that once
you have rearranged things, you want a one-click way back to the canonical layout — hence
the realign button, which discards every manual move and restores the automatic BFS grid.

This stays clear of the prime directives: it is report-viewer UI only. No LLM is involved,
the deterministic `run`/CI gate is untouched, and there is nothing app-specific — the same
rendering serves every target (iOS, web). If anything it reinforces *determinism first*:
the automatic layout remains the canonical, reproducible arrangement, and realign always
returns to it.

## Detailed design

All work is in [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js), the
report's graph renderer. The layout already computes `pos` — a `Map` from each unit id
(`g:<key>` for a collapsed group, `n:<fingerprint>` for a node/member) to its algorithmic
`{x, y}`. Edges, group frames, and unit boxes are all positioned by reading `pos`, so
overriding a node's entry in `pos` moves the node *and* re-routes everything attached to it
for free.

**Manual-position overrides.** Add a module-level `const nodeOverrides = new Map()` keyed
by unit id, alongside the existing `expandedGroups` set and `gview` object. In
`renderGraph`, after the algorithmic `pos` is built, overlay any overrides:
`nodeOverrides.forEach((p, id) => { if (pos.has(id)) pos.set(id, p) })`. Because this
happens before edges, frames, and tiles are emitted, a moved node carries its bezier
endpoints, its group frame, and its highlight wiring with it — no special-casing per
consumer. The map is module-scoped (like `gview` / `expandedGroups`), so manual positions
**persist across the per-poll re-render** while a crawl streams in: existing nodes stay
where the user put them, and only newly discovered units take their fresh algorithmic
slot. Reloading the page starts clean (no persistence to `localStorage`); this is a
deliberate scope boundary (see Alternatives).

**Dragging a node.** A node drag is a distinct gesture from canvas pan. In the existing
`mousedown` handler on `#crawl-graph`, branch on the target: if the press landed inside a
`.gnode`, start a *node* drag instead of a pan, recording the node's unit id (`data-uid`)
and start point; otherwise keep today's pan behaviour. On `mousemove`, translate the cursor
delta into content coordinates by dividing by the current zoom (`gview.k`) — the inner
layer is scaled with CSS `zoom`, so screen pixels and content pixels differ by exactly that
factor — and live-update the dragged node's `style.left/top`. To keep the drag smooth
without a full re-render per frame, update only the affected SVG edge paths (those whose
`data-a`/`data-b` equals the dragged unit id) by recomputing their `d` from the node's new
position; everything else is untouched. On `mouseup`, commit the final content position to
`nodeOverrides`.

**Click vs. drag.** Reuse the established `moved` threshold (a press that drifts more than
~3px is a drag): a node press that does *not* drift falls through to the existing `click`
handler, so tapping a node still opens its lightbox and the group expand/collapse buttons
still work. Only a genuine drag suppresses the click.

**Realign button.** Add a control next to the existing zoom buttons (`#crawl-zoomin` /
`#crawl-zoomout` / `#crawl-zoomreset`) — e.g. `#crawl-realign` — whose handler does
`nodeOverrides.clear(); redrawGraph()`. That throws away every manual move and re-renders
from the purely algorithmic `pos`, returning the whole graph to the canonical BFS layout.
It is independent of `resetView` (which only resets zoom/pan), so a user can realign nodes
without losing their zoom, or reset the view without losing their manual layout.

**Group expand/collapse interaction.** Unit ids change when a group is expanded
(`g:<key>` → one `n:<fingerprint>` per member) or collapsed. Overrides are keyed by unit
id, so an override simply stops matching when its unit's identity changes, and those
nodes fall back to the algorithmic position — acceptable and predictable. No attempt is
made to migrate a group's manual position onto its members.

**Touch.** Pointer (mouse) dragging is the core of this item. Mirroring it for touch
(one-finger drag on a node moves the node; drag on the background still pans) is a natural
extension that reuses the same override/commit path and should follow once the pointer path
lands; it can ship in the same change if low-cost, or as an immediate follow-up.

## Alternatives considered

- **Persist manual positions across reloads (`localStorage`, per run).** Heavier — needs a
  storage key per run id, a load path on first render, and eviction. Deferred: the common
  need is rearranging *during* a reading/crawl session, which module-scoped state already
  covers. Can be layered on later without changing the override model.
- **Force-directed / physics layout (drag with spring-back, auto-detangling).** A larger,
  different feature: it would replace the deterministic BFS grid with a stochastic layout
  and pull in a physics loop or a graph library (the renderer is deliberately
  library-free). Out of scope here; the request is explicitly *free* placement plus a
  return-to-canonical button.
- **Snap-to-grid / alignment guides while dragging.** A usability nicety, not required for
  the core ask. Left out to keep the gesture "drop it exactly where you let go"; could be
  added later behind the same drag path.
- **Per-node "reset this node" (vs. only a global realign).** The chosen scope is a single
  global realign. A right-click "send this node back to its slot" is a possible later
  refinement; it would just delete one key from `nodeOverrides` and redraw.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.

## References

- [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) — the crawl graph
  renderer (`renderGraph`, the `pos` map, the zoom/pan handlers).
- [BE-0072](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md) —
  responsive web UI / touch pan + pinch-zoom on this same graph; the precedent for adding
  an input path without changing the layout model.
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — the autonomous crawl that produces the screen map this view renders (unchanged by this
  proposal).

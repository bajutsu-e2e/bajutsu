**English** · [日本語](BE-0337-roadmap-dashboard-map-layout-ja.md)

# BE-0337 — Transit-map layout for the roadmap dashboard's relationship view

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0337](BE-0337-roadmap-dashboard-map-layout.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0337") |
| Implementing PR | [#1427](https://github.com/bajutsu-e2e/bajutsu/pull/1427) |
| Topic | Contributor workflow |
| Related | [BE-0335](../BE-0335-roadmap-dashboard-relationship-graph/BE-0335-roadmap-dashboard-relationship-graph.md), [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md), [BE-0311](../BE-0311-roadmap-dashboard-table-view/BE-0311-roadmap-dashboard-table-view.md) |
<!-- /BE-METADATA -->

## Introduction

This item replaces the layout behind the roadmap dashboard's relationship view with a transit-map arrangement:

- one horizontal row per topic
- the row's items placed along it in identifier order
- a curved line joining every pair of items that stand in a relation

The view keeps everything [BE-0335](../BE-0335-roadmap-dashboard-relationship-graph/BE-0335-roadmap-dashboard-relationship-graph.md) gave it: the same three relations read from the same metadata, the same status colours, and the same composition with the search box and status chips. Only two things change: where the items sit and how a reader moves through them. The view is renamed from Graph to Map to match what it now shows.

Two things follow from the new arrangement.

- The layout becomes a pure function of each item's topic and identifier. Python computes it once at build time and ships it as finished Scalable Vector Graphics (SVG) coordinates. The browser runs no force simulation at all.
- Every item now has a place a reader can predict, so pointing at an item is enough to read it. A caption line under the map names the item under the pointer and its status. The map dims everything except that item, its lines, and the items on the other end of them.

## Motivation

The force-directed layout answers "what is the shape of this graph" and little else. It settles into one large cluster with small components scattered around it. The position of any single item carries no meaning a reader can rely on. An item sits where the simulation pushed it, so the same question, "where is the item I was looking at", has a different answer after every change to the roadmap. A reader looking for one item has to hunt for it instead of knowing where to look.

A transit map trades that away for a fixed, readable address. Its rows are the topics a reader already browses by in Cards view, so the eye lands on the right row immediately. Along a row, items run in identifier order, so an item's neighbours are the items proposed around the same time. What was a hairball becomes a picture with an index. A reader who knows an item's topic knows its row. A reader who knows its number knows roughly how far along the row to look.

The rearrangement also removes the one part of the view that could not be checked. A force simulation's output is only as good as its parameters. The picture had to be judged by eye after every change to them, and no test could pin its correctness. Coordinates derived from topic and identifier are ordinary data. A test can assert three things:

- An item's row is its topic's row.
- A row's items ascend by identifier.
- Every line ends on two drawn items.

This is a documentation-site change, so it stays clear of every prime directive ([`CLAUDE.md`](../../CLAUDE.md)):

- No large language model (LLM) sits anywhere near the page.
- No `run` or continuous integration (CI) verdict path passes through the page.
- The layout is generic over whatever items the metadata produces, so nothing about it is specific to an application under test.

Moving the layout out of the browser and into the build strengthens determinism instead of bending it. The same roadmap now yields identical coordinates, and a test checks that result instead of trusting it.

## Detailed design

[`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) generates the relationship view. Today it emits the nodes and edges as a JavaScript Object Notation (JSON) payload and lets an inline script lay them out. The work is mutually exclusive, collectively exhaustive (MECE) across these units:

1. **Build-time layout.** Add a pure function that maps the drawn items to coordinates.
   - Group the items by topic.
   - Order the topics the way the shared index already orders them.
   - Sort each topic's items by identifier.
   - Place a row's *i*-th item at a fixed step along that row.

   The function takes the items and returns the rows, the node positions, and the drawing's dimensions. It has no input beyond the metadata, so the same roadmap always produces the same coordinates, and a test can assert them.
2. **Packing each row independently, and saying so.** Place an item at the *i*-th slot of its own row, not at its rank among all drawn items. One shared identifier axis across every row is the more informative arrangement, because a column then means the same moment in every row. A roadmap of ten items would use it. It does not survive this roadmap's scale. Roughly 200 drawn items on one axis make a drawing some 14,000 pixels wide, nearly all of it the whitespace of rows holding a handful of items each. Packing each row to its own items keeps the drawing near a seventh of that width. The cost is that columns no longer line up across rows. The caption states the arrangement plainly: items run in identifier order **within a row**. No reader then infers an alignment the drawing does not have.
3. **Rows.** Render one row per topic that has at least one drawn item. A row is a horizontal rule spanning the plot, with the topic's name right-aligned in a left gutter. A topic with no drawn item gets no row, so the map carries no empty lines.
4. **Curved connectors.** Render each relation as a path, not a straight line, in the shape its endpoints call for:
   - an arc bulging above the row when both items share the row
   - a curve that leaves one row horizontally and arrives at the other horizontally when the items sit in different rows

   Both read as one continuous line, not a diagonal cutting across the rows. Keep BE-0335's edge semantics unchanged. A mutual `Related` pair is one undirected line. `Origin` and `Superseded by` stay directed and keep their arrowhead and dash pattern.
5. **Nodes.** Render each item as a link holding three pieces:
   - a transparent circle wide enough to be an easy pointer and touch target
   - a smaller visible dot in its status colour
   - its identifier as a label beneath

   The link points at the item's file on GitHub, so a node stays reachable by keyboard and openable in a new tab.
6. **Pointing at an item.** On hover or focus, dim every node and line except that item, the lines touching it, and the items on their far ends. Write the item's identifier, title, and status into a caption line below the map. The line is a live region carrying a default sentence. A reader using a screen reader hears what a sighted reader sees, and the map is never silently uninformative.
7. **Retiring the simulation.** Delete the force simulation, the fit-to-box pass, and the drag, pan, and zoom handling from the inline script. What remains is the filter composition and the pointing behaviour above. A map with a fixed address for every item need not be dragged, and its container scrolls. Panning and zooming lose their purpose along with the simulation that made them necessary. This removes roughly two hundred lines of browser code, and with them the only part of the page whose output no test could pin.
8. **Fitting the drawing on the page.** Wrap the map in a container that scrolls horizontally. Give the drawing a minimum width equal to its natural size, so it is never scaled below the size its labels were drawn for. The reader scrolls a wide map instead of squinting at a shrunken one.
9. **Renaming the view.** Rename the third toggle entry from Graph to Map, and update the page's introductory prose to describe the arrangement. The stored preference keeps its key and its `graph` value, so a reader who chose this view before the change still lands on it.
10. **Tests.** Replace BE-0335's payload tests with tests over the layout:
    - Every drawn item is placed exactly once.
    - An item's row is its topic's row.
    - Each row's items ascend by identifier.
    - Every path's endpoints are drawn items.
    - No row is emitted for a topic without items.
    - The whole drawing fits inside the reported dimensions.

    These tests need no browser, so the gate stays fast and free of any LLM.

`docs/` needs no matching edit. The pages that mention the dashboard describe what it is for, not which views it offers or how they are drawn. If a future sentence there enumerates the views, the same change updates it ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).

## Alternatives considered

- **Keep the force-directed layout and add map-like controls to it**, such as zoom buttons, a minimap, or a detail panel. Rejected. The complaint this arrangement answers is that an item has no fixed place, and no control around a simulation gives it one. Navigation aids would make an unpredictable picture easier to move around, not easier to read.
- **One shared identifier axis across every row.** Rejected at this roadmap's scale, for the reason in unit 2: a drawing some 14,000 pixels wide, most of it empty. The option is worth revisiting only if the drawn set shrinks by an order of magnitude, which the roadmap's direction makes unlikely.
- **Order each row by something other than the identifier**, such as the number of relations an item has (so the busiest lead) or its status. Rejected. An identifier is the one key a reader already knows before opening the page, which is what makes a position predictable. Relation count and status both change as the roadmap changes, which would move an item that did not change at all.
- **Keep both layouts, as a fourth view.** Rejected. The dashboard would then carry two answers to one question, and this item argues the force-directed one is the weaker of the two. Retiring it removes its code as well, instead of leaving an unused simulation to maintain.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Build-time layout function, pure in the item metadata.
- [x] Per-row packing, with the caption stating the arrangement.
- [x] One row per topic that has a drawn item, with a rule and a gutter label.
- [x] Curved connectors, keeping BE-0335's directed and undirected edge semantics.
- [x] Nodes: hit target, status dot, identifier label, real link.
- [x] Hover and focus highlighting, with a live caption line naming the item.
- [x] Force simulation, fit pass, and drag / pan / zoom handling removed.
- [x] Horizontally scrolling container with a natural minimum width.
- [x] The view renamed from Graph to Map, with the intro prose updated.
- [x] Tests over the layout function.

## References

- [BE-0335 — Relationship graph view for the roadmap dashboard](../BE-0335-roadmap-dashboard-relationship-graph/BE-0335-roadmap-dashboard-relationship-graph.md)
  — the view this item rearranges, and the source of the relation model it keeps unchanged.
- [BE-0094 — Generated roadmap status dashboard](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)
  — the dashboard both items extend.
- [BE-0311 — Sortable table view for the roadmap dashboard](../BE-0311-roadmap-dashboard-table-view/BE-0311-roadmap-dashboard-table-view.md)
  — the view toggle whose third entry this item renames.
- [BE-0113 — Keep DESIGN.md and architecture.md in step with behavior](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
  — the rule governing when a behavior change must carry a documentation edit.
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — the generator the
  layout lands in.
</content>

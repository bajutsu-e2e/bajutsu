**English** · [日本語](BE-0335-roadmap-dashboard-relationship-graph-ja.md)

# BE-0335 — Relationship graph view for the roadmap dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0335](BE-0335-roadmap-dashboard-relationship-graph.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0335") |
| Implementing PR | [#1421](https://github.com/bajutsu-e2e/bajutsu/pull/1421) |
| Topic | Contributor workflow |
| Related | [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md), [BE-0219](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md), [BE-0311](../BE-0311-roadmap-dashboard-table-view/BE-0311-roadmap-dashboard-table-view.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a relationship graph to the roadmap status dashboard. The dashboard is a GitHub Pages page with three layers of history:

- [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) generates the page from each roadmap item's metadata.
- [BE-0219](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md) gave the page a search box.
- [BE-0311](../BE-0311-roadmap-dashboard-table-view/BE-0311-roadmap-dashboard-table-view.md) gave the page a sortable table.

A roadmap item's metadata block already names the other items it relates to. `Related` lists the items it belongs with. `Origin` records the item it grew out of. `Superseded by` names the item that replaced it. No view has ever drawn those relations.

The graph draws them as a third view beside Cards and Table. It shows one node per item, colored by status, and one edge per relation. A force-directed simulation lays the graph out in the reader's browser. A reader drags a node, pans and zooms the canvas, and hovers a node to light up its immediate neighbors. The search box and status chips narrow the graph exactly as they narrow the other two views.

The graph adds no new metadata field and asks no author to maintain anything new. It reads every edge from a relation an item already declares. The picture is therefore a rendering of the committed roadmap, not a second record that could disagree with it.

## Motivation

The dashboard answers questions about items one at a time. Cards view groups items by topic and shows each item's status. Table view sorts every item by id, title, topic, status, or date. Both views treat the roadmap as a list. A list cannot answer the questions a reader asks about *structure*:

- Which items form a cluster around one feature?
- Which proposal is the hub that several later items hang off?
- Which corners of the roadmap stand alone?

The metadata holds concrete answers to those questions today, and no view shows them. The `Related` row of one item tells a reader its immediate neighbors. Reaching the cluster around the item means opening each neighbor in turn and holding the accumulated picture in memory. The roadmap has roughly 330 items, and about 200 of them stand in at least one relation. Traversing that by hand is impractical. It is also the traversal a contributor performs when scoping new work, because the first question about any proposal is what it touches.

The dashboard is a static artifact of the documentation site, so this change stays clear of every prime directive ([`CLAUDE.md`](../../CLAUDE.md)):

- No large language model (LLM) sits anywhere near the page.
- No `run` or continuous integration (CI) verdict path passes through the page.
- The graph is generic over whatever items the metadata produces, so nothing in it is specific to an application under test.

Determinism still shapes the design, even away from the gate. The layout draws its starting positions from each node's index, not from a random number generator. The same roadmap always produces the same picture, so a reader comparing the page against a colleague's screen sees the same shape.

## Detailed design

[`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) generates the dashboard. It renders every item as a `.be-card` inside a topic section and mirrors the same items as `.be-row` table rows. One inline `<script>` block progressively enhances both. The graph slots into that structure as a third sibling view. The work is mutually exclusive, collectively exhaustive (MECE) across these units:

1. **Relation edges on the shared item model.** Extend [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)'s `Item` with the three relations an item's metadata already declares: `related`, `origin_refs`, and `superseded_by`. Each is a tuple of item ids.
   - Read the relations from the English metadata block by extracting every `BE-NNNN` token from the `Related`, `Origin`, and `Superseded by` values. An author writes each value as Markdown holding one or more links, so a token scan reads it without depending on the link syntax the author chose.
   - Drop a token that names an item the loaded tree has no directory for. Drop an item's reference to itself. A typo in one file then cannot produce an edge with no endpoint.
   - Reading the relations from the English file alone matches how `Status` and `Topic` are already read. It also keeps a Japanese-only wording fix from changing the graph.
2. **Graph data on the page.** Emit the nodes and edges as one JSON payload in a `<script type="application/json">` element. Attributes spread across the existing card markup would not work, because an edge belongs to a pair of items and has no natural home on either item's card.
   - A node carries its id, title, topic, status bucket, and the link to its file on GitHub.
   - An edge carries its two endpoints and its kind.
   - `Related` is a mutual relation that both items are expected to declare, so the two directions collapse into one undirected edge.
   - `Origin` and `Superseded by` each stay directed, because "grew out of" and "was replaced by" read in one direction only.
3. **Force-directed layout.** Lay the graph out in the browser with a small simulation written for this page. The simulation applies three forces:
   - repulsion between every pair of nodes
   - attraction along every edge
   - a weak pull toward the center that keeps a disconnected component from drifting off the canvas

   The simulation cools over a fixed number of steps and then stops. The details are:
   - Seed the starting positions from each node's index along a golden-angle spiral, never from `Math.random()`, so the layout is reproducible.
   - Cut the repulsion off beyond a few ideal edge lengths, and scale the settled result to fit the canvas. The roadmap's relations form one large component alongside many small ones. Nothing but the centering pull holds two components together. Without both measures, the small components drift outward and squash the large one into an unreadable blob.
   - Run the simulation the first time a reader opens the graph, not on page load. A reader who never leaves Cards view then pays nothing for it.
4. **Rendering and interaction.** Draw the result as Scalable Vector Graphics (SVG): a line per edge, and a circle per node. The circle carries its item's status color and is sized by how many relations the item takes part in.
   - Label only the hubs at rest, because two hundred labels at once are unreadable. Every other node names itself on hover, on focus, and through its tooltip.
   - Wrap each node in an SVG anchor pointing at the item's file on GitHub. A node is then a real link, reachable by keyboard and openable in a new tab, not a shape with a click handler.
   - Dragging a node moves it and suppresses the click that would otherwise follow, so a drag never navigates away.
   - Dragging the background pans, and the mouse wheel zooms.
   - Hovering or focusing a node dims everything except that node, its edges, and the items on the other end of them.
5. **A third view in the existing toggle.** Add a `Graph` button beside `Cards` and `Table`. The existing toggle already shows one view, hides the others, and persists the reader's choice in `localStorage`; the new button reuses it.
   - The graph honors the search box and the status chips. A node whose item is filtered out is hidden along with its edges. The filtered-out neighbors of a visible node leave it with fewer edges.
   - Like the rest of the dashboard's interactivity, the graph is progressive enhancement. The markup hides its view, and only the toggle shows it. A reader with scripting off sees the Cards view exactly as today.
6. **Honest coverage of items outside the graph.** An item on no edge has no place in a relationship graph. Drawing roughly 130 unconnected dots around the picture would bury the structure the graph exists to show. Render the items that take part in at least one relation, and state the omission on the page. A caption reports how many of the roadmap's items the graph draws and how many take part in none.

   Word the caption as *participation*, not *declaration*, because the two differ. `Origin` is one-directional, and `Related`'s expected reciprocity is a convention, not a checked rule. An item named only by another item is therefore drawn while declaring no relation of its own. Saying such an item "declares" a relation would be untrue. BE-0094's rule about the data the dashboard shows forbids that: never present a figure with no source in the metadata, and never let a view imply completeness it does not have.
7. **Styling.** Add the graph's canvas, node, edge, and caption rules to the generator's stylesheet. Reuse the per-status colors the cards and badges already use, so a node's color means the same thing everywhere on the page. Keep the graph legible in both the light and the dark theme.
8. **Tests.** Extend [`tests/test_roadmap_dashboard.py`](../../tests/test_roadmap_dashboard.py) and [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py) to pin the machine-checkable outcome:
   - The relation fields parse out of a metadata block.
   - A self-reference and a reference to a missing item are both dropped.
   - A mutual `Related` pair yields exactly one undirected edge.
   - Every emitted edge names two emitted nodes.
   - The payload's node set is exactly the items carrying a relation.
   - The page renders the third toggle button and the graph container.

   These tests need no browser, so the gate stays fast and free of any LLM.

The page's introductory prose gains a sentence describing the third view, next to the sentences that already describe Cards and Table. No document under [`docs/`](../../docs/README.md) needs a matching edit, because the pages that mention the dashboard describe what it is for, not which views it offers. If a future sentence there enumerates the views, the change that adds a view updates that sentence ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).

## Alternatives considered

- **Render the diagram at build time with Mermaid.** The documentation site already renders `mermaid` fences, so a generated `graph LR` block would be the smallest possible change. Rejected, for two reasons:
  - Mermaid lays out a graph of this size as a wide, deep hierarchy that a reader must scroll, not explore. It offers no filtering, no dragging, and no neighbor highlighting, which are the affordances that make a graph of about 200 nodes readable at all.
  - The existing search box and status chips would have nothing to act on, because a Mermaid diagram renders as one opaque block, not as per-item elements.
- **Adopt a graph library such as D3 or Cytoscape.** Rejected. The documentation site ships no JavaScript bundler and no dependency on a third-party script. The whole dashboard is deliberately one generated page with inline styles and one inline script. A force-directed layout for a few hundred nodes is a short, self-contained routine, so a bundled dependency, with its supply-chain surface, buys little.
- **Draw every item, including those with no relation.** Rejected. It would put roughly 130 isolated dots around a picture whose point is the structure among the remaining items. It would also make the simulation's centering force fight a large ring of nodes that no edge holds in place. The caption in unit 6 keeps the omission visible instead of silent.
- **Add a `Depends on` metadata field so the graph can draw a dependency order.** Rejected for this item. A dependency edge is a different claim from `Related`. Asserting one across roughly 330 existing items would need an author-by-author backfill that no generated view can supply. The graph draws the relations the roadmap already records, and proposing a new relation is separate work.
- **Replace Cards or Table with the graph.** Rejected. A graph serves neither view's job. Cards answers "what is in this topic, and how far along is it". Table answers "what changed most recently". A node-link picture makes neither question easier to answer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Relation edges (`related` / `origin_refs` / `superseded_by`) on the shared `Item` model.
- [x] Graph data emitted as a JSON payload on the generated page.
- [x] Deterministic force-directed layout, run on first open of the view.
- [x] SVG rendering with drag, pan, zoom, neighbor highlighting, and per-node links.
- [x] `Graph` added to the existing view toggle, composing with the search and status filters.
- [x] Caption reporting how many items the graph draws and how many take part in none.
- [x] Styling for both the light and the dark theme.
- [x] Tests in `tests/test_roadmap_dashboard.py` and `tests/test_roadmap_index.py`.

## References

- [BE-0094 — Generated roadmap status dashboard](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)
  — the dashboard this item extends, and the source of its rule against showing a figure with no
  source in the metadata.
- [BE-0219 — Text search on the roadmap status dashboard](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md)
  — the search box and status chips the graph composes with.
- [BE-0311 — Sortable table view for the roadmap dashboard](../BE-0311-roadmap-dashboard-table-view/BE-0311-roadmap-dashboard-table-view.md)
  — the view toggle this item adds a third entry to.
- [BE-0113 — Keep DESIGN.md and architecture.md in step with behavior](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
  — the rule governing when a behavior change must carry a documentation edit.
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — the generator the
  graph view lands in.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — the shared item model the
  relation fields extend.
</content>
</invoke>

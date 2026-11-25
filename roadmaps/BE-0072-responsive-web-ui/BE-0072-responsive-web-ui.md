**English** · [日本語](BE-0072-responsive-web-ui-ja.md)

# BE-0072 — Responsive serve Web UI (small-screen & touch layout)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0072](BE-0072-responsive-web-ui.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0072") |
| Implementing PR | [#288](https://github.com/bajutsu-e2e/bajutsu/pull/288) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

This proposal makes the `bajutsu serve` Web UI usable on a small screen, such as a phone or a narrow tablet. The UI is desktop-first and multi-pane. Below a width breakpoint, it reflows into a single-column, touch-friendly layout. Its mouse-only interactions gain a touch path.

The change is **frontend-only** and touches the inlined `serve.*` templates. It adds no server route, job, or config, and it changes nothing the deterministic runner does. `serve` stays a Tier-1 convenience that shells out to the deterministic CLI. The proposal therefore sits inside the prime directives ([CLAUDE.md](../../CLAUDE.md)): no large language model (LLM) enters the gate, no verdict changes, and per-app behavior stays in config.

## Motivation

The `serve` UI already declares `<meta name="viewport" content="width=device-width, initial-scale=1">` in `bajutsu/templates/serve.html.j2`. The declaration tells a phone browser to render the page at its real CSS width, not as a zoomed-out desktop page. The stylesheet behind it breaks that promise. `bajutsu/templates/serve.css` contains **no `@media` query at all**, so every layout rule belongs to one design built for a wide screen. The gap bites in four concrete ways.

1. **The core layout cannot fit a phone.** The Replay view lays its panels out as a five-track grid: `main{grid-template-columns:var(--rep-left,340px) var(--gw) var(--rep-mid,340px) var(--gw) 1fr}`. The grid holds two ~340 px side columns, resize gutters, and the report column. Before the report gets any room, the grid is already wider than a 390 px phone. The columns either force horizontal scrolling or crush to unreadable widths. The crawl side panel is pinned at `flex:0 0 22rem`. The header holds the brand, the `.toptabs` row, and the theme switch pushed out with `margin-left:auto`. It is laid out for one wide line and never wraps.

2. **Every interaction is mouse-only, and not one touch handler exists.** The UI has two stacked resize systems and a pannable graph. All of them listen to mouse events exclusively:
   - The column gutters resize on `mousedown` and `e.clientX`. The layout persists as `bajutsu-splits`.
   - The richer tiling layout lets a user drag a panel's `.tile-grip` to split or swap panes. It persists as a tree in `bajutsu-tiles`, and `mousedown` and `elementFromPoint` drive it.
   - The crawl graph pans on `mousedown` and `clientX`, and it zooms on `wheel`.

   `serve.js` registers **zero** `touchstart` or `pointerdown` handlers. On a phone, the gutters and grips are dead affordances: no pointer exists to press and drag. The drag-to-split tiling is a power feature that makes no sense on a hand-held screen. The graph cannot be panned or pinch-zoomed at all.

3. **A shrunk desktop is not a usable phone UI.** The wide layout is the only layout, so a phone gets the desktop squeezed into its viewport. Side-by-side panes have no room to coexist. Tap targets are sized for a cursor. Content is reachable only by zoom and pan. All the features are present, but a thumb cannot reach them.

4. **Hosting makes phone access a real entry point.** The hosting work turns `serve` into a shared, URL-addressable service: [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) covers public hosting and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers self-hosting. Opening the service from a phone then stops being a corner case. A reviewer who gets a "run failed" ping wants to open that run's report on the device in hand, glance at the result, and perhaps kick off a re-run. Today that is a horizontally scrolling struggle. The embedded report is *already* part-way there, because `bajutsu/templates/report.css` carries its own `@media` rules. The report content reflows, but the `serve` chrome around it does not. The shell is the piece left behind.

The goal is deliberately modest and bounded: the **same UI and the same features**, laid out so that a narrow viewport gets a readable, tappable, single-column experience instead of a miniature desktop.

## Detailed design

### Scope and constraints

The change is frontend-only and stays within `bajutsu/templates/serve.css`. One small companion change in `bajutsu/templates/serve.js` covers the one genuinely interactive gap: touch panning of the crawl graph. No server route, job model, or config key changes. The UI keeps shelling out to the deterministic CLI exactly as today.

The design keeps two constraints from [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md):

- The response stays **one self-contained HTML document** with CSS and JS inlined. It has no `/static` routes and no asset pipeline.
- The CSS stays **hand-written with no build step**. It uses no framework.

### Breakpoint strategy

The design introduces one `max-width` breakpoint, or two. The first is a phone tier (around `≤ 640px`). A second, narrow tier (around `≤ 900px`) is added if a tablet refinement is warranted.

The narrow rules are layered **after** the existing desktop rules and override only what must change. The wide layout remains the untouched default, so the desktop experience cannot regress. The exact pixel values are a small open detail. The load-bearing decision is that the desktop layout is the base and the narrow tiers are additive overrides.

### Single-column reflow, per surface

Below the phone breakpoint, the multi-track grids collapse to a single column, and each view's panes stack vertically. All three views can hold more than one pane. The reflow therefore pairs the vertical stack with a way to bring one pane to full width at a time:

* **Replay** — the five-track grid (`--rep-left` / `--rep-mid` side columns, gutters, report) becomes one column. The sims/config form, the streaming log, and the embedded report stack vertically. A segmented switcher (*Form · Log · Report*) fronts the stack, so the active pane gets the full width. This tier drops the custom-property widths and the gutters.
* **Record** — the `.rec-stack` agent-progress / output split stacks the same way (*Progress · Output*).
* **Crawl** — the fixed `22rem` side panel (*Plan · Console*) and the graph become stacked sections. A switcher (*Graph · Plan · Console*) fronts them. The pannable graph keeps the full width when it is the active pane.

### Replacing the mouse-only interaction model on small screens

The drag-resize gutters and the drag-to-split/swap tiling are desktop power features. They have no touch equivalent and no room on a phone. At the narrow tier they are hidden: `.tile-divider`, `.tile-grip`, and the column gutters get `display:none`, and the saved `bajutsu-splits` and `bajutsu-tiles` layouts are not applied. The single-column stack and the per-view switcher take over their job.

The tap targets that survive are the top tabs, the per-view switcher, the run and stop buttons, and the history rows. They are sized to a comfortable touch minimum (~44 px). The work is almost entirely CSS. `serve.js` gains a guard that skips applying the persisted desktop layouts when the narrow media query matches.

### Crawl graph: the one interactive change

The crawl graph is the one place that needs real `serve.js` work instead of CSS. Its pan is bound to `mousedown` and `clientX`, and its zoom to `wheel`. Neither fires from a touch. The change adds touch support that reuses the existing pan/zoom math. The view is already a translate-plus-`zoom` model, so the mapping is unchanged:

- `touchstart` and `touchmove` drive a single-finger pan.
- A pinch (two-finger distance) maps onto the existing `zoomBy`.

The graph is an investigation aid and never part of the gate. The change is purely additive interactivity.

### Header reflow

At the narrow tier, the header row (brand, top tabs, theme switch) wraps or condenses. For example, the brand column sits on one line and the tabs on the next, with the theme switch kept reachable. The header never forces horizontal scrolling.

### Pieces that are already fluid — preserve, don't rebuild

The screenshot modal is already viewport-relative (`.shotbox{width:min(960px,94vw);max-height:88vh}`, `.shotmain img{max-width:46vw}`). The design keeps its fluid sizing. At phone width, only its side-by-side image and next-steps list restack vertically.

The embedded report already ships its own `@media` rules in `report.css`. This item does not touch the report's internals. It changes only the `serve` shell that frames the report.

### Determinism, app-agnosticism, and the gate

* **No LLM, no verdict touched.** The UI shells out to the deterministic `run` / `record`. This change rearranges pixels and adds a touch handler. Pass/fail is never computed here, so prime directives 1 and 2 hold by construction.
* **App-agnostic.** Layout depends on the viewport, not on any app. Nothing here reads `apps.<name>`, the drivers, or the runner.
* **Linux-testable.** The verification path is the existing web backend, which runs on the Linux gate (below). The change needs no Simulator and no macOS.

### The test contract (machine-checkable)

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) already drives the `serve` UI with the Web (Playwright) backend as a deterministic Tier-2 regression net. Responsiveness is machine-checkable there. Playwright sets the `viewport`, and the assertions stay structural, never an LLM judgment of "looks good":

* **No horizontal overflow at phone width.** At a phone viewport (for example 390×844), the document's scroll width does not exceed its client width. That is the deterministic statement of "nothing spills off the side".
* **Key controls are reachable.** The run button, the top tabs, and the per-view switcher are visible and not clipped at phone width. These are existing assertions, re-run at the narrow viewport.
* **The desktop layout is unchanged.** At a wide viewport, the existing Replay/Record/Crawl UI scenarios pass exactly as before. They pin that the additive narrow rules did not regress the default.

Extending the BE-0058 net this way keeps the determinism-first contract intact. It also stops the responsive rules from regressing silently.

### Out of scope

- A broader desktop redesign of the resizable/collapsible tiling. This item hides the tiling on small screens and does not rework it for desktop.
- Any change to what `run` captures or decides.
- The report's own internals, which are already responsive via `report.css`.

## Alternatives considered

* **A separate mobile template or a mobile-specific app.** Rejected: it doubles the surface that must stay in sync with every `serve` feature. It also conflicts with the single self-contained HTML response that BE-0011 deliberately serves. One responsive template is the lower-maintenance choice, and a feature added once keeps working on every screen.
* **Adopt a CSS framework (Tailwind / Bootstrap) for its grid and breakpoints.** Rejected: `serve` is stdlib-only, with hand-written inlined CSS and no asset build step (BE-0011, *Alternatives considered*). Plain `@media` queries deliver the responsiveness without a framework dependency or a build pipeline. The tool stays installable from a fresh clone.
* **Leave it desktop-only and rely on the browser's pinch-zoom and pan.** Rejected: that is exactly the status quo this item exists to fix. The user must zoom and pan around a shrunk desktop, and the graph cannot be panned at all, because no touch handler exists.
* **Make the panes user-collapsible/resizable on desktop too (a general layout overhaul).** Out of scope here. This item is the small-screen reflow. A broader resizable/collapsible-pane redesign for desktop, if wanted, is a separate proposal and must not block the narrow-viewport fix.
* **Convert the mouse drag handlers to pointer events to get touch "for free".** Tempting, but a uniform pointer model would also bring the drag-to-split tiling and gutter-resize to touch. Both are awkward and pointless on a small screen. The conversion is also a larger, riskier change to a working desktop interaction. The smaller, safer change hides those affordances at the narrow tier. It adds a focused touch path for the graph alone, the one thing that genuinely needs to move under a finger.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md) — the prime directives this respects: AI never judges, determinism first, app-agnostic. The change adds no LLM and never computes a verdict; it only rearranges a Tier-1 convenience.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) — the UI this reshapes, and its stdlib-only, single-self-contained-HTML, no-build-step constraints that bound the design.
* [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) — the future structured-editing surface that will live in the same UI and must also behave on a small screen.
* [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — the hosting direction that turns phone access into a realistic, desirable entry point.
* [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the Web-backend regression net that asserts the responsive layout deterministically at a phone viewport.
* `bajutsu/templates/serve.css`, `bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.js` — the frontend this item changes; `bajutsu/templates/report.css` — the already-responsive embedded report this item leaves untouched.

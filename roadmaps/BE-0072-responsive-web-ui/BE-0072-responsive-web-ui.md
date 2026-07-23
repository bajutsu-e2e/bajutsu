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

Make the `bajutsu serve` Web UI usable on a small screen — a phone or a narrow tablet — by reflowing its desktop-first, multi-pane layout into a single-column, touch-friendly one below a width breakpoint, and by giving its mouse-only interactions a touch path. This is a **frontend-only** change to the inlined `serve.*` templates: it adds no server route, job, or config, and changes nothing the deterministic runner does. `serve` stays a Tier-1 convenience that only shells out to the deterministic CLI, so the proposal sits entirely inside the prime directives ([CLAUDE.md](../../CLAUDE.md)) — no LLM enters the gate, no verdict is touched, and per-app behavior stays in config.

## Motivation

The `serve` UI already declares `<meta name="viewport" content="width=device-width, initial-scale=1">` in `bajutsu/templates/serve.html.j2`, which tells a phone browser to render the page at its real CSS width rather than as a zoomed-out desktop page. That is a promise the stylesheet behind it does not keep: `bajutsu/templates/serve.css` contains **no `@media` query at all**, so every layout rule is a single design built for a wide screen. The gap bites in four concrete ways.

1. **The core layout cannot fit a phone.** The Replay view lays its panels out as a five-track grid — `main{grid-template-columns:var(--rep-left,340px) var(--gw) var(--rep-mid,340px) var(--gw) 1fr}` — two ~340 px side columns plus resize gutters plus the report column. That is already wider than a 390 px phone before the report gets any room, so the columns either force horizontal scrolling or crush to unreadable widths. The crawl side panel is pinned at `flex:0 0 22rem`, and the header (brand, the `.toptabs` row, the theme switch pushed out with `margin-left:auto`) is laid out for one wide line and never wraps.

2. **Every interaction is mouse-only — there is not one touch handler.** The UI has two stacked resize systems and a pannable graph, and all of them are wired to mouse events exclusively: the column gutters resize on `mousedown` + `e.clientX` (persisted as `bajutsu-splits`); the richer tiling layout — where you drag a panel's `.tile-grip` to split or swap panes, persisted as a tree in `bajutsu-tiles` — is driven by `mousedown` and `elementFromPoint`; the crawl graph pans on `mousedown`/`clientX` and zooms on `wheel`. `serve.js` registers **zero** `touchstart` / `pointerdown` handlers. On a phone the gutters and grips are dead affordances (there is no pointer to press-and-drag, and the drag-to-split tiling is a power feature that makes no sense on a hand-held screen), and the graph cannot be panned or pinch-zoomed at all.

3. **A shrunk desktop is not a usable phone UI.** Because the wide layout is the only layout, a phone gets the desktop squeezed into its viewport: side-by-side panes with no room to coexist, tap targets sized for a cursor, and content reachable only by zoom-and-pan. The features are all present; they are just not laid out so a thumb can reach them.

4. **Hosting makes phone access a real entry point.** As the hosting work turns `serve` into a shared, URL-addressable service — [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (public hosting) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting) — opening it from a phone stops being a corner case. A reviewer who gets a "run failed" ping wants to open that run's report on the device in their hand, glance at the result, and perhaps kick off a re-run; today that is a horizontally-scrolling struggle. Notably the embedded report is *already* part-way there — `bajutsu/templates/report.css` carries its own `@media` rules — so the report content reflows while the `serve` chrome around it does not. The shell is the piece left behind.

The goal is deliberately modest and bounded: the **same UI and the same features**, laid out so a narrow viewport gets a readable, tappable, single-column experience instead of a miniature desktop.

## Detailed design

### Scope and constraints

A frontend-only change confined to `bajutsu/templates/serve.css`, with a small companion change in `bajutsu/templates/serve.js` for the one genuinely interactive gap (touch panning of the crawl graph). No server route, job model, or config key changes; the UI keeps shelling out to the deterministic CLI exactly as today. Two constraints from [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) are kept: the response stays **one self-contained HTML document** with CSS/JS inlined (no `/static` routes, no asset pipeline), and the CSS stays **hand-written with no build step** (no framework).

### Breakpoint strategy

Introduce one — possibly two — `max-width` breakpoints: a phone tier (around `≤ 640px`) and, if a tablet refinement is warranted, a narrow tier (around `≤ 900px`). The narrow rules are layered **after** the existing desktop rules and override only what must change, so the wide layout remains the untouched default and there is no risk of regressing the desktop experience. The exact pixel values are a small open detail; the load-bearing decision is that the desktop layout is the base and the narrow tiers are additive overrides.

### Single-column reflow, per surface

Below the phone breakpoint, the multi-track grids collapse to a single column and each view's panes stack vertically. Because all three views can hold more than one pane, the reflow pairs the vertical stack with a way to bring one pane to full width at a time:

* **Replay** — the five-track grid (`--rep-left` / `--rep-mid` side columns, gutters, report) becomes one column. The sims/config form, the streaming log, and the embedded report stack vertically, fronted by a segmented switcher (*Form · Log · Report*) so the active pane gets the full width. The custom-property widths and the gutters are dropped at this tier.
* **Record** — the `.rec-stack` agent-progress / output split stacks the same way (*Progress · Output*).
* **Crawl** — the fixed `22rem` side panel (*Plan · Console*) and the graph become stacked sections fronted by a switcher (*Graph · Plan · Console*); the pannable graph keeps the full width when it is the active pane.

### Replacing the mouse-only interaction model on small screens

The drag-resize gutters and the drag-to-split/swap tiling are desktop power features with no touch equivalent and no room on a phone. At the narrow tier they are hidden — `.tile-divider`, `.tile-grip`, and the column gutters are set `display:none`, and the saved `bajutsu-splits` / `bajutsu-tiles` layouts are simply not applied — with the single-column stack and the per-view switcher taking over their job. Tap targets that survive (the top tabs, the per-view switcher, the run/stop buttons, the history rows) are sized to a comfortable touch minimum (~44 px). This is almost entirely CSS plus a guard in `serve.js` to skip applying the persisted desktop layouts when the narrow media query matches.

### Crawl graph: the one interactive change

The crawl graph is the only place that needs real `serve.js` work rather than CSS, because its pan is bound to `mousedown`/`clientX` and its zoom to `wheel` — neither fires from a touch. The change is to add touch support that reuses the existing pan/zoom math (the view is already a translate-plus-`zoom` model, so the mapping is unchanged): `touchstart`/`touchmove` for single-finger pan, and pinch (two-finger distance) mapped onto the existing `zoomBy`. The graph is an investigation aid, never part of the gate, so this is purely additive interactivity.

### Header reflow

At the narrow tier the header row (brand, top tabs, theme switch) wraps or condenses — e.g. the brand column on one line and the tabs on the next, with the theme switch kept reachable — so it never forces horizontal scrolling.

### Pieces that are already fluid — preserve, don't rebuild

The screenshot modal is already viewport-relative (`.shotbox{width:min(960px,94vw);max-height:88vh}`, `.shotmain img{max-width:46vw}`); its fluid sizing is kept, and only its side-by-side image + next-steps list is restacked vertically at phone width. The embedded report already ships its own `@media` rules in `report.css`; this item does not touch the report's internals, only the `serve` shell that frames it.

### Determinism, app-agnosticism, and the gate

* **No LLM, no verdict touched.** The UI only shells out to the deterministic `run` / `record`; this change rearranges pixels and adds a touch handler. Pass/fail is never computed here, so prime directives 1 and 2 hold by construction.
* **App-agnostic.** Layout depends on the viewport, not on any app; nothing here reads `apps.<name>`, the drivers, or the runner.
* **Linux-testable.** The verification path is the existing web backend, which runs on the Linux gate (below), so this needs no Simulator and no macOS.

### The test contract (machine-checkable)

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) already drives the `serve` UI with the Web (Playwright) backend as a deterministic Tier-2 regression net. Responsiveness is naturally machine-checkable there, because Playwright sets the `viewport` and the assertions stay structural — never an LLM judgment of "looks good":

* **No horizontal overflow at phone width** — at a phone viewport (e.g. 390×844) the document's scroll width does not exceed its client width, the deterministic statement of "nothing spills off the side".
* **Key controls are reachable** — the run button, the top tabs, and the per-view switcher are visible and not clipped at phone width (existing assertions, re-run at the narrow viewport).
* **The desktop layout is unchanged** — at a wide viewport the existing Replay/Record/Crawl UI scenarios pass exactly as before, pinning that the additive narrow rules did not regress the default.

Extending the BE-0058 net this way keeps the determinism-first contract intact and stops the responsive rules from silently regressing.

### Out of scope

A broader desktop redesign of the resizable/collapsible tiling (this item only hides it on small screens, it does not rework it for desktop); any change to what `run` captures or decides; and the report's own internals (already responsive via `report.css`).

## Alternatives considered

* **A separate mobile template or a mobile-specific app.** Rejected: it doubles the surface that must stay in sync with every `serve` feature and conflicts with the single self-contained HTML response BE-0011 deliberately serves. One responsive template is the lower-maintenance choice, and it keeps a feature added once working on every screen.
* **Adopt a CSS framework (Tailwind / Bootstrap) for its grid and breakpoints.** Rejected: `serve` is stdlib-only with hand-written, inlined CSS and no asset build step (BE-0011, *Alternatives considered*). Plain `@media` queries deliver the responsiveness without taking on a framework dependency or a build pipeline, and keep the tool installable from a fresh clone.
* **Leave it desktop-only and rely on the browser's pinch-zoom and pan.** Rejected: that is exactly the status quo this item exists to fix — a shrunk desktop the user must zoom and pan around, with the graph not pannable at all because no touch handler exists.
* **Make the panes user-collapsible/resizable on desktop too (a general layout overhaul).** Out of scope here. This item is specifically the small-screen reflow; a broader resizable/collapsible-pane redesign for desktop, if wanted, is a separate proposal and must not block the narrow-viewport fix.
* **Convert the mouse drag handlers to pointer events to get touch "for free".** Tempting, but a uniform pointer model would also bring the drag-to-split tiling and gutter-resize to touch, where they are awkward and pointless on a small screen — and it is a larger, riskier change to a working desktop interaction. Hiding those affordances at the narrow tier and adding a focused touch path only for the graph (the one thing that genuinely needs to move under a finger) is the smaller, safer change.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md) — the prime directives this respects: AI never judges, determinism first, app-agnostic. The change adds no LLM and never computes a verdict; it only rearranges a Tier-1 convenience.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) — the UI this reshapes, and its stdlib-only, single-self-contained-HTML, no-build-step constraints that bound the design.
* [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) — the future structured-editing surface that will live in the same UI and must also behave on a small screen.
* [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — the hosting direction that turns phone access into a realistic, desirable entry point.
* [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the Web-backend regression net that asserts the responsive layout deterministically at a phone viewport.
* `bajutsu/templates/serve.css`, `bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.js` — the frontend this item changes; `bajutsu/templates/report.css` — the already-responsive embedded report this item leaves untouched.

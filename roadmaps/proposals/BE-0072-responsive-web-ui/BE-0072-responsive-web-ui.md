**English** · [日本語](BE-0072-responsive-web-ui-ja.md)

# BE-0072 — Responsive serve Web UI (small-screen & touch layout)

* Proposal: [BE-0072](BE-0072-responsive-web-ui.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

Make the `bajutsu serve` Web UI (BE-0011) usable on a small screen — a phone or a narrow tablet — by reflowing its desktop-first, multi-pane layout into a single-column, touch-friendly one below a width breakpoint. This is a frontend-only change to the inlined `serve.*` templates; it adds no backend behavior and keeps `serve` a Tier-1 convenience, so it touches none of the prime directives.

## Motivation

The `serve` UI already declares `<meta name="viewport" content="width=device-width, initial-scale=1">`, which tells a phone browser to render it at its real CSS width — but the stylesheet behind that promise is built only for a wide screen. `bajutsu/templates/serve.css` has **no `@media` query at all**; every layout rule is a single fixed design:

* **Replay** lays its panels out as a five-track grid — `main{grid-template-columns:var(--rep-left,340px) var(--gw) var(--rep-mid,340px) var(--gw) 1fr}` — two ~340 px side columns plus resize gutters plus the report. On a 390 px-wide phone that grid cannot fit, so the columns either overflow horizontally or crush to unreadable widths.
* **Record** and **crawl** are nested `.tile-split` flex panes separated by `.tile-divider` drag handles whose `.tile-grip` is a *mouse* affordation (`cursor:grab`, pointer-driven resize). The crawl side panel is pinned at `flex:0 0 22rem`. None of this degrades on a touch screen: there is no pointer to hover, the grips are small, and side-by-side panes have no room to coexist.
* The header (`.toptabs`, the theme switch pushed to `margin-left:auto`) assumes one wide row and does not wrap.

The practical result: you can author and replay from a laptop, but you cannot glance at a run, kick one off, or read a report from a phone — even though `serve` is increasingly something people want to reach from a phone as the hosting work (BE-0015 public hosting, BE-0016 self-hosting) turns it into a shared, URL-addressable service. A reviewer who gets a "run failed" ping wants to open the report on the device in their hand. Today that experience is a horizontally-scrolling, zoom-and-pan struggle.

The goal is modest and bounded: the same UI, the same features, laid out so that a narrow viewport gets a readable, tappable, single-column experience instead of a shrunk-down desktop.

## Detailed design

A frontend-only change confined to `bajutsu/templates/serve.css` (and small companion tweaks in `serve.js` for the touch interactions). No server route, job model, or config changes; the UI keeps shelling out to the deterministic CLI exactly as today, so determinism and app-agnosticism are untouched.

**Breakpoint strategy.** Introduce one (possibly two) `max-width` breakpoints — a phone tier (e.g. `≤ 640px`) and optionally a narrow-tablet tier (e.g. `≤ 900px`) — layered *after* the existing desktop rules so the wide layout stays the default and the narrow rules override only what must change. Keep the hand-written, build-step-free CSS approach (no framework), matching BE-0011's stdlib-only, inlined-template constraint.

**Multi-pane → single column.** Below the breakpoint, collapse the side-by-side panes into a vertical stack, and replace the desktop's resize-divider model with a way to switch panes on a phone:

* **Replay**: the five-track grid becomes one column. The sims/config form, the streaming log, and the embedded report stack vertically (or move behind a segmented switcher — *Form · Log · Report* — so each gets the full width when active). The `--rep-left` / `--rep-mid` custom-property widths and the resize gutters are dropped at this tier.
* **Record**: the `.rec-stack` agent-progress / output split stacks or switches the same way.
* **Crawl**: the fixed `22rem` side panel and the graph become stacked sections or tabs (*Graph · Plan · Console*); the pan/zoom graph stays full-width when shown.

**Touch interactions.** Where a drag-resize divider is meaningless without a mouse, hide `.tile-divider` / `.tile-grip` at the narrow tier (the stack/switcher replaces it). Ensure tap targets meet a ~44 px minimum (tabs, the run/stop buttons, history rows). For the crawl graph, confirm pan works from touch events and consider pinch-to-zoom (it currently drag-pans via pointer events); this is the one place that may need `serve.js` work rather than pure CSS.

**Header reflow.** Allow the header row (brand, top tabs, theme switch) to wrap or condense at the narrow tier so it never forces horizontal scroll.

**Already-fluid pieces to preserve.** The screenshot modal (`.shotbox{width:min(960px,94vw);max-height:88vh}`, `.shotmain img{max-width:46vw}`) is already viewport-relative; re-check it at phone widths (the side-by-side image + next-steps list likely needs to stack) but keep its fluid sizing.

**Verification / dogfood.** BE-0058 already drives the `serve` UI with the Web (Playwright) backend as a deterministic Tier-2 regression net. Responsiveness is naturally machine-checkable there: run the existing UI scenarios at a phone viewport (Playwright sets `viewport`), and assert the key controls are present and reachable (e.g. the run button is visible and not clipped, no horizontal overflow). This keeps the determinism-first contract — pass/fail is still a machine-checkable assertion, never an LLM judgment — and extends the dogfood net to cover the small-screen layout, so the responsive rules don't silently regress.

## Alternatives considered

* **A separate mobile template / mobile-specific app.** Rejected: it doubles the surface that must stay in sync with every `serve` feature and conflicts with the single self-contained HTML response BE-0011 deliberately serves. One responsive template is the lower-maintenance choice.
* **Adopt a CSS framework (Tailwind / Bootstrap) for its grid + breakpoints.** Rejected: `serve` is stdlib-only with hand-written, inlined CSS and no asset build step (BE-0011, *Alternatives*). Plain `@media` queries get the responsiveness without taking on a framework or a build pipeline.
* **Leave it desktop-only and rely on the browser's pinch-zoom / pan.** Rejected: that is the status quo, and it is precisely the unusable experience this item exists to fix.
* **Make the panes user-collapsible on desktop too (a general layout overhaul).** Out of scope here: this item is specifically the small-screen reflow. A broader resizable/collapsible-pane redesign for desktop, if wanted, is a separate proposal and should not block the narrow-viewport fix.

## References

* BE-0011 — Local web UI (`bajutsu serve`): the UI this reshapes, and its stdlib-only / inlined-template constraints.
* BE-0013 — Scenario GUI editor: future structured-editing surface that will also need to behave on a small screen.
* BE-0015 / BE-0016 — Public / self-hosting of the web UI: the hosting direction that makes phone access a realistic, desirable entry point.
* BE-0058 — Dogfood the serve Web UI: the Web-backend regression net that can assert the responsive layout deterministically.
* `bajutsu/templates/serve.css`, `bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.js` — the frontend this item changes.

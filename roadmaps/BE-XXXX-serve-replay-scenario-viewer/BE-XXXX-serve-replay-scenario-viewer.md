**English** · [日本語](BE-XXXX-serve-replay-scenario-viewer-ja.md)

# BE-XXXX — View a scenario's contents from the Replay tab (raw YAML + structured steps)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-replay-scenario-viewer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience (record / GUI editor) |
| Related | [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI's **Replay** tab is where a user selects a scenario and runs it. Today the tab
lets you *pick* a scenario and *run* it, but never *look at what it contains*: before pressing Run
you see only the scenario file's top-level `description` and each named scenario's `name` /
`description`. The steps, selectors, assertions — the actual body of the scenario you are about to
run — are invisible. This proposal adds a read-only **scenario viewer** reachable from Replay: a
"View scenario" affordance that opens the selected scenario's contents as **raw YAML** with a toggle
to a **structured steps view**. It is a read-only, non-gating convenience surface — it never edits
the scenario and never enters the `run` / CI verdict path.

It is the scenario-level counterpart of [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md),
which added the same raw-YAML + structured-tree read-only viewer for the bound **config**.

## Motivation

Replay is the tab where a run is *chosen and launched*, yet it is the one place you cannot see what
you are launching. The current info box (`showInfo()` in `bajutsu/templates/serve.panels.js`) renders
only the descriptions returned by the `/api/scenarios` listing — never the steps. So to answer
ordinary pre-run questions — *"which selectors does this scenario tap? does it already assert the
thing I care about? how many steps is it?"* — a user must leave the UI and open the YAML on disk, or
switch to the Author tab and re-pick the scenario there through its own independent picker. The
friction is worst exactly when it matters most:

- **Before running an unfamiliar scenario** (someone else's, or one materialized from a Git-sourced
  or uploaded-zip config, where the on-disk path is an opaque content-addressed cache location) you
  have no in-UI way to confirm what it does before spending a run on it.
- **When triaging** (BE-0147) you can see *why a run failed* from Replay, but not read the scenario
  body that produced the failing step without leaving the view.
- **Determinism grading** (BE-0145) already scores the selected scenario in the Replay Form and
  reports *"step 3 uses a fragile selector"* — but you cannot see step 3 from there.

The capability is essentially already built server-side and simply not wired into Replay. The
endpoint `GET /api/scenario?target=&path=` (`bajutsu/serve/handler.py`, `ops.read_scenario` in
`bajutsu/serve/operations/reads.py`) already returns the scenario's `{"yaml": …}`; it is called today
only by the Record tab (to show just-authored YAML) and the Author editor. Surfacing that same
content where a scenario is *selected to run* closes the "see what you run" gap with almost no new
backend — the mirror of what BE-0187 did for config.

## Detailed design

A read-only viewer wired into the Replay tab. No `run` / CI path is touched (prime directive 1); this
is a Tier‑1 convenience surface, and it is app-agnostic (prime directive 3) — it reads whatever
scenario the active config exposes, with no per-app branching.

- **Reuse the existing read endpoint.** The viewer fetches the selected scenario's body from the
  existing `GET /api/scenario?target=<t>&path=<p>` (returns `{"yaml": …}`). No new server route is
  required for the raw-YAML view. The structured steps view is derived **client-side** by parsing
  that YAML, so the server contract is unchanged — keeping the whole feature front-end-only where
  possible.

- **A "View scenario" affordance in the Replay Form.** Add a control next to the existing
  scenario `<select>` / info box / grade badge (`bajutsu/templates/serve.html.j2`,
  `bajutsu/templates/serve.panels.js`) that opens the viewer for the currently selected scenario. It
  is enabled whenever a scenario is selected (no prior run required) — reading the scenario is
  independent of run artifacts, unlike BE-0262's step-picker which needs a `runId`.

- **A modal / dedicated viewer pane with a raw-YAML ↔ structured toggle** (matching the BE-0187
  pattern):
  - **Raw YAML** — the scenario file's text as returned by the endpoint, in a read-only,
    monospaced, scrollable block. This is the authoritative view (byte-for-byte what runs).
  - **Structured steps** — a per-named-scenario, human-readable list: for each scenario its
    `name` / `description`, then its ordered steps rendered compactly (action + selector/target +
    key args, e.g. `tap { id: nav.replay }`, `assert exists { id: … }`). This is the "skim what it
    does" view; the raw YAML remains one toggle away for the exact source.
  - The viewer is strictly read-only: no edit, no save, no re-bind. Editing remains the Author tab's
    job; a future fast-follow could add a "open in Author" link, but this item deliberately stops at
    viewing (see *Alternatives considered*).

- **Front-end module placement.** The Replay tab's JS lives in `bajutsu/templates/serve.panels.js`
  (Record / Replay / Triage) after the BE-0202 modularization; the markup is in
  `bajutsu/templates/serve.html.j2`. The viewer and its toggle are added there. Follow the serve
  modal / retained-pane conventions already used by BE-0187's config viewer and the theme editor so
  the overlay stacks and dismisses consistently.

- **Test IDs + dogfood coverage.** Add stable `data-testid`s for the View-scenario control, the
  viewer container, the raw/structured toggle, and the rendered content, and add a dogfood E2E
  scenario alongside `demos/serve-ui/scenarios/replay-tools.yaml` that opens the viewer, asserts the
  YAML text is shown, toggles to the structured view, and asserts a known step appears. This is the
  web-backend (Playwright) regression net for the new surface, matching the existing Replay dogfood
  fixtures (BE-0058 / BE-0189).

- **Docs.** Update the serve section of `docs/architecture.md` (and its `docs/ja/` mirror) to note
  that Replay now surfaces the selected scenario's contents read-only.

## Alternatives considered

- **A link from Replay into the Author editor instead of an in-Replay viewer.** Rejected as the
  primary design: Author is an *editor* (mutable, its own scenario state and picker), so it is
  heavier than "let me glance at what I'm about to run" and pulls the user out of the Replay context.
  A read-only viewer keeps the confirm-before-run loop inside Replay. An "open in Author" link
  remains a reasonable optional fast-follow, but is out of scope here.

- **Raw YAML only (no structured view).** The minimal version — just display `{"yaml": …}`. It
  closes the core gap with the least code, but the structured steps view is what makes a long
  scenario skimmable and pairs naturally with the determinism grade's per-step feedback (BE-0145).
  This item adopts the toggle so both are available; if scope must shrink, raw-YAML-first with the
  structured view as a follow-up is the natural split.

- **A new server-side "render scenario" endpoint.** Unnecessary — the existing `GET /api/scenario`
  already returns the YAML, and deriving the structured view client-side avoids adding a second
  representation of scenario structure on the server (which would risk drifting from the runner's own
  parse). Kept front-end-only by design.

- **Folding this into BE-0187.** BE-0187 is already Implemented and is specifically the *config*
  viewer; scenarios are a distinct subject with a distinct entry point (the Replay picker) and a
  distinct structured view (steps, not config keys). This is a sibling item that reuses BE-0187's UI
  pattern, not an extension of it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add the "View scenario" affordance to the Replay Form (markup + wiring in `serve.html.j2` /
      `serve.panels.js`), fetching from the existing `GET /api/scenario`.
- [ ] Implement the viewer overlay/pane with a raw-YAML ↔ structured-steps toggle, following the
      BE-0187 / theme-editor modal conventions.
- [ ] Derive the structured steps view client-side from the fetched YAML.
- [ ] Add `data-testid`s and a dogfood E2E scenario next to `demos/serve-ui/scenarios/replay-tools.yaml`.
- [ ] Update `docs/architecture.md` and its `docs/ja/` mirror.

## References

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) — the config viewer this
  mirrors (raw-YAML + structured-tree, read-only, non-gating).
- [BE-0145](../BE-0145-serve-audit/BE-0145-serve-audit.md) — the determinism grade already shown in
  the Replay Form, which references steps this viewer would let the user see.
- [BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md) — triage from the Replay / History view;
  the "what does the scenario contain" complement to its "why did the run fail".
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) — the serve.js
  modularization that places the Replay tab's JS in `serve.panels.js`.
- Existing endpoint: `GET /api/scenario?target=&path=` (`bajutsu/serve/handler.py`,
  `ops.read_scenario` in `bajutsu/serve/operations/reads.py`).

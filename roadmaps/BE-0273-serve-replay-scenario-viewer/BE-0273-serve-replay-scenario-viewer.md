**English** · [日本語](BE-0273-serve-replay-scenario-viewer-ja.md)

# BE-0273 — View a scenario's contents from the Replay tab (raw YAML + structured steps)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0273](BE-0273-serve-replay-scenario-viewer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0273") |
| Implementing PR | [#1131](https://github.com/bajutsu-e2e/bajutsu/pull/1131) |
| Topic | Authoring experience |
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
is a Tier-1 convenience surface, and it is app-agnostic (prime directive 3) — it reads whatever
scenario the active config exposes, with no per-app branching.

- **Reuse the existing read endpoint, and its existing runner-based step extraction.** The viewer
  fetches the selected scenario's body from the existing `GET /api/scenario?target=<t>&path=<p>`
  (returns `{"yaml": …}`), so the raw-YAML view needs no new server route. For the structured steps
  view, reuse the extraction that endpoint **already has**: `read_scenario` returns a `steps` field
  built by `_step_artifacts` / `_step_action_fields`, which calls `load_scenario_file(...).scenarios`
  (the runner's own parse) and `Step.model_dump(...)` to yield `{action, fields}` per step
  (`bajutsu/serve/operations/reads.py`, from [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).
  Today that `steps` field is only returned when a `run_id` is supplied, because the *run-scoped*
  additions it also computes (`elementsUrl` / `screenshotUrl`) need a completed run's manifest — the
  structural extraction itself does not. The work is therefore to **ungate the structural part** of
  `steps` so the endpoint can return the runner-derived step structure without a prior run (the
  run-scoped URLs stay `run_id`-only). This keeps the structured view faithful to the runner's parse
  — it *is* the runner's parse — instead of introducing a second parser (see *Alternatives
  considered*).

- **A "View scenario" affordance in the Replay Form.** Add a control next to the existing
  scenario `<select>` / info box / grade badge (`bajutsu/templates/serve.html.j2`,
  `bajutsu/templates/serve.panels.js`) that opens the viewer for the currently selected scenario. It
  is enabled whenever a scenario is selected (no prior run required) — reading the scenario is
  independent of run artifacts, unlike BE-0262's step-picker which needs a `runId`.

- **A modal / dedicated viewer pane with a raw-YAML ↔ structured toggle** (matching the BE-0187
  pattern):
  - **Raw YAML** — the scenario file's text as returned by the endpoint, in a read-only,
    monospaced, scrollable block. This is the authoritative view (byte-for-byte what runs).
  - **Structured steps** — a per-named-scenario, human-readable list rendered from the endpoint's
    runner-derived `steps` (above): for each scenario its `name` / `description`, then its ordered
    steps rendered compactly (action + selector/target + key args, e.g. `tap { id: nav.replay }`,
    `assert exists { id: … }`). This is the "skim what it does" view; the raw YAML remains one toggle
    away for the exact source.
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

- **Deriving the structured view client-side (reparsing the YAML in JS).** Considered for keeping
  the feature purely front-end, but rejected: it introduces a *second* parser of scenario structure
  in the browser, which can drift from how the runner actually parses a scenario. The server already
  avoids that — `read_scenario`'s `steps` is built from the runner's own `Step` model
  (`_step_artifacts` / `Step.model_dump`, from BE-0013), so reusing it *is* the runner's parse. The
  only reason it is `run_id`-gated today is the run-scoped `elementsUrl` / `screenshotUrl` it also
  attaches; ungating the structural part (Detailed design) is a smaller and more faithful change than
  a parallel JS parser. A new *separate* "render scenario" endpoint is likewise unnecessary — the
  extraction already lives on `read_scenario`.

- **Folding this into BE-0187.** BE-0187 is already Implemented and is specifically the *config*
  viewer; scenarios are a distinct subject with a distinct entry point (the Replay picker) and a
  distinct structured view (steps, not config keys). This is a sibling item that reuses BE-0187's UI
  pattern, not an extension of it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the "View scenario" affordance to the Replay Form (markup + wiring in `serve.html.j2` /
      `serve.panels.mjs`), fetching from the existing `GET /api/scenario`.
- [x] Implement the viewer overlay/pane with a raw-YAML ↔ structured-steps toggle, following the
      BE-0187 / theme-editor modal conventions.
- [x] Return the structural part of `read_scenario` without a `run_id` — via an opt-in `structure`
      query flag the viewer sets, so the runner-derived per-scenario steps come back with no prior
      run while the Author editor's no-run load keeps its plain `{yaml}`; the run-scoped `steps` (with
      `elementsUrl` / `screenshotUrl`) stay `run_id`-only. Render the structured view from it.
- [x] Add `data-testid`s and a dogfood E2E scenario (`demos/serve-ui/scenarios/replay-scenario-view.yaml`)
      next to `demos/serve-ui/scenarios/replay-tools.yaml`.
- [x] Update `docs/architecture.md` and its `docs/ja/` mirror.

### Log

- Implemented in PR #1131: read-only scenario viewer in the Replay Form — a "View scenario"
  control opens a modal mirroring the config viewer (BE-0187) with a raw-YAML ↔ structured-steps
  toggle. `read_scenario` gained an opt-in `structure` flag that returns the runner's per-scenario
  parse (`_scenario_structure`, reusing `_step_action_fields`) without a run, leaving every existing
  no-run caller's response byte-for-byte unchanged. Covered by unit + HTTP tests and a Playwright
  dogfood scenario.

## References

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) — the config viewer this
  mirrors (raw-YAML + structured-tree, read-only, non-gating).
- [BE-0145](../BE-0145-serve-audit/BE-0145-serve-audit.md) — the determinism grade already shown in
  the Replay Form, which references steps this viewer would let the user see.
- [BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage.md) — triage from the Replay / History view;
  the "what does the scenario contain" complement to its "why did the run fail".
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) — the serve.js
  modularization that places the Replay tab's JS in `serve.panels.js`.
- [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) — the GUI editor that
  introduced the runner-based per-step extraction (`_step_artifacts` / `Step.model_dump`) this reuses.
- Existing endpoint: `GET /api/scenario?target=&path=` (`bajutsu/serve/handler.py`,
  `ops.read_scenario` in `bajutsu/serve/operations/reads.py`), whose `steps` field is built by
  `_step_artifacts` / `_step_action_fields`.

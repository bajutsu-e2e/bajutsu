**English** · [日本語](BE-0273-serve-replay-scenario-viewer-ja.md)

# BE-0273 — View a scenario's contents from the Replay tab as raw YAML and structured steps

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

The serve Web UI's **Replay** tab is where a user selects a scenario and runs it. The tab lets a user
*pick* a scenario and *run* it, but not *look at what it contains*. Before pressing Run, the user sees
the scenario file's top-level `description` and each named scenario's `name` and `description`, and
nothing else. The steps, selectors, and assertions, which form the body of the scenario about to run,
stay invisible.

This proposal adds a read-only **scenario viewer** reachable from Replay. A "View scenario" control
opens the selected scenario's contents as **raw YAML**, with a toggle to a **structured steps view**.
The viewer is a read-only, non-gating convenience surface. It never edits the scenario and never
enters the `run` / CI verdict path.

It is the scenario-level counterpart of [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md),
which added the same raw-YAML + structured-tree read-only viewer for the bound **config**.

## Motivation

Replay is the tab where a run is *chosen and launched*, yet it is the one place where a user cannot see what is being launched. The current info box (`showInfo()` in `bajutsu/templates/serve.panels.js`) renders only the descriptions that the `/api/scenarios` listing returns, and never the steps.

Ordinary pre-run questions go unanswered in the UI: which selectors does this scenario tap, does it already assert the thing I care about, and how many steps does it have? To answer them, a user must leave the UI and open the YAML on disk. The other route is to switch to the Author tab and re-pick the scenario there through its own independent picker. The friction is worst in three situations, exactly where visibility matters most:

- **Before running an unfamiliar scenario.** The scenario may be someone else's, or it may be materialized from a Git-sourced or uploaded-zip config, where the on-disk path is an opaque content-addressed cache location. The UI offers no way to confirm what the scenario does before spending a run on it.
- **When triaging (BE-0147).** From Replay, a user can see *why a run failed*. A user cannot read the scenario body that produced the failing step without leaving the view.
- **When reading a determinism grade (BE-0145).** The Replay Form already scores the selected scenario and reports, for example, "step 3 uses a fragile selector". A user cannot see step 3 from there.

The server side already provides this capability, and Replay does not use it. The endpoint `GET /api/scenario?target=&path=` (`bajutsu/serve/handler.py`, `ops.read_scenario` in `bajutsu/serve/operations/reads.py`) already returns the scenario's `{"yaml": …}`. Today only the Record tab (to show just-authored YAML) and the Author editor call it. Surfacing the same content where a scenario is *selected to run* closes the "see what you run" gap with almost no new backend. It mirrors what BE-0187 did for config.

## Detailed design

A read-only viewer is wired into the Replay tab. No `run` / CI path is touched (prime directive 1). The viewer is a Tier-1 convenience surface. It is also app-agnostic (prime directive 3), because it reads whatever scenario the active config exposes, with no per-app branching.

- **Reuse the existing read endpoint and its runner-based step extraction.** The viewer fetches the selected scenario's body from the existing `GET /api/scenario?target=<t>&path=<p>`, which returns `{"yaml": …}`. The raw-YAML view therefore needs no new server route.

  The structured steps view reuses the extraction that the endpoint **already has**. `read_scenario` returns a `steps` field built by `_step_artifacts` and `_step_action_fields`. These call `load_scenario_file(...).scenarios` (the runner's own parse) and `Step.model_dump(...)` to yield `{action, fields}` per step (`bajutsu/serve/operations/reads.py`, from [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).

  Today the endpoint returns `steps` only when a `run_id` is supplied. The reason is that the *run-scoped* additions it also computes (`elementsUrl` and `screenshotUrl`) need a completed run's manifest. The structural extraction itself does not need one. The work is therefore to **ungate the structural part** of `steps`, so that the endpoint returns the runner-derived step structure without a prior run. The run-scoped URLs stay `run_id`-only. The structured view stays faithful to the runner's parse, because it *is* the runner's parse, and the design avoids a second parser (see *Alternatives considered*).

- **A "View scenario" control in the Replay Form.** A control next to the existing scenario `<select>`, info box, and grade badge (`bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.panels.js`) opens the viewer for the currently selected scenario. The control is enabled whenever a scenario is selected, and needs no prior run. Reading the scenario is independent of run artifacts, unlike the step picker of BE-0262, which needs a `runId`.

- **A modal or dedicated viewer pane with a raw-YAML and structured toggle**, matching the BE-0187 pattern:
  - **Raw YAML.** The scenario file's text, as the endpoint returns it, in a read-only, monospaced, scrollable block. This is the authoritative view, byte-for-byte what runs.
  - **Structured steps.** A human-readable list per named scenario, rendered from the endpoint's runner-derived `steps` (above). Each scenario shows its `name` and `description`, then its ordered steps rendered compactly: action, selector or target, and key arguments, for example `tap { id: nav.replay }` and `assert exists { id: … }`. This is the "skim what it does" view. The raw YAML remains one toggle away for the exact source.
  - **Read-only behavior.** The viewer allows no edit, no save, and no re-bind. Editing remains the Author tab's job. A future fast-follow could add an "open in Author" link, but this item deliberately stops at viewing (see *Alternatives considered*).

- **Front-end module placement.** After the BE-0202 modularization, the Replay tab's JS lives in `bajutsu/templates/serve.panels.js` (Record, Replay, and Triage), and the markup lives in `bajutsu/templates/serve.html.j2`. The viewer and its toggle are added there. The viewer follows the serve modal and retained-pane conventions that BE-0187's config viewer and the theme editor already use, so that the overlay stacks and dismisses consistently.

- **Test IDs and dogfood coverage.** Stable `data-testid`s cover the View-scenario control, the viewer container, the raw/structured toggle, and the rendered content. A dogfood E2E scenario sits alongside `demos/serve-ui/scenarios/replay-tools.yaml`. It opens the viewer, asserts that the YAML text is shown, toggles to the structured view, and asserts that a known step appears. This is the web-backend (Playwright) regression net for the new surface. It matches the existing Replay dogfood fixtures (BE-0058 and BE-0189).

- **Docs.** The serve section of `docs/architecture.md` (and its `docs/ja/` mirror) notes that Replay now surfaces the selected scenario's contents read-only.

## Alternatives considered

- **A link from Replay into the Author editor, instead of an in-Replay viewer.** Rejected as the primary design. Author is an *editor*, with mutable state and its own scenario picker. It is heavier than "let me glance at what I am about to run", and it pulls the user out of the Replay context. A read-only viewer keeps the confirm-before-run loop inside Replay. An "open in Author" link remains a reasonable optional fast-follow, and is out of scope here.
- **Raw YAML only, with no structured view.** This minimal version displays `{"yaml": …}` and nothing else. It closes the core gap with the least code. The structured steps view, however, is what makes a long scenario skimmable, and it pairs naturally with the per-step feedback of the determinism grade (BE-0145). This item adopts the toggle so that both views are available. If scope must shrink, raw YAML first with the structured view as a follow-up is the natural split.
- **Derive the structured view client-side, by reparsing the YAML in JS.** Considered as a way to keep the feature purely front-end. Rejected. It introduces a *second* parser of scenario structure in the browser, which can drift from how the runner actually parses a scenario. The server already avoids that problem: the `steps` of `read_scenario` come from the runner's own `Step` model (`_step_artifacts` and `Step.model_dump`, from BE-0013), so reusing it *is* the runner's parse. Today the endpoint gates `steps` on `run_id` solely because of the run-scoped `elementsUrl` and `screenshotUrl` it also attaches. Ungating the structural part (see *Detailed design*) is a smaller and more faithful change than a parallel JS parser. A new, *separate* "render scenario" endpoint is likewise unnecessary, because the extraction already lives on `read_scenario`.
- **Fold the viewer into BE-0187.** Rejected. BE-0187 is already Implemented and is specifically the *config* viewer. Scenarios are a distinct subject, with a distinct entry point (the Replay picker) and a distinct structured view (steps, not config keys). This item is a sibling that reuses BE-0187's UI pattern, not an extension of BE-0187.

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

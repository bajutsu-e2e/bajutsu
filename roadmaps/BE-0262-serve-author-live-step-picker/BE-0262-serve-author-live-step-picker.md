**English** · [日本語](BE-0262-serve-author-live-step-picker-ja.md)

# BE-0262 — Live step-picking and target-scoped runs in the Author editor

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0262](BE-0262-serve-author-live-step-picker.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0262") |
| Implementing PR | [#1134](https://github.com/bajutsu-e2e/bajutsu/pull/1134) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

The Author tab's Edit mode ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md))
lets an author fix a step's selector by clicking on a screenshot: the click is resolved against the
element tree and the picked selector is written back into the YAML. Today that flow depends entirely
on a *prior run's* stored artifacts. The step list comes from `/api/scenario?…&runId=…`; with no run
selected there are zero steps, and the picker's resolve call (`/api/scenario/resolve`) requires a
`runId` + `stepId` and returns `invalid or missing runId` without one. The Run dropdown that feeds it
lists **every run in the hub, unfiltered by target or scenario** (`/api/runs`).

The result: on a scenario that has never been run, Edit's headline screenshot-picker is unusable, and
even when runs exist the picker offers runs from unrelated scenarios whose step ids cannot match. This
item makes Edit usable without a prior run and scopes the run picker to what can actually match.

## Motivation

Edit mode's promise is "open a scenario, click on the screen, fix the selector". In practice an author
must first go run the scenario elsewhere to produce the artifacts, then come back, then hope the global
run list surfaces the right run. The one entry path that avoided this — flowing a just-captured scenario
straight into Edit (`auOpenSaved`) — starts from Capture, which is itself broken on `[ios]` targets
(addressed by the sibling actuator-selection proposal). So for most scenarios the screenshot picker, the
distinguishing feature of Edit over plain text editing, does not function.

Two fixes make it work:

1. **A live source for step-picking.** Like Capture, Edit can boot a live driver and take a current
   screenshot + element tree, so a selector can be resolved against *now* rather than only against a
   frozen past run. This gives Edit a working picker with no prerequisite run.
2. **Scope the run picker.** When runs *are* used (to review a specific past state), the Run dropdown
   should list only runs for the selected target and scenario, so a chosen run's step ids line up with
   the loaded scenario instead of silently mismatching.

Neither touches the verdict path: resolution is authoring assistance that proposes a selector for the
human to Apply and Save; `run` remains deterministic and AI-free. Determinism and app-agnosticism are
unaffected (the live driver is chosen per target's config, reusing the same selection as Capture).

## Detailed design

1. **Target/scenario-scoped run list.** Filter the Author Run dropdown to runs whose target and scenario
   match the current selection. Prefer filtering server-side (a scoped query param on `/api/runs`, or a
   dedicated Author runs endpoint) over client-side filtering of the global list, so the payload stays
   small and the scoping is authoritative.
2. **Live step-picking without a run.** Add a live-resolve path for Edit that boots a driver for the
   selected target (reusing the shared, cost-ordered actuator selection — see the sibling
   actuator-selection proposal — so it does not re-introduce the same `[ios]` crash), takes a screenshot
   + query, and resolves a screen click against the live tree, mirroring Capture's `mark` resolution.
   The picked selector flows into the same Apply → YAML write the run-backed path already uses.
3. **Make the mode's dependency explicit in the UI.** When no run is selected and no live session is
   open, the Edit screen states how to get a picker (start a live session) rather than showing an inert
   placeholder; when a run is selected, keep the existing per-step screenshot navigation.
4. **Session + safety reuse.** Reuse the capture-session machinery (single active session, per-actor
   ownership, teardown) so a live Edit session cannot leak a driver or collide across users, consistent
   with how Capture manages its session.
5. **Tests.** Scoped-run-list filtering (a run for another scenario is excluded); a live-resolve path
   returns a selector for a click (via the stub driver factory); the no-run/no-session placeholder state.

## Alternatives considered

- **Require a run before Edit's picker (status quo).** Keeps Edit a pure post-run refinement tool, but
  that is precisely the friction this item removes; text-only editing already covers the no-run case, so
  the picker adds nothing unless it can work live.
- **Client-side filtering of the global run list only.** Simpler, but still ships every run to the
  browser and leaves the scoping non-authoritative; a server-scoped list is cleaner and smaller.
- **Auto-run the scenario to generate artifacts on demand.** Heavier (a full deterministic run inside an
  authoring click) and conflates authoring with the verdict path; a live screenshot is enough to resolve
  a selector.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — target/scenario-scoped Author run list.
- [x] Unit 2 — live step-picking path (boot driver, screenshot, resolve click).
- [x] Unit 3 — explicit UI state for no-run/no-session.
- [x] Unit 4 — reuse capture-session lifecycle + ownership.
- [x] Unit 5 — tests for scoping, live resolve, and placeholder state.

### Log

- [#1134](https://github.com/bajutsu-e2e/bajutsu/pull/1134) — implemented all five units in one PR. Unit 1 scopes `/api/runs` by scenario name
  (`runs_payload` filter + `serve.author.mjs` `auLoadRuns`). Unit 2 boots a live driver via Capture's
  session and adds `resolve_capture_pick` (pure resolve, no actuation) reached through
  `POST /api/capture/resolve`; `read_scenario` now derives the step list from the scenario YAML when
  no run is selected so a never-run scenario still has steps to fix. Unit 3 states how to start a live
  session in place of the inert no-run placeholder. Unit 4 reuses the single-session slot, per-actor
  ownership, and adds `close_capture` (`POST /api/capture/close`) for save-less teardown. Unit 5 adds
  the scoping, live-resolve, and placeholder-state tests.
- [#1137](https://github.com/bajutsu-e2e/bajutsu/pull/1137) — follow-up on two non-blocking review notes
  from #1134. `runs_payload` scoped the run list *after* the DB's newest-50 cap, so in hosted mode a run
  of the loaded scenario outside that window was dropped; it now lists unbounded and re-caps after
  filtering when a scenario is given. Also tightened the extracted `_resolve_point` / `_feedback_payload`
  types from `Any` to `CaptureResult`.

## References

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (the Edit mode this extends)
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (the live-session machinery reused)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/templates/serve.author.js` (`auLoadRuns`, `auLoad`, `editResolve`), `bajutsu/serve/operations/reads.py` (`resolve_scenario_pick`, `read_scenario`)

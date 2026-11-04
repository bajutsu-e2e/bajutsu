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

In the Author tab's Edit mode ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)),
an author fixes a step's selector by clicking on a screenshot. The click is resolved against the
element tree, and the picked selector is written back into the YAML.

Today this flow depends entirely on the stored artifacts of a *prior run*:

- The step list comes from `/api/scenario?…&runId=…`. With no run selected, the list has zero steps.
- The picker's resolve call (`/api/scenario/resolve`) requires a `runId` and a `stepId`. Without them
  it returns `invalid or missing runId`.
- The Run dropdown that feeds the picker lists **every run in the hub, unfiltered by target or
  scenario** (`/api/runs`).

As a result, Edit's headline screenshot picker is unusable on a scenario that has never run. Even
when runs exist, the picker offers runs from unrelated scenarios, whose step ids cannot match.

This item makes Edit usable without a prior run. It also scopes the run picker to the runs that can
actually match.

## Motivation

Edit mode promises "open a scenario, click on the screen, fix the selector". In practice, an author
must first run the scenario elsewhere to produce the artifacts. The author then comes back and hopes
that the global run list surfaces the right run.

One entry path avoided this: flowing a just-captured scenario straight into Edit (`auOpenSaved`).
That path starts from Capture, which is itself broken on `[ios]` targets (the sibling
actuator-selection proposal addresses that). For most scenarios, then, the screenshot picker does
not function. The picker is the feature that distinguishes Edit from plain text editing.

Two fixes make the picker work:

1. **A live source for step-picking.** Like Capture, Edit can boot a live driver and take a current
   screenshot and element tree. A selector can then be resolved against *now*, not merely against a
   frozen past run. Edit gets a working picker with no prerequisite run.
2. **A scoped run picker.** Authors still use runs to review a specific past state. In that case the
   Run dropdown should list only the runs for the selected target and scenario. A chosen run's step
   ids then line up with the loaded scenario, instead of silently mismatching.

Neither fix touches the verdict path. Resolution is authoring assistance: it proposes a selector for
the human to Apply and Save. `run` remains deterministic and AI-free. Determinism and
app-agnosticism are unaffected, because the live driver is chosen from the target's config and reuses
the same selection as Capture.

## Detailed design

1. **Target- and scenario-scoped run list.** Filter the Author Run dropdown to the runs whose target
   and scenario match the current selection. Filter server-side, with a scoped query parameter on
   `/api/runs` or a dedicated Author runs endpoint. Server-side filtering is preferable to
   client-side filtering of the global list, because the payload stays small and the scoping is
   authoritative.
2. **Live step-picking without a run.** Add a live-resolve path for Edit. The path boots a driver for
   the selected target, takes a screenshot and a query, and resolves a screen click against the live
   tree. It mirrors Capture's `mark` resolution.
   - The driver comes from the shared, cost-ordered actuator selection (see the sibling
     actuator-selection proposal), so the path does not re-introduce the same `[ios]` crash.
   - The picked selector flows into the same Apply → YAML write that the run-backed path already
     uses.
3. **Make the mode's dependency explicit in the UI.** When no run is selected and no live session is
   open, the Edit screen states how to get a picker (start a live session), in place of an inert
   placeholder. When a run is selected, the existing per-step screenshot navigation stays.
4. **Session and safety reuse.** Reuse the capture-session machinery: a single active session,
   per-actor ownership, and teardown. A live Edit session then cannot leak a driver or collide
   across users, consistent with how Capture manages its session.
5. **Tests.** Cover three cases:
   - Scoped-run-list filtering: a run for another scenario is excluded.
   - The live-resolve path returns a selector for a click, through the stub driver factory.
   - The no-run, no-session placeholder state.

## Alternatives considered

- **Require a run before Edit's picker (status quo).** This keeps Edit a pure post-run refinement
  tool. That is precisely the friction this item removes. Text-only editing already covers the
  no-run case, so the picker adds nothing unless it can work live.
- **Client-side filtering of the global run list alone.** This is simpler, but it still ships every
  run to the browser and leaves the scoping non-authoritative. A server-scoped list is cleaner and
  smaller.
- **Auto-run the scenario to generate artifacts on demand.** This is heavier, because it runs a full
  deterministic run inside an authoring click. It also conflates authoring with the verdict path. A
  live screenshot is enough to resolve a selector.

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

- [#1134](https://github.com/bajutsu-e2e/bajutsu/pull/1134) implemented all five units in one PR.
  - Unit 1 scopes `/api/runs` by scenario name. The change is the `runs_payload` filter plus
    `auLoadRuns` in `serve.author.mjs`.
  - Unit 2 boots a live driver through Capture's session. It adds `resolve_capture_pick` (a pure
    resolve with no actuation), reached through `POST /api/capture/resolve`. When no run is
    selected, `read_scenario` now derives the step list from the scenario YAML, so a never-run
    scenario still has steps to fix.
  - Unit 3 states how to start a live session, in place of the inert no-run placeholder.
  - Unit 4 reuses the single-session slot and per-actor ownership. It adds `close_capture`
    (`POST /api/capture/close`) for save-less teardown.
  - Unit 5 adds the scoping, live-resolve, and placeholder-state tests.
- [#1137](https://github.com/bajutsu-e2e/bajutsu/pull/1137) followed up on two non-blocking review
  notes from #1134.
  - `runs_payload` scoped the run list *after* the database's newest-50 cap. In hosted mode, a run of
    the loaded scenario outside that window was dropped. When a scenario is given, the function now
    lists without a bound and re-caps after filtering.
  - The extracted `_resolve_point` and `_feedback_payload` types are tightened from `Any` to
    `CaptureResult`.

## References

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (the Edit mode this extends)
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (the live-session machinery reused)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/templates/serve.author.js` (`auLoadRuns`, `auLoad`, `editResolve`), `bajutsu/serve/operations/reads.py` (`resolve_scenario_pick`, `read_scenario`)

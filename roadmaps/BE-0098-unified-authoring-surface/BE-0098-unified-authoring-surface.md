**English** · [日本語](BE-0098-unified-authoring-surface-ja.md)

# BE-0098 — Unified authoring surface in serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0098](BE-0098-unified-authoring-surface.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0098") |
| Implementing PR | [#651](https://github.com/bajutsu-e2e/bajutsu/pull/651) |
| Topic | Authoring experience |
| Origin | [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) |
<!-- /BE-METADATA -->

## Introduction

Unify the three authoring surfaces — Capture, Editor, and Enrichment — as switchable modes over one open scenario in the `serve` UI, rather than separate tabs that each manage their own state.

## Motivation

BE-0014 established the demarcation among the authoring surfaces and shipped the enrichment loop. Today, Capture (BE-0012), the Editor (BE-0013), and Enrichment (BE-0014) are separate tabs in `serve`, each with its own target/scenario selection and independent state. The author must pick a tab up front and re-select the scenario if they switch. BE-0014's design anticipated a single view where the author opens one scenario and switches authoring mode over it — demonstrate to add steps, pick to fix a selector, propose to add assertions — without choosing a tool up front.

The concrete cost of separate tabs is workflow friction: a common flow is "capture a flow, fix a selector in the editor, then enrich with assertions," and today that requires three tab switches with three Load actions on the same scenario. A unified surface eliminates that overhead and makes the surfaces composable in practice, not just in the file format.

## Detailed design

### One scenario, three modes

The unified view replaces the separate Capture, Editor, and Enrichment tabs with a single "Author" tab. The tab hosts one open scenario (target + file + name) and exposes three modes via a mode switcher:

- **Capture** mode: the current Capture tab's functionality — start a live session, click on screenshots to record steps. Steps stream into the open scenario.
- **Edit** mode: the current Editor tab's functionality — navigate steps, click on screenshots to resolve selectors, edit YAML directly.
- **Enrich** mode: the current Enrich button's functionality, promoted to a full mode — propose assertions, review them, accept or dismiss.

The mode switcher is a row of buttons (not tabs) within the Author view, visually distinct from the top-level navigation. Switching modes does not reload the scenario or lose unsaved edits.

### Shared state

All three modes share:

- The selected target, scenario file, and scenario name.
- The YAML textarea (the single source of truth for the scenario text).
- The Save button (writes through `ScenarioScope.save()`).
- The steps list (rendered from the YAML; updated by Capture and Enrich).

Mode-specific state (the live Capture driver session, the Editor's current step index, the Enrichment proposal) is held separately but scoped to the same scenario.

### Migration path

The existing Capture and Editor tabs remain functional during the transition. The unified Author tab is added alongside them. Once validated, the separate tabs are removed and their top-level navigation buttons are replaced by the single Author button.

## Alternatives considered

* **Keep the tabs and add cross-tab state sync.** Rejected: synchronizing scenario selection and unsaved edits across three independent tabs adds complexity without removing the fundamental UX problem (the author still picks a tab up front).
* **Merge all three into the Editor tab without a mode switcher.** Rejected: overloading one tab with all controls at once would clutter the interface. A mode switcher keeps each mode's controls focused.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] **Template** — replace the Capture and Editor top-level nav buttons and both `<main>`
  view blocks with one `#view-author` tab hosting a Capture / Edit / Enrich mode switcher over
  shared target / scenario / run / YAML / steps / Save controls (`bajutsu/templates/serve.html.j2`).
- [x] **Styling** — add the `.modeswitch` / `.modetab` mode-switcher styling and unify the
  former `.cap-*` / `.edt-*` classes into shared `.au-*` classes, with a `[hidden]` rule that
  wins over the mode-group display rules (`bajutsu/templates/serve.css`).
- [x] **Behavior** — merge the two front-end modules into one Author module: shared state,
  `setMode()` that preserves the open scenario and unsaved YAML across mode switches, a shared
  screenshot-click routed by mode, and a Capture-finish → Edit hand-off on the saved scenario
  (`bajutsu/templates/serve.js`).
- [x] **Migration** — remove the separate Capture and Editor tabs directly (no dead alongside
  code); the reused backends (`/api/capture/*`, `/api/scenario`, `/api/scenario/resolve`,
  `/api/enrich`) are unchanged.
- [x] **Tests** — replace `test_http_editor_ui.py` with `test_http_author_ui.py` (unified markup,
  removed old tabs, mode switcher, shared + per-mode controls, wired endpoints, the load-bearing
  `[hidden]` visibility rule) and update the `viewswitch` count in `test_http_static.py`.

Log:

- Unified the three authoring surfaces into one Author tab with a mode switcher; ported the
  Capture / Edit / Enrich handlers into a single module reusing the existing endpoints; hardened
  the merged flows to surface (not swallow) the enrich-mode click, the capture→edit hand-off
  miss, and the empty-path Save. `make check` green.

## References

[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the demarcation design that anticipated this unification), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (Capture), [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (Editor).

**English** · [日本語](BE-0013-scenario-gui-editor-ja.md)

# BE-0013 — Scenario GUI editor

<!-- BE-METADATA -->
| Proposal | [BE-0013](BE-0013-scenario-gui-editor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Authoring experience (record / GUI editor) |
<!-- /BE-METADATA -->

## Introduction

Visually edit the scenario YAML and assertion DSL (domain-specific language). Select an element on a screenshot to resolve a selector, with integration to the doctor score.

## Motivation

A scenario is just YAML, and that is the point — humans own it after the AI writes the first draft. But editing it by hand asks the author to hold two things in their head at once: the scenario grammar (steps, waits, the assertion DSL) and the app's stable selectors. The hardest part is the selector: to write `tap: { id: settings.toggle }` correctly you must already know the element's `accessibilityIdentifier`, which means reading a `doctor` dump or the live element tree by hand. The `serve` UI (BE-0011) already exposes a raw YAML textarea; this proposal turns that into structured editing where the screenshot is the source of truth: click the element you mean, and the editor resolves and inserts the right selector, with the `doctor` convention score telling you how stable that choice is. It lowers the cost of the human-owned edit loop without ever moving authorship into the runner.

## Detailed design

The editor lives in the existing `serve` web UI as an enrichment of the scenario view, not a new surface. It has two coupled panes: a structured view of the scenario (steps and the assertion DSL, editable field by field) and the screenshot of the screen the step acts on, captured from the run that produced the report. YAML stays the canonical form — the editor reads and writes the same `*.yaml` through the existing scenario load/save path, so a round-trip through the editor and a hand-edit in `$EDITOR` are interchangeable and reviewable in a PR.

The element picker is the core. Clicking a point on the screenshot maps it, against that screen's captured element tree, to the element whose frame contains it, and the editor offers the most-stable selector for it: `id` first, falling back down the stability ladder (`label` / `traits`) only when no identifier is present, exactly as `resolve_unique` would. If the point resolves to more than one element, the picker shows the ambiguity and asks the author to narrow it (`within` / `index`) rather than silently picking one — the same "ambiguous fails" rule the runner enforces, surfaced at authoring time. The chosen selector is shown with its `doctor` score so the author sees immediately whether they picked a stable rung or a fragile one (a coordinate fallback is flagged).

The editor stays app-agnostic and Tier 1: it operates over config (`apps.<name>` for the app, its scenarios dir, identifier namespaces feeding the `doctor` score) and the artifacts a run already produces. It introduces no LLM call — selection is structural (point-in-frame plus the `doctor` heuristic), so nothing here touches the deterministic `run` / CI gate. Saving validates the YAML through the existing `load_scenario_file` before writing, so the editor can never persist a scenario the runner would reject.

## Alternatives considered

* **Keep the raw YAML textarea only.** That is the BE-0011 baseline. Rejected as the end state: it still requires the author to know selectors by hand and gives no feedback on selector stability — the picker plus `doctor` score is the whole value add.
* **A fully visual, code-free editor that hides the YAML.** Rejected: the YAML being the canonical, hand-editable, PR-reviewable artifact is a core principle. The editor augments the YAML; it must not replace or obscure it.
* **A live, interactive Simulator embedded in the page (pick on the running app).** Rejected for the first cut: it needs a live device per editing session and a streaming pipeline. Picking against the screenshot and element tree the run already captured is offline, cheap, and deterministic; a live picker can come later.

## References

[scenarios.md](../../../docs/scenarios.md), [selectors.md](../../../docs/selectors.md)

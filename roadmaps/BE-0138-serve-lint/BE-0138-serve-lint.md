**English** · [日本語](BE-0138-serve-lint-ja.md)

# BE-0138 — Inline scenario validation in the serve editor

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0138](BE-0138-serve-lint.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0138") |
| Implementing PR | [#673](https://github.com/bajutsu-e2e/bajutsu/pull/673) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

This item gives the `serve` Web UI's YAML scenario editor inline validation. It surfaces lint findings
and JSON-Schema checks *as the author edits*, instead of failing on save alone. The validation is
deterministic and AI-free. It turns the raw textarea into a guided editor. It can ship on today's
textarea, ahead of the richer structured editor
([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) and complementary to it.

## Motivation

The serve editor today is a raw YAML textarea. It validates on save alone. `load_scenario_file` raises
if the YAML is invalid, so a malformed scenario cannot be written. But the author gets no inline
feedback about *what* is wrong or *where*, and none of the lint-level checks that the CLI already runs.

Bajutsu has both halves of the answer. `bajutsu lint` validates a scenario without running it
(`bajutsu/lint.py`). `bajutsu schema` emits the scenario JSON Schema for editor integration
(`lint.scenario_json_schema`). Neither reaches the browser editor. An author who hand-edits in the UI
flies blind until Save, and then gets a single exception, when the CLI could have shown the exact line
and rule. Surfacing lint and schema where the editing happens is the cheapest, highest-frequency
authoring win.

## Detailed design

The feature is Tier-1 and deterministic. The UI shells out to the existing validators and does nothing
more.

- **Validate as you edit (debounced) and on demand.** Lint findings come from `POST /api/lint`
  (`{yaml}`), which runs `bajutsu/lint.py` and returns line-anchored diagnostics. The scenario schema
  (`lint.scenario_json_schema`, the same output that `bajutsu schema` prints) drives JSON-Schema
  validation, and the server provides it to the client. Diagnostics render inline, as gutter markers and
  a problems list anchored to lines.
- **Schema-driven assistance.** The same JSON Schema can power lightweight completion and hover for the
  scenario grammar. Editors outside the UI consume this very schema.
- **Deterministic and AI-free.** Lint and schema are static validation. Nothing runs a device or a
  model. Saving keeps its existing `load_scenario_file` guard. Inline validation makes the failure
  visible early and does not replace the save-time check.
- **App-agnostic.** Validation runs over the scenario grammar, not over any app.

## Alternatives considered

* **Keep validate-on-save alone (status quo).** Rejected. A single save-time exception with no
  location is the poorest possible feedback for an editor, and the line-anchored lint already exists.
* **Wait for the structured GUI editor
  ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) to carry
  validation.** Rejected as the gating choice. BE-0013 (element picker, structured fields, and `doctor`
  score) is a larger build. Inline lint and schema are separable and work on today's textarea, so they
  should not wait behind it. The two items are complementary: this one is the validation layer, and
  BE-0013 is the structured-editing layer.
* **Re-implement validation in client-side JS.** Rejected. The lint rules and the schema are defined
  once in Python. Reusing them server-side keeps a single source of truth and avoids drift.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/lint` endpoint (`{yaml}`) returning line-anchored diagnostics from
      `bajutsu/lint.py`
- [x] Provide the scenario JSON Schema (`lint.scenario_json_schema`) to the client and validate
      against it
- [x] Render diagnostics inline (gutter markers + problems list), debounced as you edit and on demand
- [x] Add schema-driven completion / hover for the scenario grammar

- [#673](https://github.com/bajutsu-e2e/bajutsu/pull/673) ships the whole item:
  - `lint_diagnostics` returns line-anchored findings. Parse errors carry the exact mark, and
    validation errors resolve their `loc` against the YAML node tree on a best-effort basis.
  - The `POST /api/lint` and `GET /api/schema` routes exist on both the stdlib handler and the FastAPI
    control plane.
  - The Author editor gains the gutter, the problems list, completion, and hover.

## References

* `bajutsu/lint.py` (`lint` + `scenario_json_schema`), `bajutsu/cli/commands/lint.py`,
  `bajutsu/cli/commands/schema.py` — the validators this surfaces.
* `bajutsu/serve/` — the scenario load / save path (`load_scenario_file`) this augments with early
  feedback.
* [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)
  — the richer structured editor this validation layer complements and precedes;
  [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [scenarios.md](../../docs/scenarios.md) — the scenario grammar lint and the schema validate
  against; [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — validation is static
  and AI-free, and never a verdict.

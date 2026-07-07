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

Give the `serve` Web UI's YAML scenario editor inline validation: surface lint findings and
JSON-Schema checks *as you edit*, instead of only failing on save. Deterministic and AI-free, it
turns the raw textarea into a guided editor — and it can ship on today's textarea, ahead of and
complementary to the richer structured editor
([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).

## Motivation

The serve editor today is a raw YAML textarea. It validates only on save — `load_scenario_file`
raises if the YAML is invalid, so a malformed scenario cannot be written, but the author gets no
inline feedback about *what* is wrong or *where*, and none of the lint-level checks the CLI already
runs. Bajutsu has both halves: `bajutsu lint` validates a scenario without running it
(`bajutsu/lint.py`), and `bajutsu schema` emits the scenario JSON Schema for editor integration
(`lint.scenario_json_schema`). Neither reaches the browser editor. So an author hand-editing in the
UI flies blind until they hit Save and get a single exception, when the CLI could have shown the
exact line and rule. Surfacing lint + schema where the editing happens is the cheapest,
highest-frequency authoring win.

## Detailed design

Tier-1, deterministic; the UI only shells out to the existing validators.

- **Validate as you edit (debounced) and on demand.** Lint findings come from a `POST /api/lint`
  (`{yaml}`) that runs `bajutsu/lint.py` and returns line-anchored diagnostics; JSON-Schema
  validation is driven by the scenario schema (`lint.scenario_json_schema`, the same output
  `bajutsu schema` prints), provided to the client. Diagnostics render inline — gutter markers and
  a problems list — anchored to lines.
- **Schema-driven assistance.** The same JSON Schema can power lightweight completion / hover for
  the scenario grammar, the very schema editors consume outside the UI.
- **Deterministic and AI-free.** Lint and schema are static validation; nothing runs a device or a
  model. Saving keeps its existing `load_scenario_file` guard — inline validation makes the failure
  visible early; it does not replace the save-time check.
- **App-agnostic.** Validation is over the scenario grammar, not any app.

## Alternatives considered

* **Keep validate-on-save only (status quo).** Rejected: a single save-time exception with no
  location is the poorest possible feedback for an editor; the line-anchored lint already exists.
* **Wait for the structured GUI editor
  ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) to carry
  validation.** Rejected as the gating choice: BE-0013 (element picker + structured fields +
  `doctor` score) is a larger build; inline lint / schema is separable, works on today's textarea,
  and should not wait behind it. The two are complementary — this is the validation layer, BE-0013
  is the structured-editing layer.
* **Re-implement validation in client-side JS.** Rejected: the lint rules and the schema are defined
  once in Python; reusing them server-side keeps a single source of truth and avoids drift.

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

- [#673](https://github.com/bajutsu-e2e/bajutsu/pull/673) — Ships the whole item: `lint_diagnostics` (line-anchored findings; parse errors carry
  the exact mark, validation errors resolve their `loc` against the YAML node tree best-effort), the
  `POST /api/lint` + `GET /api/schema` routes on both the stdlib handler and the FastAPI control
  plane, and the Author-editor gutter / problems list / completion / hover.

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

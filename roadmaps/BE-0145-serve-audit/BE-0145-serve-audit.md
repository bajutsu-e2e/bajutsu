**English** · [日本語](BE-0145-serve-audit-ja.md)

# BE-0145 — Determinism audit in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0145](BE-0145-serve-audit.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0145") |
| Implementing PR | [#700](https://github.com/bajutsu-e2e/bajutsu/pull/700) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

This item surfaces the determinism / flakiness audit
([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)) in the
`serve` Web UI. The UI shows a scenario's static stability score where the scenario is authored and
viewed. The score covers three things:

- selectors graded on the stability ladder
- waits gated on an over-loose `until` (no concrete condition, for example `settled`)
- raw-coordinate gestures

The feature is read-only and AI-free, and it is never a gate.

## Motivation

[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) ships
`bajutsu audit` (`bajutsu/audit.py`). The command is a read-only static score with three parts:

- It grades each selector against the stability ladder. A uniquely resolving `id` beats `label` /
  `traits`, and those beat `index` / raw coordinates.
- It flags `wait` steps gated on an over-loose `until` (no concrete element or condition).
- It flags coordinate gestures that a stable `id` could replace.

The audit makes Bajutsu's "deterministic by contract" claim tangible, but on the CLI alone. The Web
UI's scenario editor gives no determinism feedback at all. An author who just generated or
hand-edited a scenario cannot see whether the selectors are stable or fragile. The author learns
that much later, if ever.

The browser is where scenarios are written and read, so the stability signal is most useful there.
The GUI editor ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) wants the
`doctor` score inline for the same reason.

## Detailed design

The feature is Tier-1 and read-only. The UI shells out to the existing audit.

- **An "Audit" panel / badge** on a scenario, in the editor and the Replay view, posting to
  `POST /api/audit`.
  - The editor sends its live, possibly-unsaved `yaml`, so the score reflects what the author is
    writing.
  - The Replay view sends `{target, path}`, and the server reads the saved file.

  The endpoint runs the static audit. It returns the per-scenario determinism score with the
  per-selector grades and the findings (over-loose `until` waits, raw-coordinate gestures,
  off-ladder selectors).
- **Read-only, deterministic, AI-free.** The audit is static analysis over the scenario. It never
  runs a device, never calls a model, and never gates. Exactly like the CLI, it is informational.
- **The static score first.** This item surfaces the static half of BE-0049, the cheapest figure,
  whose inputs are all on disk. BE-0049's dynamic repeat-and-diff half is a heavier, device-bound
  job. If the dynamic half is surfaced later, it reuses the run job and stream machinery. The static
  score stands alone and ships first.
- **App-agnostic.** The scenario and its target resolve from config (`targets.<name>`). The
  stability ladder belongs to the tool, not to any app.

## Alternatives considered

* **Leave audit CLI-only.** Rejected. The author never sees a determinism score that stays on the
  CLI, so the score cannot shape the scenario as the author writes it. Showing the score inline is
  what changes behavior.
* **Fold this into the GUI editor
  ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).** BE-0013 integrates
  the `doctor` convention score into structured editing. The audit score is a sibling signal that
  could land there. But it is a separable read-only panel that works on the current textarea today,
  so it stands as its own item and complements BE-0013.
* **Surface the dynamic repeat-and-diff audit first.** Rejected for the first cut. The dynamic audit
  needs a device and `K` runs. The static score is instant and on disk, so it is the right first
  slice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/audit` endpoint (inline `yaml`, or `{target, path}`) that runs the static
      audit and returns the per-scenario score
- [x] Add the determinism-audit badge + findings panel in the editor and Replay view showing the
      grade, the id-based selector ratio, and the findings
- [x] Ship the static score first; the dynamic repeat-and-diff half stays a later, device-bound slice

- [#700](https://github.com/bajutsu-e2e/bajutsu/pull/700) — added `POST /api/audit`, which wraps the
  static audit `bajutsu/audit.py`. It takes inline `yaml` for the live editor content and
  `{target, path}` for the Replay view's saved file. The PR also added the grade badge and findings
  panel in the Author editor and the Replay scenario picker, and updated the docs in both languages.

## References

* `bajutsu/audit.py`, `bajutsu/cli/commands/audit.py` — the audit this item surfaces.
* [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — the feature that this item surfaces in the Web UI.
  [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)
  — the sibling inline-feedback surface (the `doctor` score).
  [BE-0050 — E2E coverage map](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)
  — reuses the audit's selector walk, and has its own Web UI surface alongside this one.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this item extends and the small-screen layout it inherits.
* [selectors.md](../../docs/selectors.md) — the stability ladder the audit grades against.
  [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — the audit is read-only and AI-free,
  never a verdict.

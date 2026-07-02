**English** · [日本語](BE-XXXX-serve-audit-ja.md)

# BE-XXXX — Determinism audit in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-audit.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface the determinism / flakiness audit ([BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md))
in the `serve` Web UI: show a scenario's static stability score — selectors graded on the stability
ladder, waits without a timeout, raw-coordinate gestures — where the scenario is authored and
viewed. Read-only, AI-free, never a gate.

## Motivation

[BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
ships `bajutsu audit`: a read-only static score that grades each selector against the stability
ladder (a uniquely resolving `id` beats `label` / `traits`, which beat `index` / raw coordinates),
flags `wait` steps with no timeout, and flags coordinate gestures a stable `id` could replace
(`bajutsu/audit.py`). It makes Bajutsu's "deterministic by contract" claim tangible — but only on
the CLI. The Web UI's scenario editor gives no determinism feedback at all, so an author who just
generated or hand-edited a scenario cannot see whether they picked a stable selector or a fragile
one until much later, if ever. The browser is where scenarios are written and read, so it is where
the stability signal is most useful — the same reason the GUI editor
([BE-0013](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) wants the
`doctor` score inline.

## Detailed design

Tier-1, read-only; the UI only shells out to the existing audit.

- **An "Audit" panel / badge** on a scenario, in the editor and the Replay view, posting to
  `POST /api/audit` (`{target, path}`). It runs the static audit and returns the per-scenario
  determinism score with the per-selector grades and the findings (timeout-less waits,
  raw-coordinate gestures, off-ladder selectors).
- **Read-only, deterministic, AI-free.** The audit is static analysis over the scenario; it never
  runs a device, never calls a model, and never gates — it is informational, exactly like the CLI.
- **The static score first.** This surfaces BE-0049's static half — the cheapest figure, whose
  inputs are fully on disk. BE-0049's dynamic repeat-and-diff half is a heavier, device-bound job;
  if surfaced later it reuses the run job / stream machinery, but the static score stands alone and
  ships first.
- **App-agnostic.** The scenario and its target resolve from config (`targets.<name>`); the
  stability ladder is the tool's, not per-app.

## Alternatives considered

* **Leave audit CLI-only.** Rejected: a determinism score the author never sees cannot shape the
  scenario as it is written; inline is where it changes behavior.
* **Fold this into the GUI editor
  ([BE-0013](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).** BE-0013
  integrates the `doctor` convention score into structured editing; the audit score is a sibling
  signal that could land there, but it is a separable read-only panel that works on the current
  textarea today, so it stands as its own item and complements BE-0013.
* **Surface the dynamic repeat-and-diff audit first.** Rejected for the first cut: it needs a device
  and `K` runs; the static score is instant and on-disk, so it is the right first slice.

## References

* `bajutsu/audit.py`, `bajutsu/cli/commands/audit.py` — the audit this surfaces.
* [BE-0049 — Determinism / flakiness audit](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — the feature this is the Web UI surface of;
  [BE-0013 — Scenario GUI editor](../../implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)
  — the sibling inline-feedback surface (the `doctor` score);
  [BE-0050 — E2E coverage map](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)
  — reuses the audit's selector walk, and has its own Web UI surface alongside this one.
* [BE-0011 — Local web UI (`bajutsu serve`)](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [selectors.md](../../../docs/selectors.md) — the stability ladder the audit grades against;
  [CLAUDE.md](../../../CLAUDE.md), [DESIGN §2](../../../DESIGN.md) — the audit is read-only and
  AI-free, never a verdict.

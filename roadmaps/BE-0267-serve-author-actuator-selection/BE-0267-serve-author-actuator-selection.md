**English** · [日本語](BE-0267-serve-author-actuator-selection-ja.md)

# BE-0267 — Reuse cost-ordered actuator selection in serve capture and enrich

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0267](BE-0267-serve-author-actuator-selection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0267") |
| Implementing PR | [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

The serve Author tab's Capture ([BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md))
and Enrich ([BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)) modes each boot a
live driver from the selected target's `backend`. They do so through a helper — `_default_driver_factory`
in `bajutsu/serve/operations/_common.py` — that resolves the backend with `backends.select_actuator`.
That function walks the platform *alias* order (`PLATFORMS["ios"] = ("xcuitest", "idb")`, BE-0019): it
returns XCUITest first for any `backend: [ios]` target.

[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
introduced a *cost-ordered*, scenario-aware selection (`select_actuator_for_scenario`, `COST_ORDER["ios"]
= ("idb", "xcuitest")`) so the run ladder prefers the cheaper idb actuator and escalates to XCUITest only
when a scenario's constructs demand it. The serve capture/enrich path never adopted it. This item closes
that gap so serve authoring selects an actuator the same way the deterministic run path does.

## Motivation

On a `backend: [ios]` target (the showcase `showcase-swiftui` / `showcase-uikit` products), pressing
**Start capture** crashes. `select_actuator(["ios"])` returns `xcuitest`; `backends.make_driver("xcuitest", …)`
then raises `ValueError: xcuitest backend requires a runner_port (the runner must be started first)`
because serve never starts an XCUITest runner. Enrich takes the identical backend-selection block, so once
an AI credential is configured it fails the same way. In practice this means Capture — the headline Author
feature — does not work on the very targets the showcase ships as its "authoring" examples.

The deeper problem is a **divergence between serve and the run ladder**. BE-0240 established that iOS
authoring should default to idb (no runner needed) and escalate only on demand; the run path honors this,
but serve authoring silently uses the older alias order and picks the one actuator it cannot bring up. A
capture/enrich session drives one live device and does not need scenario-aware escalation, but it *does*
need the cheapest bring-able actuator — which is exactly what cost ordering already computes. Reusing that
selection removes a whole class of "works on the CLI, crashes in the Web UI" surprises and keeps the actuator
choice in one place.

This is a functional bug fix, not a cosmetic cleanup: it restores Capture and Enrich on `[ios]` targets.
It brushes against no prime directive — no LLM enters the `run`/CI verdict, determinism is unchanged, and
the actuator choice stays config-driven per target.

## Detailed design

The work is behavior-preserving for single-actuator targets (`[idb]`, `[adb]`, `[playwright]`) and
corrective for multi-candidate ones (`[ios]`).

1. **Pick the cheapest bring-able actuator in `_default_driver_factory`.** Replace the bare
   `select_actuator([backend])` with a selection that honors `COST_ORDER` — i.e. prefer idb over XCUITest
   for iOS — so a live capture/enrich session on `[ios]` boots idb. A capture session has no scenario, so
   the scenario-aware `select_actuator_for_scenario` is not directly applicable; use the cost-ordered
   candidate list (`_cost_ordered` over `resolve_actuators`) and take the first available, mirroring
   BE-0240's cheapest-first intent without a scenario.
2. **Route capture/enrich through the shared selection.** `start_capture` and `start_enrich` build the
   backend list identically (`target_cfg.backend or config.defaults.backend`, then `[0]`). Fold that into
   the shared factory so both hand the full backend list — not just `[0]` — to the selector, letting the
   cost order operate over every candidate rather than pinning the alias head.
3. **Keep XCUITest reachable when it is the only choice.** A target that pins `backend: [xcuitest]`
   explicitly must still surface the clear "runner not started" error rather than being silently rewritten
   to idb. Selection changes only which candidate wins among several; a single explicit actuator stays a
   hard pin (consistent with BE-0240's "single-actuator request is a hard pin" rule).
4. **Regression tests.** A serve-operations test that a `[ios]` target resolves to idb in capture/enrich
   (via the fake/stub driver factory the existing tests already use), and that a single-actuator target is
   unchanged.

## Alternatives considered

- **Start an XCUITest runner from serve on demand.** Much larger scope (runner lifecycle, ports, teardown)
  and unnecessary for authoring, where idb suffices. Deferred to the run path, which already owns runner
  bring-up.
- **Special-case `ios` → `idb` in serve only.** A local hack that would re-diverge from the core the moment
  COST_ORDER changes. Reusing the shared cost ordering keeps one source of truth.
- **Leave capture/enrich pinned to alias order and only fix the error message.** Turns a crash into a
  readable failure but still leaves Capture non-functional on `[ios]` targets — treats the symptom, not the
  cause. (Surfacing the readable error is nonetheless worthwhile on its own, and is worth a separate serve
  uncaught-exception-handling proposal.)

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — cost-ordered pick in `_default_driver_factory` (new `backends.select_actuator_cost_first`).
- [x] Unit 2 — capture/enrich hand the full backend list to the shared factory.
- [x] Unit 3 — explicit single-actuator pin preserved (XCUITest error intact).
- [x] Unit 4 — regression tests for `[ios]` → idb and single-actuator unchanged.

Log:

- [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) — Reuse cost-ordered selection in serve
  capture/enrich: add `select_actuator_cost_first`, route `_default_driver_factory` and capture/enrich
  through it over the full backend list.

## References

- [BE-0240 — iOS capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) (the cost-ordered selection this reuses)
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/serve/operations/_common.py`, `bajutsu/serve/operations/capture.py`, `bajutsu/serve/operations/enrich.py`, `bajutsu/backends.py`

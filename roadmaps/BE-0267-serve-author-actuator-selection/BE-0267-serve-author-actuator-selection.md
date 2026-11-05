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
live driver from the selected target's `backend`. Both modes go through one helper,
`_default_driver_factory` in `bajutsu/serve/operations/_common.py`. The helper resolves the
[backend](../../docs/glossary.md#driver-backend-actuator-platform) with `backends.select_actuator`.

That function walks the platform *alias* order (`PLATFORMS["ios"] = ("xcuitest", "idb")`, BE-0019).
It returns XCUITest first for any `backend: [ios]` target.

[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
introduced a *cost-ordered*, scenario-aware selection: `select_actuator_for_scenario`, with
`COST_ORDER["ios"] = ("idb", "xcuitest")`. The run ladder then prefers the cheaper idb actuator. It
escalates to XCUITest when a scenario's constructs demand it.

The serve capture and enrich path never adopted this selection. This item closes that gap, so serve
authoring selects an actuator the same way the deterministic run path does.

## Motivation

On a `backend: [ios]` target (the showcase `showcase-swiftui` and `showcase-uikit` products),
pressing **Start capture** crashes:

1. `select_actuator(["ios"])` returns `xcuitest`.
2. `backends.make_driver("xcuitest", …)` raises `ValueError: xcuitest backend requires a
   runner_port (the runner must be started first)`, because serve never starts an XCUITest runner.

Enrich takes the identical backend-selection block. Once an AI credential is configured, Enrich
fails the same way. In practice, Capture, the headline Author feature, does not work on the
targets that the showcase ships as its "authoring" examples.

The deeper problem is a **divergence between serve and the run ladder**. BE-0240 established that iOS
authoring should default to idb, which needs no runner, and escalate on demand alone. The run path
honors this. Serve authoring uses the older alias order and picks the one actuator it
cannot bring up.

A capture or enrich session drives one live device and does not need scenario-aware escalation. It
*does* need the cheapest actuator that can be brought up, which is exactly what cost ordering
already computes. Reusing that selection removes a whole class of "works on the CLI, crashes in the
Web UI" surprises. It also keeps the actuator choice in one place.

This is a functional bug fix, not a cosmetic cleanup: it restores Capture and Enrich on `[ios]`
targets. It brushes against no prime directive. No LLM enters the `run` or CI verdict, determinism
is unchanged, and the actuator choice stays config-driven per target.

## Detailed design

The work is behavior-preserving for single-actuator targets (`[idb]`, `[adb]`, `[playwright]`). It
is corrective for multi-candidate targets (`[ios]`).

1. **Pick the cheapest bring-able actuator in `_default_driver_factory`.** Replace the bare
   `select_actuator([backend])` with a selection that honors `COST_ORDER`: prefer idb over XCUITest
   for iOS. A live capture or enrich session on `[ios]` then boots idb.
   - A capture session has no scenario, so the scenario-aware `select_actuator_for_scenario` does not
     apply directly.
   - Use the cost-ordered candidate list (`_cost_ordered` over `resolve_actuators`) and take the
     first available candidate. This mirrors BE-0240's cheapest-first intent without a scenario.
2. **Route capture and enrich through the shared selection.** `start_capture` and `start_enrich`
   build the backend list identically: `target_cfg.backend or config.defaults.backend`, then `[0]`.
   Fold that into the shared factory. Both modes then hand the full backend list, not just `[0]`, to
   the selector. The cost order operates over every candidate, not over the pinned alias head.
3. **Keep XCUITest reachable when it is the only choice.** A target that pins `backend: [xcuitest]`
   explicitly must still surface the clear "runner not started" error, not be rewritten to
   idb. Selection changes which candidate wins among several, and nothing else. A single explicit actuator stays
   a hard pin, consistent with BE-0240's "single-actuator request is a hard pin" rule.
4. **Regression tests.** Add serve-operations tests for two behaviors:
   - A `[ios]` target resolves to idb in capture and enrich, through the fake or stub driver factory
     that the existing tests already use.
   - A single-actuator target is unchanged.

## Alternatives considered

- **Start an XCUITest runner from serve on demand.** The scope is much larger: runner lifecycle,
  ports, and teardown. Authoring does not need it, because idb suffices. The run path already owns
  runner bring-up, so the work is deferred to it.
- **Special-case `ios` → `idb` in serve only.** This is a local hack that would diverge from the core
  again the moment `COST_ORDER` changes. Reusing the shared cost ordering keeps one source of truth.
- **Leave capture and enrich pinned to alias order and fix only the error message.** This turns a
  crash into a readable failure, yet Capture stays non-functional on `[ios]` targets. It treats the
  symptom, not the cause. Surfacing the readable error is still worthwhile on its own, and merits a
  separate serve uncaught-exception-handling proposal.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — cost-ordered pick in `_default_driver_factory` (new `backends.select_actuator_cost_first`).
- [x] Unit 2 — capture/enrich hand the full backend list to the shared factory.
- [x] Unit 3 — explicit single-actuator pin preserved (XCUITest error intact).
- [x] Unit 4 — regression tests for `[ios]` → idb and single-actuator unchanged.

Log:

- [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) reuses cost-ordered selection in serve
  capture and enrich. It adds `select_actuator_cost_first`, and routes `_default_driver_factory` and
  capture and enrich through it over the full backend list.

## References

- [BE-0240 — iOS capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) (the cost-ordered selection this reuses)
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/serve/operations/_common.py`, `bajutsu/serve/operations/capture.py`, `bajutsu/serve/operations/enrich.py`, `bajutsu/backends.py`

**English** · [日本語](BE-XXXX-record-human-takeover-step-ja.md)

# BE-XXXX — Human takeover step during record (CAPTCHA / biometrics / unresolvable gestures)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-record-human-takeover-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience (record / GUI editor) |
| Related | [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md), [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md), [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) |
<!-- /BE-METADATA -->

## Introduction

Rides on the record human-in-the-loop handoff substrate (`record-human-handoff`). This item covers
the case where the blocker is **not a value but an operation** the AI cannot perform or resolve — a
CAPTCHA, a biometric prompt, or a gesture/target the agent repeatedly fails to resolve. `record`
pauses, the human operates the device directly, and bajutsu observes the resulting state and
resumes. Because some of these operations have **no deterministic run-time equivalent**, this item
also defines — honestly — how the generated artifact is treated on re-run, rather than pretending
every takeover replays.

## Motivation

Not every record blocker is a missing value. Some are operations the AI cannot carry out: solving a
CAPTCHA, clearing a biometric prompt, or performing a gesture whose target the agent's proposals
keep failing to resolve (`could not resolve that target on the live screen; stopping` in
[`bajutsu/record.py`](../../../bajutsu/record.py)). Today `record` just stops there, and the author
abandons the run.

The remedy mirrors the value case in spirit — pause, let the human act, resume — but the hard part
is different and specific to operations: an action like solving a real CAPTCHA has **no deterministic
run-time equivalent** at all. There is no `${vars}` value to bridge to. So the design cannot pretend
the recorded flow replays; it must define the artifact's run behavior truthfully, distinguishing the
operations that *can* be made deterministic (via a test-build bypass) from those that genuinely
cannot, and marking the latter without ever faking a CI pass.

## Detailed design

Built on the substrate; this item defines the takeover behavior and — the delicate part — the
recorded artifact's run-time treatment.

**Takeover trigger.** The substrate's "needs human" outcome is raised when a target cannot be
resolved even after the existing recovery (the alert guard), or when the author explicitly requests
to take over. The tool does not guess which element to act on.

**Human operates the live device.** During takeover the human taps, types, or gestures directly on
the Simulator or device; bajutsu does not drive. This is the same "human performs the action" stance
as [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md)'s capture mode,
scoped here to a single blocking operation inside an AI recording.

**Resume by re-observation.** On "done", the loop re-queries and continues from the observed screen
(the substrate's resume semantics). bajutsu records the **state transition** it observes, not the
opaque manual gesture — so what lands in the scenario is the app reaching the next screen, not a
pixel-level replay of the human's hand.

**The generated artifact and its run-time treatment.** Each takeover is classified:

- **Bypassable in a test build** (biometrics behind a test flag, a CAPTCHA disabled in test
  builds). Emit a placeholder step plus a TODO to wire the bypass — often a device-control /
  device-state primitive
  ([BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md),
  [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md))
  or a build-provided bypass — so `run` becomes deterministic.
- **Not reproducible without a human** (a real CAPTCHA with no test bypass). Emit an **explicit,
  labeled manual step that is never silently on the CI path**: codegen renders it as a labeled
  `// TODO` (per [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)),
  and at `run` time it is an explicit, visible skip or failure with a clear message — never a silent
  pass and never a hang. This is the honest boundary: the tool refuses to fake determinism.

**No human or LLM in the gate.** The manual step never puts a human in the deterministic `run`
verdict. It either resolves to a deterministic bypass or stands as an explicit, visible non-CI
marker — consistent with directives 1 and 2.

**CLI and `serve`.** Both surfaces come from the substrate; this item adds the takeover flow and the
classification/emission on top.

## Alternatives considered

- **Emit a silent no-op / auto-pass for the manual step.** Rejected — a silent pass hides that the
  flow is not really being exercised, which is the spirit of directive 1 (no hidden judgment on the
  gate). An unreproducible operation must be *visible* as such.
- **Fold this into the value-entry item and treat every takeover as a value.** Rejected — an
  operation has no `${vars}` value to bridge; its determinism story (a test-build bypass, or an
  explicit non-CI marker) is fundamentally different from the value case, which is precisely why the
  two are separate items over one shared substrate.
- **Screen-record the human's raw gesture and replay the pixels.** Rejected — non-deterministic,
  fragile across devices and screen sizes, and opaque to the report; it breaks both the
  reviewable-YAML principle and app-agnosticism.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Takeover trigger on unresolved-target / explicit author request, no element guessing.
- [ ] Human-operates-the-device handoff with bajutsu not driving.
- [ ] Resume-by-re-observation recording the observed state transition, not the raw gesture.
- [ ] Artifact classification: bypassable → placeholder + bypass TODO (BE-0035 / BE-0052).
- [ ] Artifact classification: unreproducible → explicit non-CI manual marker (codegen `// TODO`, BE-0026; run-time explicit skip/fail).

## References

Substrate: `record-human-handoff`. Sibling pattern: `record-human-value-prompt` (values).
Related existing items:
[BE-0026 — Shrink unsupported syntax (codegen TODO)](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md),
[BE-0035 — Device-control primitives](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md),
[BE-0052 — Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md),
[BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md).
[`bajutsu/record.py`](../../../bajutsu/record.py).

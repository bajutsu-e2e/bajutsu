**English** · [日本語](BE-0185-record-human-takeover-step-ja.md)

# BE-0185 — Human takeover step during record (CAPTCHA / biometrics / unresolvable gestures)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0185](BE-0185-record-human-takeover-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0185") |
| Implementing PR | [#1210](https://github.com/bajutsu-e2e/bajutsu/pull/1210), [#1212](https://github.com/bajutsu-e2e/bajutsu/pull/1212) |
| Topic | Authoring experience |
| Related | [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md), [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md) |
<!-- /BE-METADATA -->

## Introduction

Rides on the record human-in-the-loop handoff substrate
([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)). This item covers
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
[`bajutsu/record.py`](../../bajutsu/record.py)). Today `record` just stops there, and the author
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

**CLI and `serve`.** Both surfaces come from the substrate, but takeover uses them differently from
the value pattern, and that difference is sharpest in the Web UI. The browser does **not** drive
the device: the `serve` handoff pane is only the coordination surface — it explains what the AI
could not do, and offers a single "I have operated the device — resume" control. The actual
tapping, biometric approval, or CAPTCHA solving happens on the device itself, and bajutsu re-reads
the screen afterwards.

That makes device *reach* a real precondition here, unlike value entry (which completes entirely
in the browser). When `serve` runs on the same machine as the Simulator, the author operates it
directly and the pane just coordinates the pause and resume. But on a **remote or self-hosted**
`serve` (BE-0015 / BE-0016) the device is not in front of the author, so takeover needs a way to
reach it — an interactive, mirrored device view in the browser, or a documented fallback
(re-record where the device is, or wire the test-build bypass without a live takeover). This
proposal flags the remote case as a first-class constraint rather than assuming it away; the
interactive-mirror surface itself is out of scope here and would be its own item.

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

- [x] Takeover trigger on an unresolved target: the loop (never an LLM) offers a takeover when the agent's target will not resolve, without guessing which element to act on. (A proactive author-initiated interrupt while the loop is progressing — the "explicit author request" variant — is a deliberate follow-up; it needs a live control channel into the record loop and is not required by the motivating case.)
- [x] Human-operates-the-device handoff with bajutsu not driving; the `serve` pane coordinates the pause/resume only.
- [x] Remote `serve`: require device reach for takeover, with a documented fallback when the device is not reachable. The hosted server backend (BE-0015) *enforces* the refusal — it is the only certain "device not in the author's reach" signal. A self-hosted `serve` (BE-0016) exposed over a network is also remote, but a serve cannot reliably tell whether a client sits at the device (a loopback bind is not a sound proxy); there the same fallback applies by documented convention, and an enforced signal for that case is a follow-up.
- [x] Resume-by-re-observation recording the observed state transition, not the raw gesture.
- [x] Artifact classification: bypassable → placeholder + bypass TODO (BE-0035 / BE-0052).
- [x] Artifact classification: unreproducible → explicit non-CI manual marker (codegen `// TODO`, BE-0026; run-time explicit skip/fail).

**Log**

- [#1210](https://github.com/bajutsu-e2e/bajutsu/pull/1210) — the artifact core slice: a `manual` step kind that records a human takeover during
  `record` (the `acted` handoff) as a marker of the observed transition, classified bypassable
  (a `bypass` the agent proposes → a wiring TODO) or unreproducible (the default), rendered as a
  labeled `// TODO` by every codegen target, and failing loudly at `run` time (`ManualStepRequired`)
  rather than faking a pass. Leaves the unresolved-target auto-trigger and the remote-`serve` reach
  constraint to follow-up slices.
- [#1212](https://github.com/bajutsu-e2e/bajutsu/pull/1212) — the trigger and reach slices, completing the item. The record loop now *offers* a
  takeover when the agent's proposed target will not resolve on the live screen (the motivating
  "could not resolve that target; stopping" dead end), rather than abandoning the recording — the
  loop raises it, never an LLM, and never guesses which element to act on. On a hosted / remote
  `serve` a device-operation takeover is refused (`respond-human` returns a clear fallback: re-record
  where the device is, or wire the test-build bypass), keeping device reach a first-class
  precondition, while value entry and cancel still work remotely. The refusal keys on the hosted
  signal — the only certain "device out of reach" indicator; the self-hosted-over-a-network case
  (BE-0016) relies on the same documented fallback by convention, with an enforced signal for it left
  to a follow-up. Docs (`recording.md`, both languages) document the trigger and the remote constraint.

## References

Substrate: [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md). Sibling
pattern: `record-human-value-prompt` (values).
Related existing items:
[BE-0026 — Shrink unsupported syntax (codegen TODO)](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md),
[BE-0035 — Device-control primitives](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md),
[BE-0052 — Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md),
[BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md).
[`bajutsu/record.py`](../../bajutsu/record.py).

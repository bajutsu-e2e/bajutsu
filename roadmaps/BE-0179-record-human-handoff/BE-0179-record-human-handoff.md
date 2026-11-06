**English** · [日本語](BE-0179-record-human-handoff-ja.md)

# BE-0179 — Human-in-the-loop handoff during record (pause / hand off / resume)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0179](BE-0179-record-human-handoff.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0179") |
| Implementing PR | [#735](https://github.com/bajutsu-e2e/bajutsu/pull/735) |
| Topic | Authoring experience |
| Related | [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md), [BE-0039](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md), [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md), [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md), [BE-0098](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md), [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal is the shared substrate under two human-in-the-loop patterns for record: human value entry (`record-human-value-prompt`) and human operation takeover (`record-human-takeover-step`).

It defines how the AI-driven `record` loop pauses mid-run, hands control to a human, accepts their input or action, and resumes. The design covers the pause triggers, the request/response contract, the CLI and `serve` surfaces, and the invariant that binds all of them. The invariant: whatever a handoff records must still re-run deterministically, with no human on the run path.

The two concrete patterns ride on this substrate and decide the *shape* of the recorded artifact. This item owns the *mechanism* and the *boundary*.

## Motivation

`record` drives an AI agent one step at a time: observe the screen, propose the next action, execute it ([`bajutsu/record.py`](../../bajutsu/record.py), the `record()` loop). When the agent has nothing valid to do, `record` simply stops. Either the agent proposes no action, or a proposed target does not resolve on the live screen (`could not resolve that target on the live screen; stopping`). There is no path to ask a human for help.

As a result, any flow gated by something the AI cannot supply is un-recordable end to end. Examples:

- A one-time password (OTP) or two-factor (2FA) code, where the agent can see the field but cannot know the value.
- A CAPTCHA or a biometric prompt that the agent cannot solve.
- A gesture or target that the agent repeatedly fails to resolve.

Today the author has to abandon the run at the blocker and hand-write the rest of the scenario. That is exactly the manual work `record` exists to remove.

Rather than bolt a separate escape hatch onto each blocker, the tool needs **one** primitive. The primitive pauses the loop, surfaces a clear request to the human, takes their response, and resumes from the live screen. The two concrete cases, supplying a *value* and performing an *operation*, then differ in what they ask for and what they record. They do not differ in how the loop hands off.

This design stays inside the prime directives because it lives entirely in **Tier 1** (`record` and authoring). The human is in the loop **while authoring**, never in the deterministic `run` / CI gate.

The substrate's core responsibility is to enforce that boundary. Every handoff must resolve to a re-runnable artifact, so a recording made with human help still replays with no human present. A design that let a handoff bake a human-only dependency into the run path would violate directives 1 and 2. Preventing that is this item's job.

## Detailed design

The work is the mechanism and its guarantees. The two child items supply the per-pattern behavior.

**Pause triggers.** A handoff is raised, never a silent stop, in two situations:

- (a) The agent signals that it cannot proceed. This is a new "needs human" turn outcome, distinct from the existing `done` and `no action` outcomes.
- (b) The author explicitly requests to take over at any turn.

The *detection heuristics* for specific blockers (an OTP-looking field, an unresolvable target) belong to the child items. This item defines the outcome and how the loop reacts to it.

**Request / response contract.** The contract is transport-neutral. A handoff *request* carries what is being asked, why, and the current screen (element summary plus screenshot). A *response* carries a value or values supplied, or "I acted on the device; re-observe". The contract is defined once. The CLI and `serve` surfaces implement the same protocol, and the two child patterns reuse it unchanged.

**CLI surface.** A `record` run driven from the terminal blocks on the request and reads the response from an interactive prompt (stdin), on a bounded, cancelable wait. It never hangs unbounded. A non-interactive or CI invocation with no responder fails cleanly (see below) rather than blocking forever.

**`serve` surface.** The Web UI is the more natural home for the handoff. When `serve` drives a `record`, a human is already at the browser watching it unfold. Pausing to ask them for help is then a small, in-context step rather than a switch to another window. Three properties of the `serve` record path shape the design:

- **It crosses a process boundary.** `serve` spawns `bajutsu record` as a background job ([`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py), BE-0127) and streams its progress over server-sent events. The record narration already flows to the browser this way. The handoff request must thus be **serializable**, not an in-process callback. It travels out to the browser as a structured event on that same stream, and the human's response travels back over a response endpoint. The transport-neutral contract above makes this the same protocol the CLI uses in-process.
- **The job enters an explicit "awaiting human" state.** A worker does not block invisibly. The paused record is a visible, resumable job state in the UI. The record view (on the unified authoring surface, BE-0098) renders the request in a modal, so the request cannot be missed below the fold. The modal shows why the loop paused, the current screen, and a response control. The screen is the screenshot the request carries, reusing the existing capture-screenshot channel. The target element travels as a text description; pixel-highlighting it is a follow-up. The human answers in the browser, and the loop resumes.
- **It is bounded and cancelable.** The modal offers cancel, which ends the record cleanly. The awaiting-human state carries the same bounded wait as the CLI. A `serve` worker never hangs indefinitely on a human who has walked away.

The two child patterns render *inside* this one affordance. The value pattern shows a value field. The takeover pattern shows an "I have operated the device — resume" control.

**Resume semantics.** After a response, the loop re-queries the live screen and continues from the observed state, in the same observe → propose → execute cycle. The loop absorbs the human's contribution (a typed value, a manual action) as a new starting point. Nothing downstream needs to special-case it.

**The deterministic-output invariant.** The substrate defines and enforces the contract that a handoff must resolve to a re-runnable artifact. It does not pick the artifact shape (a value placeholder versus an explicit manual step). That is the child items' decision. The substrate guarantees that no handoff can silently record a human-only dependency onto the run path.

**Non-interactive / CI behavior.** When `record` runs with no human responder available (CI, headless), a raised handoff becomes a clean, labeled failure: "this flow needs human handoff; re-record interactively". It never becomes an unbounded hang or an AI guess. The tooling itself stays deterministic under automation.

## Alternatives considered

- **Solve each blocker with its own bespoke code path, no shared substrate.** Rejected. The value case and the operation case would each reinvent the pause/resume plumbing. Both would also duplicate it across the CLI and `serve` surfaces. Factoring the mechanism out once keeps the two child items small and consistent.
- **Let the agent guess or hallucinate a value/action and keep going.** Rejected. This violates determinism-first: a wrong value or a wrong tap silently corrupts the recording. It is the precise failure mode this work removes. A blocker the AI cannot resolve must surface, not be papered over.
- **Put a "pause for human" step on the run path (a manual step every `run` stops at).** Rejected at the substrate level. That places a human in the deterministic `run` / CI gate, against directives 1 and 2. The substrate exists to keep the human at record time and force a deterministic output. The operation-takeover child item discusses the narrow, explicitly-non-CI case where a manual marker is unavoidable. Even there, a CI run never passes silently.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a "needs human" turn outcome to the agent/record loop, distinct from `done` / no-action.
- [x] Define the transport-neutral handoff request/response contract.
- [x] CLI surface: bounded, cancelable interactive prompt on stdin.
- [x] `serve` surface: serialize the request over the record SSE stream and take the response over an endpoint (crossing the spawned-`bajutsu record` process boundary, BE-0127). *Local `serve` only — the distributed server-backend response channel (remote worker, BE-0015) is a follow-up.*
- [x] `serve` surface: an explicit, resumable "awaiting human" job state rendered as a modal in the record view (BE-0098), with the request screenshot and a response control.
- [x] Resume-by-re-observation wiring in the record loop.
- [x] Non-interactive / CI behavior: clean labeled failure, no hang, no guess.

Deferred to follow-ups, outside the first slice of this substrate:

- The author-initiated *explicit takeover* pause trigger (b), which pauses mid-run at the author's request. It is distinct from the agent-raised "needs human" trigger (a) shipped here.
- The distributed server-backend response channel.
- Pixel-highlighting the target element on the request screenshot. The target travels as a text description for now.

The two child patterns (`record-human-value-prompt`, `record-human-takeover-step`) supply the heuristics that *raise* the "needs human" outcome. They also decide the shape of the recorded artifact.

**Log**

- Substrate landed: the `handoff` contract ([`bajutsu/handoff.py`](../../bajutsu/handoff.py)), the `needs_human` turn outcome and pause/resume in the record loop ([`bajutsu/record.py`](../../bajutsu/record.py)), the CLI stdin responders ([`bajutsu/cli/handoff.py`](../../bajutsu/cli/handoff.py)), and the local-`serve` surface (SSE `human-request` event, the `respond-human` endpoint over the record process's stdin, the awaiting-human job state, and the handoff modal in the Web UI). Non-interactive / CI raises a clean, labeled failure.
- Demo enablement: a device-verification flow in the web demo app ([`demos/web/app/index.html`](../../demos/web/app/index.html)) and an `ask_human` outcome the authoring agent ([`bajutsu/claude_agent.py`](../../bajutsu/claude_agent.py)) emits for a value it cannot know (an out-of-band one-time code), so a real headed `record` hands off end to end (`make -C demos/web record-handoff`), with a key-free offline twin for the toolchain. This `ask_human` guidance is a first, minimal slice the value-prompt / takeover child items will formalize (the field-level heuristics and the recorded-artifact shape remain theirs).

## References

Child patterns riding on this substrate: `record-human-value-prompt` (values) and
`record-human-takeover-step` (operations). Related existing items:
[BE-0046 — OTP & email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)
(the deterministic run-time bridge target for values),
[BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md)
(human-demonstrated recording, no AI),
[BE-0039 — Self-healing propose + opt-in](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md)
(the propose → human-approve pattern),
[BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation.md),
[BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md),
[BE-0120 — Tokenize secrets in recorded scenario YAML](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md).
[`bajutsu/record.py`](../../bajutsu/record.py), [`bajutsu/agent_protocols.py`](../../bajutsu/agent_protocols.py).

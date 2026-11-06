**English** · [日本語](BE-0178-record-multi-action-turn-ja.md)

# BE-0178 — Multi-action record turns (batch intra-screen actions)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0178](BE-0178-record-multi-action-turn.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0178") |
| Implementing PR | [#744](https://github.com/bajutsu-e2e/bajutsu/pull/744) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

The AI record loop can propose and execute **several actions from one observation**. The agent does so when it is confident that the current screen determines all of them. The typical case is filling multiple fields of a form before submitting.

Today the loop runs strictly one action per turn. It queries the screen and spends one slow model round-trip to get a single step. Then it executes the step and repeats.

Batching the intra-screen actions of a turn collapses N observe → model → execute cycles into one observe → model → execute-N cycle. That cuts the dominant per-step cost: the model round-trip and its screenshot. The recorded scenario keeps its shape, and no determinism guarantee weakens.

## Motivation

The record loop (`bajutsu/record.py:record`) does the same three things every iteration. It calls `driver.query()` for the current screen. It calls `agent.next_action(...)` for one proposed `Step`, a network round-trip to the model that carries a freshly encoded screenshot. Then it executes that one step.

Consider a flow the author already knows: "type the email, type the password, tap Sign in". A person sees from one glance at the login screen that all three actions are determinable up front. The loop still spends three full model turns on it.

The model turn is the expensive part of each iteration. It is a slow network call that spends tokens and re-encodes a screenshot every time (`_screenshot_bytes` + `ImagePart`). The top-of-loop `driver.query()` and screenshot run once per step on top of it. On a multi-field screen, these calls make up the bulk of record's wall-clock and token cost. All of that cost re-observes a screen that has barely changed between the fields the author fills in.

Batching is safe for free, because actuation already resolves each selector against the live screen at the moment it acts. `IdbDriver.tap(sel)` calls `_center → _settle → _resolve`, which runs a fresh `query()` and `resolve_unique`.

Even inside a batch, a later action that no longer matches the (possibly changed) screen fails loudly with `ElementNotFound` or `AmbiguousSelector`. It never taps whatever now happens to match. The safety net that makes single-step record deterministic also lets a batch abort cleanly instead of acting on a stale plan. This item spends fewer model turns. It does not relax how each individual action is resolved.

[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) is complementary and independent. That item made a *single* `query()` cheap on the XCUITest backend, with fewer XCUITest round-trips per query. This item reduces the *number* of model turns (and top-of-loop observations) a record session needs. BE-0105 shrinks the cost of an observation; this item cuts how many observations happen.

## Detailed design

The recorded scenario stays a flat list of `Step`s, so `run`, `codegen`, and the report are entirely unchanged. Batching affects *how the record loop produces* the steps, never the artifact. The change is confined to the authoring path (`agent.py`, `claude_agent.py`, the Claude Code backend, and `record.py`).

### 1. `Proposal` carries an ordered batch of steps

`bajutsu/agent.py:Proposal` grows from a single `step: Step | None` to an ordered `steps: list[Step]`. The current single-action proposal is the length-1 case. `finish` remains a separate flag with its `expect`.

The steps are the actions the agent judges executable from the *current* observation, without seeing the result of the earlier ones. The agent decides the batch boundary (Decision 1).

A `wait_for` is a legal member, for waiting on an element already visible on the current screen.

### 2. The agent emits multiple actions in one turn

The API agent (`ClaudeAgent.next_action`) already receives the whole response as `MessageResponse.content`, a list of blocks. It currently keeps just `first_tool_use()`. Batching maps **every** tool-use block in the turn to a step, in order, via the existing `proposal_from_call`.

This requires allowing parallel tool use on the forced-tool turn. Today `tool_choice=AnyTool()` forces exactly one call. The request now opts into multiple calls.

The system prompt gains a rule about when the model may batch. The model *may* emit several actions in one turn **only when each is determinable from the current screen without observing the previous action's effect**, such as filling several form fields and then submitting. Otherwise it emits one action, as today.

A `finish` block terminates the batch. Any action blocks before it in the same turn execute first. Then the loop finishes with the assertions of `finish` (Decision 3).

The Claude Code backend (`bajutsu/ai/claude_code.py`) returns a single structured-output object, not parallel tool calls. Its action schema gains an ordered `actions` list (or an equivalent list-shaped output), and `proposal_from_call` runs over that list. Both backends map the same action shape to the same steps. They produce the same `Proposal.steps` regardless of provider.

### 3. The record loop executes a batch, aborting on any screen change

`record()` executes the steps of the proposal in order. After each executed step, it compares the screen's `crawl.fingerprint(...)` with the fingerprint captured before the batch began. The fingerprint is the id-and-state projection that already detects screen transitions. Three outcomes follow:

- **Unchanged**: continue to the next step in the batch.
- **Changed with steps remaining**: the screen moved out from under the plan. The loop **aborts the rest of the batch** and lets the next iteration re-observe and re-plan from the new screen (Decision 2, "仕切り直し"). The last step of a batch may legitimately cause a transition, such as the submit tap. That is *not* an abort, because nothing is left in the batch to invalidate.
- **A step fails to resolve** (`base.SelectorError`, after the existing `_execute_with_recovery` alert clearing): the remaining batch aborts likewise, and the next turn re-observes.

**The recorded scenario receives only the steps that actually executed.** The loop discards the aborted tail of the proposal, which never ran, and never writes it. The authored YAML thus reflects what the loop did to the app, one step at a time, in the order it happened. The result matches today's loop, produced in fewer model turns.

### 4. Timing and settle behavior is unchanged

A batch is intra-screen by construction, because it aborts the moment the screen changes. No cross-screen `wait` needs insertion *within* a batch. The existing settle behaviors stay as they are. The `_settle_step` wait recorded before the `finish` assertions still applies. Each action's own driver-side `_settle`/`_resolve` retry still guards it. No fixed `sleep` is introduced anywhere.

### Determinism, prime-directive compliance, and the gate

This item stays strictly **Tier 1 (record only)**. No model call is added to `run` or CI. The abort-on-change decision is a deterministic fingerprint comparison, not an LLM judgment (prime directive 1).

Per-action resolution is unchanged. Each step still resolves uniquely against the live screen, and an ambiguous selector still fails immediately. Batching never "taps whatever matched first" (prime directive 2).

Nothing here is app-specific. The change reads no new config, and the driver interface is untouched (prime directive 3). The scenario artifact has byte-for-byte the same shape. The whole downstream (`run`, codegen, report) and its tests stay as they are.

### Test strategy (fits the Linux `make check` gate, no Simulator)

- **Loop tests with a scripted fake agent** that returns a multi-step `Proposal`:
  - Assert that all steps execute and are recorded in order when the fake driver's fingerprint stays constant.
  - Assert that the batch aborts, and only the executed prefix is recorded, when the fake driver reports a fingerprint change mid-batch.
  - Assert that a mid-batch `SelectorError` aborts and records only the prefix.
  - Assert that a length-1 proposal behaves exactly as the current loop (regression guard).
- **Backend-mapping tests**:
  - A `MessageResponse` with several tool-use blocks maps to `Proposal.steps` in order via `proposal_from_call`.
  - A `finish` block after action blocks yields `done=True` with the preceding actions still present.
  - The Claude Code list-shaped output maps to the same `steps`.
- **Round-trip**: the emitted `Scenario` still round-trips `dump_scenario_file` → `load_scenario_file` unchanged, so the artifact shape is invariant.

### Scope & non-goals

**In scope:**

- The agent proposing an ordered intra-screen batch in one turn.
- Both backends mapping to the same `Proposal.steps`.
- The record loop executing a batch with deterministic abort-on-screen-change and abort-on-resolve-failure, recording only the executed prefix.
- System-prompt guidance bounding when a batch is appropriate.

**Non-goals:**

- Batching across screen transitions. A batch is intra-screen by construction, and a transition ends it.
- The `crawl` explorer loop (`bajutsu/crawl.py`). Its per-step branching decisions are inherent to exploration and out of scope here.
- Changing the scenario schema or any `run`/replay behavior.
- Reducing the cost of a single `query()`, which is BE-0105's separate concern.

### Decisions

1. **The agent decides the batch boundary.** The model, seeing the current screen, proposes the set of actions it judges determinable up front. The loop does not mechanically infer a batch.
   *Rationale:* which actions can safely be pre-committed is a semantic judgment about the flow. All fields may be visible now, or a field may appear only after the previous tap. The authoring agent can make that judgment. The abort-on-change safety net deterministically catches any over-confidence. A wrong batch degrades to "executed a prefix, re-observed" and never to a bad recording.
2. **Abort and re-observe on any mid-batch screen change ("仕切り直し").** When the screen fingerprint changes with batch steps still pending, the loop discards the remaining steps and re-observes on the next iteration. The scenario records the executed steps alone.
   *Rationale:* a changed screen invalidates the assumption under which the batch was planned. Re-observing is the behavior of the single-step loop. The recording stays faithful to what happened instead of forcing through a stale plan.
3. **`finish` terminates a batch; preceding actions execute first.** A turn may end with `finish` after some actions. The actions run, then the loop finishes with the assertions of `finish`.
   *Rationale:* the agent can act and conclude in one turn without a special "act-then-finish" protocol. The assertions still run against the settled final screen, as today.

## Alternatives considered

- **Mechanically batch by the loop (no agent judgment).** The loop executes a queue of candidate actions until the fingerprint changes, with no model deciding the boundary. Rejected as the primary design.
  The loop has no notion of *intent*: which of the visible fields this flow should fill, in what order, with what values. It would either need the model per action anyway (no saving) or guess, which reintroduces "act on whatever is there." The agent is the right place to decide the batch. The fingerprint check is the safety net, not the planner.
- **A dedicated `batch` tool taking an ordered list of actions.** Instead of collecting parallel tool-use blocks, add one tool whose input is an array of action objects. This is viable, and the Claude Code backend needs it anyway, since that backend returns one object.
  Rejected as the API-agent primary. It duplicates the per-action schema inside an array wrapper, and it diverges from the natural "emit several tool calls" shape. The design keeps it as the backend-parity mechanism where parallel tool use is not available.
- **Keep one action per turn, and merely cache the observation.** Reuse the last `query()` and screenshot for the next turn to save the top-of-loop observe. Rejected.
  It still spends one model round-trip per action, the dominant cost. It also risks the model acting on a stale screen. Batching addresses the round-trips directly and keeps live per-action resolution intact.
- **Record the batch as a single composite step.** Emit one "batch" step into the scenario. Rejected outright.
  It would change the scenario schema and the replay semantics. That breaks the invariant that the authored artifact is a flat, individually-resolved step list, which `run` replays deterministically.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `Proposal` carries an ordered `steps: list[Step]` (single-action = length-1, exposed as a read-only
      `step` property for callers); `finish` unchanged.
- [x] API agent maps every tool-use block in a turn to a step (`_to_proposal` + `_combine`; parallel tool use
      is already the Anthropic default under `tool_choice: any`, so no adapter change was needed); system
      prompt bounds when a batch is appropriate.
- [x] Claude Code backend returns an ordered `actions` list mapping to the same `Proposal.steps`.
- [x] Record loop executes a batch with abort-on-transition and abort-on-resolve-failure, recording only the
      executed prefix. The abort check uses `crawl.screen_identity` (a transition signature that ignores
      per-element fill/enabled/selected state) rather than `crawl.fingerprint`, so a form-fill batch — the
      motivating case — is not mistaken for a transition and actually saves model turns.
- [x] Tests: loop batch/abort/prefix-recording + length-1 regression; backend multi-block mapping; scenario
      round-trip invariance.

**Log**

- [#744](https://github.com/bajutsu-e2e/bajutsu/pull/744): Implemented the batch loop end to end: `Proposal.steps`, per-turn multi-block mapping (`_combine`), the
  Claude Code list-shaped `actions` schema, and the record loop's deterministic abort-on-transition /
  abort-on-resolve-failure with executed-prefix recording. Added `crawl.screen_identity` (state-omitting
  transition signature, factored to share `fingerprint`'s reduction) after finding that the literal
  `crawl.fingerprint` would abort a form-fill batch on the first field it filled. Docs (`docs/recording.md`
  + `docs/ja/`, `DESIGN.md`) updated; `make check` green.
- [#744](https://github.com/bajutsu-e2e/bajutsu/pull/744): Record-UX follow-up on the same PR — before each
  observe the loop now prints the plan step it is about to work toward (`⏭️ next — plan k/M: …`), so the
  model round-trip is no longer a silent gap. The concrete action is still decided from the live screen; a
  `plan_cursor` advances as the agent attributes actions to plan steps.
- [#744](https://github.com/bajutsu-e2e/bajutsu/pull/744): Refined that narration — a multi-action turn is
  announced (`📦 batch — K actions from one observation`), and the `plan_cursor` advances by the number of
  actions the batch actually executed (the model labels a whole batch with one `plan_step`), so the "next"
  hint doesn't name a step the batch already did.

## References

[DESIGN §6.5](../../DESIGN.md); `bajutsu/record.py` (the record loop, `_execute_with_recovery`,
`_settle_step`, `_screenshot_bytes`), `bajutsu/agent.py` (`Observation` / `Proposal` / the `Agent`
protocol), `bajutsu/claude_agent.py` (`next_action`, `proposal_from_call`, `_to_proposal`, `SYSTEM_PROMPT`,
`TOOLS`), `bajutsu/ai/base.py` (`MessageResponse.content` / `first_tool_use`), `bajutsu/ai/claude_code.py`
(the structured-output backend), `bajutsu/crawl.py` (`fingerprint`), `bajutsu/drivers/idb.py`
(`tap`/`_center`/`_settle`/`_resolve` — live per-action resolution), `bajutsu/drivers/base.py`
(`resolve_unique`, `SelectorError`).

**Dependencies / related items:**
[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) (makes a
single query cheap on XCUITest; complementary — this item reduces the number of model turns and
observations), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (the
deterministic capture path; shares the "faithful, individually-resolved step list" stance),
[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the record-mode role demarcation
this loop lives within).

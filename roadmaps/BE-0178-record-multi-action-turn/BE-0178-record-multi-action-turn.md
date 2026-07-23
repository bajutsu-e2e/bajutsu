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

Let the AI record loop propose and execute **several actions from one observation** when the
agent is confident they are all determinable from the current screen (typically filling multiple
fields of a form before submitting). Today the loop is strictly one action per turn: it queries the
screen, spends one (slow) model round-trip to get a single step, executes it, and repeats. Batching
the intra-screen actions of a turn collapses N observe → model → execute cycles into one
observe → model → execute-N, cutting the dominant per-step cost — the model round-trip and its
screenshot — without changing the recorded scenario's shape or weakening any determinism guarantee.

## Motivation

The record loop (`bajutsu/record.py:record`) does the same three things every iteration: `driver.query()`
the current screen, `agent.next_action(...)` for one proposed `Step` (a network round-trip to the model,
carrying a freshly encoded screenshot), then execute that one step. A flow the author already knows —
"type the email, type the password, tap Sign in" — therefore costs three full model turns even though a
person can see from a single glance at the login screen that all three actions are determinable up front.

The model turn is the expensive part of each iteration: it is a slow network call that spends tokens and
re-encodes a screenshot every time (`_screenshot_bytes` + `ImagePart`), and the top-of-loop `driver.query()`
plus screenshot run once per step on top of it. On a multi-field screen this is the bulk of record's
wall-clock and token cost, and all of it is spent re-observing a screen that has not meaningfully changed
between the fields the author is filling in.

The key enabler is that **batching is safe for free**, because actuation already resolves each selector
against the live screen at the moment it acts. `IdbDriver.tap(sel)` calls `_center → _settle → _resolve`,
which runs a fresh `query()` and `resolve_unique` — so even inside a batch, a later action that no longer
matches the (possibly changed) screen fails loudly with `ElementNotFound` / `AmbiguousSelector` rather than
tapping whatever now happens to match. So the safety net that makes single-step record deterministic is
exactly what lets a batch abort cleanly instead of acting on a stale plan. This item is about spending
fewer model turns, not about relaxing how carefully each individual action is resolved.

This is complementary to, and independent of,
[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md): that item
made a *single* `query()` cheap on the XCUITest backend (fewer XCUITest round-trips per query); this item
reduces the *number* of model turns (and top-of-loop observations) a record session needs. One shrinks the
cost of an observation, the other cuts how many observations happen.

## Detailed design

The recorded scenario stays a flat list of `Step`s, so `run`, `codegen`, and the report are entirely
unchanged — batching affects only *how the record loop produces* the steps, never the artifact. The change
is confined to the authoring path (`agent.py` / `claude_agent.py` / the Claude Code backend / `record.py`).

### 1. `Proposal` carries an ordered batch of steps

`bajutsu/agent.py:Proposal` grows from a single `step: Step | None` to an ordered `steps: list[Step]`
(the current single-action proposal is the length-1 case; `finish` remains a separate flag with its
`expect`). The steps are the actions the agent judges executable from the *current* observation without
needing to see the result of the earlier ones — the agent decides the batch boundary (Decision 1). A
`wait_for` is a legal member (waiting for an element already visible on the current screen).

### 2. The agent emits multiple actions in one turn

The API agent (`ClaudeAgent.next_action`) already receives the whole response as
`MessageResponse.content` (a list of blocks); it currently keeps only `first_tool_use()`. Batching maps
**every** tool-use block in the turn to a step via the existing `proposal_from_call`, in order. This
requires allowing parallel tool use on the forced-tool turn (today `tool_choice=AnyTool()` forces exactly
one call); the request opts into multiple calls, and the system prompt is extended to say the model *may*
emit several actions in one turn **only when each is determinable from the current screen without observing
the previous action's effect** (e.g. fill several form fields, then submit) — otherwise one action, as
today. A `finish` block terminates the batch: any action blocks before it in the same turn execute first,
then the loop finishes with `finish`'s assertions (Decision 3).

The Claude Code backend (`bajutsu/ai/claude_code.py`) returns a single structured-output object, not
parallel tool calls, so its action schema gains an ordered `actions` list (or an equivalent list-shaped
output) that `proposal_from_call` is applied over, keeping both backends mapping the same action shape to
the same steps. Both backends therefore produce the same `Proposal.steps` regardless of provider.

### 3. The record loop executes a batch, aborting on any screen change

`record()` executes the proposal's steps in order. After each executed step it compares the screen's
`crawl.fingerprint(...)` (the id-and-state projection already used to detect screen transitions) against
the fingerprint captured before the batch began:

- **Unchanged** → continue to the next step in the batch.
- **Changed with steps remaining** → the screen moved out from under the plan, so **abort the rest of the
  batch** and let the next loop iteration re-observe and re-plan from the new screen (Decision 2, "仕切り直し").
  The batch's last step legitimately causing a transition (e.g. the submit tap) is *not* an abort — there is
  simply nothing left in the batch to invalidate.
- A step that fails to resolve (`base.SelectorError`, after the existing `_execute_with_recovery` alert
  clearing) likewise aborts the remaining batch and re-observes next turn.

Crucially, **only the steps that actually executed are appended to the recorded scenario**; the aborted
(never-executed) tail of the proposal is discarded, never written. So the authored YAML always reflects
exactly what was done to the app, one step at a time, in the order it happened — identical to today's
loop, just produced in fewer model turns.

### 4. Timing / settle behavior is unchanged

Because a batch is intra-screen by construction (it aborts the moment the screen changes), no cross-screen
`wait` needs to be inserted *within* a batch. The existing settle behaviors are untouched: the
`_settle_step` wait recorded before the `finish` assertions still applies, and each action's own driver-side
`_settle`/`_resolve` retry still guards it. No fixed `sleep` is introduced anywhere.

### Determinism, prime-directive compliance, and the gate

This stays strictly **Tier 1 (record only)**: no model call is added to `run` or CI, and the abort-on-change
decision is a deterministic fingerprint comparison, not an LLM judgment (prime directive 1). Per-action
resolution is unchanged — each step still resolves uniquely against the live screen and an ambiguous selector
still fails immediately, so batching never "taps whatever matched first" (prime directive 2). Nothing here is
app-specific; it reads no new config and the driver interface is untouched (prime directive 3). The scenario
artifact is byte-for-byte the same shape, so the whole downstream (`run`/codegen/report) and its tests are
unaffected.

### Test strategy (fits the Linux `make check` gate, no Simulator)

- **Loop tests with a scripted fake agent** that returns a multi-step `Proposal`: assert all steps execute
  and are recorded in order when the fake driver's fingerprint stays constant; assert the batch aborts and
  only the executed prefix is recorded when the fake driver reports a fingerprint change mid-batch; assert a
  mid-batch `SelectorError` aborts and records only the prefix; assert a length-1 proposal behaves exactly
  as the current loop (regression guard).
- **Backend-mapping tests**: a `MessageResponse` with several tool-use blocks maps to `Proposal.steps` in
  order via `proposal_from_call`; a `finish` block after action blocks yields `done=True` with the preceding
  actions still present; the Claude Code list-shaped output maps to the same `steps`.
- **Round-trip**: the emitted `Scenario` still round-trips `dump_scenario_file` → `load_scenario_file`
  unchanged (the artifact shape is invariant).

### Scope & non-goals

**In scope:** the agent proposing an ordered intra-screen batch in one turn; both backends mapping to the
same `Proposal.steps`; the record loop executing a batch with deterministic abort-on-screen-change and
abort-on-resolve-failure, recording only the executed prefix; the system-prompt guidance bounding when a
batch is appropriate.

**Non-goals:** batching across screen transitions (a batch is intra-screen by construction — a transition
ends it); the `crawl` explorer loop (`bajutsu/crawl.py`), whose per-step branching decisions are inherent to
exploration and out of scope here; changing the scenario schema or any `run`/replay behavior; reducing the
cost of a single `query()` (that is BE-0105's separate concern).

### Decisions

1. **The agent decides the batch boundary.** The model, seeing the current screen, proposes the set of
   actions it judges determinable up front; the loop does not mechanically infer a batch. *Rationale:* which
   actions are safely pre-committable is a semantic judgment about the flow (all fields visible now vs. a
   field that only appears after the previous tap), which the authoring agent is positioned to make — and any
   over-confidence is caught deterministically by the abort-on-change safety net, so a wrong batch degrades to
   "executed a prefix, re-observed", never to a bad recording.
2. **Abort and re-observe on any mid-batch screen change ("仕切り直し").** When the screen fingerprint changes
   with batch steps still pending, the remaining steps are discarded and the loop re-observes on the next
   iteration; only executed steps are recorded. *Rationale:* a changed screen invalidates the assumption the
   batch was planned under, and re-observing is exactly the single-step loop's behavior — this keeps the
   recording faithful to what actually happened rather than forcing through a stale plan.
3. **`finish` terminates a batch; preceding actions execute first.** A turn may end with `finish` after some
   actions; the actions run, then the loop finishes with `finish`'s assertions. *Rationale:* it lets the agent
   both act and conclude in one turn without a special "act-then-finish" protocol, and the assertions still
   run against the settled final screen exactly as today.

## Alternatives considered

- **Mechanically batch by the loop (no agent judgment).** Have the loop greedily execute a queue of
  candidate actions until the fingerprint changes, without the model deciding the boundary. Rejected as the
  primary design: the loop has no notion of *intent* (which of the visible fields this flow should fill, in
  what order, with what values), so it would either need the model per action anyway (no saving) or guess —
  reintroducing "act on whatever is there." The agent is the right place to decide the batch; the fingerprint
  check is the safety net, not the planner.
- **A dedicated `batch` tool taking an ordered list of actions.** Instead of collecting parallel tool-use
  blocks, add one tool whose input is an array of action objects. Viable, and effectively what the Claude Code
  backend needs anyway (it returns one object). Rejected as the API-agent primary because it duplicates the
  per-action schema inside an array wrapper and diverges from the natural "emit several tool calls" shape;
  kept as the backend-parity mechanism where parallel tool use is not available.
- **Keep one action per turn, only cache the observation.** Reuse the last `query()`/screenshot for the next
  turn to save the top-of-loop observe. Rejected: it still spends one model round-trip per action (the
  dominant cost) and risks the model acting on a stale screen; batching addresses the round-trips directly and
  keeps live per-action resolution intact.
- **Record the batch as a single composite step.** Emit one "batch" step into the scenario. Rejected
  outright: it would change the scenario schema and the replay semantics, breaking the invariant that the
  authored artifact is a flat, individually-resolved step list that `run` replays deterministically.

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

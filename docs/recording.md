**English** · [日本語](ja/recording.md)

# AI authoring (record / Tier 1)

> Tier 1 = AI live operation. From a natural-language goal, the AI explores and operates the app,
> then writes out a **deterministic scenario**. AI is involved only here (at record time). The
> resulting YAML is AI-independent and is owned by the user from that point forward.
>
> Implementation: `bajutsu/record.py` (the loop) · `bajutsu/agent_protocols.py` + `bajutsu/agent_factory.py`
> (the abstraction + construction) · `bajutsu/claude_agent.py` (the SDK authoring agent) ·
> `bajutsu/alerts.py` (system-alert handling). The breadth-first explorer `bajutsu/crawl.py` shares
> the same agent.

Related: [the two tiers in concepts](concepts.md#2-two-tiers-tier-1--tier-2) · [scenarios](scenarios.md) · [run-loop](run-loop.md)

---

## The Agent abstraction

A thin Protocol that separates the loop from the model (`agent_protocols.py`). Tests use a scripted fake;
production uses the SDK-backed `ClaudeAgent` (below).

```python
@dataclass
class Observation:
    goal: str                       # the natural-language goal
    screen: list[Element]           # the current screen's elements
    history: list[Step]             # the steps recorded so far
    screenshot: bytes | None        # a PNG of the current screen (for vision)

@dataclass
class Proposal:
    steps: list[Step] = field(default_factory=list)  # ordered batch determinable from this screen (usually 1)
    done: bool = False                               # the goal is reached
    expect: list[Assertion] = field(default_factory=list)  # on done, the assertions that verify the goal
    note: str = ""
    needs_human: bool = False       # a third outcome: hand off to a human (BE-0179)
    human_prompt: str = ""          # why, shown to the human on handoff

class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...
```

## The record loop

`record(driver, goal, agent, *, name, max_steps=30, alert_guard=None, ...) -> Scenario`
(`record.py`). Repeats observe → propose → execute up to `max_steps` turns.

```
1. (If alert_guard is set) clear anything covering the app (a system alert, etc.)
2. elements = driver.query()
   - Under alert_guard, if no element has an id, don't show the agent a dead screen (it would
     hallucinate ids); loop again to re-clear
3. Decide whether to attach a screenshot (vision-on-demand, BE-0192), capture it lazily if so,
   build an Observation, and call agent.next_action(). If the agent asks to see the screen
   (need_screenshot) on a text-only turn, re-issue the same observation once with the image attached.
4. If proposal.needs_human (BE-0179):
     - hand off to a human via the `handoff` responder; on a value/acted response, re-observe and
       continue (the human's turn records no step); on cancel, stop. With no responder, raise
       `HumanHandoffUnavailable` — a clean, labeled failure, never a hang or a guess
5. Execute proposal.steps in order (a batch of one or more actions — see below):
     - each via _execute_with_recovery (on failure with alert_guard, clear and retry once)
     - on success, push to steps; then re-observe and compare the screen's identity to the one the
       batch was planned against. If it changed with steps still pending, abort the rest and
       re-observe next turn (only the executed prefix is recorded)
     - a step that does not resolve stops the record if nothing ran this turn, else aborts the batch
6. If proposal.done: insert a settle step (below) if needed, finalize expect, and finish
7. Return Scenario(name, steps, expect)
```

**Multi-action turns (BE-0178).** A turn's proposal is an ordered *batch* of steps — the actions the
agent judges executable from the current screen without seeing the previous action's result (e.g.
fill several form fields, then submit). The batch is intra-screen by construction: after each
executed step the loop compares `crawl.screen_identity(...)` — a transition signature that ignores
per-element state (a field's fill, a control's enabled/selected flags) so the batch's own fills
don't look like a transition — against the identity captured before the batch. The moment it changes
with steps still pending, the rest is abandoned and the loop re-observes ("仕切り直し"). Only the steps
that actually executed are appended, so the recorded YAML is the same flat, individually-resolved
step list as before — batching only changes *how many model turns* produce it (one observe → model →
execute-N instead of N single-action turns), never the artifact's shape. The abort check is a
deterministic comparison, never an LLM judgment.

The output is serialized to YAML via `dump_scenarios`
([scenarios](scenarios.md#round-trip-load--dump)).

### Human-in-the-loop handoff (BE-0179)

Some flows are gated by something the AI cannot supply — a one-time password, a CAPTCHA, a biometric
prompt. When a turn's outcome is "needs human" (`proposal.needs_human`), the loop pauses and hands
control to a human through the transport-neutral `Handoff` contract (`handoff.py`): a request (why it
paused, plus the current screen summary and screenshot) goes out, a response (a supplied value, or "I
operated the device — re-observe", or cancel) comes back, and the loop resumes by re-observing the
live screen. The human is only ever in the loop **while authoring**; the recorded scenario replays
with no human on the deterministic `run` path.

The same contract has two surfaces. From the terminal, `record` reads the response from an
interactive, bounded stdin prompt. Driven from `serve`, the request is serialized onto the record's
server-sent-event stream as a `human-request` event (the paused job enters a visible, resumable
"awaiting human" state), and the browser posts the response to `/api/jobs/<id>/respond-human`, which
`serve` writes to the spawned `record` process's stdin. Either way the wait is bounded and
cancelable, so no surface hangs on a human who walked away. With no responder at all — a
non-interactive or CI invocation — a needs-human turn is a clean, labeled non-zero exit, never a hang
and never an AI guess. This substrate owns the mechanism and the boundary; the heuristics that raise
"needs human" and the shape of what a handoff records are the child items' concern.

### Automatic settle-step insertion

`_settle_step`: the agent sees a "settled screen" between turns, but deterministic replay is fast
and may verify before an async transition (e.g. a sheet) has rendered. So it records a **`wait` for
the first "must-be-present" element in expect**, just before the assertions. This makes the recorded
scenario self-sufficient without adding implicit timing to `run`.

### Video capture on mobile targets

For a mobile (iOS-simulator) target, `record` tags the recorded scenario's first step with
`capture: [video]`, so replaying it records a scenario-wide screen video. A single step's inline
`capture` is what `requested_intervals` uses to start the run-wide interval, so the whole replay is
recorded, not just one action's window. The recording is a `simctl` interval (BE-0028), so this is
specific to the iOS backend — a web target captures video by other means, and the recorded scenario
carries no `capture` for it.

### Leaning the turn payload and bounding its cost (BE-0194)

After the screenshot, the rendered accessibility element tree is the largest per-turn input, so
`_render` (`claude_agent.py`) keeps it lean without dropping anything the agent can act on:

- **Lossless element-line compaction.** Each element line prints only the fields that carry
  information — every addressing field (`id`, `label`, a non-empty `value`, non-empty `traits`) is
  kept, and an empty `value`/`traits` is dropped rather than printed as `value='' traits=[]`. The set
  of elements and the ability to address each is unchanged; it is pure character reduction.
- **A safe cap for pathological screens.** Above a global element-count threshold
  (`_LARGE_SCREEN_ELEMENTS`), the purely non-addressable remainder (elements with no `id`, `label`,
  `value`, or `traits`) is collapsed into one trailing `- (+N further non-addressable elements
  omitted)` line instead of being silently skipped. Every addressable element is still rendered in
  full — the cap never drops one — and the agent is told the screen was truncated, so it can swipe to
  reveal more if it needs to.

Two CLI knobs bound a session's spend up front; both default to today's behavior, so nothing changes
unless you opt in:

- **`--max-steps N`** caps the number of authoring turns (default 30), and therefore the worst-case
  token spend.
- **`--no-screenshot`** records an elements-only session (no image sent) — the cheapest possible
  record, for an app you know is fully instrumented.

### Vision-on-demand (BE-0192)

The screenshot is the single largest per-turn cost, and — unlike the static system prompt and tool
definitions — it is never prompt-cached, so it is paid in full at device resolution on every turn.
Yet the agent **acts only by an element's `id` or `label`** (it never invents ids), so on a screen
whose controls are all addressable the image is confirmatory, not decisive. So `record` sends it
**on demand** rather than every turn: the accessibility elements always travel in the per-turn
message, and the screenshot only when it actually adds information.

The loop decides per turn from two deterministic triggers over the element tree — no model, so this
stays Tier 1:

- **New-screen.** The current screen's `crawl.screen_identity(...)` signature differs from the
  previous turn's (or it is the first turn) — a view the agent has not seen yet gets the image. This
  is the same transition signature the batch-abort check uses; it strips per-element interactive
  state (a field's fill, a control's enabled/selected flags), so typing into a field or toggling a
  control on the same view does not force a re-attach — only a genuine view change does.
- **Degenerate-tree.** The signature took `screen_identity`'s structural path (too few accessibility
  identifiers to address by selector — the no-id, tab-bar case where `tap_point` is the way in). The
  image is attached proactively; this trigger is deliberately generous, so an id-poor screen never
  relies on an escalation round-trip.

When neither fires — a screen already seen with a rich, addressable tree — the turn is **text-only**,
and the screenshot is not even captured (lazy capture skips the `screenshot` subprocess too). Vision
is **deferred, never removed**: every distinct screen is seen with an image at least once, a
degenerate tree always gets one, and any residual need is caught by the agent itself calling
**`need_screenshot`** — the loop re-issues that one turn, unchanged, with the image attached (at most
once per turn). So the agent can always see what it needs to; it simply is not handed an image on
turns the element list already fully determines. `--no-screenshot` still forces every turn text-only
(and disables the escalation, since there is no image to hand back). The recorded scenario artifact is
byte-for-byte unchanged, so `run`, `codegen`, and the report are unaffected.

The end-of-session `AI usage:` line is followed by a per-category breakdown (`plan` / `next_action` /
`alert-guard`) attributed by call site (`usage.py`, BE-0194), so the effect of a token-saving change
is measurable. The categories partition every recorded call, so they sum to the running total. The
breakdown is reporting-only and never touches pass/fail, consistent with the rest of `usage.py`.

## The Claude authoring agent

`record` / `crawl` construct one production `agent.Agent` implementation, `ClaudeAgent`
(`claude_agent.py`, built by `agent_factory.py`): it talks to the model through the vendor-neutral
`AiBackend` seam (BE-0104), so the **provider** is a config detail, not a separate agent. The
resolved `ai.provider` ([configuration](configuration.md#ai-provider-ai-be-0047)) picks:

- **`api-key`** (default) — the **Anthropic API**, keyed by `ANTHROPIC_API_KEY` (or the env var
  named by `ai.keyEnv`). (The legacy name `anthropic` still resolves to this.)
- **`bedrock`** — **Amazon Bedrock**, authenticated by AWS credentials with a provider-prefixed
  model id (`BAJUTSU_BEDROCK_MODEL`).
- **`ant`** — the official **Anthropic CLI** (`ant auth login`, a browser-based OAuth/SSO sign-in),
  so authoring bills a Claude Pro/Max/Console seat instead of an API key, with full vision on every
  AI path (BE-0163).

The model is `claude-opus-4-8`; `anthropic` is lazy-imported (the module loads without a
credential) and the client is injectable for tests. The turn contract is the same whichever
provider is active:

- **Forced tool use**: `tool_choice={"type": "any"}` forces **exactly one** tool call per turn —
  `tap(id)` / `tap_point(x, y)` / `swipe(id, direction)` / `type_text(id, text)` /
  `wait_for(id, timeout)` / `finish(assertions)`. `finish`'s `assertions` (`exists` / `notExists` /
  `valueEquals` / `labelContains`) convert to `Assertion` (`_to_assertion`). `swipe` scrolls a
  visible element to bring an off-screen control into view; `tap_point` (above) reaches a visible
  control the tree omits.
- **Loop guard**: the recent actions are shown back to the agent each turn, and the loop
  deterministically stops if the recording repeats one action three times running or oscillates
  A,B,A,B (`_is_looping`) — turning a stuck spin into a bounded, actionable stop rather than burning
  every remaining turn.
- **`tap_point` — the vision fallback for a control absent from the tree.** When the goal needs a
  control the accessibility tree does not expose — most often an individual tab in a bottom tab bar
  on a no-id app, which `idb` collapses into one opaque group — the agent locates it in the
  screenshot and taps by **normalized coordinates [0,1]** (top-left origin). `run` scales them by the
  app-window frame to a `driver.tap_point` (the same normalized-point convention the alert locator
  uses, below). It is the bottom rung of the stability ladder (unverifiable by selector), so the
  prompt restricts it to controls genuinely missing from the element list — a listed element is
  always addressed by its far more stable `id`/`label`. For a tab-bar tab the prompt aims at the
  **center of the icon+label rectangle** — the i-th of N tabs at `x ≈ (i − 0.5)/N` — not the icon
  alone or the empty strip below the label.
- **Live per-step line**: each turn streams one line — `(plan k/N) 💭 <intent> → <action>` — so the
  watcher sees which planned step is running (the agent tags the action with its `plan_step`), the
  intent, and the concrete action together. Followed on completion by `⏱ record finished in <elapsed>`.
- **prompt cache** (API path): the static system prompt + tool definitions are marked
  `cache_control: ephemeral`; only the per-turn observation (elements + screenshot) changes.
- **Vision + elements together**: appearance / state is read from the screenshot, but the agent
  **acts only by the `id`** from the element list (it never invents ids; the list surfaces only
  elements that have an id).

```python
ClaudeAgent()                      # api: production (ANTHROPIC_API_KEY from the environment)
ClaudeAgent(client=fake_client)    # api: tests
```

## Dismissing system alerts automatically

idb's accessibility query is scoped to the foreground app, so **SpringBoard-level prompts** (e.g.
iOS "Save Password?") are invisible to it; the app's element tree collapses to a single window node
and the run is silently blocked. `alerts.py` handles clearing these.

```python
class AlertLocator(Protocol):
    def locate(self, screenshot_png, instruction) -> AlertDecision: ...

class SystemAlertGuard:
    def dismiss(self, driver) -> bool: ...   # if a prompt is present, coordinate-tap it and return True
```

- `SystemAlertGuard.dismiss`: takes a screenshot, asks the locator "is a prompt present, and where
  to tap," and multiplies the **normalized coordinates [0,1]** by the screen point size (the largest
  element frame = the app window) to tap via `driver.tap_point`. The point size is derived from the
  app-window node spanning the whole screen even when the tree has collapsed.
- `ClaudeAlertLocator`: the production implementation. It passes the PNG to Claude vision and forces the tool
  `resolve_alert`. By default it picks the **least-destructive (dismissive) button** ("Not Now" /
  "Don't Allow" / "Cancel", etc.). Given an `instruction`, it taps that button instead. Coordinates
  are returned in pixels and normalized to [0,1] using the image size obtained from the PNG IHDR.
- The locator is injectable. Tests / offline runs plug in a deterministic one.

### Usage in run / record

- `run`: the guard is **on by default** per scenario — the CLI passes a `SystemAlertGuard(...).dismiss`
  as `on_blocked` for each scenario whose [`dismissAlerts`](scenarios.md#dismissalerts-the-system-alert-guard)
  is enabled. On step failure it clears the prompt and **retries that step exactly once**
  ([run-loop](run-loop.md#run_scenario-running-one-scenario)). A scenario sets `dismissAlerts: false`
  to opt out or `{ instruction: "tap Allow" }` to name a button; `--dismiss-alerts`/`--no-dismiss-alerts`
  overrides every scenario and `--alert-instruction "..."` sets a default instruction.
- `record --dismiss-alerts`: opt-in (authoring has no scenario yet). Clears prompts that interrupt
  authoring so the agent always sees a clean screen. **A dismissal is an environment operation, not a
  recorded step** (replay handles it via each scenario's `dismissAlerts`).

> The guard uses a vision model, so it needs `ANTHROPIC_API_KEY` ([.env in cli](cli.md#environment-variables-env));
> without one it is **best-effort** and simply no-ops, never failing a run. The guard fires only to clear
> a blocking prompt — pass/fail stays machine-only and AI-independent
> ([concepts](concepts.md#1-ai-is-the-author-and-the-investigator-never-the-judge)).

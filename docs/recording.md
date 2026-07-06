**English** · [日本語](ja/recording.md)

# AI authoring (record / Tier 1)

> Tier 1 = AI live operation. From a natural-language goal, the AI explores and operates the app,
> then writes out a **deterministic scenario**. AI is involved only here (at record time). The
> resulting YAML is AI-independent and is owned by the user from that point forward.
>
> Implementation: `bajutsu/record.py` (the loop) · `bajutsu/agent.py` + `bajutsu/agents.py`
> (the abstraction + construction) · `bajutsu/claude_agent.py` (the SDK authoring agent) ·
> `bajutsu/alerts.py` (system-alert handling). The breadth-first explorer `bajutsu/crawl.py` shares
> the same agent.

Related: [the two tiers in concepts](concepts.md#2-two-tiers-tier-1--tier-2) · [scenarios](scenarios.md) · [run-loop](run-loop.md)

---

## The Agent abstraction

A thin Protocol that separates the loop from the model (`agent.py`). Tests use a scripted fake;
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
    step: Step | None = None        # the next move (None = done or stuck)
    done: bool = False              # the goal is reached
    expect: list[Assertion] = []    # on done, the assertions that verify the goal
    note: str = ""

class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...
```

## The record loop

`record(driver, goal, agent, *, name, max_steps=30, alert_guard=None, ...) -> Scenario`
(`record.py`). Repeats observe → propose → execute up to `max_steps`.

```
1. (If alert_guard is set) clear anything covering the app (a system alert, etc.)
2. elements = driver.query()
   - Under alert_guard, if no element has an id, don't show the agent a dead screen (it would
     hallucinate ids); loop again to re-clear
3. Take a screenshot, build an Observation, and call agent.next_action()
4. If proposal.done:
     - insert a settle step (below) if needed, finalize expect, and finish
   If proposal.step:
     - execute via _execute_with_recovery (on failure with alert_guard, clear and retry once)
     - on success, push to steps. If it does not resolve, break
5. Return Scenario(name, steps, expect)
```

The output is serialized to YAML via `dump_scenarios`
([scenarios](scenarios.md#round-trip-load--dump)).

### Automatic settle-step insertion

`_settle_step`: the agent sees a "settled screen" between turns, but deterministic replay is fast
and may verify before an async transition (e.g. a sheet) has rendered. So it records a **`wait` for
the first "must-be-present" element in expect**, just before the assertions. This makes the recorded
scenario self-sufficient without adding implicit timing to `run`.

## The Claude authoring agent

`record` / `crawl` construct one production `agent.Agent` implementation, `ClaudeAgent`
(`claude_agent.py`, built by `agents.py`): it talks to the model through the vendor-neutral
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

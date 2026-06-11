# Generate a scenario from natural language

`record` is Bajutsu's authoring path: an **agent** reads a natural-language *goal* plus the
live screen, proposes one action at a time, and the loop writes the executed steps out as a
deterministic scenario. `run` later replays that scenario with **no AI**
([recording](../../docs/recording.md), [concepts](../../docs/concepts.md)).

This folder demonstrates that loop **offline** — no API key, no Simulator.

## Run

```bash
uv run python demos/record/generate_from_nl.py
# pass your own goal (quote the whole sentence):
uv run python demos/record/generate_from_nl.py "tap Increment, then check the counter shows 1"
# or run a batch from a file (one goal per line; # comments allowed):
uv run python demos/record/generate_from_nl.py --file demos/record/goals.txt
```

A single goal prints the full generated YAML + replay result; `--file` prints a one-line
PASS/FAIL per goal and a tally. [`goals.txt`](goals.txt) holds ready-to-edit example goals
that all record and replay cleanly against the demo app.

[`generate_from_nl.py`](generate_from_nl.py) does three things:

1. **App** — a tiny in-memory `FakeDriver` whose taps advance through onboarding → login →
   home (the `react` callback scripts the screen changes).
2. **Author** — drives the real `record()` loop with a goal and prints the generated scenario
   as plain YAML (the same DSL `run` consumes).
3. **Replay** — runs that generated scenario back through `run_scenario` to prove it passes
   deterministically with no AI.

Change the goal and the generated scenario changes — that is the natural-language → scenario
step in miniature.

## The agent here vs. production

The production agent is [`bajutsu.claude_agent.ClaudeAgent`](../../bajutsu/claude_agent.py):
Claude reads the goal, a screenshot, and the accessibility tree, and proposes each step.
It needs an API key and a live app, and its output naturally varies.

To keep this demo reproducible, the script injects a deterministic stand-in, `KeywordAgent`,
that parses the *same* goal with a few keyword rules and grounds each action in the visible
elements. The record loop and the `Observation → Proposal` protocol it uses are the real ones
([`bajutsu/agent.py`](../../bajutsu/agent.py), [`bajutsu/record.py`](../../bajutsu/record.py)) —
only the "brain" is swapped for a deterministic one.

The stand-in understands a small grammar (clauses split on commas / `then`):

| Phrase | Becomes |
|---|---|
| `tap`/`press`/`click`/`open` *X* (optionally `twice` / `N times`) | one or more `tap` steps on the element matching *X* |
| `log in with email E and password P` | `type E` into the email field, `type P` into the password field, then tap **Log In** |
| `check`/`verify`/`confirm` *X* `shows`/`is` *V* | an `expect` that *X*'s value equals *V* |

A target (*X*) is grounded by matching the hint against each visible element's `id` (then its
label). A goal that names something not on screen raises a clear error — the same failure mode
`record` has when an agent asks for an element that does not exist.

## The real thing

Against the bundled sample app on a Simulator, the open-ended version is just:

```bash
bajutsu record out.yaml --app sample --goal "log in and increment the counter twice"
```

That uses Claude end to end (needs `ANTHROPIC_API_KEY`); see
[cli `record`](../../docs/cli.md) and [recording](../../docs/recording.md).

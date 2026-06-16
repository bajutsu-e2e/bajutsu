# Generate → run → modify a scenario against the sample2 app

**English** · [日本語](README.ja.md)

`record` is Bajutsu's authoring path: an **agent** reads a natural-language *goal* plus the
live screen, proposes one action at a time, and the loop writes the executed steps out as a
deterministic scenario. `run` later replays that scenario with **no AI**
([recording](../../docs/recording.md), [concepts](../../docs/concepts.md)).

This folder demonstrates the full lifecycle against the bundled **`sample2`** app
(`demos/record/app/`): generate a scenario from a goal, run it on a Simulator, modify it,
and observe the deterministic runner respond.

## The guided demo (`demo.sh`)

```bash
make -C demos record                 # or, directly:
./demos/record/demo.sh
```

**Prerequisites** (it checks them and tells you what is missing):

- a booted Simulator (`open -a Simulator`),
- the idb client (`brew install facebook/fb/idb-companion && uv sync --extra idb`),
- the sample2 app built (`make -C demos/record sample2-build`),
- `ANTHROPIC_API_KEY` (env or a gitignored `.env`) — step 1 authors with Claude.

The goal comes from the first non-comment line of [`goals.txt`](goals.txt) (override with
`GOAL="..." ./demo.sh`). It then walks four phases, using [`demo.config.yaml`](demo.config.yaml)
(the `sample2` app on the idb backend, with `appPath` so the app installs automatically):

1. **Author (AI)** — `bajutsu record` runs the real Tier-1 loop: **Claude** reads the goal plus
   the live screen (screenshot + accessibility tree) on the booted app and proposes each step,
   writing the executed steps out as `generated.yaml` (gitignored). For an offline, no-key run
   the keyword stand-in [`generate_from_nl.py`](generate_from_nl.py) authors the same flow.
2. **Execute** — `bajutsu run --scenario generated.yaml --app sample2 --config demo.config.yaml` on the
   booted Simulator. The counter flow passes.
3. **Modify** — edit the expected count to a wrong value → re-run → the run **fails** (the
   assertion catches it) → fix it back → it **passes** again. This is the edit-and-re-run loop
   for maintaining an AI-authored scenario.
4. **Diagnose** — change a selector label (`Log in` → `Log In`) to simulate a selector that
   drifted out from under the test → re-run → the tap **fails** to resolve → `bajutsu triage`
   reads the failed run and **diagnoses** it (category + the likely fix from the captured
   element tree). Triage stays advisory — it explains the failure, never judges pass/fail.
   The selector is then restored and the scenario re-runs **green**.

The generated scenario follows the app's onboarding → login → home → counter flow, including a
`wait for Home` so it survives the login screen transition on a real device.

## Generation on its own (offline, no Simulator)

[`generate_from_nl.py`](generate_from_nl.py) runs standalone — handy for iterating on goals:

```bash
uv run python demos/record/generate_from_nl.py                       # the default goal
uv run python demos/record/generate_from_nl.py "tap Increment, then check the counter shows 1"
uv run python demos/record/generate_from_nl.py --file demos/record/goals.txt   # a batch
uv run python demos/record/generate_from_nl.py "<goal>" --out demos/record/generated.yaml
```

It (1) drives an in-memory `FakeDriver` whose taps advance onboarding → login → home — its
ids match the real sample2 app, (2) authors via the real `record()` loop, and (3) replays the
result to prove it is valid. [`goals.txt`](goals.txt) holds ready-to-edit examples.

## The agent here vs. production

The production agent is [`bajutsu.claude_agent.ClaudeAgent`](../../bajutsu/claude_agent.py):
Claude reads the goal, a screenshot, and the accessibility tree, and proposes each step. It
needs an API key and a live app, and its output naturally varies.

To keep this demo reproducible, the script injects a deterministic stand-in, `KeywordAgent`,
that parses the *same* goal with a few keyword rules and grounds each action in the visible
elements. The record loop and the `Observation → Proposal` protocol it uses are the real ones
([`bajutsu/agent.py`](../../bajutsu/agent.py), [`bajutsu/record.py`](../../bajutsu/record.py)) —
only the "brain" is swapped for a deterministic one. The stand-in understands a small grammar
(clauses split on commas / `then`):

| Phrase | Becomes |
|---|---|
| `tap`/`press`/`click`/`open` *X* (optionally `twice` / `N times` / `thrice`) | one or more `tap` steps on the element matching *X* |
| `log in with email E and password P` | `type E` into the email field, `type P` into the password field, then tap **Log In** |
| `wait for X` (optionally `up to Ns`) | a `wait` for the element matching *X* |
| `check`/`verify`/`confirm` *X* `shows`/`is` *V* | an `expect` that *X*'s value equals *V* |

A target (*X*) is grounded by matching the hint against each visible element's `id` (then its
label). A goal that names something not on screen raises a clear error — the same failure mode
`record` has when an agent asks for an element that does not exist.

## Live Claude authoring

To author against the running sample2 app with real Claude instead of the deterministic
stand-in (needs `ANTHROPIC_API_KEY`):

```bash
uv run bajutsu record --out demos/record/generated.yaml --app sample2 \
  --config demos/record/demo.config.yaml --backend idb \
  --goal "increment the counter twice and check it reads 2"
```

then run it with the same `bajutsu run` command `demo.sh` uses. See
[cli `record`](../../docs/cli.md) and [recording](../../docs/recording.md).

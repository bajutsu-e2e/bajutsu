# `record` accuracy & stability eval (experiment)

A lightweight harness to measure how accurately and how *consistently* `record` (Tier 1 AI
authoring) produces the intended operations for a natural-language goal — the practice ground being
the showcase **`-noax`** apps (no accessibility identifiers), where `record` must fall down the
stability ladder to `label` / `traits` / coordinates (DESIGN §5, SPEC §8). That fallback is exactly
where `record` is hardest to get right, so it's the most informative surface to grade.

This is an **opt-in experiment**, not part of the deterministic gate: the real measurement drives a
live LLM against a Simulator, so it can't be `make check`. It follows prime directive #1 — the LLM
is graded here as an *author*; nothing about `run`'s pass/fail verdict is touched.

## The method (what this folder implements)

1. **Representative goals** — `cases.yaml`: a handful of NL goals spanning the showcase interaction
   classes (list → push nav, form text entry, a button-backed control, a multi-tap + clipboard
   round-trip).
2. **Expected operations** — each case declares the ordered operations a correct recording must
   contain (`tap` / `type` / `swipe` / `assert`), each targeting accepted **text fragments** rather
   than an exact selector, since `-noax` has no stable ids.
3. **Record → grade** — `run_eval.py` runs real `bajutsu record` against a `-noax` target (K times
   per goal) and `grade.py` scores each recording structurally against its expected ops.

## Grading rules (`grade.py`)

Deterministic structural comparison — no LLM judge, so results are reproducible. Each expected op
is matched as an ordered **subsequence** against the recorded steps (incidental settle `wait`s and
extra steps between required ops are skipped), and graded:

| Grade | Meaning |
|---|---|
| `✓ MATCH` | a recorded step/assertion matched the op by its readable text (selector `id`/`label`/`value`, case-insensitive substring) |
| `≈ COORD` | a plausible step was there but addressed only by coordinate/index — **unverifiable** from the YAML, and a signal `record` couldn't produce a stable selector |
| `✗ MISS` | nothing in the recording satisfies the op |

A case **passes** only if every expected op is `MATCH`. A `type` op whose value was rewritten to a
`${secrets.X}` token is accepted (a masked token can't be compared to a literal). Because the text
match includes `id`, the same grader also scores an **`-a11y`** recording (the accessibility A/B) —
the id `horse.favorite` satisfies an expected `favorite`.

## Running it

**On-device (the real measurement)** — needs a Mac, a booted Simulator with the `-noax` app built,
the XCUITest runner, and `ANTHROPIC_API_KEY`:

```bash
make -C demos/showcase runner-build
export ANTHROPIC_API_KEY=...
make -C demos/showcase swiftui-noax-build     # or uikit-noax-build
uv run python demos/showcase/record/eval/run_eval.py --reps 5
```

Useful flags: `--target showcase-uikit-noax`, `--case nav-favorite` (repeatable), `--reps N`
(>1 measures **stability** — the pass rate across runs), `--no-erase` (faster, less clean),
`--keep` (retain the recorded YAML). Grading `-a11y` for the A/B is just
`--target showcase-swiftui`.

**Offline (prove the grader)** — no device, no key, runs anywhere `make check` does:

```bash
uv run python demos/showcase/record/eval/selfcheck.py
```

`selfcheck.py` grades the offline `../generate_from_nl.py` recording (a real record loop driven by a
deterministic keyword stand-in) plus hand-built fixtures pinning the COORD / MISS / subsequence /
secret-token rules. Run it to trust the grading logic before spending device time.

## What structural grading can and can't tell you

- It **can** confirm `record` produced the right *actions in the right order* against the right
  labeled targets, and quantify how often (stability across `--reps`).
- It **can't** verify a coordinate-only step hit the intended element (no text in the YAML to match)
  — those grade `COORD`. A rising coord ratio is itself the finding: `record` is leaning on brittle
  positional addressing. To confirm those semantically you'd need the element tree captured at
  record time, which the scenario YAML doesn't carry (a possible next step if this graduates from an
  experiment to a tracked BE item).

## Files

| File | Role |
|---|---|
| `cases.yaml` | representative goals + expected operations |
| `grade.py` | pure, deterministic structural grader (I/O-free) |
| `cases.py` | loads `cases.yaml` into `Case` objects |
| `run_eval.py` | drives real `record` K times per case and reports accuracy + stability |
| `selfcheck.py` | proves the grader offline (no device / key / LLM) |

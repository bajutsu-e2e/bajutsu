---
name: investigate-scenario-flakiness
model: sonnet
description: Rank a run history's scenarios by cross-run flakiness and, on request, diagnose why each flaky one flips — one read-only report from `bajutsu flakiness` plus an optional `bajutsu triage --flaky` pass.
---

# Investigate scenario flakiness

Bind two existing commands into one procedure and return a single ranked report: which scenarios
actually flip their verdict at a constant fingerprint, and — when the caller asks for it — why.

`bajutsu flakiness` ranks; `bajutsu triage --flaky` explains. Running them separately means reading
the ranking, copying scenario names out of it, and retyping a triage invocation per name, which is
where a survey loses reproducibility: a different session picks a different cut-off and triages a
different set. This skill fixes the cut-off and the ordering so the same history yields the same
report.

It is **read-only**. It ranks, it reports, and it stops there — it never applies a fix, rewrites a
scenario, or re-runs anything. `bajutsu triage`'s `--apply` / `--write` / `--rerun` are out of
bounds. Acting on the report is the caller's decision, whether that caller is a person or
[`investigate-ci-failure`](../../../.apm/skills/investigate-ci-failure/SKILL.md).

Nothing here reaches a pass/fail verdict. The classification is read from verdicts already recorded
by the deterministic runner (`classify_stability` in
[`bajutsu/analysis/audit.py`](../../../bajutsu/analysis/audit.py)); the optional triage pass is an
advisory diagnosis, never a judgment. Prime directive 1 holds.

## Inputs

| Input | Meaning |
|---|---|
| `history` | Directory of past runs, each holding a `manifest.json`. Omit to read the `serve` database instead |
| `org` | Org whose runs to mine when `history` is omitted. Defaults to `default` |
| `limit` | Cap on how many flaky scenarios the optional triage pass covers. Defaults to 5 |
| `use_ai` | Run step 3's triage pass. **Defaults to false** — see the cost note below |

This skill does **not** assemble the history. It reads a directory (or database) that already
exists. A caller that must first collect runs — for example
[`investigate-ci-failure`](../../../.apm/skills/investigate-ci-failure/SKILL.md), which downloads them from CI
artifacts — does that assembly itself and hands over the finished directory.

### Why `use_ai` defaults to false

`bajutsu triage --flaky` has **no rule-based agent**: cross-run diagnosis reasons over the delta
between passing and failing runs, so `--ai` is mandatory and the command exits 2 without it
(`_flaky_triage` in [`bajutsu/cli/commands/triage.py`](../../../bajutsu/cli/commands/triage.py)).
Step 3 therefore always spends `ANTHROPIC_API_KEY` credit. A caller opts into that cost explicitly;
it is never incurred by default. With `use_ai` false the skill still returns the full ranking, which
is what an unattended caller normally needs.

## Steps

### 1. Rank the history

```bash
bajutsu flakiness --history <history> --json
```

With no `history`, read the database instead: `bajutsu flakiness --org <org> --json`.

The command exits 2 on a missing runs directory or an unconfigured database. That is a real failure
of the inputs, not an empty result — report it and stop rather than continuing with nothing.

Parse the JSON. Its shape is a `FlakinessReport`
([`bajutsu/serve/flakiness.py`](../../../bajutsu/serve/flakiness.py)): a `scenarios` array plus a
`skipped` count of runs that carried no fingerprint or no recorded verdict. Each scenario entry
carries `name`, `scenario_hash`, `device_os`, `runs`, `passed`, `failed`, `flip_rate`,
`classification`, `representative_pass_run_id`, and `representative_fail_run_id`.

### 2. Keep only the flaky ones

Filter `scenarios` to `classification == "flaky"` — the verdict flips while the content fingerprint
stays constant. Drop the other two classifications, and drop them for a reason worth stating in the
report rather than silently:

- `deterministic` — every observed run agreed. A consistent failure is reproducible, not flaky, and
  triaging it would return the obvious.
- `unproven` — fewer than two runs at this fingerprint. One run proves nothing either way.

The array already arrives flaky-first, then by descending `flip_rate`. Do not re-sort it. Take the
first `limit` entries as the triage set.

### 3. Diagnose each flaky scenario (only when `use_ai` is true)

**Skip this entire step when `use_ai` is false**, which is the default. Go to step 4.

When the caller passed `use_ai`, run one triage per scenario in the set:

```bash
bajutsu triage --flaky --ai --scenario <name> --history <history>
```

Three details about that invocation:

- **Pass no `run_dir`.** `--flaky` ignores the positional argument entirely and reads the whole
  history itself. Passing one is misleading, not harmful.
- **`--history` is required here too**, even though step 1 already read it. The two commands take
  the directory independently.
- **Never add `--apply`, `--write`, or `--rerun`.** They are what makes this skill read-only.

`--scenario` matches by substring, and triage resolves the exact name from the first run that
matches, so a name shared as a prefix by two scenarios never mixes them. Use the `name` exactly as
step 1 reported it.

A scenario the ranking called flaky always has runs on both sides, so the "nothing to contrast" exit
should not occur here. If it does, the history changed under the two commands — report that rather
than treating it as a clean result.

### 4. Handle an empty set

When step 2 leaves no scenario, return a report saying so, together with how many `deterministic`
and `unproven` scenarios were seen and the `skipped` count from step 1.

State this as a finding, not as silence. "No scenario is flaky in this history" and "flakiness was
never assessed" are different answers, and a caller acting on the report needs to tell them apart.

### 5. Report

Return one Markdown report. Per scenario, carry:

- the scenario `name` and its `classification`
- `flip_rate`, and the `passed` / `failed` / `runs` counts it derives from
- `representative_pass_run_id` and `representative_fail_run_id`, so a reader can open both sides'
  evidence
- `device_os` when the entry carries one — a scenario ranked per OS is not the same finding as one
  ranked across a fleet
- the triage diagnosis, when step 3 ran

Close with what was excluded and why: the `deterministic` / `unproven` counts, step 1's `skipped`
count, and how many flaky scenarios fell outside `limit`.

**When step 3 did not run, say so explicitly and name the reason** (`use_ai` was not set). A report
that simply omits the diagnosis column reads as "no cause was found", which is a different claim
from "no cause was looked for".

The report is the whole return value — there is no structured side-channel field. The same text
serves a person reading it directly and a calling skill parsing it.

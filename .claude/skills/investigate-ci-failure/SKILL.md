---
name: investigate-ci-failure
model: sonnet
description: Classify a pull request's failing CI checks — a mechanical gate slip, a real code defect, a known infrastructure flake, or something unclassified — with the evidence behind each verdict. Investigates and reports; never fixes, pushes, or re-runs.
---

# Investigate a CI failure

Turn a red pull request into a classified, evidenced report: for each failing check, what kind of
failure it is and what says so. The caller decides what to do about it.

[`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) step 2 is the wired caller. That step's own procedure —
read `--log-failed`, find the cause, fix it — suits a failure whose reason is printed in the log,
which is most `ci.yml` failures and few E2E ones. The on-device lanes fail with nothing in the log:
the layered diagnostics that explain them
([BE-0361](../../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md),
[BE-0367](../../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md))
are uploaded as artifacts under `runs/diagnostics/` and never appear in `gh run view --log-failed`
output at all. Diagnosing from the log alone there means guessing, in both directions: a host flake
read as a regression sends a pointless fix through a full CI cycle, and a real regression waved off
as "the usual flake" ships. This skill reads the artifacts instead.

## What this skill does not do

- **It does not change anything.** No edit to the code under test, no commit, no push, no
  `gh run rerun`. It classifies and reports; the caller acts. The single file it may write is its
  own [`references/known-ci-failure-patterns.md`](references/known-ci-failure-patterns.md) (step 5),
  and even then it does not commit that change.
- **It does not treat `devicefarm.yml` or `ai-smoke.yml`.** Both are `workflow_dispatch` only, so
  neither appears in a pull request's checks and neither can be the failure being investigated.
- **It does not decide a verdict.** Reading recorded failures to sort them is not judging a run.
  Pass/fail stays where prime directive 1 puts it, in the deterministic runner.

## Input

A pull request number, from [`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) step 2.

## Steps

### 1. Sort the failing checks by which workflow they belong to

```bash
gh pr checks <PR>
```

For each failing check, get its jobs:

```bash
gh run view <run-id> --json jobs --jq '.jobs[] | select(.conclusion == "failure") | .name'
```

Sort each failing job into one of three routes:

- the `check` job of `ci.yml` → step 2
- a job of `ios-e2e.yml`, `android-e2e.yml`, or `web-e2e.yml` → step 3
- anything else (`pr-title`, `roadmap-id`, `mcp-wire`, `swift`, `serve-db`, `codeql`, …) → treat as
  `code-defect` and report the failing step with its log excerpt. These fail for a reason the log
  states.

### 2. The gate job: mechanical, or a real defect

```bash
gh run view <run-id> --log-failed
```

Read which gate step failed and match it against the `gate-mechanical` table in
[`references/known-ci-failure-patterns.md`](references/known-ci-failure-patterns.md). Three steps
are mechanical and have a single fix command each; every other step points at the change itself.

- A match → `gate-mechanical`, and carry that entry's fix command into the report verbatim.
- No match → `code-defect`, with the failing step name and the relevant log lines.

The reference file's `lint-skills` note matters here: that drift has two causes with different
fixes, and the reference says how to tell them apart. Do not shortcut it.

### 3. The E2E lanes: match against known infrastructure faults

Download the failing job's own artifact. Its `path: runs/` carries `runs/diagnostics/` inside it, so
one download brings all three diagnostic layers:

```bash
gh run download <run-id> -n ios-e2e-<job>-run       # or android-e2e-<job>-run
```

Match what you find against the `e2e-known-flake` tables in
[`references/known-ci-failure-patterns.md`](references/known-ci-failure-patterns.md) — eight
confirmed patterns across the two on-device lanes, each with the artifact path that confirms it.
Read that file now rather than guessing from the job log.

A match → `e2e-known-flake`, citing the artifact path that confirmed it. No match → step 4.

Two cases the reference file settles rather than this step: a **`visual` or `golden`** job is a
non-gating signal whose red means re-record or investigate upstream, not re-run; and a probe whose
**deadline was cut off** in `probe.txt` is not a probe that found nothing.

### 4. No match: ask the run history instead

Reaching here means the symptom matches nothing known — either genuinely new, or a known fault
wearing an unfamiliar face. Rather than guess, ask whether this scenario has been flipping all
along.

Assemble a history from the same job's recent runs and hand it to
[`investigate-scenario-flakiness`](../../../.apm/skills/investigate-scenario-flakiness/SKILL.md):

```bash
gh run list --workflow <workflow> -L 20 \
  --json databaseId,conclusion,headBranch,createdAt \
  --jq '.[] | select(.headBranch == "main") | .databaseId'
```

Restrict to runs of `main` and the merge queue: a run of somebody's branch carries that branch's own
defects, which is noise against the question being asked. Download each run's artifact for the same
job into one directory, then invoke the sub-skill on it.

**Pass no `use_ai`.** What this step needs is the `classification` field; the AI triage pass costs
`ANTHROPIC_API_KEY` credit and answers a question — *why* it flips — that this classification does
not ask. The sub-skill's default already omits it.

- The sub-skill reports the scenario `flaky` → `e2e-known-flake`, on the strength of the history
  rather than a matched pattern. Say which, in the report: a data-backed verdict and a
  pattern-matched one are different kinds of evidence.
- Anything else → `e2e-unclassified`.

This step downloads up to twenty artifacts, which is why it is the last resort and not the first
move. When the sub-skill's own history is too thin to classify (`unproven`), report
`e2e-unclassified` and say the history was insufficient — not that the scenario is stable.

### 5. Record a newly confirmed pattern

When step 4 produced an `e2e-known-flake` from the history, append it to the `e2e-known-flake` table
in [`references/known-ci-failure-patterns.md`](references/known-ci-failure-patterns.md): the
symptom, the artifact path that shows it, and that it was confirmed from run history rather than a
prior diagnosis. The next investigation then matches it at step 3 and skips twenty downloads.

**Edit the file and stop there.** Do not commit it, do not push it, and do not run `make skills` —
report the edit and let the caller fold it into whatever commit it is already making. A skill that
commits on its own behalf inside somebody else's pull request puts an unrelated change in their diff.

### 6. Report

One report, one entry per failing check, each carrying four fields:

| Field | Content |
|---|---|
| Check | The failing check and job name |
| Classification | `gate-mechanical` / `code-defect` / `e2e-known-flake` / `e2e-unclassified` |
| Evidence | The log lines, the artifact path, or the sub-skill's scenario name and `flip_rate` |
| Recommended action | See below |

Recommended action by classification:

- `gate-mechanical` — the fix command from the reference table.
- `code-defect` — proceed with the normal fix flow.
- `e2e-known-flake` — a re-run is likely to be enough; no code change is indicated.
- `e2e-unclassified` — needs a person. Say what was ruled out, so the human starts where this skill
  stopped rather than from the beginning.

Where a classification rests on something not checked, say so. "The artifact carried no stall
capture" and "no artifact was downloaded" support very different next moves, and a report that
blurs them sends the caller down the wrong one.

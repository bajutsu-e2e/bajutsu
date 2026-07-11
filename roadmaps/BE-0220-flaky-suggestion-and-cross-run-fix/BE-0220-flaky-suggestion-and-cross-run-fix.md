**English** · [日本語](BE-0220-flaky-suggestion-and-cross-run-fix-ja.md)

# BE-0220 — Flaky-test suggestion and cross-run fix proposals from DB run history

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0220](BE-0220-flaky-suggestion-and-cross-run-fix.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0220") |
| Implementing PR | [#904](https://github.com/bajutsu-e2e/bajutsu/pull/904) |
| Topic | Self-healing triage (M4) |
<!-- /BE-METADATA -->

## Introduction

Mine the DB-backed run history a hosted or self-hosted `serve` accumulates, surface the
scenarios that are actually flaky (their verdict flips while the scenario itself is unchanged),
and — for each surfaced scenario — let the AI investigator read the evidence across *both* the
passing and the failing runs, explain what varies between them, and propose a concrete fix, up to
a full rewrite of the scenario YAML. The suggestion side (which scenarios are flaky, how flaky,
ranked) is fully deterministic and needs no AI; the fix side is an advisory proposal a human
reviews and applies, never an auto-edit and never on the verdict path.

This is the longitudinal, history-wide counterpart to two shipped commands: it extends
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s
`audit --history` classification from "here is each scenario's class" into a **ranked, actionable
surface**, and it extends [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md) /
[BE-0022](../BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) triage from
"diagnose one failed run" into "diagnose the *pattern* across many runs of the same scenario".

## Motivation

Bajutsu already detects flakiness two ways: `audit` runs a scenario `K` times and diffs the
outcomes (a point-in-time proof), and `audit --history` groups accumulated runs by
`(scenarioHash, name)` and classifies each as `flaky` / `deterministic` / `unproven`
(BE-0049). But detection stops at classification. A team running a hosted `serve` with hundreds
of scenarios and a growing DB of runs still has to (1) go looking for that classification, one
scenario at a time, and (2) once they find a flaky one, do the tedious cross-run reading by
hand — open a passing run and a failing run side by side, and work out what differed.

Two gaps follow.

**Gap 1 — flakiness is classified but not surfaced.** Nothing ranks the suite by how flaky each
scenario is, or puts "these five scenarios are your worst offenders this week" in front of a team
where they will act on it. The signal exists in the DB; it just is not presented.

**Gap 2 — triage is per-failure, not per-pattern.** `triage` reads the evidence of *one* failed
run and explains that failure. Intermittent failure is a different question: not "why did this run
fail" but "why does this scenario sometimes pass and sometimes fail under the same content hash".
Answering it well means reading the evidence of several runs at once — the passing ones and the
failing ones — and reasoning about the *delta* (a selector that resolved in run A but was ambiguous
in run B, a wait that beat a spinner four times out of five, a network response that varied). That
cross-run reading is exactly the judgement-free investigator work an LLM is good at, and it is the
natural extension of the existing triage boundary.

Closing both makes the determinism-first stance not just provable (BE-0049) but *maintainable*: the
team sees its flakiest tests ranked, and gets a concrete, reviewable fix for each — while the
pass/fail verdict stays entirely in the deterministic runner.

**Boundary note.** Like BE-0049, this strengthens determinism-first rather than straining it. The
suggestion half only *reports* flakiness mined from history; it never retries a test to green and
never changes a verdict. The fix half only *proposes* an edit for a human to review — and, per
[BE-0023](../BE-0023-self-healing-guards/BE-0023-self-healing-guards.md), a proposal that would
weaken an assertion (delete an `expect`, loosen a `value`, widen a selector past uniqueness) is
flagged as laxer, not silently offered. Nothing here puts an LLM on the `run` / CI verdict path.

## Detailed design

Proposal altitude. Two halves; the first is deterministic and the prerequisite for the second.

### Prerequisite — run provenance on the DB run record

Cross-run flaky detection over the DB needs each run stamped so "the same scenario" is
well-defined. BE-0049 already stamps `manifest.json` with a `provenance` block (`scenarioHash`,
`toolVersion`, `gitRevision`) and notes as a follow-up that the same provenance must land on the
serve DB run record (tracked under
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). This item
depends on that stamp existing on the DB record; if it has not shipped, delivering it is the first
unit of work here (add the columns, backfill from stored `manifest.json` where possible).

### Half 1 — flaky suggestion (deterministic, no AI)

- **Cross-run flakiness score over the DB.** Group DB run records by `(scenarioHash, name)` and
  compute a per-scenario flakiness metric over a configurable window (e.g. last `N` runs or last
  `D` days): the verdict-flip rate while the content hash is unchanged, separated from a scenario
  that was edited (hash changed) or an app/tool version that moved (BE-0049's exact distinction,
  reused). Reuse `bajutsu audit --history`'s classification (`flaky` / `deterministic` /
  `unproven`) as the base and layer the ranking on top.
- **Ranked surface in the serve Web UI.** A panel that lists scenarios sorted by flakiness,
  showing the pass/fail counts, the window, and the class, with each row linking to the
  representative passing and failing runs' evidence. This is the "Surfacing CLI features in the
  serve Web UI" pattern applied to the history view. A `--json` / CLI form of the same ranking is
  available for CI and scripting.
- **No verdict impact.** The surface is read-only over the run history; it computes no pass/fail
  and gates nothing.

### Half 2 — cross-run fix proposal (AI investigator, advisory)

- **Cross-run TriageContext.** Extend the existing `assemble` step (BE-0021) from "the first
  failed scenario of one run" to "one flaky scenario across a set of its runs": gather the failure
  message / failed step / element tree / screenshot from representative *failing* runs **and** the
  corresponding evidence from representative *passing* runs, plus the scenario definition and the
  selector ids involved. The context is the delta material.
- **Pattern diagnosis + fix proposal behind the existing protocol.** Reuse the `TriageAgent`
  protocol. A `ClaudeTriageAgent`-style implementation reads the cross-run context and is forced,
  via a structured tool call, to return (a) a root-cause category for the *intermittency*
  (`selector-ambiguity` / `timing` / `network-variance` / `state-leak` / `unknown`) and (b) a
  proposed fix. The fix ranges from a targeted structured edit (promote a `label` selector to
  `id`, add / tighten a `wait` condition, add a `within` scope to disambiguate) through, when the
  scenario is structurally fragile, a **full rewrite of the scenario YAML**.
- **Always a reviewable proposal, never an auto-edit.** Consistent with BE-0022 and DESIGN §6.5,
  the output is a proposal diff a human reviews and applies — the committed YAML never changes on
  its own. A full rewrite is presented as a diff against the current scenario, not a silent
  overwrite.
- **Laxer guard.** Per BE-0023, any proposed edit that weakens the test's assertions (removing an
  `expect`, loosening a `value` / `label` match, widening a selector past uniqueness, dropping a
  `wait` timeout) is flagged as making the test laxer, so a reviewer sees the trade-off explicitly
  rather than the fix quietly reducing coverage to "make it pass".

### Surfaces

Whether Half 2 lands as a new subcommand (e.g. `bajutsu triage --flaky <scenario>`), a mode of the
existing `audit --history`, or purely a serve Web UI action is an implementation choice deferred to
adoption. The serve panel (Half 1) is the primary surface; the CLI form keeps CI and scripting
first-class.

## Alternatives considered

* **Automatic retry / quarantine of flaky tests (the common industry answer).** Rejected for the
  gate, as in BE-0049: retry-to-pass hides flakiness. This item does the opposite — surface and
  fix.
* **Let the AI auto-apply the rewrite.** Rejected: it would let a non-deterministic model silently
  change a committed test, violating DESIGN §6.5 ("AI output is always a proposed diff") and the
  BE-0023 laxer guard. The rewrite is always a reviewable diff.
* **Point-in-time repeat-and-diff only (status quo, BE-0049 `audit`).** Acceptable but incomplete:
  running `K` times proves flakiness for one scenario on demand, but does not mine the flakiness a
  team's real run history already contains, nor rank the suite, nor reason across the accumulated
  passing/failing pairs.
* **Per-failure triage only (status quo, BE-0021).** Diagnoses a single failure well but cannot see
  intermittency — the same scenario passing and failing under one content hash — which only the
  cross-run view exposes.
* **A separate flakiness store instead of the DB run records.** Rejected: the run history already
  accumulates in the DB; adding a parallel store duplicates the system of record. This item adds a
  query and a surface over existing records (plus the provenance stamp BE-0049 already scoped).
* **Split into two items — one for the flaky suggestion surface (Half 1) and one for the cross-run
  fix proposal (Half 2).** Worth considering given that the parallel triage work in this topic
  (BE-0021, BE-0022, BE-0023) shipped as three separate items. Bundling was deliberate here because
  Half 1 delivers limited value on its own: surfacing a ranked list of flaky scenarios is most
  useful when a user can immediately act on it, and the action (reviewing a cross-run fix proposal)
  is Half 2. The two halves share the same cross-run evidence assembly step (the prerequisite DB
  provenance stamp and the `TriageContext` extension), so splitting would require coordinating the
  same prerequisite across two proposals. If implementation experience shows the AI-agent design of
  Half 2 takes significantly longer to mature than Half 1, splitting at that point is the right
  call — the Progress checklist is already structured to allow Half 1 to land and flip its boxes
  independently.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Prerequisite — run provenance (`scenarioHash` / `toolVersion` / `gitRevision`) stamped onto the DB run record (delivered here; not previously shipped under BE-0015).
- [x] Half 1 — cross-run flakiness score over the DB run history, reusing the `audit --history` classification.
- [ ] Half 1 — ranked flaky-scenario panel in the serve Web UI (+ `--json` / CLI form) linking to representative passing / failing run evidence.
- [ ] Half 2 — cross-run `TriageContext` assembling evidence from both passing and failing runs of one flaky scenario.
- [ ] Half 2 — pattern diagnosis + fix proposal (targeted edit through full YAML rewrite) behind the `TriageAgent` protocol, as a reviewable proposal diff.
- [ ] Half 2 — laxer guard (BE-0023) flagging any proposal that weakens assertions.

### Log

- 2026-07-11 — Prerequisite: stamped run provenance onto the serve DB `Run` record. Added nullable
  `scenario_hash` / `tool_version` / `git_revision` columns (indexed on `scenario_hash`, the
  flakiness grouping key) with alembic migration `0008`, threaded them through `RunRecord`, and
  populated them in `_persist_run` from the run's `manifest.json` provenance block (BE-0049). A
  pre-provenance run records with null provenance — ungroupable, never blocking. Historical backfill
  of already-stored runs is deferred (it needs artifact-store access, not a schema migration).
- 2026-07-11 — Half 1: added the deterministic cross-run flakiness score
  (`bajutsu/serve/flakiness.py:rank_flakiness`) over the DB run records. It groups runs by
  `scenario_hash`, reuses the `audit --history` classification (extracted as the shared
  `audit.classify_stability`), and ranks scenarios flaky-first then by verdict flip rate
  (`2·min(passed, failed)/runs`), with a configurable window (`window_runs` / `since`) and the
  newest passing/failing run ids for evidence linking. A run with no `scenario_hash` or no verdict
  is skipped, mirroring `audit --history`. Read-only over history: it computes no verdict and gates
  nothing. The Web UI / CLI surface for this score is the next Half 1 unit.

## References

[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the
`audit` / `audit --history` classification and run provenance this builds on;
[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md) — the `triage` `assemble` → `TriageAgent`
→ `render` flow the cross-run diagnosis extends;
[BE-0022](../BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) — structured
fixes as reviewable proposal diffs;
[BE-0023](../BE-0023-self-healing-guards/BE-0023-self-healing-guards.md) — guards against
making tests laxer;
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the DB-backed
run records this mines (and where the provenance stamp lands);
`bajutsu/triage.py` · `bajutsu/claude_triage.py` · `bajutsu/doctor.py`,
[DESIGN §3.1 / §6.5 / §10 / §11](../../DESIGN.md)

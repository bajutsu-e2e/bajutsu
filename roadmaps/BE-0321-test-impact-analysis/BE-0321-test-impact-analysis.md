**English** · [日本語](BE-0321-test-impact-analysis-ja.md)

# BE-0321 — Test impact analysis (affected-step selection from a change)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0321](BE-0321-test-impact-analysis.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0321") |
| Implementing PR | [#1390](https://github.com/bajutsu-e2e/bajutsu/pull/1390) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Given a change to the app under test, report which scenario steps that change is likely to affect —
so a developer can review and update exactly the steps at risk, and a continuous-integration (CI)
pipeline can run the affected steps first. The report is derived deterministically: it cross-references
the [stable ids](../../docs/glossary.md#scenario-authoring) each scenario step references against the
stable ids a change touches, with no large language model (LLM) in the path and no bearing on any
run's pass/fail verdict. The change is read
straight from a `git` diff, so a developer supplies a commit range rather than a hand-written list of
what changed. A new `bajutsu impact` command exposes the report.

## Motivation

A developer who edits the app under test cannot tell, from the edit alone, which of the suite's
scenarios that edit puts at risk. Today the only sound answer is to run the whole suite and read the
failures — correct, but slow to give feedback and blind to *why* a given scenario broke. The reverse
question — "I changed this; which steps should I look at?" — has no tool, even though it is the first
question a developer asks after touching a screen.

Bajutsu already holds the raw material for the answer. Static scenario analysis, the same analysis
[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) uses for its coverage map, extracts
the stable ids, screens, and asserted endpoints each scenario references. BE-0050 walks that data in
the forward direction — from the suite to the app surface it covers — to expose gaps. Reversing the
same index answers the impact question: build a map from each stable id to the scenario steps that
reference it, and a change that touches a stable id points straight at the steps that exercise it.

The remaining piece is turning "the change" into a set of touched stable ids without burdening the
developer and without breaking app-agnosticism. A stable id appears in app source as a plain string
literal — `login_button` in a Swift `accessibilityIdentifier`, a Kotlin `resource-id`, or a web
`testID` alike. Scanning a `git` diff's changed lines for the stable ids the suite already references
recovers the touched set by string match alone, across every backend, without parsing the app's source
or encoding per-app knowledge. The developer names a commit range; the tool does the rest.

Resolving the impact question this way pays off on both sides of the workflow. A developer sees the
exact steps a change puts at risk and can fix them before pushing. A CI pipeline can order the affected
steps first for fast feedback, or — with the safeguards below — narrow a pre-merge run to them. Neither
use moves the pass/fail verdict off the deterministic runner.

## Detailed design

Proposal altitude. The `bajutsu impact` command is read-only and advisory, of a piece with `audit`,
`coverage`, `stats`, and cross-run flakiness ranking — the read-only advisory analysis that
[BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md) established, which never
touches a device, never calls an LLM, and never gates CI.

- **Reverse index (deterministic).** Parse each scenario and record, per step, the selectors it acts
  on or asserts, the screens it reaches (via `setup` / deeplinks), and the endpoints it asserts on.
  Invert that into a map from each stable id — and each screen and endpoint — to the list of
  `(scenario, step)` pairs that reference it. Step granularity falls out for free, because a scenario
  already binds each step to its selectors. This reuses BE-0050's static scenario analysis rather than
  reimplementing selector extraction.

- **Change extraction from a `git` diff (deterministic, app-agnostic).** Read the changed lines of a
  `git` diff over a commit range the developer names. For each stable id the reverse index knows, test
  whether that id's string literal appears on an added or removed line. The set of ids that do is the
  *touched set*. The match is plain string search over ids the suite already declares, so it needs no
  language-specific parsing and encodes no per-app knowledge — it works identically for a Swift, Kotlin,
  or TypeScript diff.

- **Output (step granularity).** Join the touched set through the reverse index to the affected
  `(scenario, step)` pairs, and report them as JSON and as a human-readable list. Each affected step
  carries the touched id that implicates it, so the reader sees not just *which* step but *why* it is
  flagged. `bajutsu impact` emits both forms: a CI pipeline consumes the JSON; a developer reads the
  list.

- **Determinism and soundness.** Every output is a deterministic function of the diff and the
  scenarios: the same inputs always yield the same affected set, with no model and no judgment call.
  Soundness, however, is bounded in both directions. In the false-negative direction: the scan catches
  a change that edits a stable id's own line; it cannot catch a change that alters behavior without
  touching any id literal — a handler's logic, a shared helper, a network response shape. Such a
  change is *unattributable*: it maps to no id in the reverse index. In the false-positive direction:
  a short or common id that happens to appear in a comment or in an unrelated changed line will widen
  the affected set beyond the truly-affected steps. Over-selection is the safe direction for CI — no
  affected step is silently skipped — though a developer may see a handful of false positives. The
  design names both limits rather than hiding them, because how CI treats an unattributable change is
  the whole safety question below.

- **CI use — a mechanism, with a safe policy.** The tool provides the affected set; a team chooses how
  aggressively to act on it. Two safeguards keep even the aggressive choice sound:
  - **Conservative fallback.** When a diff contains any unattributable change, the tool reports the
    affected set *and* signals "incomplete — a full run is warranted". A team that narrows CI to the
    affected steps must fall back to the full suite on that signal, so a change the scan cannot attribute
    is never silently skipped.
  - **Full-suite safety cadence.** Narrowing belongs to fast pre-merge feedback, not to the last word.
    The full deterministic suite still runs at a coarser cadence — on merge to the main branch, nightly,
    or at release — so a step the scan misses at pull-request time is caught before it ships, not never.

  The safe default is additive: order the affected steps first, or flag them for a human, while still
  running the whole suite. Narrowing the pre-merge run down to the affected set is opt-in, and sound only
  with both safeguards above. In every case the pass/fail verdict stays with the deterministic runner
  (prime directive 1) and the analysis itself runs no LLM.

- **AI layer — later, advisory, off the gate.** The deterministic reverse-index scan ships first and
  stands alone. A change that touches no id literal but still alters behavior is exactly the
  unattributable case the scan misses; reading a diff semantically to surface such indirect impact is
  judgment work an LLM can do. Should it be added, it belongs on the investigator side alongside AI
  triage ([BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md)) — strictly advisory, never pruning a CI
  run, never on the pass/fail path. It is out of scope for the first delivery and noted here only to fix
  where it would sit.

## Alternatives considered

- **Parse the app's source to extract changed UI elements.** Reading a Swift, Kotlin, or TypeScript
  diff into an abstract syntax tree would catch more than a string scan — a renamed handler, a
  restructured view. It breaks app-agnosticism (prime directive 3): it needs a parser and per-language,
  per-framework knowledge of how each app declares its UI, knowledge that belongs in the app, not in
  Bajutsu. The string scan deliberately trades that reach for a match defined entirely over ids the
  suite already declares.

- **Have the developer pass the changed ids by hand.** A `bajutsu impact --changed-id a,b,c` interface
  would be trivial to build and fully deterministic, but it puts the burden of knowing what changed back
  on the developer — the burden this item exists to remove. Reading the diff keeps the developer's input
  to a commit range.

- **Diff two runs (before/after) instead of a source diff.** Comparing the element trees of a run before
  and after a change would catch behavioral impact the string scan misses. It requires running the suite
  twice, which defeats the goal of *selecting* what to run before running it. A run diff is a distinct,
  heavier capability; this item stays with static selection from a source diff.

- **Let CI prune the suite with no safeguards.** Narrowing every CI run to the affected set, with no
  fallback and no full-suite cadence, would be the fastest option and the least sound: an unattributable
  change would silently skip the steps that would have caught its regression. The conservative fallback
  and the safety cadence exist precisely to make narrowing opt-in without that risk.

- **Do nothing (status quo).** Acceptable, but the reverse question stays unanswered: a developer keeps
  running the whole suite to learn what a change broke, and CI keeps paying full-suite latency on every
  pull request, even though the index needed to answer it already exists for BE-0050.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Reverse index — invert BE-0050's static scenario analysis into `id / screen / endpoint → [(scenario, step)]` (`bajutsu/analysis/impact.py:reverse_index`, reusing the shared `audit.step_referenced_ids` / `coverage.step_requests` per-step walks).
- [x] Change extraction — scan a `git` diff's changed lines for referenced stable ids; produce the touched set and the unattributable-change signal (`parse_diff` + `impact`; a changed file matching no referenced literal marks the report incomplete).
- [x] Output — join to affected `(scenario, step)` pairs; emit JSON and a human-readable list, each step carrying the implicating id (`bajutsu impact --json` / text via `render`).
- [x] CI integration — a consumable selection artifact (`--json` with a `complete` signal) plus documentation of the safe policy (conservative fallback, full-suite cadence, additive default) in [cli.md](../../docs/cli.md#impact) and [ci.md](../../docs/ci.md).

**Log**

- 2026-07-27 — Shipped the first delivery in [#1390](https://github.com/bajutsu-e2e/bajutsu/pull/1390): the deterministic reverse-index scan (`bajutsu impact`), reusing BE-0050's per-step selector and endpoint walks; the AI layer (semantic diff reading for indirect impact) stays out of scope, off the CI verdict path.

## References

[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) (the forward coverage map this
inverts), [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md) (investigator-side AI positioning),
[BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md) (the read-only analysis
package this joins), [cli.md](../../docs/cli.md) (where the `bajutsu impact` command will be documented
on implementation), [DESIGN §2 / §7](../../DESIGN.md)

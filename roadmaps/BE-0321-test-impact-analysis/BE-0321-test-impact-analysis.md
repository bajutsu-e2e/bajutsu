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

Given a change to the app under test, this item reports which scenario steps the change is likely to
affect. A developer can then review and update exactly the steps at risk. A continuous-integration
(CI) pipeline can run the affected steps first.

The report is derived deterministically. The report cross-references two sets of
[stable ids](../../docs/glossary.md#scenario-authoring): the ids that each scenario step references,
and the ids that a change touches. No large language model (LLM) sits in the path, and the report has
no bearing on the pass/fail verdict of any run.

The change is read straight from a `git` diff. A developer supplies a commit range instead of a
hand-written list of what changed. A new `bajutsu impact` command exposes the report.

## Motivation

A developer who edits the app under test cannot tell, from the edit alone, which scenarios of the
suite the edit puts at risk. Today the only sound answer is to run the whole suite and read the
failures. That answer is correct, but slow to give feedback and blind to *why* a given scenario
broke.

The reverse question has no tool: "I changed this; which steps should I look at?" A developer asks
that question first after touching a screen.

Bajutsu already holds the raw material for the answer. Static scenario analysis extracts the stable
ids, screens, and asserted endpoints that each scenario references.
[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) uses the same analysis for its
coverage map. BE-0050 walks that data in the forward direction, from the suite to the app surface the
suite covers, to expose gaps.

Reversing the same index answers the impact question. Build a map from each stable id to the scenario
steps that reference the id. A change that touches a stable id then points straight at the steps that
exercise the id.

The remaining piece is turning "the change" into a set of touched stable ids. The conversion must not
burden the developer, and must not break app-agnosticism. A stable id appears in app source as a
plain string literal. Examples are `login_button` in a Swift `accessibilityIdentifier`, a Kotlin
`resource-id`, and a web `testID`.

A scan of the changed lines of a `git` diff, for the stable ids that the suite already references,
recovers the touched set by string match alone. The scan works across every backend. The scan does
not parse the app's source and encodes no per-app knowledge. The developer names a commit range, and
the tool does the rest.

Resolving the impact question this way pays off on both sides of the workflow. A developer sees the
exact steps that a change puts at risk, and can fix them before pushing. A CI pipeline can order the
affected steps first for fast feedback. With the safeguards below, a CI pipeline can also narrow a
pre-merge run to the affected steps. Neither use moves the pass/fail verdict off the deterministic
runner.

## Detailed design

Proposal altitude. The `bajutsu impact` command is read-only and advisory. The command belongs with
`audit`, `coverage`, `stats`, and cross-run flakiness ranking. These commands are the read-only
advisory analysis that
[BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md) established. They
never touch a device, never call an LLM, and never gate CI.

- **Reverse index (deterministic).** Parse each scenario and record three facts per step: the
  selectors the step acts on or asserts, the screens the step reaches (via `setup` or deeplinks), and
  the endpoints the step asserts on. Invert the records into a map. The map goes from each stable id,
  each screen, and each endpoint, to the list of `(scenario, step)` pairs that reference it. Step
  granularity falls out for free, because a scenario already binds each step to its selectors. The
  index reuses the static scenario analysis of BE-0050 instead of reimplementing selector extraction.

- **Change extraction from a `git` diff (deterministic, app-agnostic).** Read the changed lines of a
  `git` diff over a commit range that the developer names. For each stable id that the reverse index
  knows, test whether the string literal of the id appears on an added or removed line. The set of ids
  that appear is the *touched set*. The match is plain string search over ids that the suite already
  declares. The match needs no language-specific parsing and encodes no per-app knowledge. The match
  works identically for a Swift, Kotlin, or TypeScript diff.

- **Output (step granularity).** Join the touched set through the reverse index to the affected
  `(scenario, step)` pairs. Report the pairs as JSON and as a human-readable list. Each affected step
  carries the touched id that implicates the step. The reader then sees not merely *which* step but
  *why* the step is flagged. `bajutsu impact` emits both forms: a CI pipeline consumes the JSON, and a
  developer reads the list.

- **Determinism and soundness.** Every output is a deterministic function of the diff and the
  scenarios. The same inputs always yield the same affected set, with no model and no judgment call.
  Soundness is bounded in both directions, however.
  - **False negatives.** The scan catches a change that edits the line of a stable id. The scan cannot
    catch a change that alters behavior without touching any id literal, such as a handler's logic, a
    shared helper, or a network response shape. Such a change is *unattributable*: the change maps to
    no id in the reverse index.
  - **False positives.** A short or common id can appear in a comment or in an unrelated changed line.
    That appearance widens the affected set beyond the truly affected steps. Over-selection is the
    safe direction for CI, because no affected step is silently skipped. A developer may see a
    handful of false positives.

  The design names both limits instead of hiding them. How CI treats an unattributable change is the
  whole safety question below.

- **CI use: a mechanism, with a safe policy.** The tool provides the affected set, and a team chooses
  how aggressively to act on the set. Two safeguards keep even the aggressive choice sound:
  - **Conservative fallback.** When a diff contains any unattributable change, the tool reports the
    affected set *and* signals "incomplete — a full run is warranted". A team that narrows CI to the
    affected steps must fall back to the full suite on that signal. A change that the scan cannot
    attribute is then never silently skipped.
  - **Full-suite safety cadence.** Narrowing belongs to fast pre-merge feedback, not to the last
    word. The full deterministic suite still runs at a coarser cadence: on merge to the main branch,
    nightly, or at release. A step that the scan misses at pull-request time is then caught before it
    ships, not never.

  The safe default is additive: order the affected steps first, or flag them for a human, while still
  running the whole suite. Narrowing the pre-merge run down to the affected set is opt-in. Narrowing
  is sound with both safeguards above. In every case, the pass/fail verdict stays with the
  deterministic runner (prime directive 1), and the analysis itself runs no LLM.

- **AI layer: later, advisory, off the gate.** The deterministic reverse-index scan ships first and
  stands alone. A change that touches no id literal but still alters behavior is the unattributable
  case that the scan misses. Reading a diff semantically to surface such indirect impact is judgment
  work that an LLM can do. If the AI layer is added, the layer belongs on the investigator side,
  alongside AI triage ([BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md)). The layer would be
  advisory in every case: the layer would never prune a CI run and would never sit on the pass/fail
  path. The AI layer is out of scope for the first delivery. This item notes the layer here to fix
  where the layer would sit.

## Alternatives considered

- **Parse the app's source to extract changed UI elements.** Reading a Swift, Kotlin, or TypeScript
  diff into an abstract syntax tree would catch more than a string scan, for example a renamed
  handler or a restructured view. The approach breaks app-agnosticism (prime directive 3). The
  approach needs a parser and per-language, per-framework knowledge of how each app declares its UI.
  That knowledge belongs in the app, not in Bajutsu. The string scan deliberately trades that reach
  for a match defined entirely over ids that the suite already declares.

- **Have the developer pass the changed ids by hand.** A `bajutsu impact --changed-id a,b,c`
  interface would be trivial to build and fully deterministic. The interface puts the burden of
  knowing what changed back on the developer, which is the burden this item exists to remove. Reading
  the diff keeps the input of the developer to a commit range.

- **Diff two runs (before and after) instead of a source diff.** Comparing the element trees of a run
  before and after a change would catch behavioral impact that the string scan misses. The approach
  requires running the suite twice, which defeats the goal of *selecting* what to run before running
  it. A run diff is a distinct, heavier capability. This item stays with static selection from a
  source diff.

- **Let CI prune the suite with no safeguards.** Narrowing every CI run to the affected set, with no
  fallback and no full-suite cadence, would be the fastest option and the least sound. An
  unattributable change would silently skip the steps that would have caught its regression. The
  conservative fallback and the safety cadence exist to make narrowing opt-in without that risk.

- **Do nothing (status quo).** Acceptable, but the reverse question stays unanswered. A developer
  keeps running the whole suite to learn what a change broke. CI keeps paying full-suite latency on
  every pull request. The index needed to answer the question already exists for BE-0050.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Reverse index — invert BE-0050's static scenario analysis into `id / screen / endpoint → [(scenario, step)]` (`bajutsu/analysis/impact.py:reverse_index`, reusing the shared `audit.step_matchable_ids` / `coverage.step_requests` per-step walks).
- [x] Change extraction — scan a `git` diff's changed lines for referenced stable ids; produce the touched set and the unattributable-change signal (`parse_diff` + `impact`; a changed file matching no referenced literal marks the report incomplete).
- [x] Output — join to affected `(scenario, step)` pairs; emit JSON and a human-readable list, each step carrying the implicating id (`bajutsu impact --json` / text via `render`).
- [x] CI integration — a consumable selection artifact (`--json` with a `complete` signal) plus documentation of the safe policy (conservative fallback, full-suite cadence, additive default) in [cli.md](../../docs/cli.md#impact) and [ci.md](../../docs/ci.md).

**Log**

- 2026-07-27 — Shipped the first delivery in [#1390](https://github.com/bajutsu-e2e/bajutsu/pull/1390): the deterministic reverse-index scan (`bajutsu impact`), reusing BE-0050's per-step selector and endpoint walks; the AI layer (semantic diff reading for indirect impact) stays out of scope, off the CI verdict path.

## References

- [BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md): the forward coverage map that this item inverts.
- [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md): the investigator-side positioning of AI.
- [BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md): the read-only analysis package that this item joins.
- [cli.md](../../docs/cli.md): where the `bajutsu impact` command will be documented on implementation.
- [DESIGN §2 / §7](../../DESIGN.md)

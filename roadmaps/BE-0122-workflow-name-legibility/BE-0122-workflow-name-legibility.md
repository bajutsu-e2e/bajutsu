**English** · [日本語](BE-0122-workflow-name-legibility-ja.md)

# BE-0122 — Legible GitHub Actions workflow and job names

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0122](BE-0122-workflow-name-legibility.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0122") |
| Implementing PR | [#611](https://github.com/bajutsu-e2e/bajutsu/pull/611) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's CI surface has grown to thirteen workflows (`.github/workflows/*.yml`): the core
gate, docs, dependency audit, idb compatibility monitoring, PR-title linting, four
roadmap-automation workflows, auto-merge, and the on-device/web E2E suites. Viewed from
GitHub's Actions tab or a PR's checks list — where a reviewer sees only the workflow's
`name:` and each job's `name:`, not the YAML behind it — several of these read as bare,
context-free keywords: `docs`, `pr-title`, `roadmap-id`, `roadmap-promote`,
`roadmap-proposal-approvals`, `roadmap-tracking-issues`, `dependency audit`, `idb monitor`,
`web e2e`, `auto-merge`. A reviewer scanning a red checks list cannot tell what any of these
actually verify without opening the run. Two workflows already show the shape this item
generalizes: `E2E (Simulator)` and `Swift (BajutsuKit)` pair a short phrase with a
parenthetical naming what's exercised. This item renames the rest to the same shape — a
docs-only, zero-runtime-behavior change to `name:` fields.

## Motivation

- **The checks list is read far more often than the workflow files.** Every PR — human or
  agent — is triaged from the checks list first; a name that requires opening the run to
  decode adds a click to every red build, multiplied across the many parallel sessions this
  repo runs (see "Working in parallel" in [CLAUDE.md](../../CLAUDE.md)).
- **The good pattern already exists but is applied unevenly.** `E2E (Simulator)` and
  `Swift (BajutsuKit)` show the target shape was already discovered once; the other ten
  workflows never got the same treatment, so a new contributor can't infer the convention
  from the repo alone.
- **This is legibility, not correctness.** [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  hardened what the gate checks; this item only changes how the result is labeled, so it
  carries none of that item's behavioral risk.

## Detailed design

The work is a metadata-only rename plus one documentation addition — no job logic, trigger,
or permission changes.

1. **Rename ambiguous top-level workflow `name:` fields** to a short descriptive phrase,
   following the shape `E2E (Simulator)` / `Swift (BajutsuKit)` already set: a plain-language
   phrase naming the action, with a parenthetical for the tool or scope where that adds
   information. Illustrative mapping (final wording is decided at implementation time):

   | File | Current `name:` | Proposed `name:` |
   |---|---|---|
   | `docs.yml` | `docs` | `Docs site (build & deploy)` |
   | `pr-title.yml` | `pr-title` | `PR title lint` |
   | `roadmap-id.yml` | `roadmap-id` | `Roadmap: allocate BE IDs` |
   | `roadmap-promote.yml` | `roadmap-promote` | `Roadmap: promote shipped items` |
   | `roadmap-proposal-approvals.yml` | `roadmap-proposal-approvals` | `Roadmap: require two approvals (proposals)` |
   | `roadmap-tracking-issues.yml` | `roadmap-tracking-issues` | `Roadmap: sync tracking issues` |
   | `dependency-audit.yml` | `dependency audit` | `Dependency audit (pip-audit)` |
   | `idb-monitor.yml` | `idb monitor` | `idb compatibility monitor` |
   | `web-e2e.yml` | `web e2e` | `Web E2E (Playwright)` |
   | `auto-merge.yml` | `auto-merge` | `Auto-merge (bypass App)` |

   `ci.yml` (`CI`), `e2e.yml` (`E2E (Simulator)`), and `swift.yml` (`Swift (BajutsuKit)`) are
   already in the target shape and are left unchanged.

2. **Rename ambiguous job-level `name:` fields**, same convention, wherever a job's name
   is either bare (`build`, `deploy` in `docs.yml`) or redundant once its workflow is
   renamed (e.g. `web e2e (playwright)` restating a now-clear `Web E2E (Playwright)`
   workflow name). Illustrative mapping:

   | File | Job | Current | Proposed |
   |---|---|---|---|
   | `docs.yml` | build | `build` | `build site` |
   | `docs.yml` | deploy | `deploy` | `deploy to GitHub Pages` |
   | `dependency-audit.yml` | audit | `dependency audit (pip-audit)` | `audit (pip-audit)` |
   | `web-e2e.yml` | smoke | `web e2e (playwright)` | `smoke (playwright)` (mirrors `e2e.yml`'s `smoke (idb)`) |

3. **Leave three job names untouched, and document why.** The repository's branch-protection
   ruleset (`Require code review`, checked via `gh api repos/bajutsu-e2e/bajutsu/rulesets/<id>`)
   pins `required_status_checks` to exact job-name contexts: `check` (`ci.yml`), `E2E`
   (`e2e.yml`'s final gate job), and `require two approvals for BE proposals`
   (`roadmap-proposal-approvals.yml`). A GitHub Actions required-status-check context is the
   job's `name:` verbatim, not the workflow's — renaming any of these three without updating
   the ruleset in the same instant would strand every open PR on a check that no longer
   reports, silently blocking merges. These three are left exactly as-is by this item; a
   future rename of any of them is out of scope here and must be paired with an admin edit to
   the ruleset's `required_status_checks` contexts (a manual, human-executed step — ruleset
   edits are out-of-repo admin state, not reachable from a normal PR).

4. **Document the convention** in [`docs/ai-development.md`](../../docs/ai-development.md)
   (English) and its Japanese mirror: a short subsection ("Naming GitHub Actions workflows and
   jobs") stating the shape (short phrase, parenthetical for tool/scope, no bare single-word
   `name:`), pointing at `e2e.yml` / `swift.yml` as the canonical examples, and flagging the
   required-status-check constraint from item 3 so a future rename doesn't repeat the mistake
   item 3 heads off.

5. **Verify manually.** `actionlint` (already part of `make check`) validates workflow syntax
   but has no opinion on naming, so there is no automated check to add here. Verification is
   pushing the branch and reading the Actions tab and a PR's checks list, confirming every
   check name reads standalone without opening the run.

## Alternatives considered

- **Bracket-prefixed categories** (e.g. `[Roadmap] allocate BE IDs`, `[Docs] build & deploy`).
  Rejected: GitHub's checks list already visually groups jobs under their workflow, so a
  bracket prefix duplicates that grouping instead of adding information, and the four
  `roadmap-*` workflows already read as a family once each carries a plain `Roadmap: …` phrase
  (item 1) — a bracket buys nothing further.
- **Rename the three protected job names too, updating the ruleset in the same PR.** Rejected
  as part of this item's scope: ruleset edits are out-of-repo admin state that a normal PR
  can't carry, and getting the sequencing wrong (rename lands before the ruleset update, or
  vice versa) strands merges. Left as an explicit, separately-tracked manual follow-up (item 3)
  rather than bundled into an otherwise risk-free docs-only change.
- **Do nothing.** Rejected: the rename is docs-only with no runtime risk, while the status quo
  imposes a small but recurring cost on every contributor and agent session reading the checks
  list, which this repo's working-in-parallel model makes frequent.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename ambiguous top-level workflow `name:` fields (item 1)
- [x] Rename ambiguous job-level `name:` fields, excluding protected checks (item 2)
- [x] Leave the three ruleset-protected job names untouched (item 3 — no code change, tracked
      so it isn't silently swept into item 2)
- [x] Document the naming convention in `docs/ai-development.md` (EN + JA) (item 4)
- [ ] Manually verify the Actions tab / PR checks list read clearly after the rename (item 5 —
      done after the branch is pushed, since the check names only render on a real run)

Log:

- Renamed ten top-level workflow `name:` fields (`docs`, `pr-title`, the four `roadmap-*`,
  `dependency audit`, `idb monitor`, `web e2e`, `auto-merge`) and the redundant/bare job names in
  `docs.yml`, `dependency-audit.yml`, and `web-e2e.yml` to the `E2E (Simulator)` / `Swift
  (BajutsuKit)` shape. The four `Roadmap: …` names carry a colon-space, so they are quoted to stay
  valid YAML. Left the three ruleset-protected job names (`check`, `E2E`, `require two approvals for
  BE proposals`) exactly as-is. `codeql.yml` — added after this item was authored — already reads
  clearly, so it is left untouched alongside `ci.yml`/`e2e.yml`/`swift.yml`. Documented the
  convention and the required-status-check constraint in `docs/ai-development.md` and its Japanese
  mirror. Verified with `make check`; the naming legibility (item 5) is confirmed by reading the
  Actions tab once the branch is pushed.
- Review follow-up: renamed the `dependency-audit.yml` job from the tool-only `pip-audit` to
  `audit (pip-audit)` — a plain-language phrase plus tool parenthetical that follows this item's own
  convention and mirrors `smoke (idb)` / `smoke (playwright)`, rather than restating the workflow
  name. Also reflowed the Japanese convention section so no line break splits a word.

## References

- [`.github/workflows/`](../../.github/workflows/) — the workflows this item renames
- [`docs/ai-development.md`](../../docs/ai-development.md) — where the naming convention
  is documented
- [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) —
  prior CI-hardening item this one complements (correctness vs. legibility)
- GitHub Actions docs on required status checks matching a job's `name:` as the check context

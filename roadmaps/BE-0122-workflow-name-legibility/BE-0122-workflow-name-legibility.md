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

Bajutsu's CI surface has grown to thirteen workflows (`.github/workflows/*.yml`): the core gate, docs, dependency audit, idb compatibility monitoring, PR-title linting, four roadmap-automation workflows, auto-merge, and the on-device and web E2E suites. GitHub's Actions tab and a PR's checks list show a reviewer the workflow's `name:` and each job's `name:`, but not the YAML behind them. In that view, several workflows read as bare, context-free keywords: `docs`, `pr-title`, `roadmap-id`, `roadmap-promote`, `roadmap-proposal-approvals`, `roadmap-tracking-issues`, `dependency audit`, `idb monitor`, `web e2e`, and `auto-merge`.

A reviewer who scans a red checks list cannot tell what any of these verifies without opening the run. Two workflows already show the shape that this item generalizes. `E2E (Simulator)` and `Swift (BajutsuKit)` pair a short phrase with a parenthetical that names what the workflow exercises. This item renames the remaining workflows to the same shape. The change is docs-only: it touches `name:` fields and leaves runtime behavior alone.

## Motivation

- **The checks list is read far more often than the workflow files.** A reviewer, human or agent, triages every PR from the checks list first. A name that needs the run opened to decode adds a click to every red build. The many parallel sessions this repo runs multiply that cost (see "Working in parallel" in [CLAUDE.md](../../CLAUDE.md)).
- **The good pattern already exists but is applied unevenly.** `E2E (Simulator)` and `Swift (BajutsuKit)` show that someone already found the target shape. The other ten workflows never got the same treatment, so a new contributor cannot infer the convention from the repo alone.
- **This is legibility, not correctness.** [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) hardened what the gate checks. This item changes how the result is labeled, so it carries none of the behavioral risk of that item.

## Detailed design

The work is a metadata-only rename plus one documentation addition. No job logic, trigger, or permission changes.

1. **Rename ambiguous top-level workflow `name:` fields** to a short descriptive phrase. Follow the shape that `E2E (Simulator)` and `Swift (BajutsuKit)` already set: a plain-language phrase that names the action, with a parenthetical for the tool or scope where the parenthetical adds information. The mapping below is illustrative, and the final wording is decided at implementation time.

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

   `ci.yml` (`CI`), `e2e.yml` (`E2E (Simulator)`), and `swift.yml` (`Swift (BajutsuKit)`) already have the target shape. This item leaves them unchanged.

2. **Rename ambiguous job-level `name:` fields** by the same convention. A job name qualifies when it is bare (`build` and `deploy` in `docs.yml`). It also qualifies when it becomes redundant once its workflow is renamed. An example is `web e2e (playwright)`, which would restate the now-clear `Web E2E (Playwright)` workflow name. The mapping below is illustrative.

   | File | Job | Current | Proposed |
   |---|---|---|---|
   | `docs.yml` | build | `build` | `build site` |
   | `docs.yml` | deploy | `deploy` | `deploy to GitHub Pages` |
   | `dependency-audit.yml` | audit | `dependency audit (pip-audit)` | `audit (pip-audit)` |
   | `web-e2e.yml` | smoke | `web e2e (playwright)` | `smoke (playwright)` (mirrors `e2e.yml`'s `smoke (idb)`) |

3. **Leave three job names untouched, and document why.** The repository's branch-protection ruleset (`Require code review`, checked via `gh api repos/bajutsu-e2e/bajutsu/rulesets/<id>`) pins `required_status_checks` to exact job-name contexts. The three contexts are `check` (`ci.yml`), `E2E` (the final gate job of `e2e.yml`), and `require two approvals for BE proposals` (`roadmap-proposal-approvals.yml`).

   A GitHub Actions required-status-check context is the job's `name:` verbatim, not the workflow's. Renaming any of the three without updating the ruleset at the same moment would strand every open PR on a check that no longer reports. Merges would block without any message. This item leaves all three as they are.

   A future rename of any of them is out of scope here. It must come with an admin edit to the ruleset's `required_status_checks` contexts. That edit is a manual, human-executed step, because ruleset edits are out-of-repo admin state that a normal PR cannot reach.

4. **Document the convention** in [`docs/ai-development.md`](../../docs/ai-development.md) (English) and its Japanese mirror. Add a short subsection, "Naming GitHub Actions workflows and jobs". It states the shape: a short phrase, a parenthetical for tool or scope, and no bare single-word `name:`. It points at `e2e.yml` and `swift.yml` as the canonical examples. It also flags the required-status-check constraint from item 3, so that a future rename does not repeat the mistake that item 3 heads off.

5. **Verify manually.** `actionlint` (already part of `make check`) validates workflow syntax but has no opinion on naming, so no automated check fits here. Verification means pushing the branch, then reading the Actions tab and a PR's checks list. Every check name should read standalone, without opening the run.

## Alternatives considered

- **Bracket-prefixed categories** (for example `[Roadmap] allocate BE IDs` and `[Docs] build & deploy`). Rejected. GitHub's checks list already groups jobs visually under their workflow, so a bracket prefix duplicates that grouping and adds no information. The four `roadmap-*` workflows also read as a family once each carries a plain `Roadmap: …` phrase (item 1). A bracket adds nothing further.
- **Rename the three protected job names too, updating the ruleset in the same PR.** Rejected as part of this item's scope. Ruleset edits are out-of-repo admin state that a normal PR cannot carry. A wrong sequence strands merges: the rename lands before the ruleset update, or the reverse. The rename stays an explicit, separately tracked manual follow-up (item 3), which keeps this item an otherwise risk-free docs-only change.
- **Do nothing.** Rejected. The rename is docs-only with no runtime risk. The status quo imposes a small but recurring cost on every contributor and agent session that reads the checks list. This repo's working-in-parallel model makes those reads frequent.

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

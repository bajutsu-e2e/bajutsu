**English** · [日本語](BE-0149-roadmap-placeholder-format-guardrail-ja.md)

# BE-0149 — Close the roadmap-placeholder format-guardrail gap

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0149](BE-0149-roadmap-placeholder-format-guardrail.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0149") |
| Implementing PR | [#600](https://github.com/bajutsu-e2e/bajutsu/pull/600), [#610](https://github.com/bajutsu-e2e/bajutsu/pull/610) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

[PR #568](https://github.com/bajutsu-e2e/bajutsu/pull/568) repaired **BE-0137** and **BE-0138**,
which had merged carrying the pre-BE-0078/BE-0100 template — the retired `Track` metadata field and
no `Progress` section — leaving `main` failing `tests/test_roadmap_format.py`. That was not a one-off
slip: the format guardrail has a structural blind spot around `BE-0149` placeholders that let it
happen, and the same gap can reopen on any future template change. This item closes that gap with
three complementary changes: checking a placeholder's shape while it is still in review, catching
drift on long-lived open PRs that the template outgrows while nobody pushes to them, and hardening
the one path — merge-time id allocation — that lands content on `main` with no review gate at all.

## Motivation

### The format check is blind to placeholders by design

[`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)'s `_items()` collects
directories matching `^BE-(\d{4})-`; a `BE-0149-<slug>` placeholder never matches, so it is invisible
to both the format check and the index build. This is deliberate:
[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)'s
*Feasibility* section relies on exactly this property to guarantee "no red window" between a merge
landing `BE-0149` intact and the follow-up allocator commit renaming it to `BE-NNNN`.

The same exemption has a side effect BE-0089 did not account for: a placeholder's structural shape —
heading set and order, allowed metadata fields — is never checked at all, from the moment it is
scaffolded through review through the merge itself. If the canonical template changes while a
proposal sits in flight and its branch never receives another push, nothing re-evaluates it against
the new shape until it stops being a placeholder.

### What actually happened

BE-0137 ([PR #530](https://github.com/bajutsu-e2e/bajutsu/pull/530)) and BE-0138
([PR #536](https://github.com/bajutsu-e2e/bajutsu/pull/536)) were authored before
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
(2026-06-23, retired `Track`) and
[BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
(2026-06-30, added `Progress`) landed, and neither branch was ever rebased or pushed to again after
those changes reached `main`. `make check` stayed green on both PRs the whole time — the placeholder
exemption meant there was nothing for it to catch — and both merged on 2026-07-03 still carrying the
old template.

The merge-time allocator ([`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py),
run by [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml)) then renamed `BE-0149` to
`BE-0137`/`BE-0138` and rebuilt the index — a purely mechanical rename with no format validation of
its own; BE-0089's own design notes that the re-triggered `roadmap-id` run "finds no placeholders and
exits as a no-op," never re-invoking the format check. That rename commit lands by pushing directly
to protected `main` through the allocator's bypass App identity, with no PR and no required status
check standing in its way — the first moment the item's shape is actually checkable is also the one
moment nothing can stop a violation from landing. `ci.yml`'s `check` job does run again on that
`push: main`, but nothing alerts anyone to a red result there; this instance was caught only because
@hirosassa happened to run `make check` locally about eight minutes later while authoring an unrelated
proposal.

### The gap, restated

A placeholder can go through its entire lifecycle — authoring, review, drift as the template evolves
underneath it, merge, and allocation — without its shape ever being checked, and the one place it
finally becomes checkable is a direct, ungated push to `main`.

## Detailed design

Three changes, each closing a distinct window in the lifecycle above; together they leave no point
where a non-conformant item can reach `main` undetected.

### 1. Check a placeholder's structural shape during review

`^BE-(\d{4})-` is not a single check but a pattern independently hardcoded in four places:
`tests/test_roadmap_format.py`'s `_items()`, [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)
(which already carries three separate copies — `NUMBERED_DIR_RE`, `ITEM_DIR_RE`, `TITLE_RE` — and
they already disagree, since only `ITEM_DIR_RE` accepts `XXXX` today), [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py),
and [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py). Patching only
`test_roadmap_format.py` would leave the other three to keep drifting independently — `promote_roadmap_items.py`'s
`misfiled_items()` is a live instance: it skips `BE-0149` placeholders the same way, so a placeholder
whose `Status` changes while still unallocated can sit in the wrong category folder undetected, the
same root cause surfacing as folder-drift instead of template-format drift.

So the fix is one shared, stdlib-only predicate — e.g. a single `roadmap_ids.py` helper exposing the
id-shape regex(es) and an `is_placeholder_dir` / `is_numbered_dir` pair — that all four scripts import,
rather than a fifth ad hoc regex. `tests/test_roadmap_format.py`'s `_items()` then also collects
`BE-0149-<slug>` directories alongside numbered ones, and runs the existing `_check_file` heading/metadata
checks against them unchanged in substance. The only adjustment needed there is to the handful of regexes
that currently assume a 4-digit id (`TITLE_RE`, the bilingual header link, the `Proposal`/`提案` metadata
value): for a `BE-0149` directory, `BE-0149` is itself the expected, legitimate self-reference (exactly
as `test_no_unresolved_be_xxxx_references` already treats it), so those checks accept `BE-0149` in place
of `BE-\d{4}` only for items still living in a `BE-0149-<slug>` directory. Heading set and order, the
`Track` ban, and the required `Progress` section apply identically to placeholders and numbered items
alike. With this in place, a placeholder that drifts out of shape fails `make check` the next time CI
runs on it — the same review-time signal a numbered item already gets today — and `promote_roadmap_items.py`
gains the same placeholder awareness for free through the shared predicate.

### 2. Periodically re-check open roadmap PRs against the moving `main`, and open a fix PR

Item 1 alone does not help a PR that receives no further pushes, which is exactly what happened here:
BE-0078 and BE-0100 both landed while BE-0137/BE-0138 sat idle in review, and nothing re-triggered
their CI to notice. Add a workflow, triggered by a `push: main` that touches the shared id-shape
predicate or the BE template checks (plus `workflow_dispatch` for an on-demand run — not an
unconditional daily cron, since the triggering hazard is a template change, not the calendar), that:

- Lists open PRs touching `roadmaps/**` whose head branch lives in this repository, not a fork — the
  same constraint `roadmap-promote.yml` already applies, since a fork branch can't be pushed to or
  targeted by a same-repo PR.
- For each, computes the PR head merged with the current `main` tip **without pushing anything to the
  branch** — a read-only, local merge simulation — and runs the (now placeholder-aware, per item 1)
  format and index checks against that merged tree.
- On failure, runs a mechanical fixer over the same mechanical shape PR #568 applied by hand — drop
  banned metadata fields, insert any missing required `## ` section with the template's skeleton
  (`TBD` prose, an empty checklist) — and opens a small pull request whose base is the stale branch,
  carrying that fix. Posts or updates a single marker comment on the original PR linking to the fix
  PR, so the author sees the drift and a one-click remedy while it is still cheap to fix.

This item depends on item 1: re-checking a still-unallocated PR's merged tree only catches
placeholder-shape drift once the placeholder-aware format check exists, so item 2 should ship after or
together with item 1, not before it. The no-push constraint on the *detection* step is load-bearing:
BE-0089 established that a commit pushed to a PR branch after approval trips branch protection's
"dismiss stale approvals" and stalls auto-merge, which is why allocation itself was moved off the
approval-time path. A read-only merge simulation reports drift without ever touching the branch, so it
cannot dismiss a review.

The fix itself is different: it only lands when the PR author explicitly merges the fix PR, not through
an automated push. Unlike the merge-time allocator's contentless rename (which BE-0089 deliberately
keeps invisible to review, since there is nothing in it for a reviewer to re-approve), the fix here
changes the proposal's actual template content, so it is correct — not merely tolerable — for merging
it to re-trigger the branch's normal review requirements: the original approval covered a version that,
unknown to the reviewer, had already drifted out of shape.

Item 2's contribution is feedback latency and remediation cost, not the core safety property: items 1
and 3 together already guarantee that no non-conformant item reaches `main`, since item 3 validates
unconditionally at the one truly gate-free moment regardless of how stale the source branch was. Item 2
only shortens the days a stale PR can sit undetected, and turns fixing it from "re-derive the diff by
hand" (as PR #568 did) into "review and merge one generated PR."

### 3. Make the merge-time allocator self-validate before landing on `main`

`.github/workflows/roadmap-id.yml` already runs a guard between the renumber commit and the push —
[`scripts/check_renumber_diff.py`](../../scripts/check_renumber_diff.py), which caps the commit's
blast radius to `roadmaps/**` (BE-0089). Extend that same guard, rather than adding a second, parallel
one, to also call the shared predicate/checker item 1 introduces against the renumbered item, run
right after `check_renumber_diff.py` and before `git push`. On failure, abort the push (`main` is left
exactly as the merge already left it — no broken commit lands) and fail the workflow loudly, naming the
offending item and violations, so a human fixes the format via a follow-up PR immediately rather than
`main` sitting silently red.

The check called here must stay the stdlib-only function item 1 factors out of `test_roadmap_format.py`,
not a `pytest` invocation: `roadmap-id.yml` deliberately runs no dependency install today (every script
it calls — `allocate_roadmap_ids.py`, `build_roadmap_index.py`, `check_renumber_diff.py` — is stdlib-only),
matching BE-0089's "Securing the bypass identity" goal of keeping the privileged, bypass-token-holding
job's footprint minimal. Wiring in `uv sync` plus `pytest` just to re-run the test file would reintroduce
the third-party-code-alongside-the-token surface BE-0089 deliberately excluded. This is defense in depth
for the one path that bypasses review entirely: the allocator's commit lands on protected `main` through
a bypass identity with no PR and no required check, so it must carry its own gate.

## Alternatives considered

- **Require branches to be up to date before merging (or a merge queue).** Rejected: forcing a rebase
  push to the PR branch trips the same "dismiss stale approvals" problem BE-0089 designed around when
  it moved id allocation off the approval-time path — it would stall exactly the auto-merge flow the
  roadmap process depends on.
- **Rely only on the format check extension and the allocator gate (items 1 and 3), skip the periodic
  re-check (item 2).** Items 1 and 3 alone already guarantee the core safety property — no
  non-conformant item reaches `main` undetected — since item 3 validates unconditionally at merge time
  regardless of branch staleness. What this alternative gives up is feedback latency: without item 2, a
  branch like BE-0137/BE-0138's can still sit silently out of shape for days before item 3 catches it at
  merge time. Kept as a real, separable improvement rather than folded into "required to close the gap."
- **Drop the placeholder exemption altogether and require a real 4-digit id shape everywhere.**
  Rejected: this is precisely what BE-0089 designed against — a placeholder is legitimately
  unallocated, and forcing numbered-item shape onto it would reintroduce the "red window" between
  merge and allocation that BE-0089 eliminated.
- **Generic alerting on any red `push: main` CI run (e.g. a Slack notification for all workflows),
  instead of a roadmap-specific fix.** Useful defense in depth on its own, but orthogonal — it shortens
  the discovery window without preventing the broken commit from landing in the first place, and does
  nothing for drift that surfaces before merge. Worth pursuing separately; not a substitute for items
  1–3 above.
- **Push item 2's mechanical fix directly to the stale PR's branch instead of opening a fix PR against
  it.** Rejected: an unreviewed bot commit landing on someone else's branch is a bigger surprise than a
  fix PR the author chooses when to merge, and — the same problem BE-0089 designed around for the
  allocator — it dismisses stale approvals without giving the author a chance to look at the fix first.
  A fix PR keeps the same remedy but makes merging it, and any resulting re-review, an explicit choice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Extract a shared, stdlib-only id-shape predicate and adopt it (placeholder-aware) in
      `tests/test_roadmap_format.py`, `scripts/build_roadmap_index.py`, `scripts/allocate_roadmap_ids.py`,
      and `scripts/promote_roadmap_items.py`
- [x] Add a workflow, triggered when a template-affecting commit lands on `main`, that re-checks open
      roadmap PRs against current `main` read-only, and opens a fix PR against a stale branch on drift
      (depends on the item above)
- [x] Extend `scripts/check_renumber_diff.py`'s invocation in `roadmap-id.yml` to also run the shared
      check before `git push`

Items 1 and 3 — the two that together guarantee no non-conformant item reaches `main` — landed first:
the shared id-shape predicate lives in `scripts/roadmap_ids.py`, the format check moved to the
stdlib-only `scripts/check_roadmap_format.py` (now placeholder-aware), and the merge-time allocator
self-validates via `scripts/check_renumber_diff.py` before pushing ([#600](https://github.com/bajutsu-e2e/bajutsu/pull/600)).
Item 2 shipped next: `scripts/check_stale_roadmap_prs.py` re-checks every open, same-repo roadmap PR's
own files against the current template via a read-only overlay (no push to the PR's branch), and on
drift, the mechanical fixer in `scripts/fix_roadmap_drift.py` — the same narrow shape PR #568 applied
by hand — opens a small fix PR whose base is the stale branch, wired up by the
`roadmap-drift-check` workflow. All three items are in place; the item is complete.

## References

- [PR #568](https://github.com/bajutsu-e2e/bajutsu/pull/568) — the format-conformance fix that
  surfaced this gap
- [PR #530](https://github.com/bajutsu-e2e/bajutsu/pull/530),
  [PR #536](https://github.com/bajutsu-e2e/bajutsu/pull/536) — the merges that carried the stale
  template
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — merge-time id allocation; its *Feasibility* section is the source of the placeholder exemption
  this item narrows, and its *Securing the bypass identity* section is why item 3 must stay
  dependency-free
- [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md),
  [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
  — the two template changes the stale branches missed
- `tests/test_roadmap_format.py`, `scripts/build_roadmap_index.py`, `scripts/allocate_roadmap_ids.py`,
  `scripts/promote_roadmap_items.py` — the four places the id-shape pattern is duplicated today
- `scripts/check_renumber_diff.py`, `.github/workflows/roadmap-id.yml` — the existing commit-then-push
  guard item 3 extends
- `scripts/check_stale_roadmap_prs.py`, `scripts/fix_roadmap_drift.py`,
  `.github/workflows/roadmap-drift-check.yml` — item 2's periodic re-check, mechanical fixer, and the
  workflow that wires them together

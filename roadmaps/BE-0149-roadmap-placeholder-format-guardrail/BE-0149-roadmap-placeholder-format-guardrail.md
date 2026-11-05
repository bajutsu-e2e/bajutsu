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

[PR #568](https://github.com/bajutsu-e2e/bajutsu/pull/568) repaired **BE-0137** and **BE-0138**.
Both items had merged carrying the pre-BE-0078/BE-0100 template: the retired `Track` metadata field
and no `Progress` section. `main` then failed `tests/test_roadmap_format.py`.

That was not a one-off slip. The format guardrail has a structural blind spot around `BE-0149`
placeholders, and the blind spot let it happen. The same gap can reopen on any future template
change. This item closes the gap with three complementary changes:

- Check a placeholder's shape while it is still in review.
- Catch drift on long-lived open PRs that the template outgrows while nobody pushes to them.
- Harden merge-time id allocation, the one path that lands content on `main` with no review gate at
  all.

## Motivation

### The format check is blind to placeholders by design

[`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)'s `_items()` collects
directories matching `^BE-(\d{4})-`. A `BE-0149-<slug>` placeholder never matches, so it is invisible
to both the format check and the index build.

The blindness is deliberate. The *Feasibility* section of
[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) relies on
exactly this property. It guarantees "no red window" between a merge landing `BE-0149` intact and
the follow-up allocator commit renaming it to `BE-NNNN`.

The same exemption has a side effect BE-0089 did not account for. A placeholder's structural shape
(heading set and order, allowed metadata fields) is never checked. That holds from the moment the
placeholder is scaffolded, through review, to the merge itself. Suppose the canonical template
changes while a proposal sits in flight, and its branch never receives another push. Nothing
re-evaluates the proposal against the new shape until it stops being a placeholder.

### What actually happened

BE-0137 ([PR #530](https://github.com/bajutsu-e2e/bajutsu/pull/530)) and BE-0138
([PR #536](https://github.com/bajutsu-e2e/bajutsu/pull/536)) were authored before two changes landed:

- [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) (2026-06-23)
  retired `Track`.
- [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
  (2026-06-30) added `Progress`.

Neither branch was rebased or pushed to again after those changes reached `main`. `make check` stayed
green on both PRs the whole time, because the placeholder exemption left nothing for it to catch.
Both PRs merged on 2026-07-03 still carrying the old template.

The merge-time allocator then renamed `BE-0149` to `BE-0137` and `BE-0138` and rebuilt the index. The
allocator is [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py), run by
[`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml). The rename was purely mechanical, with no
format validation of its own. BE-0089's own design notes that the re-triggered `roadmap-id` run
"finds no placeholders and exits as a no-op," so it never re-invokes the format check.

The rename commit lands by pushing directly to protected `main`, through the allocator's bypass App
identity. No PR and no required status check stands in its way. The first moment the item's shape is
checkable is also the one moment nothing can stop a violation from landing.

`ci.yml`'s `check` job does run again on that `push: main`, but nothing alerts anyone to a red
result there. This instance was caught by chance: @hirosassa happened to run `make check` locally
about eight minutes later, while authoring an unrelated proposal.

### The gap, restated

A placeholder can go through its entire lifecycle without its shape ever being checked. The
lifecycle covers authoring, review, drift as the template evolves underneath it, merge, and
allocation. The one place it finally becomes checkable is a direct, ungated push to `main`.

## Detailed design

Three changes, each closing a distinct window in the lifecycle above. Together they leave no point
where a non-conformant item can reach `main` undetected.

### 1. Check a placeholder's structural shape during review

The pattern `^BE-(\d{4})-` is not a single check. It is hardcoded independently in four places:

- `tests/test_roadmap_format.py`'s `_items()`.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py). It already carries three
  separate copies (`NUMBERED_DIR_RE`, `ITEM_DIR_RE`, `TITLE_RE`), and they already disagree: today
  only `ITEM_DIR_RE` accepts `XXXX`.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py).
- [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py).

Patching `test_roadmap_format.py` alone would leave the other three to keep drifting independently.
`promote_roadmap_items.py`'s `misfiled_items()` is a live instance. It skips `BE-0149` placeholders
the same way. A placeholder whose `Status` changes while still unallocated can sit in the wrong
category folder undetected. The root cause is the same, surfacing as folder drift instead of
template-format drift.

The fix is one shared, stdlib-only predicate that all four scripts import, rather than a fifth ad hoc
regex. For example, a single `roadmap_ids.py` helper exposes the id-shape regex(es) and an
`is_placeholder_dir` / `is_numbered_dir` pair.

`tests/test_roadmap_format.py`'s `_items()` then also collects `BE-0149-<slug>` directories alongside
numbered ones. It runs the existing `_check_file` heading and metadata checks against them, unchanged
in substance.

A handful of regexes currently assume a 4-digit id: `TITLE_RE`, the bilingual header link, and the
`Proposal` / `提案` metadata value. Those need adjusting. For a `BE-0149` directory, `BE-0149` is
itself the expected, legitimate self-reference, exactly as `test_no_unresolved_be_xxxx_references`
already treats it. Those checks accept `BE-0149` in place of `BE-\d{4}`, for items still living in a
`BE-0149-<slug>` directory.

Heading set and order, the `Track` ban, and the required `Progress` section apply identically to
placeholders and numbered items.

With this in place, a placeholder that drifts out of shape fails `make check` the next time CI runs
on it. A numbered item already gets the same review-time signal today. `promote_roadmap_items.py`
gains the same placeholder awareness for free through the shared predicate.

### 2. Periodically re-check open roadmap PRs against the moving `main`, and open a fix PR

Item 1 alone does not help a PR that receives no further pushes, which is exactly what happened
here. BE-0078 and BE-0100 both landed while BE-0137 and BE-0138 sat idle in review. Nothing
re-triggered their CI to notice.

Add a workflow triggered by a `push: main` that touches the shared id-shape predicate or the BE
template checks. It also runs on `workflow_dispatch` for an on-demand run. It is not an
unconditional daily cron, since the triggering hazard is a template change, not the calendar. The
workflow does three things:

- Lists open PRs touching `roadmaps/**` whose head branch lives in this repository, not a fork.
  This is the same constraint `roadmap-promote.yml` already applies, since a fork branch can't be
  pushed to or targeted by a same-repo PR.
- For each, computes the PR head merged with the current `main` tip **without pushing anything to
  the branch**. This is a read-only, local merge simulation. The workflow runs the format and index
  checks against that merged tree. The checks are placeholder-aware, per item 1.
- On failure, runs a mechanical fixer over the same mechanical shape PR #568 applied by hand. The
  fixer drops banned metadata fields. It inserts any missing required `## ` section with the
  template's skeleton (`TBD` prose, an empty checklist). The workflow then opens a small pull
  request carrying that fix, whose base is the stale branch. It posts or updates a single marker
  comment on the original PR, linking to the fix PR. The author sees the drift and a one-click
  remedy while it is still cheap to fix.

Item 2 depends on item 1. Re-checking a still-unallocated PR's merged tree catches placeholder-shape
drift only once the placeholder-aware format check exists. Item 2 should ship after item 1 or
together with it, not before.

The no-push constraint on the *detection* step is load-bearing. BE-0089 established that a commit
pushed to a PR branch after approval trips branch protection's "dismiss stale approvals" and stalls
auto-merge. That is why allocation itself was moved off the approval-time path. A read-only merge
simulation reports drift without ever touching the branch, so it cannot dismiss a review.

The fix itself is different. It lands when the PR author explicitly merges the fix PR, not through an
automated push. Compare the merge-time allocator's contentless rename. BE-0089 deliberately keeps
that rename invisible to review, since a reviewer has nothing in it to re-approve. The fix here
changes the proposal's actual template content, so it is correct, not merely tolerable, for merging
the fix PR to re-trigger the branch's normal review requirements. The original approval covered a
version that, unknown to the reviewer, had already drifted out of shape.

Item 2's contribution is feedback latency and remediation cost, not the core safety property. Items 1
and 3 together already guarantee that no non-conformant item reaches `main`. Item 3 validates
unconditionally at the one truly gate-free moment, however stale the source branch was. Item 2
shortens the days a stale PR can sit undetected. It also turns the fix from "re-derive the diff by
hand" (as PR #568 did) into "review and merge one generated PR."

### 3. Make the merge-time allocator self-validate before landing on `main`

`.github/workflows/roadmap-id.yml` already runs a guard between the renumber commit and the push:
[`scripts/check_renumber_diff.py`](../../scripts/check_renumber_diff.py). The guard caps the commit's
blast radius to `roadmaps/**` (BE-0089).

Extend that guard rather than adding a second, parallel one. The extended guard also calls the
shared predicate/checker that item 1 introduces, against the renumbered item. The call runs right
after `check_renumber_diff.py` and before `git push`.

On failure, the workflow aborts the push. `main` is left exactly as the merge already left it, so no
broken commit lands. The workflow then fails loudly, naming the offending item and violations. A
human fixes the format through a follow-up PR immediately, rather than `main` sitting silently red.

The check called here must stay the stdlib-only function that item 1 factors out of
`test_roadmap_format.py`, not a `pytest` invocation. `roadmap-id.yml` deliberately runs no dependency
install today. Every script it calls is stdlib-only: `allocate_roadmap_ids.py`,
`build_roadmap_index.py`, and `check_renumber_diff.py`. That matches the "Securing the bypass
identity" goal of BE-0089, which keeps the footprint of the privileged, bypass-token-holding job
minimal. Wiring in `uv sync` plus `pytest` just to re-run the test file would reintroduce the
third-party-code-alongside-the-token surface that BE-0089 deliberately excluded.

This is defense in depth for the one path that bypasses review entirely. The allocator's commit lands
on protected `main` through a bypass identity, with no PR and no required check. It must carry its
own gate.

## Alternatives considered

- **Require branches to be up to date before merging (or a merge queue).** Rejected: forcing a rebase
  push to the PR branch trips the same "dismiss stale approvals" problem BE-0089 designed around when
  it moved id allocation off the approval-time path. It would stall exactly the auto-merge flow the
  roadmap process depends on.
- **Rely on the format check extension and the allocator gate (items 1 and 3) alone, and skip the
  periodic re-check (item 2).** Items 1 and 3 already guarantee the core safety property: no
  non-conformant item reaches `main` undetected. Item 3 validates unconditionally at merge time,
  however stale the branch was. What this alternative gives up is feedback latency. Without item 2, a
  branch like BE-0137's or BE-0138's can still sit silently out of shape for days before item 3
  catches it at merge time. The item is kept as a real, separable improvement rather than folded into
  "required to close the gap."
- **Drop the placeholder exemption altogether and require a real 4-digit id shape everywhere.**
  Rejected: this is precisely what BE-0089 designed against. A placeholder is legitimately
  unallocated. Forcing numbered-item shape onto it would reintroduce the "red window" between merge
  and allocation that BE-0089 eliminated.
- **Generic alerting on any red `push: main` CI run (e.g. a Slack notification for all workflows),
  instead of a roadmap-specific fix.** Useful defense in depth on its own, but orthogonal. It
  shortens the discovery window without preventing the broken commit from landing in the first
  place, and it does nothing for drift that surfaces before merge. Worth pursuing separately; not a
  substitute for items 1–3 above.
- **Push item 2's mechanical fix directly to the stale PR's branch instead of opening a fix PR
  against it.** Rejected: an unreviewed bot commit landing on someone else's branch is a bigger
  surprise than a fix PR the author chooses when to merge. It also dismisses stale approvals without
  giving the author a chance to look at the fix first, the same problem BE-0089 designed around for
  the allocator. A fix PR keeps the same remedy but makes merging it, and any resulting re-review, an
  explicit choice.

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

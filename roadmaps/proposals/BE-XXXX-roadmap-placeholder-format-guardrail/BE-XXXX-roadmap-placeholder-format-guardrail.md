**English** · [日本語](BE-XXXX-roadmap-placeholder-format-guardrail-ja.md)

# BE-XXXX — Close the roadmap-placeholder format-guardrail gap

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-roadmap-placeholder-format-guardrail.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

[PR #568](https://github.com/bajutsu-e2e/bajutsu/pull/568) repaired **BE-0137** and **BE-0138**,
which had merged carrying the pre-BE-0078/BE-0100 template — the retired `Track` metadata field and
no `Progress` section — leaving `main` failing `tests/test_roadmap_format.py`. That was not a one-off
slip: the format guardrail has a structural blind spot around `BE-XXXX` placeholders that let it
happen, and the same gap can reopen on any future template change. This item closes that gap with
three complementary changes: checking a placeholder's shape while it is still in review, catching
drift on long-lived open PRs that the template outgrows while nobody pushes to them, and hardening
the one path — merge-time id allocation — that lands content on `main` with no review gate at all.

## Motivation

### The format check is blind to placeholders by design

[`tests/test_roadmap_format.py`](../../../tests/test_roadmap_format.py)'s `_items()` collects
directories matching `^BE-(\d{4})-`; a `BE-XXXX-<slug>` placeholder never matches, so it is invisible
to both the format check and the index build. This is deliberate:
[BE-0089](../../implemented/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)'s
*Feasibility* section relies on exactly this property to guarantee "no red window" between a merge
landing `BE-XXXX` intact and the follow-up allocator commit renaming it to `BE-NNNN`.

The same exemption has a side effect BE-0089 did not account for: a placeholder's structural shape —
heading set and order, allowed metadata fields — is never checked at all, from the moment it is
scaffolded through review through the merge itself. If the canonical template changes while a
proposal sits in flight and its branch never receives another push, nothing re-evaluates it against
the new shape until it stops being a placeholder.

### What actually happened

BE-0137 ([PR #530](https://github.com/bajutsu-e2e/bajutsu/pull/530)) and BE-0138
([PR #536](https://github.com/bajutsu-e2e/bajutsu/pull/536)) were authored before
[BE-0078](../../implemented/BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
(2026-06-23, retired `Track`) and
[BE-0100](../../implemented/BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
(2026-06-30, added `Progress`) landed, and neither branch was ever rebased or pushed to again after
those changes reached `main`. `make check` stayed green on both PRs the whole time — the placeholder
exemption meant there was nothing for it to catch — and both merged on 2026-07-03 still carrying the
old template.

The merge-time allocator ([`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py),
run by [`roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml)) then renamed `BE-XXXX` to
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

Extend `_items()` in `tests/test_roadmap_format.py` to also collect `BE-XXXX-<slug>` directories
alongside numbered ones, and run the existing `_check_file` heading/metadata checks against them
unchanged in substance. The only adjustment needed is to the handful of regexes that currently assume
a 4-digit id (`TITLE_RE`, the bilingual header link, the `Proposal`/`提案` metadata value): for a
`BE-XXXX` directory, `BE-XXXX` is itself the expected, legitimate self-reference (exactly as
`test_no_unresolved_be_xxxx_references` already treats it), so those checks accept `BE-XXXX` in place
of `BE-\d{4}` only for items still living in a `BE-XXXX-<slug>` directory. Heading set and order, the
`Track` ban, and the required `Progress` section apply identically to placeholders and numbered items
alike. With this in place, a placeholder that drifts out of shape fails `make check` the next time CI
runs on it — the same review-time signal a numbered item already gets today.

### 2. Periodically re-check open roadmap PRs against the moving `main`

Change alone does not help a PR that receives no further pushes, which is exactly what happened here:
BE-0078 and BE-0100 both landed while BE-0137/BE-0138 sat idle in review, and nothing re-triggered
their CI to notice. Add a scheduled workflow (daily, plus `workflow_dispatch` for an on-demand run)
that:

- Lists open PRs touching `roadmaps/**`.
- For each, computes the PR head merged with the current `main` tip **without pushing anything to the
  branch** — a read-only, local merge simulation — and runs the (now placeholder-aware) format and
  index checks against that merged tree.
- On failure, posts or updates a single marker comment on the PR naming the drift, so the author sees
  it while it is still cheap to fix.

The no-push constraint is load-bearing: BE-0089 established that a commit pushed to a PR branch after
approval trips branch protection's "dismiss stale approvals" and stalls auto-merge, which is why
allocation itself was moved off the approval-time path. A read-only merge simulation reports drift
without ever touching the branch, so it cannot dismiss a review.

### 3. Make the merge-time allocator self-validate before landing on `main`

Harden `allocate_roadmap_ids.py` (or the surrounding `roadmap-id.yml` steps) to run the same format
check against its own output — after the `BE-XXXX` → `BE-NNNN` rename and index rebuild, before the
`git commit`/push. On failure, abort the push (`main` is left exactly as the merge already left it —
no broken commit lands) and fail the workflow loudly, naming the offending item and violations, so a
human fixes the format via a follow-up PR immediately rather than `main` sitting silently red. This is
defense in depth for the one path that bypasses review entirely: the allocator's commit lands on
protected `main` through a bypass identity with no PR and no required check, so it must carry its own
gate.

## Alternatives considered

- **Require branches to be up to date before merging (or a merge queue).** Rejected: forcing a rebase
  push to the PR branch trips the same "dismiss stale approvals" problem BE-0089 designed around when
  it moved id allocation off the approval-time path — it would stall exactly the auto-merge flow the
  roadmap process depends on.
- **Rely only on the format check extension (item 1), skip the periodic re-check (item 2).**
  Insufficient on its own: it does not address the failure mode that actually occurred, where the
  branch received no push at all for the days it sat in review while the template changed underneath
  it.
- **Drop the placeholder exemption altogether and require a real 4-digit id shape everywhere.**
  Rejected: this is precisely what BE-0089 designed against — a placeholder is legitimately
  unallocated, and forcing numbered-item shape onto it would reintroduce the "red window" between
  merge and allocation that BE-0089 eliminated.
- **Generic alerting on any red `push: main` CI run (e.g. a Slack notification for all workflows),
  instead of a roadmap-specific fix.** Useful defense in depth on its own, but orthogonal — it shortens
  the discovery window without preventing the broken commit from landing in the first place, and does
  nothing for drift that surfaces before merge. Worth pursuing separately; not a substitute for items
  1–3 above.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Extend `tests/test_roadmap_format.py` to check `BE-XXXX` placeholders' structural shape
- [ ] Add a scheduled workflow that re-checks open roadmap PRs against the current `main`, read-only
- [ ] Harden `allocate_roadmap_ids.py` / `roadmap-id.yml` to self-validate before pushing to `main`

No PR has landed yet.

## References

- [PR #568](https://github.com/bajutsu-e2e/bajutsu/pull/568) — the format-conformance fix that
  surfaced this gap
- [PR #530](https://github.com/bajutsu-e2e/bajutsu/pull/530),
  [PR #536](https://github.com/bajutsu-e2e/bajutsu/pull/536) — the merges that carried the stale
  template
- [BE-0089](../../implemented/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — merge-time id allocation; its *Feasibility* section is the source of the placeholder exemption
  this item narrows
- [BE-0078](../../implemented/BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md),
  [BE-0100](../../implemented/BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
  — the two template changes the stale branches missed
- `tests/test_roadmap_format.py`, `scripts/allocate_roadmap_ids.py`,
  `.github/workflows/roadmap-id.yml` — the code this item extends

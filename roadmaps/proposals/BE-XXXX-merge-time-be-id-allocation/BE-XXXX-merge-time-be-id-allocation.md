**English** · [日本語](BE-XXXX-merge-time-be-id-allocation-ja.md)

# BE-XXXX — Merge-time BE-ID allocation on main

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-merge-time-be-id-allocation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

A roadmap item's permanent `BE-NNNN` id is assigned the moment its pull request opens: the
[`roadmap-id`](../../../.github/workflows/roadmap-id.yml) workflow runs on `pull_request`,
allocates the next free number, claims it atomically as a `refs/be-claims/*` ref, pushes the rename
back onto the branch, and rewrites the PR title's `BE-XXXX` to the allocated id.
[BE-0061](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
hardened that path so two branches can never take the same number. The consequence is that a number
is **spent at PR-open time — before the proposal is accepted.**

This item moves allocation to **after the PR merges**, and runs it **on `main`**. An item keeps the
`BE-XXXX` placeholder all the way through authoring, review, and the merge itself: the branch is
merged *as-is*, with `BE-XXXX` intact, by auto-merge (or a merge queue). Then a workflow triggered by
the push to `main` runs the existing allocator against `main`'s tree, renames the placeholder to the
next free `BE-NNNN`, and commits the result directly to `main`. The number is therefore assigned only
to an item that has actually shipped, in merge order — so the `BE-NNNN` sequence on `main` is
**contiguous by construction** and a number is never burned by a proposal that is rejected or
abandoned.

This direction grew out of an alternative BE-0061 set aside ("assign the real id only at merge
time"). The reason it was awkward then — and the reason an earlier draft of *this* item, which
allocated on approval by pushing a rename to the branch, was itself rejected — is the same: any
commit pushed to a PR branch after approval trips branch protection's "dismiss stale approvals" and
stalls the merge. Allocating **on `main` after the merge** removes that entirely: nothing is ever
pushed to the reviewed branch after approval, so there is no review to dismiss. It is
contributor-workflow infrastructure only — no LLM enters any path, `run` and CI stay deterministic,
nothing app-specific moves into the tool — so it touches none of the prime directives. It is a direct
sibling of BE-0061: that item made allocation *collision-proof*; this one makes it *acceptance-gated*
and *gap-free*.

## Motivation

### A number is spent before the item is accepted

Allocation at PR-open means every opened roadmap PR consumes a `BE-NNNN`, whether or not it is ever
accepted. The existing machinery softens this but does not remove it:

- **Rejected PRs free their claim.** When a roadmap PR closes — merged or not —
  [`roadmap-claims-gc`](../../../.github/workflows/roadmap-claims-gc.yml) releases the
  `refs/be-claims/*` it introduced, and the branch rename never reached `main`, so a rejection alone
  leaves no row on `main`.
- **But the number sequence can still gap.** Allocation is **monotonic** — `max(used) + 1`, never the
  smallest free number. So when allocation order and merge order diverge and a *lower*-numbered PR is
  rejected, its number becomes a permanent hole. Concretely: PR-A allocates `BE-0080`, PR-B allocates
  `BE-0081`; `BE-0081` merges first; PR-A is then rejected. The next item allocates `BE-0082`, and
  `BE-0080` is gone for good.

So the `BE-NNNN` ids on `main` are not guaranteed contiguous, and an id can be permanently spent on a
proposal that never ships. Gaps are not catastrophic — ids are permanent and monotonic by design, and
the Swift-Evolution numbering Bajutsu follows tolerates them — but they invite "what was BE-00xx?"
confusion and waste a citable number.

Allocating in **merge order, on `main`** removes the gap by construction: a rejected PR never merges,
so it never reaches the allocator and never consumes a number; and because allocation runs against
`main` in the order items land, the sequence is contiguous and monotonic with no holes.

### Why allocate *after* the merge rather than at approval

The obvious place to defer allocation to is the *approval* — allocate when a reviewer approves, push
the rename to the branch, then auto-merge. That is what an earlier draft of this item proposed, and
it does not work cleanly: the allocator's commit lands on the PR branch *after* the approving review,
which trips branch protection's "dismiss stale pull request approvals when new commits are pushed".
The approval is dismissed and auto-merge stalls. Avoiding that needs either disabling stale-approval
dismissal repo-wide (blunt — every post-approval push then keeps its approval) or a bot re-approval
that GitHub does not reliably count toward required reviews.

Allocating on `main` after the merge sidesteps the whole problem: the reviewed branch is merged
exactly as approved, with `BE-XXXX` still in it, and the number is assigned by a commit to `main`,
not to the branch. No post-approval push to the branch means no dismissal — and, as a bonus, the
trigger collapses from "approval, then auto-merge, then rename" to a single "push to `main`".

## Detailed design

The allocation logic ([`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py))
is reused **unchanged**: it already finds every `BE-XXXX-<slug>/` placeholder in the working tree,
allocates `max(used) + 1` per item (sorted by slug for determinism), `git mv`s the directory and
files, rewrites the in-file token, and fixes the index rows. What changes is *where* and *when* it
runs — against `main`, after a merge — plus the workflow plumbing around it.

### Flow

1. The `ideation` skill authors the item as `BE-XXXX-<slug>` (unchanged). The PR opens with a
   `[BE-XXXX]` title.
2. The reviewer reviews and approves the `BE-XXXX` content. **No allocation happens on the branch.**
3. Auto-merge (or the merge queue) merges the branch **as-is**, `BE-XXXX` intact. No commit is pushed
   to the branch after approval, so no approval is dismissed.
4. The merge is a push to `main`. A `roadmap-id` job triggered by `push: main` runs the allocator
   against `main`, commits the rename + regenerated index directly to `main`, and (best effort)
   rewrites the merged PR's title `BE-XXXX` → `BE-NNNN`.

### The allocate-on-main workflow

`roadmap-id`'s trigger changes from `pull_request` to `push` on `main`:

```yaml
on:
  push:
    branches: [main]
    paths: ['roadmaps/**']
concurrency:
  group: roadmap-id-main
  cancel-in-progress: false   # serialize; never drop a queued allocation
permissions:
  contents: write             # commit the renumber to main
  pull-requests: write        # rewrite the merged PR's title
```

The job checks out `main`, runs the allocator, and pushes the renumber commit back to `main`:

```bash
out="$(python3 scripts/allocate_roadmap_ids.py)"      # renames BE-XXXX dirs in place
echo "$out" | grep -q '^Allocated ' || exit 0          # no placeholders -> no-op (see self-trigger)
python3 scripts/build_roadmap_index.py                 # add the now-numbered rows
git add -A && git commit -m "docs(roadmap): allocate BE IDs for merged placeholder items"
for attempt in 1 2 3 4 5; do                            # main may have moved; rebase and retry
  git push origin HEAD:main && break
  git fetch origin main && git rebase origin/main || git rebase --abort
done
```

A separate, tiny `pull_request_review` workflow enables auto-merge on approval so the flow is
hands-free (`gh pr merge --auto`); enabling auto-merge pushes no commit, so it cannot dismiss the
review. Auto-merge can equally be turned on by the author or by a merge queue — the allocator does
not depend on which.

### Feasibility

Each load-bearing assumption, made concrete:

- **A transient `BE-XXXX` on `main` keeps the gate green.** All three roadmap tools key on
  `^BE-(\d{4})-` and skip anything else: [`tests/test_roadmap_format.py`](../../../tests/test_roadmap_format.py)
  and [`tests/test_roadmap_index.py`](../../../tests/test_roadmap_index.py) (through
  [`build_roadmap_index.py`](../../../scripts/build_roadmap_index.py), whose `load_items` `continue`s
  on a non-numbered dir) and [`promote_roadmap_items.py`](../../../scripts/promote_roadmap_items.py).
  So a `BE-XXXX` directory is invisible to the format check, contributes no index row (no drift), and
  is not promoted. Empirically, `make check` passes with this very placeholder item present in the
  tree. There is therefore **no red window** on `main` between the merge commit and the renumber
  commit.
- **Allocation is contiguous and gap-free by construction.** On `main` the allocator's `used` set is
  just `main`'s numbered items, so it hands out `max + 1` in the order items merge. Merge order is
  allocation order; a rejected PR never merges, so it never consumes a number. No `max + 1`/merge-order
  divergence remains, which is the exact gap source described in *Motivation*.
- **Concurrent merges are serialized, multi-item merges handled.** `concurrency: roadmap-id-main`
  with `cancel-in-progress: false` queues allocate runs so two near-simultaneous merges renumber one
  at a time. A single push carrying several placeholders (a PR that adds two items, or two merges that
  land before the job runs) is handled already: `allocate()` iterates `placeholder_dirs()` sorted by
  slug and assigns consecutive ids. The push-back can race a later merge; the `fetch` + `rebase` +
  retry loop above reconciles it. The renumber commit itself touches `roadmaps/**` and so re-triggers
  the workflow, but that run finds no placeholders and exits as a no-op.
- **Pushing to `main` is the one new prerequisite.** Today the bot pushes renames to *PR branches*;
  here it must push the renumber commit to *protected* `main`. That needs a token allowed to push to
  `main` — a GitHub App installation token (or a PAT) on the workflow's bypass list — since the
  default `GITHUB_TOKEN` is typically blocked by `main`'s protection. This is the only genuinely new
  infrastructure requirement; the *Alternatives* note a no-direct-push fallback.
- **Rewriting the merged PR's title is best-effort.** A `push` event carries no PR number, so the job
  resolves it from the merge commit — `gh api repos/{owner}/{repo}/commits/${SHA}/pulls` — then
  `gh pr edit`. The title is cosmetic/historical, so a miss is harmless.

### Interaction with BE-0061's machinery

Allocating on `main` **serializes** allocation on a single branch, which makes the same-window race
BE-0061 closed largely moot: at most one allocate run touches `main` at a time, and it always reads
the latest `main`. The atomic `refs/be-claims/*` ledger,
[`roadmap-id-repair`](../../../.github/workflows/roadmap-id-repair.yml), and `roadmap-claims-gc` are
therefore mostly redundant under this model. To keep the scope of *this* item tight, the recommended
plan is to **leave them in place** as defense in depth (they do no harm) and treat **retiring the
claims/repair/gc complexity** as an optional follow-up once merge-time allocation has proven out.

### Prime-directive compliance

Contributor-workflow infrastructure only. No LLM is added to any path; `run` and CI stay
deterministic; nothing app-specific moves into the tool, drivers, or runner. It sits in the same
family as
[BE-0043](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
BE-0061,
[BE-0074](../../implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md),
and [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md).

## Alternatives considered

- **Allocate at approval by pushing a rename to the branch, then auto-merge.** The earlier shape of
  this item. Rejected: the post-approval bot commit trips "dismiss stale approvals" and stalls
  auto-merge; avoiding that needs disabling stale-dismissal repo-wide (blunt) or a bot re-approval
  GitHub does not reliably count. Allocating on `main` after the merge avoids any post-approval push,
  so the problem cannot arise.
- **Keep PR-open allocation; switch `max + 1` to smallest-free.** Far lower churn — no trigger change,
  no `main` push — and it would fill the hole a rejected lower-numbered PR leaves. Rejected as the
  primary path because reusing a number whose `[BE-00xx]` already appeared in a rejected PR's title
  and review thread makes that id *ambiguous* in project history, the very thing the "never reuse a
  number" rule prevents. A reasonable lighter-weight fallback.
- **Renumber via a follow-up auto-merged PR instead of a direct push to `main`.** Avoids the
  `main`-bypass token requirement by opening a tiny renumber PR. Rejected as the default: it adds a
  second PR per allocation and lengthens the window in which `BE-XXXX` sits on `main`. It is the
  natural fallback if direct pushes to `main` are disallowed by policy.
- **Accept gaps as harmless; document and stop.** Cheapest: ids are permanent and monotonic by
  design, and Swift-Evolution tolerates gaps. Rejected against the stated goal of keeping `main`'s
  numbering contiguous by construction.
- **Leave BE-0061's decision untouched (do nothing).** Rejected: the spent-id and non-contiguous
  concern is real, if minor, and is removable without reopening any of BE-0061's race guarantees.

## References

- [BE-0061 — Collision-proof BE-ID allocation](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — the item this extends; its *Alternatives considered* records the "assign id at merge time" option
  this realizes, and its hardening (atomic claims, repair, claims-gc) is reused or, optionally, later
  retired.
- [`.github/workflows/roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) (the trigger this
  moves from `pull_request` to `push: main`),
  [`roadmap-id-repair.yml`](../../../.github/workflows/roadmap-id-repair.yml),
  [`roadmap-claims-gc.yml`](../../../.github/workflows/roadmap-claims-gc.yml) — the workflows in scope.
- [`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py) (reused unchanged),
  [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py),
  [`scripts/promote_roadmap_items.py`](../../../scripts/promote_roadmap_items.py) — the three tools
  that all skip a `BE-XXXX` directory, which is what keeps `main` green during the transient window.
- [`tests/test_roadmap_format.py`](../../../tests/test_roadmap_format.py),
  [`tests/test_roadmap_index.py`](../../../tests/test_roadmap_index.py) — the gate tests that key on
  `^BE-(\d{4})-` and so ignore the placeholder.
- [`CLAUDE.md`](../../../CLAUDE.md) ·
  [`roadmaps/README.md`](../../README.md) ·
  [`docs/ai-development.md`](../../../docs/ai-development.md) — the authoring rules updated to say the
  number is allocated on `main` after the merge, not at PR-open.
- GitHub docs — *Automatically merging a pull request* (auto-merge) and *Managing a merge queue* — the
  native mechanisms that merge the `BE-XXXX` branch as-is.
